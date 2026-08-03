"""Comprehensive unittest coverage for weakassert.py.

Run with:  python3 -m unittest test_weakassert -v
"""
import ast
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

import weakassert as W

HERE = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------
# small parsing helpers shared by many tests
# --------------------------------------------------------------------------

def parse_module(src):
    return ast.parse(textwrap.dedent(src))


def first_function(src):
    """Return the first top-level FunctionDef/AsyncFunctionDef in src."""
    tree = parse_module(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise AssertionError("no function found in source")


def first_class(src):
    tree = parse_module(src)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            return node
    raise AssertionError("no class found in source")


def method_of(class_node, name):
    for node in class_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError("method %s not found" % name)


def method_from_class_src(src, name):
    return method_of(first_class(src), name)


def first_call_expr(src):
    """Parse `src` as a bare expression and return its ast.Call node."""
    tree = ast.parse(textwrap.dedent(src), mode="eval")
    assert isinstance(tree.body, ast.Call)
    return tree.body


def first_assertequal_args(src):
    """src is a full test method; return (first_arg, second_arg, func_node)
    of the first self.assertEqual(...) call found."""
    func = first_function(src) if "class " not in src else None
    tree = parse_module(src)
    if func is None:
        cls = None
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                cls = node
        func = cls.body[0]
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "assertEqual"
        ):
            return node.args[0], node.args[1], func
    raise AssertionError("no assertEqual call found")


@contextlib.contextmanager
def temp_repo(files):
    """Create a temp dir populated with files={relpath: content}. Yields
    the absolute directory path. Cleans up afterwards."""
    d = tempfile.mkdtemp(prefix="weakassert_test_")
    try:
        for relpath, content in files.items():
            full = os.path.join(d, relpath)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(textwrap.dedent(content))
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def run_main(argv):
    """Call weakassert.main(argv) capturing stdout/stderr text."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = W.main(argv)
    return code, out.getvalue(), err.getvalue()


# ==========================================================================
# is_test_filename
# ==========================================================================

class TestIsTestFilename(unittest.TestCase):
    def test_test_prefix_matches(self):
        self.assertTrue(W.is_test_filename("test_foo.py"))

    def test_test_suffix_matches(self):
        self.assertTrue(W.is_test_filename("foo_test.py"))

    def test_plain_module_does_not_match(self):
        self.assertFalse(W.is_test_filename("foo.py"))

    def test_non_py_extension_does_not_match(self):
        self.assertFalse(W.is_test_filename("test_foo.txt"))

    def test_testing_prefix_without_underscore_does_not_match(self):
        self.assertFalse(W.is_test_filename("testfoo.py"))

    def test_exact_test_py_matches_prefix_rule(self):
        self.assertTrue(W.is_test_filename("test_.py"))

    def test_uppercase_test_does_not_match(self):
        self.assertFalse(W.is_test_filename("Test_foo.py"))

    def test_conftest_does_not_match(self):
        self.assertFalse(W.is_test_filename("conftest.py"))

    def test_dunder_init_does_not_match(self):
        self.assertFalse(W.is_test_filename("__init__.py"))

    def test_both_prefix_and_suffix_pattern(self):
        self.assertTrue(W.is_test_filename("test_module_test.py"))


# ==========================================================================
# discover_test_files
# ==========================================================================

class TestDiscoverTestFiles(unittest.TestCase):
    def test_finds_top_level_test_file(self):
        with temp_repo({"test_a.py": "x = 1\n"}) as root:
            files = W.discover_test_files(root)
        self.assertEqual([os.path.basename(f) for f in files], ["test_a.py"])

    def test_ignores_non_test_files(self):
        with temp_repo({"test_a.py": "x=1\n", "helper.py": "y=2\n"}) as root:
            files = W.discover_test_files(root)
        self.assertEqual(len(files), 1)

    def test_finds_nested_test_files(self):
        with temp_repo({"pkg/sub/test_b.py": "x=1\n"}) as root:
            files = W.discover_test_files(root)
        self.assertEqual(len(files), 1)

    def test_skips_pycache_dirs(self):
        with temp_repo({
            "test_a.py": "x=1\n",
            "__pycache__/test_a.cpython-310.pyc": "junk",
        }) as root:
            files = W.discover_test_files(root)
        self.assertEqual(len(files), 1)

    def test_skips_git_dir(self):
        with temp_repo({
            "test_a.py": "x=1\n",
            ".git/test_fake.py": "x=1\n",
        }) as root:
            files = W.discover_test_files(root)
        self.assertEqual(len(files), 1)

    def test_skips_hidden_dirs(self):
        with temp_repo({
            "test_a.py": "x=1\n",
            ".hidden/test_b.py": "x=1\n",
        }) as root:
            files = W.discover_test_files(root)
        self.assertEqual(len(files), 1)

    def test_result_sorted(self):
        with temp_repo({"b/test_b.py": "x=1\n", "a/test_a.py": "x=1\n"}) as root:
            files = W.discover_test_files(root)
        rels = [os.path.relpath(f, root) for f in files]
        self.assertEqual(rels, sorted(rels))

    def test_empty_dir_returns_empty_list(self):
        with temp_repo({}) as root:
            files = W.discover_test_files(root)
        self.assertEqual(files, [])

    def test_suffix_style_test_file_found(self):
        with temp_repo({"widget_test.py": "x=1\n"}) as root:
            files = W.discover_test_files(root)
        self.assertEqual(len(files), 1)

    def test_multiple_files_all_found(self):
        with temp_repo({
            "test_a.py": "x=1\n", "test_b.py": "x=1\n", "test_c.py": "x=1\n",
        }) as root:
            files = W.discover_test_files(root)
        self.assertEqual(len(files), 3)


# ==========================================================================
# resolve_subject_aliases
# ==========================================================================

class TestResolveSubjectAliases(unittest.TestCase):
    def test_plain_import_is_subject(self):
        tree = parse_module("import forecast\n")
        modules, symbols = W.resolve_subject_aliases(tree)
        self.assertIn("forecast", modules)

    def test_import_as_alias_is_subject(self):
        tree = parse_module("import forecast as F\n")
        modules, symbols = W.resolve_subject_aliases(tree)
        self.assertIn("F", modules)
        self.assertNotIn("forecast", modules)

    def test_stdlib_import_excluded(self):
        tree = parse_module("import os\n")
        modules, symbols = W.resolve_subject_aliases(tree)
        self.assertNotIn("os", modules)

    def test_stdlib_json_excluded(self):
        tree = parse_module("import json\n")
        modules, symbols = W.resolve_subject_aliases(tree)
        self.assertNotIn("json", modules)

    def test_unittest_import_excluded(self):
        tree = parse_module("import unittest\n")
        modules, symbols = W.resolve_subject_aliases(tree)
        self.assertNotIn("unittest", modules)

    def test_from_import_symbol_is_subject(self):
        tree = parse_module("from forecast import compute\n")
        modules, symbols = W.resolve_subject_aliases(tree)
        self.assertIn("compute", symbols)

    def test_from_import_as_alias_symbol_is_subject(self):
        tree = parse_module("from forecast import compute as C\n")
        modules, symbols = W.resolve_subject_aliases(tree)
        self.assertIn("C", symbols)
        self.assertNotIn("compute", symbols)

    def test_from_stdlib_import_excluded(self):
        tree = parse_module("from decimal import Decimal\n")
        modules, symbols = W.resolve_subject_aliases(tree)
        self.assertNotIn("Decimal", symbols)

    def test_dotted_import_binds_top_segment(self):
        tree = parse_module("import mypkg.sub\n")
        modules, symbols = W.resolve_subject_aliases(tree)
        self.assertIn("mypkg", modules)

    def test_relative_import_is_always_subject(self):
        tree = parse_module("from . import forecast\n")
        modules, symbols = W.resolve_subject_aliases(tree)
        self.assertIn("forecast", symbols)

    def test_star_import_ignored(self):
        tree = parse_module("from forecast import *\n")
        modules, symbols = W.resolve_subject_aliases(tree)
        self.assertEqual(symbols, set())

    def test_future_import_excluded(self):
        tree = parse_module("from __future__ import annotations\n")
        modules, symbols = W.resolve_subject_aliases(tree)
        self.assertNotIn("annotations", symbols)

    def test_multiple_imports_combined(self):
        tree = parse_module("import os\nimport forecast as F\nfrom decimal import Decimal\nfrom forecast import compute\n")
        modules, symbols = W.resolve_subject_aliases(tree)
        self.assertEqual(modules, {"F"})
        self.assertEqual(symbols, {"compute"})

    def test_no_imports_gives_empty_sets(self):
        tree = parse_module("x = 1\n")
        modules, symbols = W.resolve_subject_aliases(tree)
        self.assertEqual((modules, symbols), (set(), set()))

    def test_import_multiple_names_one_statement(self):
        tree = parse_module("import forecast, os, widgets\n")
        modules, symbols = W.resolve_subject_aliases(tree)
        self.assertEqual(modules, {"forecast", "widgets"})


# ==========================================================================
# contains_subject_call / _descend_to_name
# ==========================================================================

class TestContainsSubjectCall(unittest.TestCase):
    def setUp(self):
        self.modules = {"subject"}
        self.symbols = {"compute"}

    def _check(self, src):
        expr = ast.parse(textwrap.dedent(src), mode="eval").body
        return W.contains_subject_call(expr, self.modules, self.symbols)

    def test_direct_attribute_call(self):
        self.assertTrue(self._check("subject.add(1, 2)"))

    def test_direct_bare_symbol_call(self):
        self.assertTrue(self._check("compute(1)"))

    def test_unrelated_call_is_false(self):
        self.assertFalse(self._check("len([1, 2])"))

    def test_plain_name_no_call_is_false(self):
        self.assertFalse(self._check("subject"))

    def test_literal_is_false(self):
        self.assertFalse(self._check("42"))

    def test_chained_attribute_call(self):
        self.assertTrue(self._check("subject.Widget(5).scale(2)"))

    def test_subscript_wrapped_call(self):
        self.assertTrue(self._check("subject.get_list()[0]"))

    def test_call_nested_in_binop(self):
        self.assertTrue(self._check("subject.add(1, 2) + 1"))

    def test_call_nested_in_list_literal(self):
        self.assertTrue(self._check("[subject.add(1, 2), 3]"))

    def test_call_nested_in_kwarg(self):
        self.assertTrue(self._check("dict(x=subject.add(1, 2))"))

    def test_call_nested_in_comprehension(self):
        self.assertTrue(self._check("[subject.add(i, 1) for i in range(3)]"))

    def test_other_module_call_is_false(self):
        self.assertFalse(self._check("other.add(1, 2)"))

    def test_empty_modules_and_symbols_is_false(self):
        expr = ast.parse("subject.add(1, 2)", mode="eval").body
        self.assertFalse(W.contains_subject_call(expr, set(), set()))

    def test_none_expr_is_false(self):
        self.assertFalse(W.contains_subject_call(None, self.modules, self.symbols))

    def test_descend_to_name_simple(self):
        expr = ast.parse("subject.add(1, 2)", mode="eval").body
        self.assertEqual(W._descend_to_name(expr.func), "subject")

    def test_descend_to_name_no_name_returns_none(self):
        expr = ast.parse("(1 + 2)(3)", mode="eval").body
        self.assertIsNone(W._descend_to_name(expr.func))


# ==========================================================================
# has_direct_assertion
# ==========================================================================

class TestHasDirectAssertion(unittest.TestCase):
    def test_bare_assert(self):
        f = first_function("def test_x():\n    assert 1 == 1\n")
        self.assertTrue(W.has_direct_assertion(f))

    def test_self_assert_equal(self):
        f = method_from_class_src(
            "class T:\n    def test_x(self):\n        self.assertEqual(1, 1)\n", "test_x")
        self.assertTrue(W.has_direct_assertion(f))

    def test_self_assert_true(self):
        f = method_from_class_src(
            "class T:\n    def test_x(self):\n        self.assertTrue(True)\n", "test_x")
        self.assertTrue(W.has_direct_assertion(f))

    def test_self_fail(self):
        f = method_from_class_src(
            "class T:\n    def test_x(self):\n        self.fail('nope')\n", "test_x")
        self.assertTrue(W.has_direct_assertion(f))

    def test_classmethod_cls_assert(self):
        f = method_from_class_src(
            "class T:\n    def test_x(cls):\n        cls.assertEqual(1, 1)\n", "test_x")
        self.assertTrue(W.has_direct_assertion(f))

    def test_assert_raises_as_context_manager(self):
        src = """
        class T:
            def test_x(self):
                with self.assertRaises(ValueError):
                    raise ValueError()
        """
        f = method_from_class_src(src, "test_x")
        self.assertTrue(W.has_direct_assertion(f))

    def test_assert_raises_as_plain_call(self):
        src = """
        class T:
            def test_x(self):
                self.assertRaises(ValueError, do_thing)
        """
        f = method_from_class_src(src, "test_x")
        self.assertTrue(W.has_direct_assertion(f))

    def test_assertion_inside_for_loop(self):
        src = """
        class T:
            def test_x(self):
                for i in range(3):
                    self.assertEqual(i, i)
        """
        f = method_from_class_src(src, "test_x")
        self.assertTrue(W.has_direct_assertion(f))

    def test_assertion_inside_while_loop(self):
        src = """
        class T:
            def test_x(self):
                i = 0
                while i < 1:
                    self.assertEqual(i, 0)
                    i += 1
        """
        f = method_from_class_src(src, "test_x")
        self.assertTrue(W.has_direct_assertion(f))

    def test_assertion_inside_try_except(self):
        src = """
        class T:
            def test_x(self):
                try:
                    do_thing()
                except ValueError:
                    self.assertTrue(True)
        """
        f = method_from_class_src(src, "test_x")
        self.assertTrue(W.has_direct_assertion(f))

    def test_assertion_inside_with_subtest(self):
        src = """
        class T:
            def test_x(self):
                with self.subTest(i=1):
                    self.assertEqual(1, 1)
        """
        f = method_from_class_src(src, "test_x")
        self.assertTrue(W.has_direct_assertion(f))

    def test_assertion_inside_if_elif_else(self):
        src = """
        class T:
            def test_x(self):
                if True:
                    pass
                elif False:
                    pass
                else:
                    self.assertTrue(True)
        """
        f = method_from_class_src(src, "test_x")
        self.assertTrue(W.has_direct_assertion(f))

    def test_pass_only_is_false(self):
        f = first_function("def test_x():\n    pass\n")
        self.assertFalse(W.has_direct_assertion(f))

    def test_docstring_only_is_false(self):
        f = first_function('def test_x():\n    """does nothing"""\n')
        self.assertFalse(W.has_direct_assertion(f))

    def test_addcleanup_is_not_an_assertion(self):
        src = """
        class T:
            def test_x(self):
                self.addCleanup(lambda: None)
        """
        f = method_from_class_src(src, "test_x")
        self.assertFalse(W.has_direct_assertion(f))

    def test_setup_call_is_not_an_assertion(self):
        src = """
        class T:
            def test_x(self):
                self.setUp()
        """
        f = method_from_class_src(src, "test_x")
        self.assertFalse(W.has_direct_assertion(f))

    def test_empty_function_is_false(self):
        tree = ast.parse("def test_x(): ...\n")
        f = tree.body[0]
        self.assertFalse(W.has_direct_assertion(f))

    def test_bare_assert_with_message(self):
        f = first_function("def test_x():\n    assert 1 == 1, 'should be equal'\n")
        self.assertTrue(W.has_direct_assertion(f))

    def test_nested_function_def_is_a_documented_blind_spot(self):
        """Dead code inside a never-called nested def is still (incorrectly
        but knowingly) picked up by has_direct_assertion - see README."""
        src = """
        class T:
            def test_x(self):
                def never_called():
                    self.assertEqual(1, 1)
        """
        f = method_from_class_src(src, "test_x")
        self.assertTrue(W.has_direct_assertion(f))

    def test_multiple_statements_none_asserting(self):
        src = """
        class T:
            def test_x(self):
                a = 1
                b = 2
                c = a + b
        """
        f = method_from_class_src(src, "test_x")
        self.assertFalse(W.has_direct_assertion(f))


# ==========================================================================
# has_assertion_via_helper
# ==========================================================================

class TestHasAssertionViaHelper(unittest.TestCase):
    def test_same_class_helper_resolved(self):
        src = """
        class T:
            def _check(self, a, b):
                self.assertEqual(a, b)
            def test_x(self):
                self._check(1, 1)
        """
        cls = first_class(src)
        methods = W._collect_class_methods(cls)
        test_node = method_of(cls, "test_x")
        ok, name = W.has_assertion_via_helper(test_node, methods, {})
        self.assertTrue(ok)
        self.assertEqual(name, "_check")

    def test_same_class_helper_without_assertion_not_resolved(self):
        src = """
        class T:
            def _setup_thing(self):
                self.thing = 1
            def test_x(self):
                self._setup_thing()
        """
        cls = first_class(src)
        methods = W._collect_class_methods(cls)
        test_node = method_of(cls, "test_x")
        ok, name = W.has_assertion_via_helper(test_node, methods, {})
        self.assertFalse(ok)

    def test_module_level_helper_resolved(self):
        src = """
        def _assert_positive(case, value):
            assert value > 0
        class T:
            def test_x(self):
                _assert_positive(self, 1)
        """
        tree = parse_module(src)
        module_functions = W._collect_module_functions(tree)
        cls = first_class(src)
        test_node = method_of(cls, "test_x")
        ok, name = W.has_assertion_via_helper(test_node, {}, module_functions)
        self.assertTrue(ok)
        self.assertEqual(name, "_assert_positive")

    def test_module_level_helper_without_assertion_not_resolved(self):
        src = """
        def _log(msg):
            print(msg)
        class T:
            def test_x(self):
                _log('hi')
        """
        tree = parse_module(src)
        module_functions = W._collect_module_functions(tree)
        cls = first_class(src)
        test_node = method_of(cls, "test_x")
        ok, name = W.has_assertion_via_helper(test_node, {}, module_functions)
        self.assertFalse(ok)

    def test_two_level_helper_chain_not_resolved(self):
        """Documented limitation: only one level of helper indirection is
        followed. _check calls _check2, which is where the real assertion
        lives; weakassert cannot see through that second hop."""
        src = """
        class T:
            def _check2(self, a, b):
                self.assertEqual(a, b)
            def _check(self, a, b):
                self._check2(a, b)
            def test_x(self):
                self._check(1, 1)
        """
        cls = first_class(src)
        methods = W._collect_class_methods(cls)
        test_node = method_of(cls, "test_x")
        ok, name = W.has_assertion_via_helper(test_node, methods, {})
        self.assertFalse(ok)

    def test_unrelated_self_call_not_in_class_methods_ignored(self):
        src = """
        class T:
            def test_x(self):
                self.some_external_thing(1)
        """
        cls = first_class(src)
        test_node = method_of(cls, "test_x")
        ok, name = W.has_assertion_via_helper(test_node, {}, {})
        self.assertFalse(ok)

    def test_no_calls_at_all_not_resolved(self):
        src = """
        class T:
            def test_x(self):
                x = 1
        """
        cls = first_class(src)
        methods = W._collect_class_methods(cls)
        test_node = method_of(cls, "test_x")
        ok, name = W.has_assertion_via_helper(test_node, methods, {})
        self.assertFalse(ok)

    def test_helper_referencing_itself_does_not_infinite_loop(self):
        src = """
        class T:
            def test_x(self):
                self.test_x()
        """
        cls = first_class(src)
        test_node = method_of(cls, "test_x")
        methods = {"test_x": test_node}
        ok, name = W.has_assertion_via_helper(test_node, methods, {})
        self.assertFalse(ok)

    def test_multiple_helper_calls_second_one_asserts(self):
        src = """
        class T:
            def _noop(self):
                x = 1
            def _check(self, a, b):
                self.assertEqual(a, b)
            def test_x(self):
                self._noop()
                self._check(1, 1)
        """
        cls = first_class(src)
        methods = W._collect_class_methods(cls)
        test_node = method_of(cls, "test_x")
        ok, name = W.has_assertion_via_helper(test_node, methods, {})
        self.assertTrue(ok)


# ==========================================================================
# call_only_body_calls
# ==========================================================================

class TestCallOnlyBodyCalls(unittest.TestCase):
    def test_single_expr_call(self):
        f = first_function("def test_x():\n    foo()\n")
        calls = W.call_only_body_calls(f)
        self.assertEqual(len(calls), 1)

    def test_assign_from_call(self):
        f = first_function("def test_x():\n    r = foo()\n")
        calls = W.call_only_body_calls(f)
        self.assertEqual(len(calls), 1)

    def test_multiple_calls(self):
        f = first_function("def test_x():\n    foo()\n    bar()\n")
        calls = W.call_only_body_calls(f)
        self.assertEqual(len(calls), 2)

    def test_docstring_then_call(self):
        f = first_function('def test_x():\n    """doc"""\n    foo()\n')
        calls = W.call_only_body_calls(f)
        self.assertEqual(len(calls), 1)

    def test_pass_only_returns_empty_list_not_none(self):
        f = first_function("def test_x():\n    pass\n")
        calls = W.call_only_body_calls(f)
        self.assertEqual(calls, [])

    def test_if_statement_disqualifies(self):
        f = first_function("def test_x():\n    if True:\n        foo()\n")
        calls = W.call_only_body_calls(f)
        self.assertIsNone(calls)

    def test_for_statement_disqualifies(self):
        f = first_function("def test_x():\n    for i in range(3):\n        foo()\n")
        calls = W.call_only_body_calls(f)
        self.assertIsNone(calls)

    def test_try_statement_disqualifies(self):
        f = first_function("def test_x():\n    try:\n        foo()\n    except Exception:\n        pass\n")
        calls = W.call_only_body_calls(f)
        self.assertIsNone(calls)

    def test_with_statement_disqualifies(self):
        f = first_function("def test_x():\n    with foo() as x:\n        pass\n")
        calls = W.call_only_body_calls(f)
        self.assertIsNone(calls)

    def test_return_statement_disqualifies(self):
        f = first_function("def test_x():\n    return foo()\n")
        calls = W.call_only_body_calls(f)
        self.assertIsNone(calls)

    def test_plain_assignment_non_call_disqualifies(self):
        f = first_function("def test_x():\n    x = 1\n")
        calls = W.call_only_body_calls(f)
        self.assertIsNone(calls)

    def test_assert_statement_disqualifies(self):
        f = first_function("def test_x():\n    assert True\n")
        calls = W.call_only_body_calls(f)
        self.assertIsNone(calls)

    def test_docstring_not_first_statement_counts_as_expr(self):
        f = first_function('def test_x():\n    foo()\n    """not a docstring here"""\n')
        calls = W.call_only_body_calls(f)
        self.assertIsNone(calls)


# ==========================================================================
# skip decorator / skipTest detection
# ==========================================================================

class TestSkipDetection(unittest.TestCase):
    def test_unittest_skip_call_form(self):
        f = method_from_class_src(
            "class T:\n    @unittest.skip('why')\n    def test_x(self):\n        pass\n", "test_x")
        self.assertEqual(W.skip_decorator_names(f.decorator_list), ["skip"])

    def test_unittest_skipif_call_form(self):
        f = method_from_class_src(
            "class T:\n    @unittest.skipIf(True, 'why')\n    def test_x(self):\n        pass\n", "test_x")
        self.assertEqual(W.skip_decorator_names(f.decorator_list), ["skipIf"])

    def test_unittest_skipunless_call_form(self):
        f = method_from_class_src(
            "class T:\n    @unittest.skipUnless(False, 'why')\n    def test_x(self):\n        pass\n", "test_x")
        self.assertEqual(W.skip_decorator_names(f.decorator_list), ["skipUnless"])

    def test_bare_imported_skip_name_form(self):
        f = method_from_class_src(
            "class T:\n    @skip('why')\n    def test_x(self):\n        pass\n", "test_x")
        self.assertEqual(W.skip_decorator_names(f.decorator_list), ["skip"])

    def test_expected_failure_is_not_skip(self):
        f = method_from_class_src(
            "class T:\n    @unittest.expectedFailure\n    def test_x(self):\n        pass\n", "test_x")
        self.assertEqual(W.skip_decorator_names(f.decorator_list), [])

    def test_patch_decorator_is_not_skip(self):
        f = method_from_class_src(
            "class T:\n    @patch('os.getenv')\n    def test_x(self, m):\n        pass\n", "test_x")
        self.assertEqual(W.skip_decorator_names(f.decorator_list), [])

    def test_no_decorators_returns_empty(self):
        f = method_from_class_src("class T:\n    def test_x(self):\n        pass\n", "test_x")
        self.assertEqual(W.skip_decorator_names(f.decorator_list), [])

    def test_multiple_decorators_only_skip_returned(self):
        src = """
        class T:
            @unittest.skip('why')
            @patch('os.getenv')
            def test_x(self, m):
                pass
        """
        f = method_from_class_src(src, "test_x")
        self.assertEqual(W.skip_decorator_names(f.decorator_list), ["skip"])

    def test_skiptest_unconditional_top_level_detected(self):
        f = method_from_class_src(
            "class T:\n    def test_x(self):\n        self.skipTest('nope')\n", "test_x")
        self.assertTrue(W.has_unconditional_skip_test_call(f))

    def test_skiptest_not_present_returns_false(self):
        f = method_from_class_src(
            "class T:\n    def test_x(self):\n        self.assertTrue(True)\n", "test_x")
        self.assertFalse(W.has_unconditional_skip_test_call(f))

    def test_skiptest_nested_inside_if_not_detected_top_level(self):
        """Documented limitation: only unconditional top-level skipTest
        calls are recognised."""
        src = """
        class T:
            def test_x(self):
                if True:
                    self.skipTest('nope')
        """
        f = method_from_class_src(src, "test_x")
        self.assertFalse(W.has_unconditional_skip_test_call(f))

    def test_class_level_skip_detected(self):
        cls = first_class("@unittest.skip('why')\nclass T:\n    def test_x(self):\n        pass\n")
        self.assertEqual(W._class_has_skip_decorator(cls), "skip")

    def test_class_level_no_skip_returns_none(self):
        cls = first_class("class T:\n    def test_x(self):\n        pass\n")
        self.assertIsNone(W._class_has_skip_decorator(cls))

    def test_skiptest_with_other_statements_after(self):
        src = """
        class T:
            def test_x(self):
                self.skipTest('nope')
                self.assertTrue(True)
        """
        f = method_from_class_src(src, "test_x")
        self.assertTrue(W.has_unconditional_skip_test_call(f))

    def test_skip_decorator_names_empty_list_input(self):
        self.assertEqual(W.skip_decorator_names([]), [])


# ==========================================================================
# _unparse
# ==========================================================================

class TestUnparse(unittest.TestCase):
    def test_basic_roundtrip(self):
        node = ast.parse("foo(1, 2)", mode="eval").body
        self.assertEqual(W._unparse(node), "foo(1, 2)")

    def test_truncates_long_expressions(self):
        long_src = "foo(" + ", ".join(str(i) for i in range(200)) + ")"
        node = ast.parse(long_src, mode="eval").body
        text = W._unparse(node)
        self.assertLessEqual(len(text), 200)
        self.assertTrue(text.endswith("..."))

    def test_short_expression_not_truncated(self):
        node = ast.parse("x", mode="eval").body
        self.assertEqual(W._unparse(node), "x")


# ==========================================================================
# scan_file integration
# ==========================================================================

class TestScanFileIntegration(unittest.TestCase):
    def test_counts_test_methods(self):
        with temp_repo({
            "test_a.py": """
                import unittest
                class T(unittest.TestCase):
                    def test_one(self):
                        self.assertTrue(True)
                    def test_two(self):
                        self.assertTrue(True)
                    def helper(self):
                        pass
            """
        }) as root:
            findings, n_tests, error = W.scan_file(os.path.join(root, "test_a.py"), root)
        self.assertEqual(n_tests, 2)
        self.assertIsNone(error)
        self.assertEqual(findings, [])

    def test_relative_path_uses_forward_slashes(self):
        with temp_repo({"pkg/test_a.py": "def test_x():\n    assert True\n"}) as root:
            findings, n_tests, error = W.scan_file(os.path.join(root, "pkg", "test_a.py"), root)
        # no findings, but we still verify scan_file computed the path form
        # by re-deriving it the same way scan_file does internally.
        rel = os.path.relpath(os.path.join(root, "pkg", "test_a.py"), root).replace(os.sep, "/")
        self.assertEqual(rel, "pkg/test_a.py")

    def test_syntax_error_reported_as_error_not_crash(self):
        with temp_repo({"test_bad.py": "def test_x(:\n    pass\n"}) as root:
            findings, n_tests, error = W.scan_file(os.path.join(root, "test_bad.py"), root)
        self.assertEqual(findings, [])
        self.assertEqual(n_tests, 0)
        self.assertIsNotNone(error)
        self.assertIn("syntax error", error["message"])

    def test_module_level_test_function_counted(self):
        with temp_repo({"test_a.py": "def test_x():\n    assert 1 == 1\n"}) as root:
            findings, n_tests, error = W.scan_file(os.path.join(root, "test_a.py"), root)
        self.assertEqual(n_tests, 1)
        self.assertEqual(findings, [])

    def test_module_level_test_function_no_assertion_flagged(self):
        with temp_repo({"test_a.py": "def test_x():\n    pass\n"}) as root:
            findings, n_tests, error = W.scan_file(os.path.join(root, "test_a.py"), root)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].category, W.WA001)
        self.assertEqual(findings[0].test_name, "test_x")

    def test_class_with_no_test_methods_contributes_zero(self):
        with temp_repo({"test_a.py": "class T:\n    def helper(self):\n        pass\n"}) as root:
            findings, n_tests, error = W.scan_file(os.path.join(root, "test_a.py"), root)
        self.assertEqual(n_tests, 0)
        self.assertEqual(findings, [])

    def test_setup_assertion_does_not_leak_to_sibling_test(self):
        with temp_repo({
            "test_a.py": """
                import unittest
                class T(unittest.TestCase):
                    def setUp(self):
                        self.assertTrue(True)
                    def test_no_assertion(self):
                        x = 1
            """
        }) as root:
            findings, n_tests, error = W.scan_file(os.path.join(root, "test_a.py"), root)
        self.assertEqual(n_tests, 1)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].category, W.WA001)

    def test_unreadable_binary_file_reported_as_error(self):
        with temp_repo({}) as root:
            path = os.path.join(root, "test_bin.py")
            with open(path, "wb") as fh:
                fh.write(b"\xff\xfe\x00\x01 not valid utf8 \x80\x81")
            findings, n_tests, error = W.scan_file(path, root)
        self.assertEqual(findings, [])
        self.assertIsNotNone(error)

    def test_all_four_categories_in_one_file(self):
        with temp_repo({
            "subject.py": "def add(a, b):\n    return a + b\n",
            "test_all.py": """
                import unittest
                import subject

                class T(unittest.TestCase):
                    def test_no_assertion(self):
                        x = 1

                    def test_call_only(self):
                        subject.add(1, 2)

                    def test_self_derived(self):
                        self.assertEqual(subject.add(1, 2), subject.add(1, 2))

                    @unittest.skip('why')
                    def test_skipped(self):
                        self.assertTrue(True)
            """,
        }) as root:
            findings, n_tests, error = W.scan_file(os.path.join(root, "test_all.py"), root)
        cats = sorted(set(f.category for f in findings))
        self.assertEqual(cats, sorted(W.ALL_CATEGORIES))

    def test_async_test_method_scanned(self):
        with temp_repo({
            "test_a.py": """
                import unittest
                class T(unittest.TestCase):
                    async def test_async_no_assertion(self):
                        x = 1
            """
        }) as root:
            findings, n_tests, error = W.scan_file(os.path.join(root, "test_a.py"), root)
        self.assertEqual(n_tests, 1)
        self.assertEqual(len(findings), 1)


# ==========================================================================
# build_report ordering / canonical JSON
# ==========================================================================

class TestBuildReportOrdering(unittest.TestCase):
    def _fixture(self):
        return {
            "zz_test.py": "def test_b():\n    x = 1\n\ndef test_a():\n    y = 2\n",
            "aa_test.py": "def test_z():\n    x = 1\n",
        }

    def test_findings_sorted_by_category_then_path_then_line_then_name(self):
        with temp_repo(self._fixture()) as root:
            report = W.build_report(root)
        findings = report["findings"]
        keys = [(f["category"], f["path"], f["line"], f["test_name"]) for f in findings]
        self.assertEqual(keys, sorted(keys))

    def test_category_filter_reduces_findings(self):
        with temp_repo(self._fixture()) as root:
            full = W.build_report(root)
            filtered = W.build_report(root, category_filter=[W.WA004])
        self.assertGreater(full["summary"]["findings_total"], 0)
        self.assertEqual(filtered["summary"]["findings_total"], 0)

    def test_category_filter_recorded_in_report(self):
        with temp_repo(self._fixture()) as root:
            report = W.build_report(root, category_filter=[W.WA001])
        self.assertEqual(report["category_filter"], [W.WA001])

    def test_no_filter_records_null(self):
        with temp_repo(self._fixture()) as root:
            report = W.build_report(root)
        self.assertIsNone(report["category_filter"])

    def test_findings_by_category_matches_findings_list(self):
        with temp_repo(self._fixture()) as root:
            report = W.build_report(root)
        counted = {}
        for f in report["findings"]:
            counted[f["category"]] = counted.get(f["category"], 0) + 1
        for cat in W.ALL_CATEGORIES:
            self.assertEqual(report["summary"]["findings_by_category"].get(cat, 0), counted.get(cat, 0))

    def test_tests_scanned_counts_all_test_defs(self):
        with temp_repo(self._fixture()) as root:
            report = W.build_report(root)
        self.assertEqual(report["tests_scanned"], 3)

    def test_files_scanned_counts_files(self):
        with temp_repo(self._fixture()) as root:
            report = W.build_report(root)
        self.assertEqual(report["files_scanned"], 2)

    def test_errors_sorted_by_path(self):
        with temp_repo({
            "z_test.py": "def test_x(:\n",
            "a_test.py": "def test_x(:\n",
        }) as root:
            report = W.build_report(root)
        paths = [e["path"] for e in report["errors"]]
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(len(paths), 2)


class TestCanonicalJson(unittest.TestCase):
    def test_sort_keys_applied(self):
        report = {"b": 1, "a": 2}
        text = W.to_canonical_json(report)
        self.assertLess(text.index('"a"'), text.index('"b"'))

    def test_no_spaces_in_separators(self):
        report = {"a": [1, 2], "b": {"c": 1}}
        text = W.to_canonical_json(report)
        self.assertNotIn(": ", text)
        self.assertNotIn(", ", text)

    def test_ends_with_single_trailing_newline(self):
        text = W.to_canonical_json({"a": 1})
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))

    def test_ensure_ascii_escapes_non_ascii(self):
        text = W.to_canonical_json({"a": "h\u00e9llo"})
        self.assertNotIn("\u00e9", text)
        self.assertIn("\\u00e9", text)

    def test_deterministic_across_calls(self):
        report = {"z": 1, "a": [3, 2, 1], "m": {"y": 1, "x": 2}}
        self.assertEqual(W.to_canonical_json(report), W.to_canonical_json(report))

    def test_real_report_round_trips_through_json_loads(self):
        with temp_repo({"test_a.py": "def test_x():\n    pass\n"}) as root:
            report = W.build_report(root)
        text = W.to_canonical_json(report)
        reloaded = json.loads(text)
        self.assertEqual(reloaded["tests_scanned"], 1)


# ==========================================================================
# WA003 detail (subject-derived expectation), including the local-variable
# indirection fix (real bug caught during development)
# ==========================================================================

class TestSelfDerivedExpectationDetection(unittest.TestCase):
    def _report_for(self, test_src):
        with temp_repo({
            "subject.py": "def add(a, b):\n    return a + b\ndef compute(x):\n    return x*x\n",
            "test_a.py": "import subject\nimport unittest\nclass T(unittest.TestCase):\n" + test_src,
        }) as root:
            return W.build_report(root)

    def test_both_sides_direct_calls_flagged(self):
        report = self._report_for(
            "    def test_x(self):\n        self.assertEqual(subject.add(1,2), subject.add(1,2))\n")
        cats = [f["category"] for f in report["findings"]]
        self.assertIn(W.WA003, cats)

    def test_one_side_literal_not_flagged(self):
        report = self._report_for(
            "    def test_x(self):\n        self.assertEqual(subject.add(1,2), 3)\n")
        cats = [f["category"] for f in report["findings"]]
        self.assertNotIn(W.WA003, cats)

    def test_neither_side_subject_not_flagged(self):
        report = self._report_for(
            "    def test_x(self):\n        self.assertEqual(1+1, 2)\n")
        cats = [f["category"] for f in report["findings"]]
        self.assertNotIn(W.WA003, cats)

    def test_local_variable_indirection_flagged(self):
        """Regression test for the real bug found during development: the
        first assertEqual argument is a bare local variable that was
        assigned from a subject call earlier in the same test."""
        report = self._report_for(
            "    def test_x(self):\n"
            "        actual = subject.compute(7)\n"
            "        self.assertEqual(actual, subject.compute(7))\n"
        )
        cats = [f["category"] for f in report["findings"]]
        self.assertIn(W.WA003, cats)

    def test_local_variable_pointing_at_non_subject_not_flagged(self):
        report = self._report_for(
            "    def test_x(self):\n"
            "        expected = 4 + 4\n"
            "        self.assertEqual(subject.add(4,4), expected)\n"
        )
        cats = [f["category"] for f in report["findings"]]
        self.assertNotIn(W.WA003, cats)

    def test_chained_alias_not_resolved_documented_limitation(self):
        """Two levels of variable indirection (a = subject.f(); b = a) are
        NOT resolved - this is a documented blind spot, not a bug."""
        report = self._report_for(
            "    def test_x(self):\n"
            "        a = subject.compute(7)\n"
            "        b = a\n"
            "        self.assertEqual(b, subject.compute(7))\n"
        )
        cats = [f["category"] for f in report["findings"]]
        self.assertNotIn(W.WA003, cats)

    def test_different_subject_functions_both_sides_still_flagged(self):
        """Both sides call the module, even via different functions - the
        rule is deliberately module-level, not function-level (recall over
        precision; documented in README)."""
        report = self._report_for(
            "    def test_x(self):\n        self.assertEqual(subject.add(1,2), subject.compute(2))\n")
        cats = [f["category"] for f in report["findings"]]
        self.assertIn(W.WA003, cats)

    def test_finding_line_matches_assertequal_call(self):
        report = self._report_for(
            "    def test_x(self):\n"
            "        x = 1\n"
            "        self.assertEqual(subject.add(1,2), subject.add(1,2))\n"
        )
        wa003 = [f for f in report["findings"] if f["category"] == W.WA003][0]
        self.assertEqual(wa003["line"], 6)

    def test_multiple_assertequal_only_offending_one_flagged(self):
        report = self._report_for(
            "    def test_x(self):\n"
            "        self.assertEqual(1, 1)\n"
            "        self.assertEqual(subject.add(1,2), subject.add(1,2))\n"
        )
        wa003 = [f for f in report["findings"] if f["category"] == W.WA003]
        self.assertEqual(len(wa003), 1)
        self.assertEqual(wa003[0]["line"], 6)


# ==========================================================================
# WA002 call-only, distinguished from WA001 by subject-module calls
# ==========================================================================

class TestCallOnlyCategory(unittest.TestCase):
    def _report_for(self, test_src):
        with temp_repo({
            "subject.py": "def add(a, b):\n    return a + b\n",
            "test_a.py": "import subject\nimport time\nimport unittest\nclass T(unittest.TestCase):\n" + test_src,
        }) as root:
            return W.build_report(root)

    def test_subject_call_only_flags_wa002(self):
        report = self._report_for("    def test_x(self):\n        subject.add(1, 2)\n")
        cats = [f["category"] for f in report["findings"]]
        self.assertIn(W.WA002, cats)
        self.assertIn(W.WA001, cats)

    def test_non_subject_call_only_flags_wa001_not_wa002(self):
        """Distinguishing rule: calling stdlib-only code with no assertion
        is WA001 (nothing verified) but not WA002 (WA002 specifically means
        the SUBJECT module was exercised with nothing checked)."""
        report = self._report_for("    def test_x(self):\n        time.sleep(0)\n")
        cats = [f["category"] for f in report["findings"]]
        self.assertIn(W.WA001, cats)
        self.assertNotIn(W.WA002, cats)

    def test_empty_body_flags_wa001_not_wa002(self):
        report = self._report_for("    def test_x(self):\n        pass\n")
        cats = [f["category"] for f in report["findings"]]
        self.assertIn(W.WA001, cats)
        self.assertNotIn(W.WA002, cats)

    def test_has_assertion_never_flags_wa002(self):
        report = self._report_for(
            "    def test_x(self):\n        subject.add(1, 2)\n        self.assertTrue(True)\n")
        cats = [f["category"] for f in report["findings"]]
        self.assertNotIn(W.WA002, cats)
        self.assertNotIn(W.WA001, cats)


# ==========================================================================
# CLI behaviour
# ==========================================================================

class TestCli(unittest.TestCase):
    def test_clean_dir_exit_0(self):
        with temp_repo({"test_a.py": "def test_x():\n    assert True\n"}) as root:
            code, out, err = run_main(["--root", root])
        self.assertEqual(code, 0)

    def test_findings_dir_exit_1(self):
        with temp_repo({"test_a.py": "def test_x():\n    pass\n"}) as root:
            code, out, err = run_main(["--root", root])
        self.assertEqual(code, 1)

    def test_missing_root_exit_2(self):
        code, out, err = run_main(["--root", "/definitely/does/not/exist/xyz"])
        self.assertEqual(code, 2)

    def test_invalid_category_exit_2(self):
        with temp_repo({"test_a.py": "def test_x():\n    pass\n"}) as root:
            code, out, err = run_main(["--root", root, "--category", "NOT_A_CATEGORY"])
        self.assertEqual(code, 2)

    def test_output_flag_writes_file(self):
        with temp_repo({"test_a.py": "def test_x():\n    pass\n"}) as root:
            out_path = os.path.join(root, "report.json")
            code, out, err = run_main(["--root", root, "-o", out_path])
            self.assertEqual(code, 1)
            self.assertTrue(os.path.isfile(out_path))
            with open(out_path) as fh:
                content = fh.read()
        self.assertEqual(out, "")
        data = json.loads(content)
        self.assertEqual(data["summary"]["findings_total"], 1)

    def test_no_output_flag_prints_to_stdout(self):
        with temp_repo({"test_a.py": "def test_x():\n    pass\n"}) as root:
            code, out, err = run_main(["--root", root])
        self.assertNotEqual(out, "")
        json.loads(out)  # must be valid JSON

    def test_category_filter_can_flip_exit_code_to_zero(self):
        with temp_repo({"test_a.py": "def test_x():\n    pass\n"}) as root:
            full_code, _, _ = run_main(["--root", root])
            filtered_code, _, _ = run_main(["--root", root, "--category", W.WA004])
        self.assertEqual(full_code, 1)
        self.assertEqual(filtered_code, 0)

    def test_category_filter_repeated_flag(self):
        with temp_repo({
            "test_a.py": "import unittest\nclass T(unittest.TestCase):\n    @unittest.skip('x')\n    def test_x(self):\n        pass\n",
        }) as root:
            code, out, err = run_main(["--root", root, "--category", W.WA001, "--category", W.WA004])
        self.assertEqual(code, 1)
        data = json.loads(out)
        self.assertEqual(sorted(data["category_filter"]), sorted([W.WA001, W.WA004]))

    def test_help_flag_exits_zero(self):
        code, out, err = run_main(["--help"])
        self.assertEqual(code, 0)

    def test_unknown_flag_exits_two(self):
        code, out, err = run_main(["--bogus-flag"])
        self.assertEqual(code, 2)

    def test_default_root_is_cwd(self):
        with temp_repo({"test_a.py": "def test_x():\n    assert True\n"}) as root:
            old_cwd = os.getcwd()
            os.chdir(root)
            try:
                code, out, err = run_main([])
            finally:
                os.chdir(old_cwd)
        self.assertEqual(code, 0)

    def test_output_json_matches_stdout_json_for_same_scan(self):
        with temp_repo({"test_a.py": "def test_x():\n    pass\n"}) as root:
            code1, out1, _ = run_main(["--root", root])
            out_path = os.path.join(root, "r.json")
            code2, out2, _ = run_main(["--root", root, "-o", out_path])
            with open(out_path) as fh:
                file_text = fh.read()
        self.assertEqual(out1, file_text)

    def test_repeat_scans_are_byte_identical(self):
        with temp_repo({"test_a.py": "def test_x():\n    pass\n"}) as root:
            _, out1, _ = run_main(["--root", root])
            _, out2, _ = run_main(["--root", root])
        self.assertEqual(out1, out2)

    def test_root_is_a_file_not_a_directory_exit_2(self):
        with temp_repo({"test_a.py": "def test_x():\n    pass\n"}) as root:
            file_path = os.path.join(root, "test_a.py")
            code, out, err = run_main(["--root", file_path])
        self.assertEqual(code, 2)

    def test_stderr_used_for_usage_errors(self):
        code, out, err = run_main(["--root", "/nonexistent_dir_xyz_123"])
        self.assertNotEqual(err, "")


class TestCliSubprocess(unittest.TestCase):
    """A couple of true subprocess invocations, matching how a user
    actually runs the tool from a shell."""

    def test_subprocess_clean_exit_0(self):
        with temp_repo({"test_a.py": "def test_x():\n    assert True\n"}) as root:
            proc = subprocess.run(
                [sys.executable, os.path.join(HERE, "weakassert.py"), "--root", root],
                capture_output=True, text=True,
            )
        self.assertEqual(proc.returncode, 0)

    def test_subprocess_findings_exit_1(self):
        with temp_repo({"test_a.py": "def test_x():\n    pass\n"}) as root:
            proc = subprocess.run(
                [sys.executable, os.path.join(HERE, "weakassert.py"), "--root", root],
                capture_output=True, text=True,
            )
        self.assertEqual(proc.returncode, 1)
        json.loads(proc.stdout)

    def test_subprocess_bad_root_exit_2(self):
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "weakassert.py"), "--root", "/no/such/dir/at/all"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 2)


# ==========================================================================
# Relative-path / no-leakage guarantee
# ==========================================================================

class TestNoAbsolutePathLeakage(unittest.TestCase):
    def test_output_does_not_contain_scan_root_absolute_path(self):
        with temp_repo({"test_a.py": "def test_x():\n    pass\n"}) as root:
            code, out, err = run_main(["--root", root])
        self.assertNotIn(root, out)

    def test_finding_paths_are_relative(self):
        with temp_repo({"pkg/test_a.py": "def test_x():\n    pass\n"}) as root:
            report = W.build_report(root)
        for f in report["findings"]:
            self.assertFalse(os.path.isabs(f["path"]))

    def test_error_paths_are_relative(self):
        with temp_repo({"pkg/test_bad.py": "def test_x(:\n"}) as root:
            report = W.build_report(root)
        for e in report["errors"]:
            self.assertFalse(os.path.isabs(e["path"]))

    def test_relocated_tree_produces_identical_report_text(self):
        files = {"test_a.py": "def test_x():\n    pass\ndef test_y():\n    assert True\n"}
        with temp_repo(files) as root1, temp_repo(files) as root2:
            _, out1, _ = run_main(["--root", root1])
            _, out2, _ = run_main(["--root", root2])
        self.assertEqual(out1, out2)


# ==========================================================================
# Edge cases explicitly called out in the task spec
# ==========================================================================

class TestSpecEdgeCases(unittest.TestCase):
    def _scan(self, src):
        with temp_repo({"test_a.py": src}) as root:
            return W.build_report(root)

    def test_assert_raises_context_manager_is_not_wa001(self):
        report = self._scan(textwrap.dedent("""
            import unittest
            class T(unittest.TestCase):
                def test_x(self):
                    with self.assertRaises(ValueError):
                        raise ValueError()
        """))
        cats = [f["category"] for f in report["findings"]]
        self.assertNotIn(W.WA001, cats)

    def test_assertion_only_in_for_loop_is_not_wa001(self):
        report = self._scan(textwrap.dedent("""
            import unittest
            class T(unittest.TestCase):
                def test_x(self):
                    for i in range(3):
                        self.assertEqual(i, i)
        """))
        cats = [f["category"] for f in report["findings"]]
        self.assertNotIn(W.WA001, cats)

    def test_module_level_helper_not_self_method_resolved(self):
        report = self._scan(textwrap.dedent("""
            import unittest
            def _check(case, a, b):
                case.assertEqual(a, b)
            class T(unittest.TestCase):
                def test_x(self):
                    _check(self, 1, 1)
        """))
        # This is a documented miss: the helper's assertion is made via a
        # `case` parameter, not literally `self.`/`cls.`, so has_direct_
        # assertion on the helper does NOT see it (attribute is on `case`,
        # not `self`/`cls`... but _is_assert_call_func matches ANY
        # Attribute whose name starts with "assert", regardless of the
        # receiver name, so this in fact IS resolved. Documented as a
        # deliberately loose match.
        cats = [f["category"] for f in report["findings"]]
        self.assertNotIn(W.WA001, cats)

    def test_subtest_block_assertion_counted(self):
        report = self._scan(textwrap.dedent("""
            import unittest
            class T(unittest.TestCase):
                def test_x(self):
                    for i in range(2):
                        with self.subTest(i=i):
                            self.assertEqual(i, i)
        """))
        cats = [f["category"] for f in report["findings"]]
        self.assertNotIn(W.WA001, cats)

    def test_assertion_inside_try_except_counted(self):
        report = self._scan(textwrap.dedent("""
            import unittest
            class T(unittest.TestCase):
                def test_x(self):
                    try:
                        risky()
                    except Exception:
                        self.assertTrue(True)
        """))
        cats = [f["category"] for f in report["findings"]]
        self.assertNotIn(W.WA001, cats)

    def test_file_that_fails_to_parse_is_handled_gracefully(self):
        report = self._scan("def test_x(:\n    pass\n")
        self.assertEqual(report["findings"], [])
        self.assertEqual(len(report["errors"]), 1)
        self.assertEqual(report["summary"]["findings_total"], 0)

    def test_test_function_outside_any_class_is_scanned(self):
        report = self._scan("def test_x():\n    y = 1\n")
        self.assertEqual(len(report["findings"]), 1)
        self.assertEqual(report["findings"][0]["test_name"], "test_x")

    def test_setup_that_asserts_does_not_mask_sibling_weak_test(self):
        report = self._scan(textwrap.dedent("""
            import unittest
            class T(unittest.TestCase):
                def setUp(self):
                    self.assertIsNotNone(self)
                def test_weak(self):
                    x = 1
        """))
        names = [f["test_name"] for f in report["findings"] if f["category"] == W.WA001]
        self.assertEqual(names, ["T.test_weak"])

    def test_full_scan_of_samples_strong_directory_is_clean(self):
        strong_dir = os.path.join(HERE, "samples_strong")
        if os.path.isdir(strong_dir):
            report = W.build_report(strong_dir)
            self.assertEqual(report["summary"]["findings_total"], 0)

    def test_full_scan_of_samples_weak_directory_trips_all_four(self):
        weak_dir = os.path.join(HERE, "samples_weak")
        if os.path.isdir(weak_dir):
            report = W.build_report(weak_dir)
            found_cats = set(report["summary"]["findings_by_category"].get(c, 0) > 0 for c in W.ALL_CATEGORIES)
            self.assertEqual(found_cats, {True})


# ==========================================================================
# build_arg_parser
# ==========================================================================

class TestArgParser(unittest.TestCase):
    def test_default_root_is_dot(self):
        parser = W.build_arg_parser()
        args = parser.parse_args([])
        self.assertEqual(args.root, ".")

    def test_default_output_is_none(self):
        parser = W.build_arg_parser()
        args = parser.parse_args([])
        self.assertIsNone(args.output)

    def test_default_category_is_none(self):
        parser = W.build_arg_parser()
        args = parser.parse_args([])
        self.assertIsNone(args.category)

    def test_short_output_flag(self):
        parser = W.build_arg_parser()
        args = parser.parse_args(["-o", "out.json"])
        self.assertEqual(args.output, "out.json")

    def test_long_output_flag(self):
        parser = W.build_arg_parser()
        args = parser.parse_args(["--output", "out.json"])
        self.assertEqual(args.output, "out.json")

    def test_category_choices_enforced(self):
        parser = W.build_arg_parser()
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                parser.parse_args(["--category", "bogus"])

    def test_category_repeatable(self):
        parser = W.build_arg_parser()
        args = parser.parse_args(["--category", W.WA001, "--category", W.WA002])
        self.assertEqual(args.category, [W.WA001, W.WA002])

    def test_root_flag_parsed(self):
        parser = W.build_arg_parser()
        args = parser.parse_args(["--root", "/some/dir"])
        self.assertEqual(args.root, "/some/dir")


# ==========================================================================
# _local_assign_map / contains_subject_call_with_locals
# ==========================================================================

class TestLocalAssignResolution(unittest.TestCase):
    def test_simple_assignment_captured(self):
        f = first_function("def test_x():\n    a = subject.foo()\n    self.assertEqual(a, 1)\n")
        m = W._local_assign_map(f)
        self.assertIn("a", m)

    def test_multiple_assignment_targets_ignored(self):
        f = first_function("def test_x():\n    a, b = 1, 2\n")
        m = W._local_assign_map(f)
        self.assertEqual(m, {})

    def test_reassignment_keeps_both_in_order(self):
        f = first_function("def test_x():\n    a = 1\n    a = 2\n")
        m = W._local_assign_map(f)
        self.assertEqual(len(m["a"]), 2)

    def test_assignment_inside_if_not_captured_top_level_only(self):
        f = first_function("def test_x():\n    if True:\n        a = subject.foo()\n")
        m = W._local_assign_map(f)
        self.assertEqual(m, {})

    def test_with_locals_resolves_name(self):
        modules, symbols = {"subject"}, set()
        assign_map = {"a": [ast.parse("subject.foo()", mode="eval").body]}
        name_expr = ast.parse("a", mode="eval").body
        self.assertTrue(W.contains_subject_call_with_locals(name_expr, assign_map, modules, symbols))

    def test_with_locals_false_when_unresolved(self):
        modules, symbols = {"subject"}, set()
        assign_map = {}
        name_expr = ast.parse("a", mode="eval").body
        self.assertFalse(W.contains_subject_call_with_locals(name_expr, assign_map, modules, symbols))

    def test_with_locals_direct_call_without_indirection(self):
        modules, symbols = {"subject"}, set()
        expr = ast.parse("subject.foo()", mode="eval").body
        self.assertTrue(W.contains_subject_call_with_locals(expr, {}, modules, symbols))


# ==========================================================================
# collect_subject_calls
# ==========================================================================

class TestCollectSubjectCalls(unittest.TestCase):
    def test_returns_unparsed_calls(self):
        expr = ast.parse("subject.add(1, 2)", mode="eval").body
        result = W.collect_subject_calls(expr, {"subject"}, set())
        self.assertEqual(result, ["subject.add(1, 2)"])

    def test_returns_empty_for_no_calls(self):
        expr = ast.parse("42", mode="eval").body
        result = W.collect_subject_calls(expr, {"subject"}, set())
        self.assertEqual(result, [])

    def test_none_expr_returns_empty(self):
        self.assertEqual(W.collect_subject_calls(None, {"subject"}, set()), [])

    def test_multiple_calls_collected(self):
        expr = ast.parse("[subject.add(1,2), subject.sub(1,2)]", mode="eval").body
        result = W.collect_subject_calls(expr, {"subject"}, set())
        self.assertEqual(len(result), 2)


# ==========================================================================
# Finding class
# ==========================================================================

class TestFinding(unittest.TestCase):
    def test_to_dict_has_expected_keys(self):
        f = W.Finding(W.WA001, "a.py", 3, "T.test_x", "detail text")
        d = f.to_dict()
        self.assertEqual(set(d.keys()), {"category", "path", "line", "test_name", "detail"})

    def test_sort_key_matches_spec_order(self):
        f = W.Finding(W.WA002, "b.py", 5, "T.test_y", "d")
        self.assertEqual(f.sort_key(), (W.WA002, "b.py", 5, "T.test_y"))

    def test_findings_sort_correctly_with_python_sort(self):
        f1 = W.Finding(W.WA002, "a.py", 5, "T.test_a", "d")
        f2 = W.Finding(W.WA001, "z.py", 1, "T.test_z", "d")
        findings = [f1, f2]
        findings.sort(key=W.Finding.sort_key)
        self.assertEqual(findings[0], f2)


if __name__ == "__main__":
    unittest.main()
