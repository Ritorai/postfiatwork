#!/usr/bin/env python3
"""docval.py -- README-vs-argparse documentation validator.

Stdlib-only (ast, re, json, argparse, os, sys, subprocess, shlex). See
README.md in this distribution for the full design rationale, the exact
detection rule + false-positive risk for every finding code, and the
command-execution safety contract.

Exit codes (of docval.py itself):
  0 = scan completed, no findings (README and code agree)
  1 = scan completed, findings present (documentation defects detected)
  2 = usage error or scan error (bad --root, cannot read tree, etc.)
"""

import argparse
import ast
import json
import os
import re
import shlex
import subprocess
import sys

PROG = "docval.py"
EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2

COMMAND_TIMEOUT_SECS = 60

# ---------------------------------------------------------------------------
# Canonical JSON
# ---------------------------------------------------------------------------

def canonical_dumps(obj):
    """Deterministic, byte-stable JSON: sorted keys, tight separators,
    ASCII-only, single trailing newline. No wall-clock, no absolute paths,
    no duration -- the caller must not put any of those into `obj`."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------

class Finding:
    __slots__ = ("code", "path", "detail")

    def __init__(self, code, path, detail):
        self.code = code
        self.path = path
        self.detail = detail

    def sort_key(self):
        return (self.code, self.path, self.detail)

    def to_dict(self):
        return {"code": self.code, "path": self.path, "detail": self.detail}


def relpath(path, root):
    rel = os.path.relpath(path, root)
    return rel.replace(os.sep, "/")


# ---------------------------------------------------------------------------
# argparse extraction (AST-based -- we never import target code)
# ---------------------------------------------------------------------------

class ArgparseInfo:
    def __init__(self):
        self.has_argument_parser = False
        self.has_parse_args = False
        self.flags = set()            # {"--budget-cap", "-k", ...} statically resolvable, explicitly authored
        self.implicit_flags = set()   # {"-h", "--help"} auto-added by argparse; NOT required to be documented
        self.dynamic_flag_calls = 0   # add_argument() calls whose flag text is not a literal
        self.positionals = []         # dest names of positional args (no leading '-')
        self.add_help = True          # False only if add_help=False literal is found anywhere
        self.exit_codes = set()       # statically-resolvable integers the module can exit with
        self.dynamic_exit = False     # True if some exit/return value could not be resolved
        self.parse_error_present = False  # True if module calls .error( on a parser-like object


def _literal_int(node, const_map):
    """Best-effort resolution of an int literal, possibly via a module-level
    name -> int constant. Returns (True, value) or (False, None)."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        ok, val = _literal_int(node.operand, const_map)
        if ok:
            return True, (-val if isinstance(node.op, ast.USub) else val)
        return False, None
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return True, node.value
    if isinstance(node, ast.Name) and node.id in const_map:
        return True, const_map[node.id]
    return False, None


def _collect_const_map(tree):
    """Module-level (and class-body) simple NAME = <int literal> assignments,
    so sys.exit(SOME_CONST) can be resolved without importing anything."""
    const_map = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) >= 1:
            ok, val = _literal_int(node.value, {})
            if ok:
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        const_map[t.id] = val
    return const_map


def _func_defs_by_name(tree):
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            out.setdefault(node.name, node)
    return out


def _returns_of(func_node, const_map, funcs, seen=None):
    """Collect the set of possible integer return values of a function,
    resolving one level of NAME lookups. Marks dynamic=True on unresolved
    return expressions. Also accounts for implicit `return None` (falling
    off the end of the function), which is exit code 0 in sys.exit(fn())."""
    if seen is None:
        seen = set()
    if id(func_node) in seen:
        return set(), False
    seen.add(id(func_node))

    codes = set()
    dynamic = False
    has_bare_or_missing_return = False
    saw_any_return = False

    def _walk_body(node):
        """Like ast.walk(), but does not descend into nested function
        definitions -- their own `return` statements belong to THEM, not
        to func_node, and must not leak into func_node's exit-code set."""
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            yield from _walk_body(child)

    for node in _walk_body(func_node):
        if isinstance(node, ast.Return):
            saw_any_return = True
            if node.value is None:
                has_bare_or_missing_return = True
                continue
            if isinstance(node.value, ast.Constant) and node.value.value is None:
                has_bare_or_missing_return = True
                continue
            ok, val = _literal_int(node.value, const_map)
            if ok:
                codes.add(val)
            elif isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) \
                    and node.value.func.id in funcs and node.value.func.id != func_node.name:
                sub_codes, sub_dyn = _returns_of(funcs[node.value.func.id], const_map, funcs, seen)
                codes |= sub_codes
                dynamic = dynamic or sub_dyn
            else:
                dynamic = True

    if not saw_any_return:
        # falls off the end -> implicit None -> exit code 0 when used as sys.exit(fn())
        codes.add(0)
    elif has_bare_or_missing_return:
        codes.add(0)

    return codes, dynamic


def extract_argparse_info(py_path):
    info = ArgparseInfo()
    try:
        with open(py_path, "r", encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    except OSError:
        return info
    try:
        tree = ast.parse(src, filename=py_path)
    except SyntaxError:
        return info

    const_map = _collect_const_map(tree)
    funcs = _func_defs_by_name(tree)

    parser_var_names = set()

    # Pass 1: find variables assigned from an ArgumentParser(...) constructor
    # (argparse.ArgumentParser, or `from argparse import ArgumentParser` -> ArgumentParser)
    # and variables assigned from <parser>.add_subparsers()/add_parser() results,
    # which are themselves parser-like objects supporting add_argument().
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            call = node.value
            if isinstance(call, ast.Call):
                fname = None
                if isinstance(call.func, ast.Attribute):
                    fname = call.func.attr
                elif isinstance(call.func, ast.Name):
                    fname = call.func.id
                if fname in ("ArgumentParser", "add_parser", "add_subparsers"):
                    info.has_argument_parser = True
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            parser_var_names.add(t.id)
                # add_help=False detection on ArgumentParser(...) calls
                if fname == "ArgumentParser":
                    for kw in call.keywords or []:
                        if kw.arg == "add_help" and isinstance(kw.value, ast.Constant) and kw.value.value is False:
                            info.add_help = False

    # Pass 2: any call whose func is `<name>.add_argument(...)` or `<name>.parse_args(`
    # or `<name>.error(` -- lenient (do not require perfect type proof: reduces
    # false negatives, at the statistically small risk of a non-parser object that
    # happens to also expose an add_argument()/parse_args() method).
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr
        if attr == "add_argument":
            flag_strs = []
            saw_non_literal_flag_pos = False
            positional_name = None
            for a in node.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    if a.value.startswith("-"):
                        flag_strs.append(a.value)
                    elif positional_name is None:
                        positional_name = a.value
                else:
                    saw_non_literal_flag_pos = True
            if flag_strs:
                info.flags.update(flag_strs)
            elif positional_name is not None:
                info.positionals.append(positional_name)
            if saw_non_literal_flag_pos:
                info.dynamic_flag_calls += 1
        elif attr == "parse_args":
            info.has_parse_args = True
        elif attr == "error":
            # parser.error(...) is argparse's own path to SystemExit(2)
            info.parse_error_present = True

    # Exit-code reachability.
    entry_call_names = set()
    for node in ast.walk(tree):
        call = None
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
        elif isinstance(node, ast.Call):
            call = node
        if call is None:
            continue
        fname = None
        if isinstance(call.func, ast.Attribute) and call.func.attr == "exit" and \
                isinstance(call.func.value, ast.Name) and call.func.value.id == "sys":
            fname = "sys.exit"
        elif isinstance(call.func, ast.Name) and call.func.id == "SystemExit":
            fname = "SystemExit"
        if fname is None:
            continue
        if not call.args:
            info.exit_codes.add(0)
            continue
        arg0 = call.args[0]
        ok, val = _literal_int(arg0, const_map)
        if ok:
            info.exit_codes.add(val)
        elif isinstance(arg0, ast.Call) and isinstance(arg0.func, ast.Name) and arg0.func.id in funcs:
            entry_call_names.add(arg0.func.id)
        elif isinstance(arg0, ast.Constant) and arg0.value is None:
            info.exit_codes.add(0)
        else:
            info.dynamic_exit = True

    for fn_name in entry_call_names:
        codes, dyn = _returns_of(funcs[fn_name], const_map, funcs)
        info.exit_codes |= codes
        info.dynamic_exit = info.dynamic_exit or dyn

    # argparse's own implicit behavior: -h/--help exits 0; a usage/parse
    # error (or an explicit parser.error() call) exits 2. This holds for
    # *any* module that actually builds and drives an ArgumentParser,
    # regardless of whether the author ever wrote sys.exit(0)/sys.exit(2)
    # by hand. We add these to the reachable set as a documented modeling
    # assumption (see README "Detection rules" / "Limitations").
    if info.has_argument_parser and info.has_parse_args:
        if info.add_help:
            info.exit_codes.add(0)
            info.implicit_flags.add("-h")
            info.implicit_flags.add("--help")
        info.exit_codes.add(2)

    return info


# ---------------------------------------------------------------------------
# README extraction
# ---------------------------------------------------------------------------

FENCE_RE = re.compile(r"^```([^\n`]*)\n(.*?)^```[ \t]*$", re.DOTALL | re.MULTILINE)
FLAG_TOKEN_RE = re.compile(r"(?<!\w)(--[A-Za-z][A-Za-z0-9-]*|-[A-Za-z])(?!\w)")
# `python3 -m <module> ...` lines invoke a DIFFERENT program's CLI (e.g. `python3
# -m unittest ... -v`), not the tool's own argparse parser. Every sibling
# README in this repo opens its rerun-commands block with exactly this line,
# so without this mask '-m' and '-v' would be reported as DOC002 phantom
# flags for nearly every tool -- a predictable, uninteresting false positive.
# We mask ONLY this specific idiom; flags belonging to other non-python3
# programs shown in examples (sha256sum, cmp, ...) are NOT masked and remain
# a documented false-positive risk (see README "Limitations").
MODULE_INVOCATION_LINE_RE = re.compile(r"^[ \t]*python3\s+-m\b.*$", re.MULTILINE)
# Flexible on purpose: real READMEs write this half a dozen different ways
# ("exit **0**", "exit=2", "Exit codes: `0` clean", "Exit-`2` is reserved",
# "exit code: 1"). We accept any run of whitespace/hyphen/colon/equals
# between "exit" and the number, an optional (possibly plural) "code(s)"
# word, and optional backtick/markdown-bold wrapping around the digit.
EXIT_INLINE_RE = re.compile(
    r"exit(?:[\s-]*codes?)?[\s:=-]*[\*`]{0,2}(-?\d+)[\*`]{0,2}", re.IGNORECASE
)
# The three shapes real "Exit codes" sections in this repo actually use:
#   dot/bullet prose:  "0 = within budget ... (middot) 1 = ... (middot) 2 = ..."
#   markdown table:     "| `0` | meaning |"  or  "| 0 | meaning |"
#   dash bullet list:   "- `0` - scan completed..." / "* 0: description"
# All three are scoped to the section under an "Exit code(s)" heading (see
# _exit_code_sections) to avoid treating unrelated numbered tables/lists
# elsewhere in the README as exit-code documentation.
EXIT_EQUALS_RE = re.compile(r"(?:^|[\xb7|*\-\n])\s*\*{0,2}(\d+)\*{0,2}\s*=\s*\S")
EXIT_TABLE_ROW_RE = re.compile(r"^[ \t]*\|[ \t]*`?(-?\d+)`?[ \t]*\|", re.MULTILINE)
EXIT_BULLET_RE = re.compile(r"^[ \t]*[-*][ \t]*`?(-?\d+)`?[ \t]*[-:)=]", re.MULTILINE)
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)


class ReadmeInfo:
    def __init__(self):
        self.exists = False
        self.text = ""
        self.doc_flags = set()
        self.doc_exit_codes = set()
        self.code_blocks = []  # list of (lang, body_text, start_line)


def _line_of_offset(text, offset):
    return text.count("\n", 0, offset) + 1


def _exit_code_sections(text):
    """Yield the text spans that fall under a heading whose title mentions
    'exit code(s)' (any '#' level), from just after the heading line to the
    next heading of any level (or end of document). Table rows and bullet
    lists are only trusted as exit-code documentation inside these spans --
    elsewhere a bare leading number is far too ambiguous (test counts,
    ids, thresholds, etc. also render as numbered tables/lists)."""
    headings = list(HEADING_RE.finditer(text))
    spans = []
    for i, h in enumerate(headings):
        title = h.group(2).strip()
        if re.search(r"exit\s*code", title, re.IGNORECASE):
            start = h.end()
            end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
            spans.append(text[start:end])
    return spans


def extract_readme_info(readme_path):
    info = ReadmeInfo()
    if not os.path.isfile(readme_path):
        return info
    info.exists = True
    try:
        with open(readme_path, "r", encoding="utf-8", errors="replace") as fh:
            info.text = fh.read()
    except OSError:
        return info

    flag_scan_text = MODULE_INVOCATION_LINE_RE.sub("", info.text)
    for m in FLAG_TOKEN_RE.finditer(flag_scan_text):
        info.doc_flags.add(m.group(1))

    # Global: "exit **N**" / "exit code: N" / "exit=N" -- the word "exit"
    # right next to a number is a strong, unambiguous signal wherever it
    # appears in the document (not just under a dedicated heading).
    for m in EXIT_INLINE_RE.finditer(info.text):
        try:
            info.doc_exit_codes.add(int(m.group(1)))
        except ValueError:
            pass

    # Scoped: bare numbers only count as documented exit codes inside an
    # "Exit codes" section, in any of the three real shapes seen in this
    # repo (dot-separated prose, markdown table, dash bullet list).
    for section in _exit_code_sections(info.text):
        for rx in (EXIT_EQUALS_RE, EXIT_TABLE_ROW_RE, EXIT_BULLET_RE):
            for m in rx.finditer(section):
                try:
                    info.doc_exit_codes.add(int(m.group(1)))
                except ValueError:
                    pass

    for m in FENCE_RE.finditer(info.text):
        lang = m.group(1).strip()
        body = m.group(2)
        start_line = _line_of_offset(info.text, m.start(2))
        info.code_blocks.append((lang, body, start_line))

    return info


# ---------------------------------------------------------------------------
# Repository-local entrypoint resolution (DOC005 target check + DOC009)
# ---------------------------------------------------------------------------
#
# A README command line names a file this repository is supposed to contain:
# `python3 docval.py ...`, `bash capture.sh`, `python3 -m unittest test_docval`.
# Whether that file EXISTS is a static question, and it is a different
# question from whether docval is willing to RUN the line. The safety gate
# below refuses most launchers outright, and before this resolver existed a
# refusal was all a broken entrypoint ever produced: `bash capture.sh` and
# `bash capture_typo.sh` were reported identically, as one more DOC006
# "not a python3 invocation". That is the blind spot DOC009 closes.
#
# Two working directories, because this repository's READMEs genuinely use
# both:
#
#   * the tool's own directory      -- `python3 driftcheck.py --root .`
#   * the directory containing it   -- `python3 transcript-drift/driftcheck.py`
#     (i.e. the repository root)
#
# A target that exists under either one is not broken. Resolving against only
# the tool directory is what made DOC005 report three non-existent defects in
# transcript-drift/README.md, whose command block is written to be run from
# the repository root and is correct as written.

# Characters that mean "fill this in yourself", not a real filename.
PLACEHOLDER_RE = re.compile(r"[{}<>*?$]|\bFILE\b|\bPATH\b|\bDIR\b")

# Launchers whose next non-flag word is a repository-local script.
SCRIPT_LAUNCHERS = ("bash", "sh")
PYTHON_LAUNCHERS = ("python3", "python")

# `python3 -m unittest <this>` is treated as naming a local module only when
# it looks like one of this repository's test modules. It deliberately does
# not match `discover`.
TEST_MODULE_RE = re.compile(r"^test[_A-Za-z0-9]*$")


class EntrypointRef:
    """A repository-local file that a README command line says it will run.

    `spec` is the path relative to whichever working directory the line
    assumes, exactly as it must resolve on disk (a `-m` module has already
    been turned into a `.py` path). `needs_exec` is true only for the
    `./script` form, where the shell requires the executable bit."""

    def __init__(self, spec, form, needs_exec):
        self.spec = spec
        self.form = form            # "script" | "module"
        self.needs_exec = needs_exec


def _module_to_relpath(module):
    """`a.b.c` -> `a/b/c.py`. Only the leading dotted path is a module; a
    unittest target like `test_x.TestY.test_z` is reduced by the caller."""
    return os.path.join(*module.split(".")) + ".py"


def _first_non_flag(tokens):
    """The first word that is not a flag, or None.

    `-c` is not skipped like an ordinary flag: it consumes the next word as
    an inline program, so `bash -c 'echo hello'` names no file at all.
    Treating `echo hello` as a path is how a shell one-liner in a README
    turns into a phantom broken entrypoint."""
    for tok in tokens:
        if tok == "-c":
            return None
        if tok.startswith("-"):
            continue
        return tok
    return None


def entrypoint_of(argv):
    """The repository-local entrypoint `argv` references, or None.

    Deliberately small. It recognizes only the launcher forms that actually
    occur in this repository's READMEs; anything unrecognized returns None
    and is left entirely to the safety gate, so DOC009 can never invent a
    finding for a command shape nobody writes."""
    if not argv:
        return None
    head = argv[0]

    if head.startswith("./"):
        return EntrypointRef(head[2:], "script", True)

    if head in SCRIPT_LAUNCHERS:
        target = _first_non_flag(argv[1:])
        return EntrypointRef(target, "script", False) if target else None

    if head in PYTHON_LAUNCHERS:
        rest = argv[1:]
        if not rest:
            return None
        if rest[0] == "-m":
            if len(rest) < 2:
                return None
            if rest[1] != "unittest":
                # `-m pip`, `-m venv`, `-m json.tool`, `-m http.server`: a
                # module name says nothing about where the module lives, and
                # an installed one is not a file in this repository. There is
                # no way to tell a typo'd local module from a stdlib one
                # without importing, so nothing is claimed. Reporting these
                # was five false positives on shapes this repository writes.
                return None
            target = _first_non_flag(rest[2:])
            if target is None:
                return None
            # `test_x.TestY.test_z` -- only `test_x` is the module.
            module = target.split(".")[0]
            if not TEST_MODULE_RE.match(module):
                # `python3 -m unittest discover` -- `discover` is a
                # subcommand, not a module, and it is the most common
                # invocation in this repository. Anything that is not
                # obviously a local test module is left alone.
                return None
            return EntrypointRef(_module_to_relpath(module), "module", False)
        if rest[0].startswith("-"):
            return None             # -c, --version, ... : no file named
        return EntrypointRef(rest[0], "script", False)

    return None


def entrypoint_bases(tool_dir):
    """The working directories a README line may assume, most specific
    first: the tool directory, then the directory containing it."""
    tool_abs = os.path.abspath(tool_dir)
    parent = os.path.dirname(tool_abs)
    return [tool_abs] if parent == tool_abs else [tool_abs, parent]


def is_repo_local_spec(spec):
    """Is `spec` a plain repository-local relative path?

    False for an absolute path, a `..` escape, and placeholder text such as
    `{REPORT}` or `path/to/FILE`. Those are the safety gate's business or
    are not filenames at all, and reporting them as broken entrypoints
    would be wrong.

    This is a SEPARATE question from "does the file exist", and the two
    must not be collapsed. `resolve_entrypoint` returns (None, None) for
    both, which is why callers ask this first: a review found that folding
    them together turned every one of these specs into a phantom finding,
    including absolute paths naming files that were really there."""
    if not spec or os.path.isabs(spec):
        return False
    if PLACEHOLDER_RE.search(spec):
        return False
    parts = spec.replace("\\", "/").split("/")
    return os.pardir not in parts


def resolve_entrypoint(spec, tool_dir):
    """Return (abs_path, base) for the first working directory under which
    `spec` names an existing file, else (None, None). Specs that are not
    repository-local paths at all never reach the disk."""
    if not is_repo_local_spec(spec):
        return None, None
    for base in entrypoint_bases(tool_dir):
        candidate = os.path.normpath(os.path.join(base, spec))
        if os.path.isfile(candidate):
            return candidate, base
    return None, None


def entrypoint_finding_detail(ref, tool_dir):
    """The DOC009 detail for `ref`, or None if the entrypoint is fine.

    Two distinct defects share the code because they are the same mistake
    from a reader's point of view -- the documented command cannot start:

      * the file does not exist under either working directory;
      * the file exists but the `./` form was used and it is not executable,
        so the shell will refuse it with "Permission denied".
    """
    if not is_repo_local_spec(ref.spec):
        return None
    resolved, _base = resolve_entrypoint(ref.spec, tool_dir)
    if resolved is None:
        if ref.form == "module":
            return ("references module %r, but %s exists neither in this tool "
                    "directory nor in the directory containing it"
                    % (ref.spec[:-3].replace(os.sep, "."), ref.spec))
        return ("references %r, which exists neither in this tool directory "
                "nor in the directory containing it" % ref.spec)
    if ref.needs_exec and not os.access(resolved, os.X_OK):
        return ("runs './%s', but that file is not executable, so the shell "
                "cannot start it" % ref.spec)
    return None


# ---------------------------------------------------------------------------
# Command block safety gate + execution (DOC005 / DOC006)
# ---------------------------------------------------------------------------

METACHAR_RE = re.compile(r"[|&<>`]|\$\(")
ECHO_EXIT_TRAILER_RE = re.compile(r"^echo\b.*\$\?")
CONT_BACKSLASH_RE = re.compile(r"(?<!\\)\\\s*$")


def _join_continuations(body):
    """Join lines ending in a lone trailing backslash (shell line
    continuation) so a command that "spans multiple lines" is evaluated as
    one logical command."""
    raw_lines = body.split("\n")
    logical = []
    buf = None
    buf_start = None
    for idx, line in enumerate(raw_lines):
        if buf is None:
            buf_start = idx
            buf = line
        else:
            buf += " " + line.strip()
        if CONT_BACKSLASH_RE.search(buf):
            buf = CONT_BACKSLASH_RE.sub("", buf).rstrip()
            continue
        logical.append((buf_start, buf))
        buf = None
    if buf is not None:
        logical.append((buf_start, buf))
    return logical


def _split_echo_trailer(line):
    """Strip a trailing `; echo "exit=$?"`-style idiom (pure exit-code
    echoing; never changes program behavior). Any OTHER use of ';' to chain
    additional commands is left intact so the metachar gate refuses it.
    Returns (core_command, was_stripped)."""
    if ";" not in line:
        return line, False
    parts = [p.strip() for p in line.split(";")]
    if len(parts) == 2 and ECHO_EXIT_TRAILER_RE.match(parts[1]):
        return parts[0], True
    return line, False


class CommandOutcome:
    def __init__(self, kind, detail):
        self.kind = kind  # "ok", "DOC005", "DOC006"
        self.detail = detail


def evaluate_command_line(raw_line, tool_dir, no_run):
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None  # not a command at all

    core, _stripped = _split_echo_trailer(line)
    core = core.strip()
    if not core:
        return None

    if ";" in core:
        return CommandOutcome("DOC006_COMMAND_BLOCK_UNPARSEABLE", "refused: ';' chains multiple shell commands (%r)" % core)

    meta = METACHAR_RE.search(core)
    if meta:
        return CommandOutcome("DOC006_COMMAND_BLOCK_UNPARSEABLE", "refused: shell metacharacter %r present in %r" % (meta.group(0), core))

    try:
        argv = shlex.split(core, comments=False, posix=True)
    except ValueError as exc:
        return CommandOutcome("DOC006_COMMAND_BLOCK_UNPARSEABLE", "refused: could not safely tokenize command %r (%s)" % (core, exc))

    if not argv:
        return None

    if argv[0] != "python3":
        return CommandOutcome("DOC006_COMMAND_BLOCK_UNPARSEABLE", "refused: not a python3 invocation (got %r)" % argv[0])

    if len(argv) < 2:
        return CommandOutcome("DOC006_COMMAND_BLOCK_UNPARSEABLE", "refused: python3 invoked with no target file")

    target = argv[1]
    if target.startswith("-"):
        return CommandOutcome(
            "DOC006_COMMAND_BLOCK_UNPARSEABLE",
            "refused: python3 invoked with %r (module/flag form, not a file inside the tool "
            "directory) -- e.g. '-m unittest' is refused under the hard contract" % target,
        )

    if os.path.isabs(target):
        return CommandOutcome("DOC006_COMMAND_BLOCK_UNPARSEABLE", "refused: absolute target path %r not allowed" % target)

    norm_tool_dir = os.path.normpath(tool_dir)
    target_path = os.path.normpath(os.path.join(tool_dir, target))
    if target_path != norm_tool_dir and not target_path.startswith(norm_tool_dir + os.sep):
        return CommandOutcome("DOC006_COMMAND_BLOCK_UNPARSEABLE", "refused: target path %r escapes the tool directory" % target)

    if not os.path.isfile(target_path):
        # Whether the target exists is now one question with one answer, asked
        # by the entrypoint resolver for every launcher form and reported as
        # DOC009. Two cases arrive here and both stop here rather than
        # producing a DOC005:
        #
        #   * the file is genuinely absent -- DOC009 says so, and says it in
        #     the same words whether the line was `python3 x.py` or
        #     `bash x.sh`, which is the point of having one code;
        #   * the file exists relative to the directory CONTAINING the tool,
        #     because the line is written to be run from the repository root
        #     (`python3 transcript-drift/driftcheck.py --root .`). That line
        #     is correct as written, so it is not a finding at all.
        #
        # Either way there is nothing to execute, so return before the
        # subprocess call below.
        return None

    if no_run:
        return None  # static-only mode: safety gate already passed, do not execute

    full_argv = ["python3", target_path] + argv[2:]
    try:
        proc = subprocess.run(
            full_argv,
            cwd=tool_dir,
            timeout=COMMAND_TIMEOUT_SECS,
            capture_output=True,
            text=True,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return CommandOutcome("DOC005_COMMAND_BLOCK_FAILED", "does not run as written: timed out after %ss (%r)" % (COMMAND_TIMEOUT_SECS, core))
    except OSError as exc:
        return CommandOutcome("DOC005_COMMAND_BLOCK_FAILED", "does not run as written: failed to start (%s) (%r)" % (exc, core))

    if "Traceback (most recent call last):" in (proc.stderr or ""):
        return CommandOutcome("DOC005_COMMAND_BLOCK_FAILED", "does not run as written: uncaught exception while running %r" % core)

    return None  # ran fine (any exit code, including nonzero, is acceptable)


def is_command_bearing_block(lang, body):
    lang_l = lang.lower()
    if any(tag in lang_l for tag in ("bash", "sh", "shell", "console", "zsh")):
        return True
    if "python3 " in body:
        return True
    return False


def line_changes_directory(raw_line):
    """Is this logical line a `cd`?

    Decided by tokenizing the line and looking at its first word, not by
    searching the block text for `cd`. A regex over the body both
    over-matched -- `python3 -c "import os; cd nowhere"` exempted a whole
    block on a `cd` inside a quoted Python string -- and under-matched, on
    an indented `  cd somewhere`. Both were found by review."""
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return False
    try:
        argv = shlex.split(line, comments=False, posix=True)
    except ValueError:
        # Unparseable: the working directory after it is anyone's guess, so
        # treat it as a change rather than assume it is not one.
        return True
    return bool(argv) and argv[0] == "cd"


def check_entrypoint_line(raw_line, tool_dir):
    """DOC009 for one logical command line, or None.

    Runs beside the safety gate rather than inside it. A refused line still
    produces its DOC006 -- docval still will not execute it, and that fact is
    unchanged -- but if the line also names a repository-local file that is
    not there, that is a second, more specific fact and it gets its own
    finding. Purely static: nothing here executes anything, so DOC009 is
    identical with and without --no-run."""
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None
    core, _stripped = _split_echo_trailer(line)
    core = core.strip()
    if not core:
        return None
    try:
        argv = shlex.split(core, comments=False, posix=True)
    except ValueError:
        return None                 # unparseable: DOC006's business
    ref = entrypoint_of(argv)
    if ref is None:
        return None
    detail = entrypoint_finding_detail(ref, tool_dir)
    if detail is None:
        return None
    return "%s (%r)" % (detail, core)


def check_command_blocks(readme_info, tool_dir, readme_rel_path, no_run):
    findings = []
    for lang, body, start_line in readme_info.code_blocks:
        if not is_command_bearing_block(lang, body):
            continue
        # A block that changes directory sets a working directory this
        # checker cannot know (`cd postfiatwork/schema-checker` after a
        # `git clone` names a directory that does not exist yet). Entrypoint
        # resolution is skipped for the whole block rather than guessed at.
        # The safety gate is unaffected: DOC005/DOC006 still apply.
        lines = _join_continuations(body)
        block_changes_directory = any(
            line_changes_directory(text) for _off, text in lines)
        for offset, logical_line in lines:
            lineno = start_line + offset
            outcome = evaluate_command_line(logical_line, tool_dir, no_run)
            if outcome is not None:
                findings.append(
                    Finding(
                        outcome.kind,
                        readme_rel_path,
                        "%s:%d: %s" % (readme_rel_path, lineno, outcome.detail),
                    )
                )
            if block_changes_directory:
                continue
            entry_detail = check_entrypoint_line(logical_line, tool_dir)
            if entry_detail is not None:
                findings.append(
                    Finding(
                        "DOC009_BROKEN_ENTRYPOINT",
                        readme_rel_path,
                        "%s:%d: %s" % (readme_rel_path, lineno, entry_detail),
                    )
                )
    return findings


# ---------------------------------------------------------------------------
# Per-tool comparison (DOC001-004, DOC007, DOC008)
# ---------------------------------------------------------------------------

def find_cli_py_files(tool_dir):
    out = []
    for name in sorted(os.listdir(tool_dir)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(tool_dir, name)
        if not os.path.isfile(path):
            continue
        info = extract_argparse_info(path)
        if info.has_argument_parser and info.has_parse_args:
            out.append((name, path, info))
    return out


def check_tool_dir(tool_dir, root, no_run):
    findings = []
    rel_dir = relpath(tool_dir, root)
    readme_path = os.path.join(tool_dir, "README.md")
    readme_info = extract_readme_info(readme_path)
    cli_files = find_cli_py_files(tool_dir)

    if cli_files and not readme_info.exists:
        names = ", ".join(n for n, _, _ in cli_files)
        findings.append(Finding(
            "DOC007_NO_README", rel_dir,
            "%s: directory contains an argparse CLI (%s) but no README.md" % (rel_dir, names),
        ))
        return findings  # nothing else to compare without a README

    if readme_info.exists and not cli_files:
        findings.append(Finding(
            "DOC008_NO_CLI", rel_dir,
            "%s: directory contains README.md but no discoverable argparse CLI "
            "(.py file that builds an ArgumentParser and calls parse_args)" % rel_dir,
        ))
        return findings

    if not readme_info.exists and not cli_files:
        return findings  # nothing to validate here

    readme_rel = rel_dir + "/README.md"

    for py_name, py_path, info in cli_files:
        py_rel = rel_dir + "/" + py_name

        for flag in sorted(info.flags):
            if flag not in readme_info.doc_flags:
                findings.append(Finding(
                    "DOC001_UNDOCUMENTED_FLAG", readme_rel,
                    "%s: argparse in %s defines flag '%s' which appears nowhere in %s"
                    % (readme_rel, py_rel, flag, readme_rel),
                ))

        for flag in sorted(readme_info.doc_flags):
            if flag not in info.flags and flag not in info.implicit_flags:
                findings.append(Finding(
                    "DOC002_PHANTOM_FLAG", readme_rel,
                    "%s: documents flag '%s' which %s does not define via argparse"
                    % (readme_rel, flag, py_rel),
                ))

        if not info.dynamic_exit:
            for code in sorted(readme_info.doc_exit_codes):
                if code not in info.exit_codes:
                    findings.append(Finding(
                        "DOC003_EXIT_CODE_UNREACHABLE", readme_rel,
                        "%s: documents exit code %d but no sys.exit/return path in %s can produce it"
                        % (readme_rel, code, py_rel),
                    ))

        for code in sorted(info.exit_codes):
            if code not in readme_info.doc_exit_codes:
                findings.append(Finding(
                    "DOC004_EXIT_CODE_UNDOCUMENTED", readme_rel,
                    "%s: %s can exit with code %d but %s never documents exit code %d"
                    % (readme_rel, py_rel, code, readme_rel, code),
                ))

    if readme_info.exists:
        findings.extend(check_command_blocks(readme_info, tool_dir, readme_rel, no_run))

    return findings


# ---------------------------------------------------------------------------
# Tool-directory discovery
# ---------------------------------------------------------------------------

SKIP_DIR_NAMES = {"__pycache__", ".git", ".hg", ".svn", ".mypy_cache", ".pytest_cache"}


def _dir_has_readme_or_cli(d):
    readme = os.path.isfile(os.path.join(d, "README.md"))
    cli = bool(find_cli_py_files(d))
    return readme or cli


def discover_tool_dirs(root):
    """A 'tool dir' is the first directory found on each descent path that
    directly contains README.md and/or a qualifying argparse CLI .py file.
    Once such a directory is found we do not descend further into it -- this
    keeps nested fixture directories (e.g. a tool's own sample bundles that
    happen to carry their own tiny README+".py" pair) from being reported as
    separate top-level tools. See README "Limitations"."""
    result = []

    def walk(d):
        try:
            entries = sorted(os.listdir(d))
        except OSError:
            return
        if _dir_has_readme_or_cli(d):
            result.append(d)
            return
        for name in entries:
            if name in SKIP_DIR_NAMES or name.startswith("."):
                continue
            full = os.path.join(d, name)
            if os.path.isdir(full):
                walk(full)

    walk(root)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser():
    p = argparse.ArgumentParser(
        prog=PROG,
        description="Check README documentation against argparse CLI code (stdlib-only).",
    )
    p.add_argument("--root", default=".", help="root directory to scan (default: current directory)")
    p.add_argument("-o", "--output", default=None, help="write the canonical JSON report to this path (default: stdout)")
    p.add_argument("--no-run", action="store_true", help="skip actually executing README command blocks (static checks only)")
    return p


def run(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    root = args.root
    if not os.path.isdir(root):
        sys.stderr.write("docval: error: --root %r is not a directory\n" % root)
        return EXIT_ERROR

    root = os.path.abspath(root)

    try:
        tool_dirs = discover_tool_dirs(root)
    except Exception as exc:  # scan error, not a documentation finding
        sys.stderr.write("docval: error: scan failed: %s\n" % exc)
        return EXIT_ERROR

    all_findings = []
    try:
        for d in tool_dirs:
            all_findings.extend(check_tool_dir(d, root, args.no_run))
    except Exception as exc:
        sys.stderr.write("docval: error: scan failed: %s\n" % exc)
        return EXIT_ERROR

    all_findings.sort(key=lambda f: f.sort_key())

    counts = {}
    for f in all_findings:
        counts[f.code] = counts.get(f.code, 0) + 1

    report = {
        "tool_count": len(tool_dirs),
        "finding_count": len(all_findings),
        "counts": counts,
        "findings": [f.to_dict() for f in all_findings],
        "ok": len(all_findings) == 0,
    }

    out_text = canonical_dumps(report)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(out_text)
        except OSError as exc:
            sys.stderr.write("docval: error: could not write output file: %s\n" % exc)
            return EXIT_ERROR
    else:
        sys.stdout.write(out_text)

    return EXIT_FINDINGS if all_findings else EXIT_OK


def main(argv=None):
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
