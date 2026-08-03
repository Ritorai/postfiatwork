#!/usr/bin/env python3
"""ndscan - stdlib-only static scanner for non-determinism risk patterns in Python source.

Scans a directory tree of ``.py`` files with the ``ast`` module (never regex --
see README.md "Why AST, not regex" for the rationale) and reports six classes
of non-determinism risk:

  ND001_WALL_CLOCK          wall-clock reads (datetime.now/utcnow, date.today,
                             time.time/monotonic)
  ND002_UNSORTED_LISTDIR    os.listdir/os.scandir/os.walk/glob.glob whose
                             result is not immediately wrapped in sorted()
  ND003_UNORDERED_ITERATION iterating a set/dict without sorted() where the
                             loop body accumulates into another collection
  ND004_UNSAFE_REPR         repr()/!r/%r applied to a value that may fall
                             back to default object.__repr__ (heuristic --
                             see README.md, this cannot be decided statically)
  ND005_UNSEEDED_RANDOM     random.*/secrets.* used without random.seed()
  ND006_FLOAT_IN_MONEY      float() or a float literal touching an
                             identifier that looks like a money field

Output is canonical JSON (sort_keys=True, compact separators, ASCII-only,
one trailing newline) so that identical input always produces byte-identical
output. Exit codes: 0 = clean, 1 = findings (or per-file scan errors),
2 = usage error or fatal scan setup error. See README.md for the full
contract, the exact AST pattern matched by each rule, and the documented
false positives/negatives of each rule.
"""

import argparse
import ast
import json
import os
import re
import sys
from dataclasses import dataclass

SCHEMA_VERSION = 1

RULE_IDS = (
    "ND001_WALL_CLOCK",
    "ND002_UNSORTED_LISTDIR",
    "ND003_UNORDERED_ITERATION",
    "ND004_UNSAFE_REPR",
    "ND005_UNSEEDED_RANDOM",
    "ND006_FLOAT_IN_MONEY",
)

# Severity is this tool's editorial judgement about how likely a finding is
# to matter, NOT a property of the Python language. See README.md "Severity
# is editorial". One fixed severity per rule (not per finding).
RULE_SEVERITY = {
    "ND001_WALL_CLOCK": "high",
    "ND002_UNSORTED_LISTDIR": "high",
    "ND003_UNORDERED_ITERATION": "medium",
    "ND004_UNSAFE_REPR": "low",
    "ND005_UNSEEDED_RANDOM": "high",
    "ND006_FLOAT_IN_MONEY": "medium",
}

SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}
SEVERITY_CHOICES = ("low", "medium", "high")

IGNORED_DIR_NAMES = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".pytest_cache", ".tox", "build", "dist", ".eggs",
}


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Finding:
    rule_id: str
    path: str
    line: int
    col: int
    detail: str
    severity: str

    def to_dict(self):
        return {
            "rule_id": self.rule_id,
            "path": self.path,
            "line": self.line,
            "col": self.col,
            "detail": self.detail,
            "severity": self.severity,
        }

    def sort_key(self):
        # The trailing canonical_json(self.to_dict()) is a total-order tiebreak.
        # severity is RULE_SEVERITY[rule_id], a pure function of rule_id, so two
        # findings agreeing on the five leading fields always produce the same
        # dump -- this therefore does NOT change the de-duplication behaviour
        # that also keys on sort_key(); it only makes the ordering total.
        return (
            self.rule_id,
            self.path,
            self.line,
            self.col,
            self.detail,
            canonical_json(self.to_dict()),
        )


# ---------------------------------------------------------------------------
# Import-alias resolution
#
# Shared by ND001, ND002, ND005 (and partially useful to ND003) so that
# `import time as t; t.time()` and `from datetime import datetime as dt;
# dt.now()` resolve to the same canonical dotted name as the unaliased
# spelling. This is intentionally file-scoped and not scope-aware: an
# import inside one function is treated as visible to the whole file. See
# README.md "Limitations" for what this misses.
# ---------------------------------------------------------------------------

def build_aliases(tree):
    """Map local name -> canonical dotted target, from every Import/ImportFrom
    node anywhere in the module (not scope-aware; last write wins)."""
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    aliases[top] = top
        elif isinstance(node, ast.ImportFrom):
            if node.module is None or (node.level or 0) > 0:
                # `from . import x` / `from .pkg import y`: relative imports
                # have no statically-known absolute module name here, so we
                # cannot build a canonical target. Documented false negative.
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                aliases[local] = f"{node.module}.{alias.name}"
    return aliases


def _dotted_chain(node):
    """Return root->leaf tokens for a plain Name/Attribute chain, or None if
    the chain includes anything else (e.g. a call: `f().attr`)."""
    tokens = []
    while isinstance(node, ast.Attribute):
        tokens.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        tokens.append(node.id)
        tokens.reverse()
        return tokens
    return None


def resolve_call_target(call_node, aliases):
    """Resolve a Call node's callee to a canonical dotted name using the
    file's alias map, e.g. `t.time()` with aliases={'t': 'time'} ->
    'time.time'. Returns None if the callee is not a simple Name/Attribute
    chain (e.g. `get_module().now()`)."""
    tokens = _dotted_chain(call_node.func)
    if tokens is None:
        return None
    head = aliases.get(tokens[0], tokens[0])
    if len(tokens) > 1:
        return ".".join([head] + tokens[1:])
    return head


# ---------------------------------------------------------------------------
# Parent-pointer map (needed by ND002 to see "what immediately wraps this
# call")
# ---------------------------------------------------------------------------

def build_parent_map(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


def _walk_no_nested_defs(node):
    """Like ast.walk but does not descend into nested function/class/lambda
    bodies -- used by ND003 so an accumulation call inside a nested def
    inside the loop body does not count as "the loop body accumulates"."""
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        yield from _walk_no_nested_defs(child)


# ---------------------------------------------------------------------------
# ND001_WALL_CLOCK
#
# AST pattern: a Call node whose callee, after alias resolution, is exactly
# one of the canonical targets below.
#
# Known false negatives:
#   - The module/class is never imported anywhere ndscan can see (e.g. it is
#     imported in another file and passed in, imported inside a
#     try/except ImportError fallback under a different name we didn't
#     resolve, or obtained via getattr/importlib). Resolution requires a
#     literal Name/Attribute chain rooted at a name in the alias map.
#   - `getattr(time, "time")()` or any indirection through a variable that
#     is reassigned from the module after import (aliasing is import-based
#     only, not full data-flow).
# Known false positives:
#   - A local variable that happens to be named the same as an imported
#     module/class (shadowing), e.g. a function parameter `datetime` of an
#     unrelated type, when `.now()` is called on it -- ndscan cannot tell
#     the shadowing var from the real import within the same file-wide
#     alias map.
# ---------------------------------------------------------------------------

WALL_CLOCK_TARGETS = frozenset({
    "datetime.datetime.now",
    "datetime.datetime.utcnow",
    "datetime.date.today",
    "time.time",
    "time.monotonic",
})


def check_nd001(tree, aliases):
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = resolve_call_target(node, aliases)
            if target in WALL_CLOCK_TARGETS:
                out.append((node.lineno, node.col_offset, f"call to {target}() reads the wall clock"))
    return out


# ---------------------------------------------------------------------------
# ND002_UNSORTED_LISTDIR
#
# AST pattern: a Call node whose callee resolves to one of the canonical
# targets below, whose immediate parent node is NOT `sorted(<this call>,
# ...)` with the call as the first positional argument.
#
# Known false positives:
#   - `items = os.listdir(d); items = sorted(items)` -- sorted() is applied
#     one statement later, not immediately, so this flags even though the
#     final result is in fact sorted before use.
#   - `sorted(list(os.listdir(d)))` -- sorted() wraps `list(...)`, not the
#     listdir call directly, so this flags even though the end result is
#     sorted.
# Known false negatives:
#   - `sorted` reassigned/shadowed to something that is not the builtin
#     (ndscan trusts any Call whose func Name is literally "sorted").
#   - Any wrapping helper that internally sorts, e.g. `my_sorted_listdir(d)`.
# ---------------------------------------------------------------------------

LISTDIR_TARGETS = frozenset({"os.listdir", "os.scandir", "os.walk", "glob.glob"})


def _is_immediate_sorted_wrap(call_node, parent):
    return (
        isinstance(parent, ast.Call)
        and isinstance(parent.func, ast.Name)
        and parent.func.id == "sorted"
        and bool(parent.args)
        and parent.args[0] is call_node
    )


def check_nd002(tree, aliases, parents):
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = resolve_call_target(node, aliases)
            if target in LISTDIR_TARGETS:
                parent = parents.get(id(node))
                if _is_immediate_sorted_wrap(node, parent):
                    continue
                out.append((node.lineno, node.col_offset, f"{target}() result is not immediately wrapped in sorted()"))
    return out


# ---------------------------------------------------------------------------
# ND003_UNORDERED_ITERATION
#
# AST pattern: a `for` loop whose iterable is (a) a set literal, (b) a
# `set(...)`/`frozenset(...)` call, (c) a dict literal, or (d) a
# `.keys()`/`.values()`/`.items()` method call -- and is NOT itself wrapped
# in `sorted(...)` -- where the loop body (excluding nested def/class/lambda
# bodies) contains an `.append(`/`.extend(`/`.add(`/`.update(` call or an
# `x += [...]`/`x += (...)` augmented assignment with a list/tuple literal
# RHS (a heuristic proxy for "accumulates into another collection"; plain
# scalar `total += item` is intentionally excluded, see _body_has_accum).
#
# Known false positives:
#   - The body's append/extend call is unrelated to loop order (e.g. it
#     always appends the same constant, or appends to a collection that is
#     immediately re-sorted after the loop) -- ndscan does not check what is
#     appended or what happens after the loop.
#   - `.update()`/`.add()` on the loop variable itself rather than on an
#     outer accumulator.
#   - `result += some_list_variable` (a Name, not a List/Tuple literal) is
#     not counted, even though it is genuine list concatenation, because
#     ndscan cannot tell a Name holds a list without type inference.
# Known false negatives:
#   - `for k in some_dict:` where `some_dict` is a plain Name/Attribute, not
#     a dict literal or `.keys()/.values()/.items()` call -- ndscan has no
#     type inference, so a dict referenced only by variable name is
#     invisible to this rule.
#   - Accumulation via `result[key] = value` (dict item assignment) instead
#     of `.append`/`.extend`/`+=`.
#   - Accumulation via a helper function call, e.g. `collect(item)`.
# ---------------------------------------------------------------------------

ACCUM_METHOD_NAMES = frozenset({"append", "extend", "add", "update"})


def _body_has_accum(stmts):
    for stmt in stmts:
        # A statement that is itself a nested def/class (directly in the
        # loop body, e.g. `def helper(): ...`) must be skipped at this
        # level too -- _walk_no_nested_defs only excludes such nodes when
        # they are encountered as a *child* during recursion, not when
        # they are the node passed in directly. Caught during self-testing
        # (see README.md "Bug found during development").
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for node in _walk_no_nested_defs(stmt):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ACCUM_METHOD_NAMES:
                return True
            # `x += [...]` / `x += (...)` is list/tuple concatenation -- an
            # order-sensitive accumulation. Plain scalar `total += item` is
            # deliberately NOT counted: for int accumulators addition is
            # commutative and loop order does not change the result, so
            # treating every `+=` as "accumulation" produced a false
            # positive on exactly this pattern during self-testing (see
            # README.md "Bug found during development").
            if isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add) and isinstance(node.value, (ast.List, ast.Tuple)):
                return True
    return False


def _unordered_iter_reason(it_node):
    if isinstance(it_node, ast.Call) and isinstance(it_node.func, ast.Name) and it_node.func.id == "sorted":
        return None  # already explicitly sorted, regardless of what's inside
    if isinstance(it_node, ast.Set):
        return "a set literal"
    if isinstance(it_node, ast.Dict):
        return "a dict literal"
    if isinstance(it_node, ast.Call):
        if isinstance(it_node.func, ast.Name) and it_node.func.id in ("set", "frozenset"):
            return f"a {it_node.func.id}() call"
        if isinstance(it_node.func, ast.Attribute) and it_node.func.attr in ("keys", "values", "items"):
            return f"a .{it_node.func.attr}() call (assumed dict-like)"
    return None


def check_nd003(tree):
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.For):
            reason = _unordered_iter_reason(node.iter)
            if reason and _body_has_accum(node.body):
                out.append((
                    node.lineno,
                    node.col_offset,
                    f"for-loop iterates {reason} without sorted() and the body accumulates into another collection",
                ))
    return out


# ---------------------------------------------------------------------------
# ND004_UNSAFE_REPR
#
# THIS RULE CANNOT BE DECIDED STATICALLY. Whether repr(x) is safe depends on
# x's runtime type and whether that type defines a deterministic __repr__;
# ndscan does no type inference. The heuristic actually used:
#
#   Flag repr(X) / f"{X!r}" / "...%r..." % X UNLESS X is judged "obviously
#   safe": a literal constant, a list/tuple/set/dict built entirely out of
#   other obviously-safe expressions, a Compare or BoolOp (always yields
#   bool), a unary/binary op over obviously-safe operands, or a call to one
#   of a small whitelist of builtins whose return type has a deterministic
#   repr (str, int, float, bool, list, dict, tuple, set, frozenset, bytes,
#   bytearray, complex, len, repr, sorted, abs, round, min, max, sum).
#
# What this over-fires on (false positives): repr() of ANY variable,
# attribute, subscript, or call to a user-defined/unknown function is
# flagged even when the underlying value is actually a plain int/str held in
# a variable (e.g. `n = 3; repr(n)` flags, because ndscan cannot see that
# `n` is an int).
#
# What this misses (false negatives): repr() of a call to a whitelisted
# builtin that has been shadowed by user code (e.g. a local `def str(x): ...`
# masking the builtin) is treated as safe and not flagged. Also, the object
# argument to a whitelisted builtin call is not itself checked, e.g.
# `str(some_custom_obj)` is judged safe even though `str()` will itself call
# `__str__`/`__repr__` on `some_custom_obj` -- ndscan only judges the
# outermost repr()/!r/%r site, not the whole value chain.
#
# %r handling is coarse: if the format string constant contains "%r"
# anywhere, the whole right-hand operand of `%` is flagged once, without
# matching it to the correct positional specifier when there are multiple
# `%` conversions.
# ---------------------------------------------------------------------------

SAFE_BUILTIN_CALLS = frozenset({
    "str", "int", "float", "bool", "list", "dict", "tuple", "set", "frozenset",
    "bytes", "bytearray", "complex", "len", "repr", "sorted", "abs", "round",
    "min", "max", "sum",
})


def _is_repr_safe(node):
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_repr_safe(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(_is_repr_safe(k) for k in node.keys if k is not None) and all(
            _is_repr_safe(v) for v in node.values
        )
    if isinstance(node, (ast.Compare, ast.BoolOp)):
        return True
    if isinstance(node, ast.UnaryOp):
        return _is_repr_safe(node.operand)
    if isinstance(node, ast.BinOp):
        return _is_repr_safe(node.left) and _is_repr_safe(node.right)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in SAFE_BUILTIN_CALLS:
        return True
    return False


def check_nd004(tree):
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "repr" and node.args:
            arg = node.args[0]
            if not _is_repr_safe(arg):
                out.append((node.lineno, node.col_offset, "repr() applied to a non-literal expression that may fall back to default object.__repr__"))
        elif isinstance(node, ast.JoinedStr):
            for value in node.values:
                if isinstance(value, ast.FormattedValue) and value.conversion == 114:  # ord('r')
                    if not _is_repr_safe(value.value):
                        out.append((value.lineno, value.col_offset, "f-string !r conversion applied to a non-literal expression that may fall back to default object.__repr__"))
        elif (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Mod)
            and isinstance(node.left, ast.Constant)
            and isinstance(node.left.value, str)
            and "%r" in node.left.value
        ):
            out.append((node.lineno, node.col_offset, "%r format applied to a value that may fall back to default object.__repr__ (format string contains %r)"))
    return out


# ---------------------------------------------------------------------------
# ND005_UNSEEDED_RANDOM
#
# AST pattern: any Call resolving (via alias map) to `random.<attr>` other
# than `random.seed` itself, when the module contains no call resolving to
# `random.seed` anywhere; and any Call resolving to `secrets.<attr>`
# (always -- `secrets` has no seed mechanism by design, so it is impossible
# to "clean" a secrets.* finding; this is intentional, see README.md).
#
# Known false negative (documented, not fixed): presence of a
# `random.seed()` call ANYWHERE in the module suppresses ALL random.*
# findings in that module, regardless of whether the seed call textually/
# temporally precedes the random usage. `if False: random.seed(1)` followed
# by `random.random()` is judged clean by ndscan even though the seed never
# actually executes before the random call -- ndscan does no control-flow or
# execution-order analysis.
# ---------------------------------------------------------------------------

def check_nd005(tree, aliases):
    has_seed = False
    random_calls = []
    secrets_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = resolve_call_target(node, aliases)
            if target is None:
                continue
            if target == "random.seed":
                has_seed = True
            elif target.startswith("random."):
                random_calls.append((node, target))
            elif target.startswith("secrets."):
                secrets_calls.append((node, target))

    out = []
    for node, target in secrets_calls:
        out.append((node.lineno, node.col_offset, f"call to {target}(): secrets module has no seed mechanism, always flagged for reviewer awareness"))
    if not has_seed:
        for node, target in random_calls:
            out.append((node.lineno, node.col_offset, f"call to {target}() with no random.seed() call found in this module"))
    return out


# ---------------------------------------------------------------------------
# ND006_FLOAT_IN_MONEY
#
# AST pattern (two independent triggers, both checked; a single statement
# can trip both and produce two distinct findings -- this is intentional,
# not a bug, see README.md):
#   (a) Call to bare `float(...)` whose first argument is a Name/Attribute
#       whose identifier matches MONEY_RE.
#   (b) An assignment (Assign/AugAssign/AnnAssign) target that is a
#       Name/Attribute matching MONEY_RE, whose value is a float Constant
#       literal, or a `float(...)` call.
#
# MONEY_RE = /amount|price|reward|balance|total|payout|fee|drops/i -- exactly
# as specified, i.e. an unanchored substring match with no word boundaries.
#
# Known false positives (a direct consequence of the unanchored regex, given
# verbatim by the task spec): identifiers such as `toffee_shop` (contains
# "fee"), `coffee_price` (contains both "fee" and "price"), `total_eclipse`
# style names, etc. all match even where "money" is not the intent.
#
# Known false negatives:
#   - Implicit float results never explicitly wrapped in float() or a float
#     literal, e.g. `amount = total_cents / 100` (true division always
#     yields float in Py3) is invisible to this rule.
#   - Money stored via subscript, e.g. `data["price"] = 9.99` -- the target
#     is a Subscript, not a Name/Attribute, so it is not inspected.
#   - float values passed as function arguments without being assigned,
#     e.g. `charge(9.99)` where the parameter is named `amount` in the
#     callee -- ndscan only looks at the call site's own AST, not the
#     callee's signature.
# ---------------------------------------------------------------------------

MONEY_RE = re.compile(r"(amount|price|reward|balance|total|payout|fee|drops)", re.IGNORECASE)


def _ident_of(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def check_nd006(tree):
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float" and node.args:
            arg_name = _ident_of(node.args[0])
            if arg_name and MONEY_RE.search(arg_name):
                out.append((node.lineno, node.col_offset, f"float() called on money-like identifier '{arg_name}'"))
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            value = node.value
            if value is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                name = _ident_of(t)
                if not name or not MONEY_RE.search(name):
                    continue
                if isinstance(value, ast.Constant) and isinstance(value.value, float):
                    out.append((t.lineno, t.col_offset, f"float literal assigned to money-like identifier '{name}'"))
                elif isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "float":
                    out.append((t.lineno, t.col_offset, f"float() result assigned to money-like identifier '{name}'"))
    return out


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

RULE_CHECKS = {
    "ND001_WALL_CLOCK": lambda tree, aliases, parents: check_nd001(tree, aliases),
    "ND002_UNSORTED_LISTDIR": lambda tree, aliases, parents: check_nd002(tree, aliases, parents),
    "ND003_UNORDERED_ITERATION": lambda tree, aliases, parents: check_nd003(tree),
    "ND004_UNSAFE_REPR": lambda tree, aliases, parents: check_nd004(tree),
    "ND005_UNSEEDED_RANDOM": lambda tree, aliases, parents: check_nd005(tree, aliases),
    "ND006_FLOAT_IN_MONEY": lambda tree, aliases, parents: check_nd006(tree),
}


def scan_source(source, rel_path, rule_ids):
    """Parse `source` (already-decoded text) and run the requested rules.
    Returns a list of Finding. Raises SyntaxError if `source` doesn't parse
    -- caller decides what that means for exit codes / error reporting."""
    tree = ast.parse(source, filename=rel_path)
    aliases = build_aliases(tree)
    parents = build_parent_map(tree)
    findings = []
    for rule_id in rule_ids:
        for line, col, detail in RULE_CHECKS[rule_id](tree, aliases, parents):
            findings.append(Finding(rule_id, rel_path, line, col, detail, RULE_SEVERITY[rule_id]))
    return findings


def scan_file(abs_path, rel_path, rule_ids):
    """Returns (findings, error_dict_or_None). Never raises -- I/O, decode,
    and parse failures are all captured as a per-file error entry so one bad
    file does not abort a scan of many files. See README.md "SyntaxError
    files" for why this is a finding-adjacent error, not a fatal exit-2."""
    try:
        with open(abs_path, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        msg = e.strerror or type(e).__name__
        return [], {"path": rel_path, "message": f"could not read file: {msg}"}

    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        return [], {"path": rel_path, "message": f"not valid UTF-8: {e.reason} at byte offset {e.start}"}

    try:
        findings = scan_source(source, rel_path, rule_ids)
    except SyntaxError as e:
        return [], {"path": rel_path, "message": f"SyntaxError: {e.msg} (line {e.lineno}, col {e.offset})"}

    return findings, None


def scan_root(root, rule_ids, min_severity):
    findings = []
    errors = []
    files_scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIR_NAMES]
        for fname in sorted(filenames):
            if not fname.endswith(".py"):
                continue
            abs_path = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(abs_path, root).replace(os.sep, "/")
            files_scanned += 1
            file_findings, err = scan_file(abs_path, rel_path, rule_ids)
            if err is not None:
                errors.append(err)
            findings.extend(file_findings)

    min_rank = SEVERITY_RANK[min_severity]
    findings = [f for f in findings if SEVERITY_RANK[f.severity] >= min_rank]

    # Deduplicate exact-duplicate findings defensively, then apply the
    # mandated total ordering. sort_keys=True on json.dumps only orders
    # object keys, never list elements, so this explicit sort is required
    # for canonical/reproducible output.
    findings = list({f.sort_key(): f for f in findings}.values())
    findings.sort(key=lambda f: f.sort_key())
    errors = list({(e["path"], e["message"]): e for e in errors}.values())
    errors.sort(key=lambda e: (e["path"], e["message"]))

    return findings, errors, files_scanned


def _count_by(findings, attr, universe):
    counts = {key: 0 for key in universe}
    for f in findings:
        key = getattr(f, attr)
        counts[key] = counts.get(key, 0) + 1
    return counts


def build_report(findings, errors, rule_ids, min_severity, files_scanned):
    rules_run = sorted(rule_ids)
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": "ndscan",
        "rules_run": rules_run,
        "min_severity": min_severity,
        "summary": {
            "files_scanned": files_scanned,
            "files_errored": len(errors),
            "findings_count": len(findings),
            "by_rule": _count_by(findings, "rule_id", rules_run),
            "by_severity": _count_by(findings, "severity", SEVERITY_CHOICES),
        },
        "findings": [f.to_dict() for f in findings],
        "errors": errors,
    }


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="ndscan",
        description="Scan Python source (ast-based, stdlib-only) for six non-determinism risk patterns.",
    )
    parser.add_argument("--root", required=True, help="Root directory to scan for .py files")
    parser.add_argument("-o", "--output", help="Write the canonical JSON report to this path instead of stdout")
    parser.add_argument(
        "--rule",
        action="append",
        dest="rules",
        choices=sorted(RULE_IDS),
        metavar="RULE_ID",
        help="Restrict the scan to this rule id (repeatable). Default: run all six rules.",
    )
    parser.add_argument(
        "--min-severity",
        choices=SEVERITY_CHOICES,
        default="low",
        help="Only include findings at or above this severity (default: low = include everything)",
    )
    return parser


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)  # argparse itself calls sys.exit(2) on bad usage

    if not os.path.isdir(args.root):
        print(f"ndscan: error: --root path does not exist or is not a directory: {args.root}", file=sys.stderr)
        return 2

    rule_ids = sorted(set(args.rules)) if args.rules else sorted(RULE_IDS)

    findings, errors, files_scanned = scan_root(args.root, rule_ids, args.min_severity)
    report = build_report(findings, errors, rule_ids, args.min_severity, files_scanned)
    text = canonical_json(report)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        except OSError as e:
            msg = e.strerror or type(e).__name__
            print(f"ndscan: error: could not write output file: {msg}", file=sys.stderr)
            return 2
    else:
        sys.stdout.write(text)

    return 1 if (findings or errors) else 0


if __name__ == "__main__":
    sys.exit(main())
