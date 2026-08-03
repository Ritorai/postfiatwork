#!/usr/bin/env python3
"""weakassert - a stdlib-only static scanner for weak unittest tests.

Scans a directory tree of Python unittest-style test suites and reports
tests that look "weak": no assertion, call-only ("does not raise") checks,
expected values that are themselves derived from the module under test, and
skipped tests.

The scanner works purely on the ``ast`` module - it never uses regular
expressions on source text, and it never imports or executes the code it
scans. See README.md for the exact detection rules and their known blind
spots.

Exit codes:
    0 - scan completed, no findings (after any --category filter)
    1 - scan completed, findings were reported
    2 - usage error or scan error (bad --root, invalid --category, ...)
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys

SCHEMA_VERSION = 1

WA001 = "WA001_NO_ASSERTION"
WA002 = "WA002_CALL_ONLY"
WA003 = "WA003_SELF_DERIVED_EXPECTATION"
WA004 = "WA004_SKIPPED_TEST"

ALL_CATEGORIES = (WA001, WA002, WA003, WA004)

TEST_METHOD_PREFIX = "test"  # matches unittest.TestLoader.testMethodPrefix

SKIP_DECORATOR_NAMES = {"skip", "skipIf", "skipUnless"}

IGNORED_DIR_NAMES = {
    "__pycache__", ".git", ".hg", ".svn", ".venv", "venv", "env",
    "node_modules", ".tox", ".mypy_cache", ".pytest_cache", "build", "dist",
}

# Fallback for interpreters older than 3.10 (sys.stdlib_module_names added
# in 3.10). This list only needs to be "good enough" to keep obvious stdlib
# imports (json, os, unittest, ...) out of the "subject module" guess - see
# README's WA003 section for what this heuristic can and cannot see.
_STDLIB_FALLBACK = {
    "__future__", "abc", "argparse", "array", "ast", "asyncio", "atexit",
    "base64", "bisect", "builtins", "bz2", "calendar", "collections",
    "configparser", "contextlib", "copy", "copyreg", "csv", "ctypes",
    "dataclasses", "datetime", "decimal", "difflib", "dis", "doctest",
    "email", "enum", "errno", "faulthandler", "fnmatch", "fractions",
    "functools", "gc", "getopt", "getpass", "glob", "gzip", "hashlib",
    "heapq", "hmac", "html", "http", "imaplib", "importlib", "inspect",
    "io", "ipaddress", "itertools", "json", "keyword", "linecache",
    "locale", "logging", "lzma", "math", "mimetypes", "mmap",
    "multiprocessing", "numbers", "operator", "os", "pathlib", "pickle",
    "platform", "plistlib", "pprint", "queue", "quopri", "random", "re",
    "reprlib", "sched", "secrets", "select", "selectors", "shelve",
    "shlex", "shutil", "signal", "site", "smtplib", "socket",
    "socketserver", "sqlite3", "ssl", "stat", "statistics", "string",
    "stringprep", "struct", "subprocess", "sys", "sysconfig", "tarfile",
    "tempfile", "textwrap", "threading", "time", "timeit", "tkinter",
    "token", "tokenize", "trace", "traceback", "tracemalloc", "types",
    "typing", "unicodedata", "unittest", "urllib", "uuid", "venv",
    "warnings", "weakref", "webbrowser", "xml", "xmlrpc", "zipapp",
    "zipfile", "zipimport", "zlib", "zoneinfo",
}


def _stdlib_module_names():
    names = getattr(sys, "stdlib_module_names", None)
    if names:
        return set(names)
    return set(_STDLIB_FALLBACK)


STDLIB_MODULES = _stdlib_module_names()


# --------------------------------------------------------------------------
# File discovery
# --------------------------------------------------------------------------

def is_test_filename(name):
    if not name.endswith(".py"):
        return False
    return name.startswith("test_") or name.endswith("_test.py")


def discover_test_files(root):
    """Return a sorted list of absolute paths to test files under root."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in IGNORED_DIR_NAMES and not d.startswith(".")
        )
        for fname in sorted(filenames):
            if is_test_filename(fname):
                found.append(os.path.join(dirpath, fname))
    found.sort()
    return found


# --------------------------------------------------------------------------
# Subject-module resolution (used by WA002 / WA003)
# --------------------------------------------------------------------------

def resolve_subject_aliases(tree):
    """Guess which bound names in this file refer to "the module under
    test", from this file's own import statements.

    Rule (documented in README): any name bound by an `import X [as Y]` or
    `from X import A [as B]` statement, where X is not a recognised stdlib
    module, is treated as referring to the subject module. Whole-module
    binds (import X, import X as Y) go into `modules`; symbol-level binds
    (from X import A as B) go into `symbols`. Relative imports (`from . import
    x`) are always treated as subject, since a relative import can only
    point at local package code.
    """
    modules = set()
    symbols = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in STDLIB_MODULES:
                    continue
                bound = alias.asname or top
                modules.add(bound)
        elif isinstance(node, ast.ImportFrom):
            is_relative = (node.level or 0) > 0
            top = (node.module or "").split(".")[0] if node.module else ""
            if not is_relative and top in STDLIB_MODULES:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound = alias.asname or alias.name
                symbols.add(bound)
    return modules, symbols


def _descend_to_name(expr):
    """Descend through Attribute/Call/Subscript wrappers to find the
    left-most ast.Name, e.g. mod.Class(x).method[0] -> Name('mod')."""
    cur = expr
    seen = 0
    while seen < 200:
        seen += 1
        if isinstance(cur, ast.Attribute):
            cur = cur.value
        elif isinstance(cur, ast.Call):
            cur = cur.func
        elif isinstance(cur, ast.Subscript):
            cur = cur.value
        elif isinstance(cur, ast.Starred):
            cur = cur.value
        else:
            break
    if isinstance(cur, ast.Name):
        return cur.id
    return None


def contains_subject_call(expr, subject_modules, subject_symbols):
    """True if `expr` contains (anywhere, at any depth) a Call into the
    resolved subject module/symbols of this file."""
    if expr is None:
        return False
    for node in ast.walk(expr):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        root = _descend_to_name(func)
        if root is not None and root in subject_modules:
            return True
        if isinstance(func, ast.Name) and func.id in subject_symbols:
            return True
    return False


def _local_assign_map(func_node):
    """Map var name -> list of value-expr ASTs for simple single-target
    top-level `name = <expr>` assignments in this function's body (not
    inside if/for/while/try). Used to resolve ONE level of local-variable
    indirection when checking assertEqual arguments for subject-module
    calls, e.g. `actual = subject.compute(7); self.assertEqual(actual,
    subject.compute(7))`. Chained aliasing (`b = a` where `a` itself was
    bound to a subject call) is NOT followed - see README limitations.
    """
    mapping = {}
    for stmt in func_node.body:
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
        ):
            mapping.setdefault(stmt.targets[0].id, []).append(stmt.value)
    return mapping


def contains_subject_call_with_locals(expr, local_assigns, subject_modules, subject_symbols):
    """Like contains_subject_call, but additionally resolves a bare Name
    argument one level through `_local_assign_map`. This is what WA003
    uses so that `actual = subject.compute(7); self.assertEqual(actual,
    subject.compute(7))` is still caught.

    Deliberately narrow: only resolved when the *entire* assertEqual
    argument expression is a single bare Name (e.g. `actual`), NOT when a
    Name merely appears somewhere nested inside a larger expression (e.g.
    `sorted(rels)`, `len(shingles(...))`). The broader "resolve every Name
    anywhere in the tree" version was tried during development and turned
    the common, legitimate `assertEqual(x, sorted(x))` /
    `assertEqual(x, len(y))` idioms - which are real assertions, not
    tautologies - into a wall of false positives when `x`/`y` happened to
    be assigned from a subject-module call earlier in the test. See
    README "WA003 false-positive risk" for the concrete example.
    """
    if expr is None:
        return False
    if contains_subject_call(expr, subject_modules, subject_symbols):
        return True
    if isinstance(expr, ast.Name):
        for value_expr in local_assigns.get(expr.id, []):
            if contains_subject_call(value_expr, subject_modules, subject_symbols):
                return True
    return False


def collect_subject_calls(expr, subject_modules, subject_symbols):
    """Return unparsed source text of every subject-module Call inside
    expr, for use in finding detail messages."""
    out = []
    if expr is None:
        return out
    for node in ast.walk(expr):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        root = _descend_to_name(func)
        hit = (root is not None and root in subject_modules) or (
            isinstance(func, ast.Name) and func.id in subject_symbols
        )
        if hit:
            out.append(_unparse(node))
    return out


# --------------------------------------------------------------------------
# Assertion detection
# --------------------------------------------------------------------------

def _is_assert_call_func(func):
    if isinstance(func, ast.Attribute):
        return func.attr.startswith("assert") or func.attr == "fail"
    if isinstance(func, ast.Name):
        return func.id.startswith("assert") or func.id == "fail"
    return False


def has_direct_assertion(func_node):
    """True if func_node's body contains, anywhere at any nesting depth
    (if/for/while/try/with/subTest/...), a bare `assert` statement or a
    call whose attribute/name starts with "assert" or is "fail" (this
    covers self.assertX(...), cls.assertX(...), bare assertX(...), and
    `with self.assertRaises(...):` since the context expression is itself
    such a Call node).

    Known blind spot: this also matches inside nested function/class
    definitions that are lexically present in the test body but never
    called (dead code), and it does not verify that a bound "self"/"cls"
    name in a helper actually refers to the TestCase instance.
    """
    for node in ast.walk(func_node):
        if node is func_node:
            continue
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call) and _is_assert_call_func(node.func):
            return True
    return False


def _self_attr_call_name(call):
    """If `call` is `self.<name>(...)` or `cls.<name>(...)`, return <name>."""
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id in ("self", "cls"):
            return func.attr
    return None


def _bare_call_name(call):
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def has_assertion_via_helper(func_node, class_methods, module_functions):
    """One-level helper resolution: if the test body calls `self.<h>(...)`
    where <h> is a method of the same class (not itself a test_* method),
    or calls a bare `<h>(...)` that resolves to a module-level function,
    and that helper's OWN body (checked with has_direct_assertion, i.e.
    NOT recursing into what the helper calls) contains an assertion, the
    test counts as asserting.

    This intentionally goes only one level deep. A test that calls
    `self._check(...)` where `_check` calls `self._check2(...)` which
    contains the actual `self.assertEqual` will NOT be resolved and will
    be reported as WA001. See README "What WA001 cannot resolve".
    """
    checked_helper_names = set()
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        name = _self_attr_call_name(node)
        if name is not None and name in class_methods:
            if name in checked_helper_names:
                continue
            checked_helper_names.add(name)
            helper_node = class_methods[name]
            if helper_node is not func_node and has_direct_assertion(helper_node):
                return True, name
            continue
        name = _bare_call_name(node)
        if name is not None and name in module_functions:
            if name in checked_helper_names:
                continue
            checked_helper_names.add(name)
            helper_node = module_functions[name]
            if helper_node is not func_node and has_direct_assertion(helper_node):
                return True, name
    return False, None


# --------------------------------------------------------------------------
# WA002: call-only bodies
# --------------------------------------------------------------------------

def _is_docstring_stmt(stmt, index):
    return (
        index == 0
        and isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def call_only_body_calls(func_node):
    """If every non-docstring top-level statement in func_node's body is
    either a bare call-expression statement or a simple assignment whose
    value is a single Call, return the list of those Call nodes.
    Otherwise return None.

    This only looks at TOP-LEVEL statements of the function body (not
    inside if/for/while/try), so a call-only assertion wrapped in a
    redundant `if True:` will not be recognised as call-only. This is a
    documented limitation, not an oversight - see README.
    """
    calls = []
    body = func_node.body
    for i, stmt in enumerate(body):
        if _is_docstring_stmt(stmt, i):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            calls.append(stmt.value)
            continue
        if (
            isinstance(stmt, ast.Assign)
            and isinstance(stmt.value, ast.Call)
        ):
            calls.append(stmt.value)
            continue
        if isinstance(stmt, ast.Pass):
            continue
        # Any other statement kind (if/for/while/try/with/return/raise/...)
        # disqualifies this test from the (deliberately narrow) WA002
        # pattern.
        return None
    return calls


# --------------------------------------------------------------------------
# WA004: skip detection
# --------------------------------------------------------------------------

def _decorator_skip_reason(decorator):
    node = decorator
    if isinstance(node, ast.Call):
        func = node.func
    else:
        func = node
    if isinstance(func, ast.Attribute) and func.attr in SKIP_DECORATOR_NAMES:
        return func.attr
    if isinstance(func, ast.Name) and func.id in SKIP_DECORATOR_NAMES:
        return func.id
    return None


def skip_decorator_names(decorator_list):
    found = []
    for dec in decorator_list:
        name = _decorator_skip_reason(dec)
        if name is not None:
            found.append(name)
    return found


def has_unconditional_skip_test_call(func_node):
    """True if a top-level statement in the body is a bare
    `self.skipTest(...)` expression call (not nested inside an if/for/...).
    """
    for stmt in func_node.body:
        if (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Call)
            and _self_attr_call_name(stmt.value) == "skipTest"
        ):
            return True
    return False


# --------------------------------------------------------------------------
# Source helpers
# --------------------------------------------------------------------------

def _unparse(node):
    try:
        text = ast.unparse(node)
    except Exception:
        text = "<unparseable>"
    if len(text) > 200:
        text = text[:197] + "..."
    return text


# --------------------------------------------------------------------------
# Per-file scan
# --------------------------------------------------------------------------

class Finding:
    __slots__ = ("category", "path", "line", "test_name", "detail")

    def __init__(self, category, path, line, test_name, detail):
        self.category = category
        self.path = path
        self.line = line
        self.test_name = test_name
        self.detail = detail

    def sort_key(self):
        return (self.category, self.path, self.line, self.test_name)

    def to_dict(self):
        return {
            "category": self.category,
            "path": self.path,
            "line": self.line,
            "test_name": self.test_name,
            "detail": self.detail,
        }


def _is_test_def(node):
    return (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith(TEST_METHOD_PREFIX)
    )


def _collect_class_methods(class_node):
    methods = {}
    for node in class_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods[node.name] = node
    return methods


def _collect_module_functions(tree):
    funcs = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[node.name] = node
    return funcs


def _class_has_skip_decorator(class_node):
    names = skip_decorator_names(class_node.decorator_list)
    return names[0] if names else None


def scan_test_node(test_node, qualified_name, rel_path, subject_modules,
                    subject_symbols, class_methods, module_functions,
                    class_skip_name):
    """Run all four detectors against a single test function/method node.
    Returns a list of Finding objects. Categories are independent: a single
    test may appear once in several categories.
    """
    findings = []
    line = test_node.lineno

    # WA004 first (decorators + skipTest body), independent of the rest.
    own_skip_names = skip_decorator_names(test_node.decorator_list)
    skip_reasons = list(own_skip_names)
    if class_skip_name is not None:
        skip_reasons.append("class:" + class_skip_name)
    body_skip = has_unconditional_skip_test_call(test_node)
    if body_skip:
        skip_reasons.append("self.skipTest")
    if skip_reasons:
        findings.append(Finding(
            WA004, rel_path, line, qualified_name,
            "skip marker(s): " + ", ".join(skip_reasons),
        ))

    # WA001 (+ one-level helper resolution)
    direct = has_direct_assertion(test_node)
    via_helper, helper_name = (False, None)
    if not direct:
        via_helper, helper_name = has_assertion_via_helper(
            test_node, class_methods, module_functions
        )
    has_assertion = direct or via_helper
    if not has_assertion:
        detail = "no self.assert*/fail/bare-assert/assertRaises found in body"
        detail += " (checked one level of self./bare helper calls)"
        findings.append(Finding(WA001, rel_path, line, qualified_name, detail))

    # WA002: call-only body that calls into the subject module.
    call_nodes = call_only_body_calls(test_node)
    if not has_assertion and call_nodes:
        subject_call_texts = []
        any_subject = False
        for c in call_nodes:
            if contains_subject_call(c, subject_modules, subject_symbols):
                any_subject = True
                subject_call_texts.append(_unparse(c))
        if any_subject:
            detail = "body is calls only, no assertion; subject calls: " + \
                ", ".join(subject_call_texts)
            findings.append(Finding(WA002, rel_path, line, qualified_name, detail))

    # WA003: self.assertEqual(actual, expected) where BOTH sides call into
    # the subject module.
    for node in ast.walk(test_node):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "assertEqual"):
            continue
        args = list(node.args)
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        first = args[0] if len(args) > 0 else kw.get("first")
        second = args[1] if len(args) > 1 else kw.get("second")
        if first is None or second is None:
            continue
        local_assigns = _local_assign_map(test_node)
        first_hit = contains_subject_call_with_locals(first, local_assigns, subject_modules, subject_symbols)
        second_hit = contains_subject_call_with_locals(second, local_assigns, subject_modules, subject_symbols)
        if first_hit and second_hit:
            detail = "assertEqual(%s, %s) - both sides call the subject module" % (
                _unparse(first), _unparse(second),
            )
            findings.append(Finding(WA003, rel_path, node.lineno, qualified_name, detail))

    return findings


def scan_file(path, root):
    """Parse and scan a single file. Returns (findings, tests_scanned,
    error) where error is a dict {"path", "message"} or None."""
    rel_path = os.path.relpath(path, root).replace(os.sep, "/")
    try:
        with open(path, "r", encoding="utf-8", errors="strict") as fh:
            source = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        return [], 0, {"path": rel_path, "message": "read error: %s" % exc}

    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        return [], 0, {"path": rel_path, "message": "syntax error: %s" % exc}

    subject_modules, subject_symbols = resolve_subject_aliases(tree)
    module_functions = _collect_module_functions(tree)

    findings = []
    tests_scanned = 0

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            class_methods = _collect_class_methods(node)
            class_skip_name = _class_has_skip_decorator(node)
            for member in node.body:
                if _is_test_def(member):
                    tests_scanned += 1
                    qualified = "%s.%s" % (node.name, member.name)
                    findings.extend(scan_test_node(
                        member, qualified, rel_path, subject_modules,
                        subject_symbols, class_methods, module_functions,
                        class_skip_name,
                    ))
        elif _is_test_def(node):
            tests_scanned += 1
            findings.extend(scan_test_node(
                node, node.name, rel_path, subject_modules, subject_symbols,
                {}, module_functions, None,
            ))

    return findings, tests_scanned, None


# --------------------------------------------------------------------------
# Report assembly
# --------------------------------------------------------------------------

def build_report(root, category_filter=None):
    files = discover_test_files(root)
    all_findings = []
    errors = []
    tests_scanned = 0

    for path in files:
        findings, n_tests, error = scan_file(path, root)
        tests_scanned += n_tests
        if error is not None:
            errors.append(error)
        all_findings.extend(findings)

    if category_filter:
        wanted = set(category_filter)
        all_findings = [f for f in all_findings if f.category in wanted]

    all_findings.sort(key=Finding.sort_key)
    errors.sort(key=lambda e: e["path"])

    by_category = {c: 0 for c in ALL_CATEGORIES}
    for f in all_findings:
        by_category[f.category] += 1
    if category_filter:
        by_category = {c: by_category[c] for c in ALL_CATEGORIES if c in set(category_filter)}

    report = {
        "schema_version": SCHEMA_VERSION,
        "tool": "weakassert",
        "files_scanned": len(files),
        "tests_scanned": tests_scanned,
        "category_filter": sorted(category_filter) if category_filter else None,
        "summary": {
            "findings_total": len(all_findings),
            "findings_by_category": by_category,
            "files_with_errors": len(errors),
        },
        "errors": errors,
        "findings": [f.to_dict() for f in all_findings],
    }
    return report


def to_canonical_json(report):
    return json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ) + "\n"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="weakassert",
        description="Scan unittest test suites for weak/low-signal tests.",
    )
    parser.add_argument(
        "--root", default=".", help="root directory to scan (default: .)",
    )
    parser.add_argument(
        "-o", "--output", default=None,
        help="write JSON report to this file instead of stdout",
    )
    parser.add_argument(
        "--category", action="append", default=None, metavar="CAT",
        choices=list(ALL_CATEGORIES),
        help="restrict scan/verdict to this category; may be repeated",
    )
    return parser


def main(argv=None):
    parser = build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse already prints its own usage error and uses code 2 for
        # errors / 0 for --help; keep that behaviour as-is.
        return exc.code if isinstance(exc.code, int) else 2

    root = args.root
    if not os.path.isdir(root):
        sys.stderr.write("weakassert: error: --root %r is not a directory\n" % root)
        return 2

    try:
        report = build_report(root, category_filter=args.category)
    except Exception as exc:  # pragma: no cover - defensive
        sys.stderr.write("weakassert: error: scan failed: %s\n" % exc)
        return 2

    text = to_canonical_json(report)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        except OSError as exc:
            sys.stderr.write("weakassert: error: cannot write %r: %s\n" % (args.output, exc))
            return 2
    else:
        sys.stdout.write(text)

    return 1 if report["summary"]["findings_total"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
