#!/usr/bin/env python3
"""Unit tests for docval.py. Stdlib-only (unittest, tempfile, os, sys, json,
subprocess). Organized by the module surface: canonical JSON, AST-based
argparse extraction, README extraction, the command safety gate + execution,
tool-directory discovery, per-tool comparison (DOC001-008), and end-to-end
CLI behavior (exit codes, determinism, relocation)."""

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import docval  # noqa: E402


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(content) if content.startswith("\n") else content)


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="docval_test_")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def p(self, *parts):
        return os.path.join(self._tmp, *parts)


# ===========================================================================
# Canonical JSON
# ===========================================================================

class TestCanonicalJson(unittest.TestCase):
    def test_sorted_keys(self):
        out = docval.canonical_dumps({"b": 1, "a": 2})
        self.assertTrue(out.index('"a"') < out.index('"b"'))

    def test_tight_separators(self):
        out = docval.canonical_dumps({"a": [1, 2], "b": 3})
        self.assertNotIn(", ", out)
        self.assertNotIn(": ", out)

    def test_trailing_newline(self):
        out = docval.canonical_dumps({"a": 1})
        self.assertTrue(out.endswith("\n"))
        self.assertFalse(out.endswith("\n\n"))

    def test_ascii_only(self):
        out = docval.canonical_dumps({"a": "café"})
        self.assertNotIn("é", out)
        self.assertIn("\\u00e9", out)

    def test_deterministic_across_calls(self):
        obj = {"z": 1, "y": [3, 2, 1], "x": {"n": 2, "m": 1}}
        self.assertEqual(docval.canonical_dumps(obj), docval.canonical_dumps(obj))

    def test_empty_object(self):
        self.assertEqual(docval.canonical_dumps({}), "{}\n")

    def test_nested_sorted_keys(self):
        out = docval.canonical_dumps({"outer": {"z": 1, "a": 2}})
        self.assertTrue(out.index('"a"') < out.index('"z"'))


# ===========================================================================
# Finding
# ===========================================================================

class TestFinding(unittest.TestCase):
    def test_sort_key_order(self):
        f = docval.Finding("DOC001_UNDOCUMENTED_FLAG", "a/README.md", "zzz")
        self.assertEqual(f.sort_key(), ("DOC001_UNDOCUMENTED_FLAG", "a/README.md", "zzz"))

    def test_to_dict(self):
        f = docval.Finding("DOC002_PHANTOM_FLAG", "x", "y")
        self.assertEqual(f.to_dict(), {"code": "DOC002_PHANTOM_FLAG", "path": "x", "detail": "y"})

    def test_findings_sort_by_code_then_path_then_detail(self):
        a = docval.Finding("DOC002_PHANTOM_FLAG", "a", "1")
        b = docval.Finding("DOC001_UNDOCUMENTED_FLAG", "b", "1")
        c = docval.Finding("DOC001_UNDOCUMENTED_FLAG", "a", "2")
        d = docval.Finding("DOC001_UNDOCUMENTED_FLAG", "a", "1")
        items = [a, b, c, d]
        items.sort(key=lambda f: f.sort_key())
        # DOC001 items sort before DOC002; within DOC001, "a" before "b";
        # within path "a", detail "1" before "2".
        self.assertEqual([f.sort_key() for f in items], [
            ("DOC001_UNDOCUMENTED_FLAG", "a", "1"),
            ("DOC001_UNDOCUMENTED_FLAG", "a", "2"),
            ("DOC001_UNDOCUMENTED_FLAG", "b", "1"),
            ("DOC002_PHANTOM_FLAG", "a", "1"),
        ])


class TestRelpath(unittest.TestCase):
    def test_forward_slashes(self):
        root = os.path.join("a", "b")
        full = os.path.join("a", "b", "c", "d.py")
        rel = docval.relpath(full, root)
        self.assertEqual(rel, "c/d.py")
        self.assertNotIn("\\", rel)

    def test_same_dir(self):
        self.assertEqual(docval.relpath("/x/y", "/x/y"), ".")


# ===========================================================================
# _literal_int / _collect_const_map / _returns_of (AST helpers)
# ===========================================================================

def _parse_expr(src):
    return ast.parse(src, mode="eval").body


class TestLiteralInt(unittest.TestCase):
    def test_positive_int(self):
        self.assertEqual(docval._literal_int(_parse_expr("5"), {}), (True, 5))

    def test_zero(self):
        self.assertEqual(docval._literal_int(_parse_expr("0"), {}), (True, 0))

    def test_negative_int_unary(self):
        self.assertEqual(docval._literal_int(_parse_expr("-1"), {}), (True, -1))

    def test_bool_is_not_int(self):
        ok, _ = docval._literal_int(_parse_expr("True"), {})
        self.assertFalse(ok)

    def test_string_is_not_int(self):
        ok, _ = docval._literal_int(_parse_expr("'2'"), {})
        self.assertFalse(ok)

    def test_name_resolved_via_const_map(self):
        node = _parse_expr("EXIT_CODE")
        self.assertEqual(docval._literal_int(node, {"EXIT_CODE": 7}), (True, 7))

    def test_name_not_in_const_map_is_unresolved(self):
        node = _parse_expr("UNKNOWN_NAME")
        ok, _ = docval._literal_int(node, {})
        self.assertFalse(ok)

    def test_call_expression_is_unresolved(self):
        node = _parse_expr("compute()")
        ok, _ = docval._literal_int(node, {})
        self.assertFalse(ok)

    def test_negative_name_lookup(self):
        node = _parse_expr("-EXIT_CODE")
        self.assertEqual(docval._literal_int(node, {"EXIT_CODE": 3}), (True, -3))


class TestCollectConstMap(unittest.TestCase):
    def test_simple_assignment(self):
        tree = ast.parse("EXIT_OK = 0\nEXIT_BAD = 1\n")
        cmap = docval._collect_const_map(tree)
        self.assertEqual(cmap.get("EXIT_OK"), 0)
        self.assertEqual(cmap.get("EXIT_BAD"), 1)

    def test_string_assignment_ignored(self):
        tree = ast.parse("NAME = 'not an int'\n")
        cmap = docval._collect_const_map(tree)
        self.assertNotIn("NAME", cmap)

    def test_nested_in_function_still_collected(self):
        tree = ast.parse("def f():\n    X = 9\n    return X\n")
        cmap = docval._collect_const_map(tree)
        self.assertEqual(cmap.get("X"), 9)

    def test_multiple_targets(self):
        tree = ast.parse("A = B = 4\n")
        cmap = docval._collect_const_map(tree)
        self.assertEqual(cmap.get("A"), 4)
        self.assertEqual(cmap.get("B"), 4)

    def test_negative_constant(self):
        tree = ast.parse("CODE = -5\n")
        cmap = docval._collect_const_map(tree)
        self.assertEqual(cmap.get("CODE"), -5)


class TestReturnsOf(unittest.TestCase):
    def _returns(self, src, funcname="main"):
        tree = ast.parse(src)
        const_map = docval._collect_const_map(tree)
        funcs = docval._func_defs_by_name(tree)
        return docval._returns_of(funcs[funcname], const_map, funcs)

    def test_single_int_return(self):
        codes, dyn = self._returns("def main():\n    return 2\n")
        self.assertEqual(codes, {2})
        self.assertFalse(dyn)

    def test_multiple_branches(self):
        src = "def main():\n    if x:\n        return 1\n    return 0\n"
        codes, dyn = self._returns(src)
        self.assertEqual(codes, {0, 1})
        self.assertFalse(dyn)

    def test_bare_return_is_zero(self):
        src = "def main():\n    if x:\n        return\n    return 1\n"
        codes, dyn = self._returns(src)
        self.assertIn(0, codes)
        self.assertIn(1, codes)

    def test_return_none_is_zero(self):
        src = "def main():\n    return None\n"
        codes, dyn = self._returns(src)
        self.assertEqual(codes, {0})

    def test_falls_off_end_is_zero(self):
        src = "def main():\n    x = 1\n"
        codes, dyn = self._returns(src)
        self.assertEqual(codes, {0})

    def test_return_via_const_name(self):
        src = "EXIT_BAD = 3\ndef main():\n    return EXIT_BAD\n"
        codes, dyn = self._returns(src)
        self.assertEqual(codes, {3})
        self.assertFalse(dyn)

    def test_return_dynamic_expression(self):
        src = "def main():\n    return compute_code()\n"
        codes, dyn = self._returns(src)
        self.assertTrue(dyn)

    def test_return_via_nested_function_call(self):
        src = (
            "def helper():\n    return 4\n"
            "def main():\n    return helper()\n"
        )
        codes, dyn = self._returns(src)
        self.assertEqual(codes, {4})
        self.assertFalse(dyn)

    def test_does_not_descend_into_nested_def_returns(self):
        src = (
            "def main():\n"
            "    def inner():\n"
            "        return 99\n"
            "    return 1\n"
        )
        codes, dyn = self._returns(src)
        self.assertEqual(codes, {1})
        self.assertNotIn(99, codes)


# ===========================================================================
# extract_argparse_info -- AST-based, never imports the target module
# ===========================================================================

class TestExtractArgparseInfo(TempDirCase):
    def _write_py(self, src):
        path = self.p("mod.py")
        write(path, textwrap.dedent(src))
        return path

    def test_simple_flags_detected(self):
        path = self._write_py("""
            import argparse
            p = argparse.ArgumentParser()
            p.add_argument("-o", "--output")
            args = p.parse_args()
        """)
        info = docval.extract_argparse_info(path)
        self.assertIn("-o", info.flags)
        self.assertIn("--output", info.flags)

    def test_positional_argument_tracked_separately(self):
        path = self._write_py("""
            import argparse
            p = argparse.ArgumentParser()
            p.add_argument("input")
            p.add_argument("-o", "--output")
            args = p.parse_args()
        """)
        info = docval.extract_argparse_info(path)
        self.assertIn("input", info.positionals)
        self.assertNotIn("input", info.flags)

    def test_has_argument_parser_and_parse_args_flags(self):
        path = self._write_py("""
            import argparse
            p = argparse.ArgumentParser()
            p.add_argument("--x")
            args = p.parse_args()
        """)
        info = docval.extract_argparse_info(path)
        self.assertTrue(info.has_argument_parser)
        self.assertTrue(info.has_parse_args)

    def test_no_argparse_at_all(self):
        path = self._write_py("""
            import sys
            print(sys.argv)
        """)
        info = docval.extract_argparse_info(path)
        self.assertFalse(info.has_argument_parser)
        self.assertFalse(info.has_parse_args)
        self.assertEqual(info.flags, set())

    def test_parser_without_parse_args_call_not_a_cli(self):
        path = self._write_py("""
            import argparse
            p = argparse.ArgumentParser()
            p.add_argument("--x")
        """)
        info = docval.extract_argparse_info(path)
        self.assertTrue(info.has_argument_parser)
        self.assertFalse(info.has_parse_args)

    def test_dynamic_flag_variable_not_added_as_literal_flag(self):
        # Edge case from spec: add_argument called with a variable instead
        # of a string literal. We cannot know the flag text statically, so
        # it must not silently become a fabricated flag name.
        path = self._write_py("""
            import argparse
            FLAG_NAME = "--dynamic"
            p = argparse.ArgumentParser()
            p.add_argument(FLAG_NAME)
            args = p.parse_args()
        """)
        info = docval.extract_argparse_info(path)
        self.assertNotIn("--dynamic", info.flags)
        self.assertEqual(info.dynamic_flag_calls, 1)

    def test_one_form_only_add_argument_output_documented_form(self):
        path = self._write_py("""
            import argparse
            p = argparse.ArgumentParser()
            p.add_argument("-o", "--output")
            args = p.parse_args()
        """)
        info = docval.extract_argparse_info(path)
        self.assertEqual(info.flags, {"-o", "--output"})

    def test_add_help_false_removes_implicit_help_flags(self):
        path = self._write_py("""
            import argparse
            p = argparse.ArgumentParser(add_help=False)
            p.add_argument("--x")
            args = p.parse_args()
        """)
        info = docval.extract_argparse_info(path)
        self.assertFalse(info.add_help)
        self.assertNotIn("-h", info.implicit_flags)
        self.assertNotIn(0, info.exit_codes)  # no --help path => no implicit 0

    def test_add_help_default_true_adds_implicit_help(self):
        path = self._write_py("""
            import argparse
            p = argparse.ArgumentParser()
            p.add_argument("--x")
            args = p.parse_args()
        """)
        info = docval.extract_argparse_info(path)
        self.assertIn("-h", info.implicit_flags)
        self.assertIn("--help", info.implicit_flags)
        self.assertNotIn("-h", info.flags)  # implicit, not explicit

    def test_implicit_flags_not_in_explicit_flags_set(self):
        path = self._write_py("""
            import argparse
            p = argparse.ArgumentParser()
            p.add_argument("--x")
            args = p.parse_args()
        """)
        info = docval.extract_argparse_info(path)
        self.assertEqual(info.flags, {"--x"})

    def test_implicit_exit_codes_0_and_2_present(self):
        path = self._write_py("""
            import argparse
            p = argparse.ArgumentParser()
            p.add_argument("--x")
            args = p.parse_args()
        """)
        info = docval.extract_argparse_info(path)
        self.assertIn(0, info.exit_codes)
        self.assertIn(2, info.exit_codes)

    def test_direct_sys_exit_int(self):
        path = self._write_py("""
            import argparse, sys
            p = argparse.ArgumentParser()
            p.add_argument("--x")
            args = p.parse_args()
            sys.exit(5)
        """)
        info = docval.extract_argparse_info(path)
        self.assertIn(5, info.exit_codes)

    def test_sys_exit_via_const_defined_elsewhere(self):
        # Edge case from spec: sys.exit(EXIT_CONST) where the constant is
        # defined far from the call site.
        path = self._write_py("""
            import argparse, sys
            EXIT_SCHEMA_ERROR = 4

            def other_stuff():
                pass

            p = argparse.ArgumentParser()
            p.add_argument("--x")
            args = p.parse_args()
            sys.exit(EXIT_SCHEMA_ERROR)
        """)
        info = docval.extract_argparse_info(path)
        self.assertIn(4, info.exit_codes)

    def test_sys_exit_main_pattern_resolves_returns(self):
        path = self._write_py("""
            import argparse, sys

            def main(argv=None):
                p = argparse.ArgumentParser()
                p.add_argument("--x")
                args = p.parse_args(argv)
                if args.x:
                    return 1
                return 0

            if __name__ == "__main__":
                sys.exit(main())
        """)
        info = docval.extract_argparse_info(path)
        self.assertEqual({0, 1, 2}, info.exit_codes)  # 0/1 explicit, 2 implicit

    def test_sys_exit_dynamic_unresolvable(self):
        path = self._write_py("""
            import argparse, sys
            p = argparse.ArgumentParser()
            p.add_argument("--x")
            args = p.parse_args()
            sys.exit(compute_code())
        """)
        info = docval.extract_argparse_info(path)
        self.assertTrue(info.dynamic_exit)

    def test_raise_system_exit_literal(self):
        path = self._write_py("""
            import argparse
            p = argparse.ArgumentParser()
            p.add_argument("--x")
            args = p.parse_args()
            raise SystemExit(9)
        """)
        info = docval.extract_argparse_info(path)
        self.assertIn(9, info.exit_codes)

    def test_bare_sys_exit_is_zero(self):
        path = self._write_py("""
            import argparse, sys
            p = argparse.ArgumentParser()
            p.add_argument("--x")
            args = p.parse_args()
            sys.exit()
        """)
        info = docval.extract_argparse_info(path)
        self.assertIn(0, info.exit_codes)

    def test_sys_exit_none_is_zero(self):
        path = self._write_py("""
            import argparse, sys
            p = argparse.ArgumentParser()
            p.add_argument("--x")
            args = p.parse_args()
            sys.exit(None)
        """)
        info = docval.extract_argparse_info(path)
        self.assertIn(0, info.exit_codes)

    def test_parser_error_call_detected(self):
        path = self._write_py("""
            import argparse
            p = argparse.ArgumentParser()
            p.add_argument("--x")
            args = p.parse_args()
            if not args.x:
                p.error("missing --x")
        """)
        info = docval.extract_argparse_info(path)
        self.assertTrue(info.parse_error_present)

    def test_subparsers_add_parser_flags_detected(self):
        path = self._write_py("""
            import argparse
            p = argparse.ArgumentParser()
            sub = p.add_subparsers()
            build = sub.add_parser("build")
            build.add_argument("--fast")
            args = p.parse_args()
        """)
        info = docval.extract_argparse_info(path)
        self.assertIn("--fast", info.flags)

    def test_multiple_add_argument_calls_all_collected(self):
        path = self._write_py("""
            import argparse
            p = argparse.ArgumentParser()
            p.add_argument("-a", "--alpha")
            p.add_argument("-b", "--beta")
            p.add_argument("--gamma")
            args = p.parse_args()
        """)
        info = docval.extract_argparse_info(path)
        self.assertEqual(info.flags, {"-a", "--alpha", "-b", "--beta", "--gamma"})

    def test_string_literal_containing_fake_argparse_code_is_ignored(self):
        # This is the exact trap seen in one of the real sibling tools'
        # test suites: a triple-quoted string TEMPLATE containing what
        # *looks* like argparse code as plain text. ast.parse must not be
        # fooled -- string contents are never re-parsed as code.
        path = self._write_py('''
            import argparse

            TEMPLATE = """
            import argparse, json, sys
            ap = argparse.ArgumentParser()
            ap.add_argument("-o", "--output")
            args = ap.parse_args()
            """

            p = argparse.ArgumentParser()
            p.add_argument("--real-flag")
            args = p.parse_args()
        ''')
        info = docval.extract_argparse_info(path)
        self.assertEqual(info.flags, {"--real-flag"})
        self.assertNotIn("--output", info.flags)
        self.assertNotIn("-o", info.flags)

    def test_syntax_error_file_handled_gracefully(self):
        path = self._write_py("def broken(:\n    pass\n")
        info = docval.extract_argparse_info(path)
        self.assertFalse(info.has_argument_parser)

    def test_nonexistent_file_handled_gracefully(self):
        info = docval.extract_argparse_info(self.p("does_not_exist.py"))
        self.assertFalse(info.has_argument_parser)

    def test_store_true_action_flag_detected(self):
        path = self._write_py("""
            import argparse
            p = argparse.ArgumentParser()
            p.add_argument("--verbose", action="store_true")
            args = p.parse_args()
        """)
        info = docval.extract_argparse_info(path)
        self.assertIn("--verbose", info.flags)

    def test_exit_codes_from_multiple_call_sites(self):
        path = self._write_py("""
            import argparse, sys
            p = argparse.ArgumentParser()
            p.add_argument("--x")
            args = p.parse_args()
            if args.x == "a":
                sys.exit(3)
            if args.x == "b":
                sys.exit(4)
        """)
        info = docval.extract_argparse_info(path)
        self.assertIn(3, info.exit_codes)
        self.assertIn(4, info.exit_codes)

    def test_class_level_const_not_confused_with_module_const(self):
        path = self._write_py("""
            import argparse, sys

            class Foo:
                BAR = 8

            p = argparse.ArgumentParser()
            p.add_argument("--x")
            args = p.parse_args()
            sys.exit(Foo.BAR)
        """)
        info = docval.extract_argparse_info(path)
        # Foo.BAR is an Attribute access, not a bare Name -> unresolvable,
        # must be recorded as dynamic rather than silently guessed.
        self.assertTrue(info.dynamic_exit)


# ===========================================================================
# extract_readme_info -- flag tokens, exit codes, fenced code blocks
# ===========================================================================

class TestFlagTokenExtraction(TempDirCase):
    def _doc_flags(self, text):
        path = self.p("README.md")
        write(path, text)
        return docval.extract_readme_info(path).doc_flags

    def test_long_flag_in_prose(self):
        self.assertIn("--budget-cap", self._doc_flags("Use --budget-cap to set a limit."))

    def test_short_flag_in_prose(self):
        self.assertIn("-o", self._doc_flags("Pass -o to write output."))

    def test_flag_in_backticks(self):
        self.assertIn("--output", self._doc_flags("The `--output` flag writes a file."))

    def test_flag_in_bold(self):
        self.assertIn("-k", self._doc_flags("**-k** sets the open-tasks file."))

    def test_flag_in_table_cell(self):
        self.assertIn("--strict", self._doc_flags("| flag | meaning |\n|---|---|\n| `--strict` | fail hard |\n"))

    def test_table_separator_row_not_a_flag(self):
        flags = self._doc_flags("| a | b |\n|------|------|\n| 1 | 2 |\n")
        self.assertEqual(flags, set())

    def test_horizontal_rule_not_a_flag(self):
        self.assertEqual(self._doc_flags("above\n\n---\n\nbelow\n"), set())

    def test_negative_number_not_a_flag(self):
        self.assertEqual(self._doc_flags("the delta was -15 units\n"), set())

    def test_version_number_not_a_flag(self):
        self.assertEqual(self._doc_flags("see RFC-3339 for the format\n"), set())

    def test_hyphenated_word_not_a_flag(self):
        self.assertEqual(self._doc_flags("this is a well-known convention\n"), set())

    def test_flag_at_start_of_line(self):
        self.assertIn("--root", self._doc_flags("--root PATH sets the scan root.\n"))

    def test_flag_inside_parentheses(self):
        self.assertIn("-v", self._doc_flags("run it verbosely (-v) if needed\n"))

    def test_multiple_distinct_flags(self):
        flags = self._doc_flags("Use `-o`, `--output`, and `--no-run` together.\n")
        self.assertEqual(flags, {"-o", "--output", "--no-run"})

    def test_no_readme_file_returns_not_exists(self):
        info = docval.extract_readme_info(self.p("README.md"))
        self.assertFalse(info.exists)
        self.assertEqual(info.doc_flags, set())

    def test_module_invocation_line_masked_from_flags(self):
        text = "```\npython3 -m unittest test_x -v\n```\n"
        flags = self._doc_flags(text)
        self.assertNotIn("-m", flags)
        self.assertNotIn("-v", flags)

    def test_flag_used_in_own_command_block_counts_as_documented(self):
        text = "```\npython3 tool.py in.json --output out.json\n```\n"
        flags = self._doc_flags(text)
        self.assertIn("--output", flags)

    def test_module_invocation_mask_does_not_eat_following_lines(self):
        text = "```\npython3 -m unittest test_x -v\npython3 tool.py --keep\n```\n"
        flags = self._doc_flags(text)
        self.assertIn("--keep", flags)


class TestExitCodeExtraction(TempDirCase):
    def _doc_codes(self, text):
        path = self.p("README.md")
        write(path, text)
        return docval.extract_readme_info(path).doc_exit_codes

    def test_exit_bold_style(self):
        self.assertIn(0, self._doc_codes("returns exit **0** on success\n"))

    def test_exit_equals_style(self):
        self.assertIn(2, self._doc_codes("exit=2\n"))

    def test_exit_code_colon_style(self):
        self.assertIn(1, self._doc_codes("exit code: 1\n"))

    def test_exit_codes_plural_backtick_style(self):
        codes = self._doc_codes("Exit codes: `0` clean, `1` findings\n")
        self.assertIn(0, codes)

    def test_exit_codes_list_only_captures_first_number(self):
        # Documented false-negative risk: a single "exit(s):" lead-in
        # followed by a comma-separated list of codes only anchors the
        # FIRST number to the word "exit" -- subsequent numbers in the same
        # sentence are not independently "exit N"-shaped and are missed
        # unless mentioned again elsewhere in the document (see README
        # "Limitations"). This pins the real, current behavior rather than
        # an aspirational one.
        codes = self._doc_codes("Exit codes: `0` clean, `1` findings\n")
        self.assertNotIn(1, codes)

    def test_exit_word_directly_adjacent_to_backtick_number(self):
        self.assertIn(1, self._doc_codes("both tools still exit `1` in that case\n"))

    def test_exit_hyphen_backtick_style(self):
        self.assertIn(2, self._doc_codes("Exit-`2` is reserved for usage errors\n"))

    def test_dot_separated_list_under_heading(self):
        text = "## Exit codes\n\n0 = ok \xb7 1 = findings \xb7 2 = bad input\n"
        codes = self._doc_codes(text)
        self.assertEqual(codes, {0, 1, 2})

    def test_dot_separated_list_NOT_under_heading_is_ignored(self):
        # Bare "N = ..." prose with no Exit-codes heading nearby is too
        # ambiguous (could be any kind of enumerated list) -- only the
        # unambiguous "exit N" style is trusted outside a dedicated section.
        text = "Some unrelated list: 0 = apples \xb7 1 = oranges\n"
        codes = self._doc_codes(text)
        self.assertEqual(codes, set())

    def test_markdown_table_under_exit_heading(self):
        text = (
            "## Exit codes\n\n"
            "| Code | Meaning |\n"
            "|------|---------|\n"
            "| `0`  | clean |\n"
            "| `1`  | findings |\n"
            "| `2`  | usage error |\n"
        )
        self.assertEqual(self._doc_codes(text), {0, 1, 2})

    def test_markdown_table_NOT_under_exit_heading_is_ignored(self):
        text = (
            "## Some other table\n\n"
            "| Code | Meaning |\n"
            "|------|---------|\n"
            "| 0  | not an exit code, just a coincidence |\n"
        )
        self.assertEqual(self._doc_codes(text), set())

    def test_dash_bullet_list_under_heading(self):
        text = (
            "### Exit codes\n\n"
            "- `0` - scan completed, zero findings\n"
            "- `1` - scan completed, one or more findings\n"
            "- `2` - usage error\n"
        )
        self.assertEqual(self._doc_codes(text), {0, 1, 2})

    def test_section_ends_at_next_heading(self):
        text = (
            "## Exit codes\n\n"
            "- `0` - ok\n"
            "## Something else entirely\n\n"
            "- `9` - not actually an exit code, different section\n"
        )
        codes = self._doc_codes(text)
        self.assertIn(0, codes)
        self.assertNotIn(9, codes)

    def test_h1_heading_also_recognized(self):
        text = "# Exit Codes\n\n- `0` - ok\n"
        self.assertIn(0, self._doc_codes(text))

    def test_no_exit_codes_documented_at_all(self):
        self.assertEqual(self._doc_codes("Just a plain README with no exit info.\n"), set())


class TestFencedCodeBlockExtraction(TempDirCase):
    def _blocks(self, text):
        path = self.p("README.md")
        write(path, text)
        return docval.extract_readme_info(path).code_blocks

    def test_single_untagged_block(self):
        blocks = self._blocks("before\n\n```\npython3 tool.py x\n```\n\nafter\n")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0][0], "")

    def test_language_tag_captured(self):
        blocks = self._blocks("```bash\npython3 tool.py x\n```\n")
        self.assertEqual(blocks[0][0], "bash")

    def test_multiple_blocks(self):
        text = "```\ncmd1\n```\n\ntext\n\n```json\n{}\n```\n"
        blocks = self._blocks(text)
        self.assertEqual(len(blocks), 2)

    def test_no_fences_yields_no_blocks(self):
        self.assertEqual(self._blocks("Just prose, no code fences anywhere.\n"), [])

    def test_start_line_number_correct(self):
        text = "line1\nline2\n```\ncmd_on_line4\n```\n"
        blocks = self._blocks(text)
        self.assertEqual(blocks[0][2], 4)  # body starts at line 4

    def test_block_body_preserved_verbatim(self):
        text = "```\npython3 tool.py a\npython3 tool.py b\n```\n"
        blocks = self._blocks(text)
        self.assertIn("python3 tool.py a", blocks[0][1])
        self.assertIn("python3 tool.py b", blocks[0][1])


# ===========================================================================
# Command safety gate + execution (DOC005 / DOC006)
# ===========================================================================

class TestEvaluateCommandLine(TempDirCase):
    def _tool_dir(self):
        d = self.p("tool")
        os.makedirs(d, exist_ok=True)
        write(os.path.join(d, "ok.py"), "import sys\nprint('hi')\nsys.exit(0)\n")
        write(os.path.join(d, "crash.py"), "raise RuntimeError('boom')\n")
        write(os.path.join(d, "nonzero.py"), "import sys\nsys.exit(3)\n")
        write(os.path.join(d, "slow.py"), "import time\ntime.sleep(120)\n")
        return d

    def test_blank_line_ignored(self):
        self.assertIsNone(docval.evaluate_command_line("   ", self._tool_dir(), True))

    def test_comment_line_ignored(self):
        self.assertIsNone(docval.evaluate_command_line("# a comment", self._tool_dir(), True))

    def test_pipe_refused(self):
        out = docval.evaluate_command_line("echo hi | python3 ok.py", self._tool_dir(), True)
        self.assertEqual(out.kind, "DOC006_COMMAND_BLOCK_UNPARSEABLE")

    def test_ampersand_refused(self):
        out = docval.evaluate_command_line("python3 ok.py &", self._tool_dir(), True)
        self.assertEqual(out.kind, "DOC006_COMMAND_BLOCK_UNPARSEABLE")

    def test_double_ampersand_refused(self):
        out = docval.evaluate_command_line("python3 ok.py && echo done", self._tool_dir(), True)
        self.assertEqual(out.kind, "DOC006_COMMAND_BLOCK_UNPARSEABLE")

    def test_redirect_refused(self):
        out = docval.evaluate_command_line("python3 ok.py > out.txt", self._tool_dir(), True)
        self.assertEqual(out.kind, "DOC006_COMMAND_BLOCK_UNPARSEABLE")

    def test_input_redirect_refused(self):
        out = docval.evaluate_command_line("python3 ok.py < in.txt", self._tool_dir(), True)
        self.assertEqual(out.kind, "DOC006_COMMAND_BLOCK_UNPARSEABLE")

    def test_backtick_refused(self):
        out = docval.evaluate_command_line("python3 ok.py `whoami`", self._tool_dir(), True)
        self.assertEqual(out.kind, "DOC006_COMMAND_BLOCK_UNPARSEABLE")

    def test_command_substitution_refused(self):
        out = docval.evaluate_command_line("python3 ok.py $(whoami)", self._tool_dir(), True)
        self.assertEqual(out.kind, "DOC006_COMMAND_BLOCK_UNPARSEABLE")

    def test_multiple_semicolon_commands_refused(self):
        out = docval.evaluate_command_line("python3 ok.py ; python3 ok.py", self._tool_dir(), True)
        self.assertEqual(out.kind, "DOC006_COMMAND_BLOCK_UNPARSEABLE")

    def test_trailing_echo_exit_idiom_stripped_not_refused(self):
        out = docval.evaluate_command_line('python3 ok.py ; echo "exit=$?"', self._tool_dir(), True)
        self.assertIsNone(out)  # --no-run: passes safety gate, not executed, no finding

    def test_non_python3_program_refused(self):
        out = docval.evaluate_command_line("sha256sum out.json", self._tool_dir(), True)
        self.assertEqual(out.kind, "DOC006_COMMAND_BLOCK_UNPARSEABLE")

    def test_bare_python3_no_target_refused(self):
        out = docval.evaluate_command_line("python3", self._tool_dir(), True)
        self.assertEqual(out.kind, "DOC006_COMMAND_BLOCK_UNPARSEABLE")

    def test_module_flag_refused(self):
        out = docval.evaluate_command_line("python3 -m unittest test_x -v", self._tool_dir(), True)
        self.assertEqual(out.kind, "DOC006_COMMAND_BLOCK_UNPARSEABLE")

    def test_dash_c_flag_refused(self):
        out = docval.evaluate_command_line("python3 -c \"print(1)\"", self._tool_dir(), True)
        self.assertEqual(out.kind, "DOC006_COMMAND_BLOCK_UNPARSEABLE")

    def test_absolute_target_path_refused(self):
        out = docval.evaluate_command_line("python3 /etc/ok.py", self._tool_dir(), True)
        self.assertEqual(out.kind, "DOC006_COMMAND_BLOCK_UNPARSEABLE")

    def test_path_escape_refused(self):
        out = docval.evaluate_command_line("python3 ../../evil.py", self._tool_dir(), True)
        self.assertEqual(out.kind, "DOC006_COMMAND_BLOCK_UNPARSEABLE")

    def test_unbalanced_quotes_refused(self):
        out = docval.evaluate_command_line('python3 ok.py "unterminated', self._tool_dir(), True)
        self.assertEqual(out.kind, "DOC006_COMMAND_BLOCK_UNPARSEABLE")

    def test_missing_target_script_is_doc005(self):
        out = docval.evaluate_command_line("python3 nope.py", self._tool_dir(), True)
        self.assertEqual(out.kind, "DOC005_COMMAND_BLOCK_FAILED")

    def test_missing_target_script_detected_even_under_no_run(self):
        # Static check -- does not require execution.
        d = self._tool_dir()
        out_static = docval.evaluate_command_line("python3 nope.py", d, True)
        out_run = docval.evaluate_command_line("python3 nope.py", d, False)
        self.assertEqual(out_static.kind, "DOC005_COMMAND_BLOCK_FAILED")
        self.assertEqual(out_run.kind, "DOC005_COMMAND_BLOCK_FAILED")

    def test_no_run_skips_execution_for_valid_target(self):
        out = docval.evaluate_command_line("python3 ok.py", self._tool_dir(), True)
        self.assertIsNone(out)  # safety gate passed, not executed -> no finding either way

    def test_clean_run_produces_no_finding(self):
        out = docval.evaluate_command_line("python3 ok.py", self._tool_dir(), False)
        self.assertIsNone(out)

    def test_clean_nonzero_exit_is_not_a_finding(self):
        # A tool intentionally exiting nonzero (e.g. "findings present") is
        # correct behavior, not a documentation defect.
        out = docval.evaluate_command_line("python3 nonzero.py", self._tool_dir(), False)
        self.assertIsNone(out)

    def test_uncaught_traceback_is_doc005(self):
        out = docval.evaluate_command_line("python3 crash.py", self._tool_dir(), False)
        self.assertIsNotNone(out)
        self.assertEqual(out.kind, "DOC005_COMMAND_BLOCK_FAILED")

    def test_crash_only_detected_when_actually_run(self):
        d = self._tool_dir()
        out_static = docval.evaluate_command_line("python3 crash.py", d, True)
        out_run = docval.evaluate_command_line("python3 crash.py", d, False)
        self.assertIsNone(out_static)       # --no-run: file exists, safety gate passes, no exec
        self.assertIsNotNone(out_run)       # actually run: traceback detected

    def test_timeout_is_doc005(self):
        old_timeout = docval.COMMAND_TIMEOUT_SECS
        docval.COMMAND_TIMEOUT_SECS = 1
        try:
            out = docval.evaluate_command_line("python3 slow.py", self._tool_dir(), False)
        finally:
            docval.COMMAND_TIMEOUT_SECS = old_timeout
        self.assertEqual(out.kind, "DOC005_COMMAND_BLOCK_FAILED")
        self.assertIn("timed out", out.detail)

    def test_extra_arguments_passed_through(self):
        d = self._tool_dir()
        write(os.path.join(d, "echoarg.py"), "import sys\nprint(sys.argv[1:])\n")
        out = docval.evaluate_command_line("python3 echoarg.py --flag value", d, False)
        self.assertIsNone(out)

    def test_relative_subdir_target_allowed(self):
        d = self._tool_dir()
        os.makedirs(os.path.join(d, "sub"), exist_ok=True)
        write(os.path.join(d, "sub", "nested.py"), "import sys\nsys.exit(0)\n")
        out = docval.evaluate_command_line("python3 sub/nested.py", d, False)
        self.assertIsNone(out)


class TestJoinContinuations(unittest.TestCase):
    def test_single_line_no_continuation(self):
        # A trailing "\n" always yields one extra empty logical line at the
        # end (harmless -- evaluate_command_line() skips blank lines).
        result = docval._join_continuations("python3 tool.py x\n")
        self.assertEqual(result[0][1], "python3 tool.py x")
        self.assertTrue(all(text == "" for _, text in result[1:]))

    def test_backslash_continuation_joins_lines(self):
        body = "python3 tool.py \\\n    --flag value\n"
        result = docval._join_continuations(body)
        texts = [t for _, t in result if t]
        self.assertEqual(len(texts), 1)
        self.assertIn("--flag value", texts[0])
        self.assertIn("python3 tool.py", texts[0])

    def test_multiple_independent_lines(self):
        body = "python3 a.py\npython3 b.py\n"
        result = docval._join_continuations(body)
        texts = [t for _, t in result if t]
        self.assertEqual(texts, ["python3 a.py", "python3 b.py"])


class TestIsCommandBearingBlock(unittest.TestCase):
    def test_bash_tag_qualifies(self):
        self.assertTrue(docval.is_command_bearing_block("bash", "ls\n"))

    def test_untagged_with_python3_qualifies(self):
        self.assertTrue(docval.is_command_bearing_block("", "python3 tool.py x\n"))

    def test_json_block_does_not_qualify(self):
        self.assertFalse(docval.is_command_bearing_block("json", '{"a": 1}\n'))

    def test_untagged_prose_snippet_does_not_qualify(self):
        self.assertFalse(docval.is_command_bearing_block("", "just some text\n"))

    def test_console_tag_qualifies(self):
        self.assertTrue(docval.is_command_bearing_block("console", "$ ls\n"))


# ===========================================================================
# Tool-directory discovery
# ===========================================================================

class TestDiscoverToolDirs(TempDirCase):
    def _cli(self, path):
        write(path, "import argparse\np = argparse.ArgumentParser()\np.add_argument('--x')\np.parse_args()\n")

    def test_single_tool_at_root(self):
        write(self.p("README.md"), "# doc\n")
        self._cli(self.p("tool.py"))
        dirs = docval.discover_tool_dirs(self._tmp)
        self.assertEqual(dirs, [self._tmp])

    def test_multiple_sibling_tools(self):
        for name in ("alpha", "beta", "gamma"):
            os.makedirs(self.p(name))
            write(self.p(name, "README.md"), "# doc\n")
            self._cli(self.p(name, "tool.py"))
        dirs = sorted(docval.discover_tool_dirs(self._tmp))
        self.assertEqual(len(dirs), 3)

    def test_does_not_descend_into_qualified_tool_subdirs(self):
        os.makedirs(self.p("toolA", "fixture_bundle"))
        write(self.p("toolA", "README.md"), "# doc\n")
        self._cli(self.p("toolA", "tool.py"))
        write(self.p("toolA", "fixture_bundle", "README.md"), "# nested fixture\n")
        self._cli(self.p("toolA", "fixture_bundle", "inner.py"))
        dirs = docval.discover_tool_dirs(self._tmp)
        self.assertEqual(dirs, [self.p("toolA")])

    def test_descends_when_parent_not_qualified(self):
        os.makedirs(self.p("container", "realtool"))
        write(self.p("container", "realtool", "README.md"), "# doc\n")
        self._cli(self.p("container", "realtool", "tool.py"))
        dirs = docval.discover_tool_dirs(self._tmp)
        self.assertEqual(dirs, [self.p("container", "realtool")])

    def test_pycache_skipped(self):
        os.makedirs(self.p("__pycache__"))
        write(self.p("__pycache__", "x.pyc"), "junk")
        dirs = docval.discover_tool_dirs(self._tmp)
        self.assertEqual(dirs, [])

    def test_dot_dirs_skipped(self):
        os.makedirs(self.p(".git"))
        write(self.p(".git", "config"), "junk")
        dirs = docval.discover_tool_dirs(self._tmp)
        self.assertEqual(dirs, [])

    def test_empty_tree_yields_nothing(self):
        os.makedirs(self.p("empty_dir"))
        self.assertEqual(docval.discover_tool_dirs(self._tmp), [])

    def test_readme_only_dir_is_discovered(self):
        write(self.p("README.md"), "# doc, no cli\n")
        dirs = docval.discover_tool_dirs(self._tmp)
        self.assertEqual(dirs, [self._tmp])

    def test_cli_only_dir_is_discovered(self):
        self._cli(self.p("tool.py"))
        dirs = docval.discover_tool_dirs(self._tmp)
        self.assertEqual(dirs, [self._tmp])


class TestFindCliPyFiles(TempDirCase):
    def test_test_file_with_fake_argparse_in_string_not_qualifying(self):
        # Mirrors the real sibling-repo trap: a test file containing a
        # TEMPLATE string with argparse-looking text should not itself be
        # picked up as a second CLI entry point.
        write(self.p("tool.py"), "import argparse\np=argparse.ArgumentParser()\np.add_argument('--x')\np.parse_args()\n")
        write(self.p("test_tool.py"), 'TEMPLATE = """\nimport argparse\np = argparse.ArgumentParser()\np.add_argument("-o")\nargs = p.parse_args()\n"""\n')
        found = docval.find_cli_py_files(self._tmp)
        names = [n for n, _, _ in found]
        self.assertEqual(names, ["tool.py"])

    def test_non_qualifying_py_file_excluded(self):
        write(self.p("helpers.py"), "def add(a, b):\n    return a + b\n")
        found = docval.find_cli_py_files(self._tmp)
        self.assertEqual(found, [])

    def test_manual_sys_argv_parsing_not_a_cli(self):
        # A script that hand-parses sys.argv (no argparse at all) does not
        # qualify -- docval only understands argparse-based CLIs.
        write(self.p("greeter.py"), "import sys\nprint(sys.argv[1:])\n")
        found = docval.find_cli_py_files(self._tmp)
        self.assertEqual(found, [])

    def test_multiple_qualifying_files_all_returned(self):
        write(self.p("a.py"), "import argparse\np=argparse.ArgumentParser()\np.add_argument('--x')\np.parse_args()\n")
        write(self.p("b.py"), "import argparse\np=argparse.ArgumentParser()\np.add_argument('--y')\np.parse_args()\n")
        found = docval.find_cli_py_files(self._tmp)
        names = sorted(n for n, _, _ in found)
        self.assertEqual(names, ["a.py", "b.py"])


# ===========================================================================
# check_tool_dir -- per-tool comparison producing DOC001-008
# ===========================================================================

class TestCheckToolDir(TempDirCase):
    def _codes(self, tool_dir):
        findings = docval.check_tool_dir(tool_dir, self._tmp, no_run=True)
        return sorted(f.code for f in findings)

    def test_doc007_cli_without_readme(self):
        write(self.p("tool.py"), "import argparse\np=argparse.ArgumentParser()\np.add_argument('--x')\np.parse_args()\n")
        self.assertEqual(self._codes(self._tmp), ["DOC007_NO_README"])

    def test_doc008_readme_without_cli(self):
        write(self.p("README.md"), "# nothing to run here\n")
        self.assertEqual(self._codes(self._tmp), ["DOC008_NO_CLI"])

    def test_doc008_readme_with_non_argparse_py(self):
        write(self.p("README.md"), "# doc\n")
        write(self.p("script.py"), "print('hello')\n")
        self.assertEqual(self._codes(self._tmp), ["DOC008_NO_CLI"])

    def test_neither_readme_nor_cli_yields_nothing(self):
        write(self.p("notes.txt"), "just notes\n")
        self.assertEqual(self._codes(self._tmp), [])

    def test_fully_consistent_pair_yields_nothing(self):
        write(self.p("tool.py"), textwrap.dedent("""
            import argparse, sys
            def main(argv=None):
                p = argparse.ArgumentParser()
                p.add_argument("input")
                p.add_argument("-o", "--output")
                args = p.parse_args(argv)
                return 0
            if __name__ == "__main__":
                sys.exit(main())
        """))
        write(self.p("README.md"), textwrap.dedent("""
            # tool

            Flags: `input`, `-o`/`--output`.

            ## Exit codes

            - `0` - always
            - `2` - usage error
        """))
        self.assertEqual(self._codes(self._tmp), [])

    def test_doc001_undocumented_flag(self):
        write(self.p("tool.py"), textwrap.dedent("""
            import argparse, sys
            p = argparse.ArgumentParser()
            p.add_argument("--secret")
            args = p.parse_args()
        """))
        write(self.p("README.md"), "# tool\nno flags mentioned here.\n")
        self.assertIn("DOC001_UNDOCUMENTED_FLAG", self._codes(self._tmp))

    def test_doc002_phantom_flag(self):
        write(self.p("tool.py"), textwrap.dedent("""
            import argparse
            p = argparse.ArgumentParser()
            p.add_argument("--real")
            args = p.parse_args()
        """))
        write(self.p("README.md"), "# tool\nUse `--real` and `--fake` together.\n")
        self.assertIn("DOC002_PHANTOM_FLAG", self._codes(self._tmp))

    def test_doc003_unreachable_exit_code(self):
        write(self.p("tool.py"), textwrap.dedent("""
            import argparse, sys
            p = argparse.ArgumentParser()
            p.add_argument("--x")
            args = p.parse_args()
            sys.exit(0)
        """))
        write(self.p("README.md"), "# tool\n\n## Exit codes\n\n- `0` - ok\n- `7` - never happens\n")
        self.assertIn("DOC003_EXIT_CODE_UNREACHABLE", self._codes(self._tmp))

    def test_doc004_undocumented_exit_code(self):
        write(self.p("tool.py"), textwrap.dedent("""
            import argparse, sys
            p = argparse.ArgumentParser()
            p.add_argument("--x")
            args = p.parse_args()
            sys.exit(5)
        """))
        write(self.p("README.md"), "# tool\n\n## Exit codes\n\n- `0` - ok\n")
        self.assertIn("DOC004_EXIT_CODE_UNDOCUMENTED", self._codes(self._tmp))

    def test_doc003_and_doc004_not_emitted_when_dynamic_exit(self):
        # Cannot know a code isn't reachable/documented if the module's own
        # exit value is unresolvable -- must not guess.
        write(self.p("tool.py"), textwrap.dedent("""
            import argparse, sys
            p = argparse.ArgumentParser()
            p.add_argument("--x")
            args = p.parse_args()
            sys.exit(compute_dynamic_code())
        """))
        write(self.p("README.md"), "# tool\n\n## Exit codes\n\n- `0` - ok\n- `77` - never\n")
        codes = self._codes(self._tmp)
        self.assertNotIn("DOC003_EXIT_CODE_UNREACHABLE", codes)

    def test_doc005_from_missing_script_in_readme_command(self):
        write(self.p("tool.py"), textwrap.dedent("""
            import argparse, sys
            p = argparse.ArgumentParser()
            p.add_argument("--x")
            args = p.parse_args()
            sys.exit(0)
        """))
        write(self.p("README.md"), "# tool\n\n```\npython3 typo_tool.py --x 1\n```\n")
        self.assertIn("DOC005_COMMAND_BLOCK_FAILED", self._codes(self._tmp))

    def test_doc006_from_pipe_in_readme_command(self):
        write(self.p("tool.py"), textwrap.dedent("""
            import argparse, sys
            p = argparse.ArgumentParser()
            p.add_argument("--x")
            args = p.parse_args()
            sys.exit(0)
        """))
        write(self.p("README.md"), "# tool\n\n```\necho hi | python3 tool.py --x 1\n```\n")
        self.assertIn("DOC006_COMMAND_BLOCK_UNPARSEABLE", self._codes(self._tmp))

    def test_readme_with_no_code_fences_at_all(self):
        # Edge case from spec: a README with no code fences. There is
        # simply nothing to run -- must not crash, must not fabricate a
        # DOC005/006 finding out of nothing.
        write(self.p("tool.py"), textwrap.dedent("""
            import argparse, sys
            p = argparse.ArgumentParser()
            p.add_argument("--x")
            args = p.parse_args()
            sys.exit(0)
        """))
        write(self.p("README.md"), "# tool\n\nJust prose. `--x` is the only flag. No examples.\n\n## Exit codes\n\n- `0` - ok\n")
        codes = self._codes(self._tmp)
        self.assertNotIn("DOC005_COMMAND_BLOCK_FAILED", codes)
        self.assertNotIn("DOC006_COMMAND_BLOCK_UNPARSEABLE", codes)

    def test_command_block_spanning_multiple_lines_via_backslash(self):
        write(self.p("tool.py"), textwrap.dedent("""
            import argparse, sys
            p = argparse.ArgumentParser()
            p.add_argument("--x")
            args = p.parse_args()
            sys.exit(0)
        """))
        readme = (
            "# tool\n\n`--x` is the only flag.\n\n"
            "## Exit codes\n\n- `0` - ok\n- `2` - usage error\n\n"
            "```\npython3 tool.py \\\n    --x 1\n```\n"
        )
        write(self.p("README.md"), readme)
        self.assertEqual(self._codes(self._tmp), [])


# ===========================================================================
# End-to-end CLI behavior (run() / main()) -- exit codes, flags, determinism
# ===========================================================================

class TestRunCliInProcess(TempDirCase):
    """Exercises docval.run() directly (in-process) for exit-code and
    output-routing behavior. Command-block execution uses --no-run here so
    these tests do not spawn subprocesses themselves."""

    def _consistent_tool(self, d):
        write(os.path.join(d, "tool.py"), textwrap.dedent("""
            import argparse, sys
            def main(argv=None):
                p = argparse.ArgumentParser()
                p.add_argument("--x")
                args = p.parse_args(argv)
                return 0
            if __name__ == "__main__":
                sys.exit(main())
        """))
        write(os.path.join(d, "README.md"), "# tool\n\n`--x` is the only flag.\n\n## Exit codes\n\n- `0` - always\n- `2` - usage error\n")

    def _inconsistent_tool(self, d):
        write(os.path.join(d, "tool.py"), textwrap.dedent("""
            import argparse, sys
            p = argparse.ArgumentParser()
            p.add_argument("--undocumented")
            args = p.parse_args()
        """))
        write(os.path.join(d, "README.md"), "# tool\nnothing documented\n")

    def test_exit_0_on_consistent_tree(self):
        self._consistent_tool(self._tmp)
        rc = docval.run(["--root", self._tmp, "--no-run"])
        self.assertEqual(rc, docval.EXIT_OK)

    def test_exit_1_on_inconsistent_tree(self):
        self._inconsistent_tool(self._tmp)
        rc = docval.run(["--root", self._tmp, "--no-run"])
        self.assertEqual(rc, docval.EXIT_FINDINGS)

    def test_exit_2_on_missing_root(self):
        rc = docval.run(["--root", self.p("does_not_exist_at_all")])
        self.assertEqual(rc, docval.EXIT_ERROR)

    def test_output_flag_writes_file(self):
        self._consistent_tool(self._tmp)
        out_path = self.p("report.json")
        rc = docval.run(["--root", self._tmp, "--no-run", "-o", out_path])
        self.assertEqual(rc, docval.EXIT_OK)
        self.assertTrue(os.path.isfile(out_path))

    def test_output_file_is_canonical_json(self):
        self._inconsistent_tool(self._tmp)
        out_path = self.p("report.json")
        docval.run(["--root", self._tmp, "--no-run", "-o", out_path])
        with open(out_path, "r", encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))
        parsed = json.loads(text)
        reencoded = json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
        self.assertEqual(text, reencoded)

    def test_long_output_flag_form(self):
        self._consistent_tool(self._tmp)
        out_path = self.p("report2.json")
        rc = docval.run(["--root", self._tmp, "--no-run", "--output", out_path])
        self.assertEqual(rc, docval.EXIT_OK)
        self.assertTrue(os.path.isfile(out_path))

    def test_report_has_no_absolute_paths(self):
        self._inconsistent_tool(self._tmp)
        out_path = self.p("report.json")
        docval.run(["--root", self._tmp, "--no-run", "-o", out_path])
        with open(out_path) as fh:
            text = fh.read()
        self.assertNotIn(self._tmp, text)

    def test_report_paths_are_relative_and_forward_slashed(self):
        os.makedirs(self.p("sub"))
        self._inconsistent_tool(self.p("sub"))
        out_path = self.p("report.json")
        docval.run(["--root", self._tmp, "--no-run", "-o", out_path])
        with open(out_path) as fh:
            report = json.load(fh)
        for f in report["findings"]:
            self.assertFalse(os.path.isabs(f["path"]))
            self.assertNotIn("\\", f["path"])

    def test_report_has_no_duration_or_timestamp_keys(self):
        self._consistent_tool(self._tmp)
        out_path = self.p("report.json")
        docval.run(["--root", self._tmp, "--no-run", "-o", out_path])
        with open(out_path) as fh:
            report = json.load(fh)
        blob = json.dumps(report)
        for banned in ("timestamp", "duration", "elapsed", "wall_clock", "generated_at"):
            self.assertNotIn(banned, blob)

    def test_findings_sorted_deterministically(self):
        self._inconsistent_tool(self._tmp)
        write(self.p("tool2.py"), "import argparse\np=argparse.ArgumentParser()\np.add_argument('--another')\np.parse_args()\n")
        os.makedirs(self.p("zzz"))
        self._inconsistent_tool(self.p("zzz"))
        out_path = self.p("report.json")
        docval.run(["--root", self._tmp, "--no-run", "-o", out_path])
        with open(out_path) as fh:
            report = json.load(fh)
        keys = [(f["code"], f["path"], f["detail"]) for f in report["findings"]]
        self.assertEqual(keys, sorted(keys))

    def test_no_run_reduces_finding_count_vs_full_run(self):
        d = self._tmp
        write(os.path.join(d, "tool.py"), textwrap.dedent("""
            import argparse, sys
            p = argparse.ArgumentParser()
            p.add_argument("--x")
            args = p.parse_args()
            sys.exit(0)
        """))
        write(os.path.join(d, "README.md"), (
            "# tool\n\n`--x` is the only flag.\n\n## Exit codes\n\n- `0` - ok\n- `2` - usage error\n\n"
            "```\npython3 crash_me.py\n```\n"
        ))
        write(os.path.join(d, "crash_me.py"), "raise RuntimeError('boom')\n")
        out_norun = self.p("norun.json")
        out_run = self.p("run.json")
        docval.run(["--root", d, "--no-run", "-o", out_norun])
        docval.run(["--root", d, "-o", out_run])
        with open(out_norun) as fh:
            c_norun = json.load(fh)["finding_count"]
        with open(out_run) as fh:
            c_run = json.load(fh)["finding_count"]
        self.assertLess(c_norun, c_run)

    def test_ok_field_matches_finding_count(self):
        self._consistent_tool(self._tmp)
        out_path = self.p("report.json")
        docval.run(["--root", self._tmp, "--no-run", "-o", out_path])
        with open(out_path) as fh:
            report = json.load(fh)
        self.assertEqual(report["ok"], report["finding_count"] == 0)

    def test_counts_matches_findings_tally(self):
        self._inconsistent_tool(self._tmp)
        out_path = self.p("report.json")
        docval.run(["--root", self._tmp, "--no-run", "-o", out_path])
        with open(out_path) as fh:
            report = json.load(fh)
        tally = {}
        for f in report["findings"]:
            tally[f["code"]] = tally.get(f["code"], 0) + 1
        self.assertEqual(report["counts"], tally)

    def test_default_root_is_current_directory(self):
        parser = docval.build_arg_parser()
        args = parser.parse_args([])
        self.assertEqual(args.root, ".")

    def test_no_run_flag_default_false(self):
        parser = docval.build_arg_parser()
        args = parser.parse_args([])
        self.assertFalse(args.no_run)

    def test_no_run_flag_settable(self):
        parser = docval.build_arg_parser()
        args = parser.parse_args(["--no-run"])
        self.assertTrue(args.no_run)


class TestRunCliSubprocess(TempDirCase):
    """A handful of true subprocess invocations of docval.py itself, to
    prove the __main__ entry point and real exit codes work end to end
    (not just docval.run() called in-process)."""

    def _docval_path(self):
        return os.path.join(HERE, "docval.py")

    def _run(self, args, cwd=None):
        return subprocess.run(
            [sys.executable, self._docval_path()] + args,
            cwd=cwd, capture_output=True, text=True, timeout=30,
        )

    def test_help_flag_exits_zero(self):
        proc = self._run(["--help"])
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--root", proc.stdout)

    def test_bad_flag_exits_two(self):
        proc = self._run(["--not-a-real-flag"])
        self.assertEqual(proc.returncode, 2)

    def test_missing_root_exits_two(self):
        proc = self._run(["--root", self.p("nope")])
        self.assertEqual(proc.returncode, 2)

    def test_consistent_samples_exit_zero(self):
        samples = os.path.join(HERE, "samples_consistent")
        proc = self._run(["--root", samples])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_inconsistent_samples_exit_one(self):
        samples = os.path.join(HERE, "samples_inconsistent")
        proc = self._run(["--root", samples, "--no-run"])
        self.assertEqual(proc.returncode, 1)


class TestDeterminismAndRelocation(TempDirCase):
    def _inconsistent_tool(self, d):
        write(os.path.join(d, "tool.py"), textwrap.dedent("""
            import argparse, sys
            p = argparse.ArgumentParser()
            p.add_argument("--undocumented")
            args = p.parse_args()
        """))
        write(os.path.join(d, "README.md"), "# tool\nnothing documented\n\n```\npython3 tool.py\n```\n")

    def test_two_runs_byte_identical(self):
        self._inconsistent_tool(self._tmp)
        r1 = self.p("r1.json")
        r2 = self.p("r2.json")
        docval.run(["--root", self._tmp, "-o", r1])
        docval.run(["--root", self._tmp, "-o", r2])
        with open(r1, "rb") as fh:
            b1 = fh.read()
        with open(r2, "rb") as fh:
            b2 = fh.read()
        self.assertEqual(b1, b2)

    def test_relocated_tree_same_hash(self):
        import hashlib
        self._inconsistent_tool(self.p("orig"))
        r1 = self.p("r1.json")
        docval.run(["--root", self.p("orig"), "-o", r1])

        relocated = self.p("elsewhere_entirely")
        shutil.copytree(self.p("orig"), relocated)
        r2 = self.p("r2.json")
        docval.run(["--root", relocated, "-o", r2])

        with open(r1, "rb") as fh:
            h1 = hashlib.sha256(fh.read()).hexdigest()
        with open(r2, "rb") as fh:
            h2 = hashlib.sha256(fh.read()).hexdigest()
        self.assertEqual(h1, h2)


class TestCanonicalDumpsRoundTrip(unittest.TestCase):
    def test_roundtrip_preserves_data(self):
        obj = {"findings": [{"code": "DOC001_UNDOCUMENTED_FLAG", "path": "a", "detail": "d"}], "ok": False}
        text = docval.canonical_dumps(obj)
        self.assertEqual(json.loads(text), obj)

    def test_two_calls_same_object_identical_bytes(self):
        obj = {"c": [3, 2, 1], "a": 1}
        self.assertEqual(
            docval.canonical_dumps(obj).encode("ascii"),
            docval.canonical_dumps(obj).encode("ascii"),
        )


if __name__ == "__main__":
    unittest.main()
