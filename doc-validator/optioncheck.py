#!/usr/bin/env python3
"""optioncheck.py -- cross-tool shared command-line option checker.

WHAT IT ANSWERS

`--root` appears in a dozen CLIs in this repository. `-o` appears in most
of them. Nothing checked that they mean the same thing. This answers one
question: **when two tools accept the same long option, do they accept it
the same way?** Same action, same value-taking shape, same `type`, same
`choices`. A reviewer who learns `--root` from one tool should not be
surprised by it in another.

WHY IT LIVES IN doc-validator/ AND NOT ITS OWN DIRECTORY

`docval.py` already does the hard and dangerous part: it AST-parses every
`add_argument(...)` call in the repository without ever importing the
target module (see its README, "Why AST, never import"). Building a second
argparse extractor beside it would be a duplicate of the exact code most
worth having only one of. So this module **imports docval** and reuses its
discovery (`discover_tool_dirs`, `find_cli_py_files`), its constant
resolution (`_collect_const_map`) and its output conventions (`relpath`,
`canonical_dumps`). What it adds is the part docval does not model:
`docval.ArgparseInfo` records flags as a bare set of strings, which is
enough to ask "is this flag documented?" and not enough to ask "do two
tools agree about it?". This module extracts the option *shape*.

THE FOUR DIMENSIONS COMPARED

  action       the literal `action=` value, or "store" when absent
  takes_value  whether a value follows the flag, derived from action and
               nargs rather than guessed
  type         the literal `type=` name (`int`, `float`, a function name),
               or null when absent
  choices      the sorted literal `choices=` members, or null when absent

Two tools "conflict" on an option when they disagree on any of these.
Agreement on all four is reported as a match, so the report says what is
consistent as well as what is not.

WHAT IT DELIBERATELY WILL NOT GUESS

An `add_argument(*spec)`, `choices=SOME_CONSTANT` that is not a literal
list, or `type=` referencing a subscript or lambda cannot be resolved from
the syntax tree without executing code. Those are collected into
`unsupported_dynamic` with the reason, and are **excluded from conflict
comparison entirely** rather than compared on their resolvable half. A
half-resolved option compared against a fully-resolved one produces
confident nonsense; listing it as unsupported produces a true statement.

Usage:
    python3 optioncheck.py --root ..
    python3 optioncheck.py --root .. -o option_report.json
    python3 optioncheck.py --root .. --option --root

Exit codes:
    0  scan completed, no conflicts
    1  scan completed, at least one conflict
    2  usage error (--root is not a directory)
"""
import argparse
import ast
import os
import sys

import docval

PROG = "optioncheck.py"
EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2

#: Actions that consume no value from the command line.
VALUELESS_ACTIONS = frozenset({
    "store_true", "store_false", "store_const", "append_const",
    "count", "help", "version",
})

#: The action argparse uses when none is given.
DEFAULT_ACTION = "store"

#: Compared dimensions, in report order.
DIMENSIONS = ("action", "takes_value", "type", "choices")

#: argparse adds these itself; comparing them across tools is noise.
IMPLICIT_OPTIONS = frozenset({"--help", "-h"})


def _literal(node):
    """Return (ok, value) for a node that is a plain literal."""
    if isinstance(node, ast.Constant):
        return True, node.value
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        out = []
        for elt in node.elts:
            ok, val = _literal(elt)
            if not ok:
                return False, None
            out.append(val)
        return True, out
    return False, None


def _name_of(node):
    """`int` -> 'int'; `mod.fn` -> 'mod.fn'; anything else -> None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name_of(node.value)
        return "%s.%s" % (base, node.attr) if base else None
    return None


def _kw(call, name):
    for kw in call.keywords or []:
        if kw.arg == name:
            return kw.value
    return None


def spec_from_call(call, const_map):
    """Describe one add_argument(...) call.

    Returns a dict with `options` (the literal flag strings), the four
    compared dimensions, and `dynamic` -- a list of reasons this call could
    not be fully resolved. A non-empty `dynamic` means the call is reported
    but never compared.
    """
    dynamic = []
    options = []
    positional = None
    for a in call.args:
        ok, val = _literal(a)
        if ok and isinstance(val, str):
            if val.startswith("-"):
                options.append(val)
            elif positional is None:
                positional = val
        else:
            dynamic.append("non-literal option string")

    if getattr(call, "keywords", None):
        for kw in call.keywords:
            if kw.arg is None:
                dynamic.append("**kwargs expansion")

    action_node = _kw(call, "action")
    action = DEFAULT_ACTION
    if action_node is not None:
        ok, val = _literal(action_node)
        if ok and isinstance(val, str):
            action = val
        else:
            named = _name_of(action_node)
            if named and named in const_map:
                action = str(const_map[named])
            else:
                dynamic.append("non-literal action=")

    nargs_node = _kw(call, "nargs")
    nargs = None
    if nargs_node is not None:
        ok, val = _literal(nargs_node)
        if ok:
            nargs = val
        else:
            dynamic.append("non-literal nargs=")

    type_node = _kw(call, "type")
    type_name = None
    if type_node is not None:
        type_name = _name_of(type_node)
        if type_name is None:
            dynamic.append("non-literal type=")

    choices_node = _kw(call, "choices")
    choices = None
    if choices_node is not None:
        ok, val = _literal(choices_node)
        if ok and isinstance(val, list):
            try:
                choices = sorted(str(c) for c in val)
            except TypeError:                      # pragma: no cover
                dynamic.append("unsortable choices=")
        else:
            dynamic.append("non-literal choices=")

    if action in VALUELESS_ACTIONS:
        takes_value = False
    elif nargs == 0:
        takes_value = False
    else:
        takes_value = True

    return {
        "options": sorted(options),
        "action": action,
        "takes_value": takes_value,
        "type": type_name,
        "choices": choices,
        "dynamic": sorted(set(dynamic)),
        "positional": positional,
    }


def specs_in_file(py_path):
    """Every add_argument(...) spec in one file, source order preserved."""
    try:
        with open(py_path, "r", encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    except OSError:
        return []
    try:
        tree = ast.parse(src, filename=py_path)
    except SyntaxError:
        return []
    const_map = docval._collect_const_map(tree)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "add_argument":
            continue
        spec = spec_from_call(node, const_map)
        spec["line"] = getattr(node, "lineno", 0)
        out.append(spec)
    out.sort(key=lambda s: (s["line"], s["options"]))
    return out


def long_options(spec):
    """The comparable keys of a spec: its long options only.

    Short flags are deliberately not the grouping key. `-o` means "output"
    almost everywhere here but `-k` does not mean one thing, and grouping on
    a single letter would manufacture conflicts out of unrelated options.
    Short flags are still reported, as `aliases`, so a reader can see which
    spellings travel together.
    """
    return sorted(o for o in spec["options"]
                  if o.startswith("--") and o not in IMPLICIT_OPTIONS)


def tool_dirs(root):
    """The tool directories one level under `root`.

    Not `docval.discover_tool_dirs`, and the reason is worth stating: that
    function stops descending at the first directory carrying a README.md,
    and this repository's root carries one -- so it returns the root itself
    and nothing else. It is right for docval's own "point me at a tree"
    contract and wrong here. One level down is the layout every other
    repo-wide tool in this repository already assumes.
    """
    out = []
    for name in sorted(os.listdir(root)):
        if name.startswith(".") or name in docval.SKIP_DIR_NAMES:
            continue
        d = os.path.join(root, name)
        if os.path.isdir(d):
            out.append(d)
    return out


def collect(root):
    """-> (usages, dynamic) across every tool directory."""
    usages = []
    dynamic = []
    for tool_dir in tool_dirs(root):
        rel_dir = docval.relpath(tool_dir, root)
        for name, path, _info in docval.find_cli_py_files(tool_dir):
            rel_file = docval.relpath(path, root)
            for spec in specs_in_file(path):
                longs = long_options(spec)
                entry = {
                    "tool": rel_dir,
                    "file": rel_file,
                    "line": spec["line"],
                    "options": spec["options"],
                    "aliases": sorted(o for o in spec["options"]
                                      if not o.startswith("--")),
                    "action": spec["action"],
                    "takes_value": spec["takes_value"],
                    "type": spec["type"],
                    "choices": spec["choices"],
                }
                if spec["dynamic"]:
                    entry["reasons"] = spec["dynamic"]
                    dynamic.append(entry)
                    continue
                if not longs:
                    continue
                for opt in longs:
                    e = dict(entry)
                    e["option"] = opt
                    usages.append(e)
    usages.sort(key=lambda e: (e["option"], e["tool"], e["file"], e["line"]))
    dynamic.sort(key=lambda e: (e["file"], e["line"], e["options"]))
    return usages, dynamic


def shape_of(usage):
    return tuple(usage[d] if not isinstance(usage[d], list)
                 else tuple(usage[d]) for d in DIMENSIONS)


def compare(usages, only_option=None):
    """Group usages by long option and classify each group."""
    by_option = {}
    for u in usages:
        if only_option and u["option"] != only_option:
            continue
        by_option.setdefault(u["option"], []).append(u)

    options = []
    for opt in sorted(by_option):
        group = by_option[opt]
        shapes = {}
        for u in group:
            shapes.setdefault(shape_of(u), []).append(u)
        variants = []
        for shape in sorted(shapes, key=lambda s: [str(x) for x in s]):
            members = shapes[shape]
            variants.append({
                "action": members[0]["action"],
                "takes_value": members[0]["takes_value"],
                "type": members[0]["type"],
                "choices": members[0]["choices"],
                "used_by": sorted({m["tool"] for m in members}),
                "sites": [{"file": m["file"], "line": m["line"],
                           "aliases": m["aliases"]} for m in members],
            })
        tools = sorted({u["tool"] for u in group})
        if len(variants) > 1:
            differing = [d for d in DIMENSIONS
                         if len({str(v[d]) for v in variants}) > 1]
            state = "conflict"
        else:
            differing = []
            state = "match" if len(tools) > 1 else "single_use"
        options.append({
            "option": opt,
            "state": state,
            "tools": tools,
            "tool_count": len(tools),
            "usage_count": len(group),
            "differing_dimensions": differing,
            "variants": variants,
        })
    return options


def build_report(root, only_option=None):
    usages, dynamic = collect(root)
    options = compare(usages, only_option)
    counts = {"conflict": 0, "match": 0, "single_use": 0}
    for o in options:
        counts[o["state"]] += 1
    return {
        "schema_version": 1,
        "tool": "optioncheck",
        "option_filter": only_option,
        "counts": counts,
        "totals": {
            "options": len(options),
            "usages": len(usages),
            "unsupported_dynamic": len(dynamic),
        },
        "dimensions": list(DIMENSIONS),
        "conflicts": [o for o in options if o["state"] == "conflict"],
        "options": options,
        "unsupported_dynamic": dynamic,
    }


def build_arg_parser():
    ap = argparse.ArgumentParser(prog=PROG)
    ap.add_argument("--root", default="..",
                    help="repository root to scan (default: ..)")
    ap.add_argument("-o", "--output", help="write the JSON report here")
    ap.add_argument("--option",
                    help="restrict the report to one long option, e.g. --root")
    return ap


def run(argv=None):
    args = build_arg_parser().parse_args(argv)
    if not os.path.isdir(args.root):
        sys.stderr.write("%s: --root is not a directory: %s\n"
                         % (PROG, args.root))
        return EXIT_ERROR
    report = build_report(os.path.abspath(args.root), args.option)
    text = docval.canonical_dumps(report)
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return EXIT_FINDINGS if report["counts"]["conflict"] else EXIT_OK


def main(argv=None):
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
