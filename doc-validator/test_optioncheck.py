#!/usr/bin/env python3
"""Tests for optioncheck.py.

Three layers:

1. **Grammar** -- `spec_from_call` against hand-written `add_argument`
   snippets whose correct answer is decidable by reading argparse's rules.
2. **Fixture repository** -- a small tree with a deliberate match, a
   deliberate conflict, aliases and a dynamic definition, so every state
   the report can emit is exercised without depending on what the sibling
   tools happen to look like today.
3. **The real tree** -- the committed report must byte-match a live rescan,
   and the scan must be relocation-invariant.

Run:
    python3 -m unittest test_optioncheck
"""
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

import optioncheck as oc  # noqa: E402

REPORT = os.path.join(THIS_DIR, "option_report.json")


def parse_call(src):
    """Parse a single `p.add_argument(...)` expression into a spec."""
    tree = ast.parse(src)
    call = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "add_argument":
            call = node
            break
    assert call is not None, src
    return oc.spec_from_call(call, {})


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


CLI_TEMPLATE = """import argparse


def build():
    p = argparse.ArgumentParser()
%s
    return p


def main():
    build().parse_args()


if __name__ == "__main__":
    main()
"""


def make_tool(root, name, lines):
    body = "\n".join("    " + l for l in lines)
    write(os.path.join(root, name, "%s.py" % name.replace("-", "_")),
          CLI_TEMPLATE % body)
    write(os.path.join(root, name, "README.md"), "# %s\n" % name)


class TestSpecGrammar(unittest.TestCase):
    def test_default_action_is_store(self):
        s = parse_call('p.add_argument("--root")')
        self.assertEqual(s["action"], "store")
        self.assertTrue(s["takes_value"])

    def test_store_true_takes_no_value(self):
        s = parse_call('p.add_argument("--all", action="store_true")')
        self.assertEqual(s["action"], "store_true")
        self.assertFalse(s["takes_value"])

    def test_count_takes_no_value(self):
        s = parse_call('p.add_argument("-v", action="count")')
        self.assertFalse(s["takes_value"])

    def test_append_takes_a_value(self):
        s = parse_call('p.add_argument("--rule", action="append")')
        self.assertTrue(s["takes_value"])

    def test_nargs_zero_takes_no_value(self):
        s = parse_call('p.add_argument("--x", nargs=0)')
        self.assertFalse(s["takes_value"])

    def test_type_name_is_recorded(self):
        self.assertEqual(parse_call('p.add_argument("--n", type=int)')["type"],
                         "int")

    def test_dotted_type_name_is_recorded(self):
        s = parse_call('p.add_argument("--p", type=pathlib.Path)')
        self.assertEqual(s["type"], "pathlib.Path")

    def test_absent_type_is_null(self):
        self.assertIsNone(parse_call('p.add_argument("--x")')["type"])

    def test_choices_are_sorted(self):
        s = parse_call('p.add_argument("--m", choices=["z", "a", "m"])')
        self.assertEqual(s["choices"], ["a", "m", "z"])

    def test_choices_tuple_is_accepted(self):
        s = parse_call('p.add_argument("--m", choices=("b", "a"))')
        self.assertEqual(s["choices"], ["a", "b"])

    def test_options_are_sorted_and_include_short_flags(self):
        s = parse_call('p.add_argument("-o", "--output")')
        self.assertEqual(s["options"], ["--output", "-o"])

    def test_positional_is_captured_separately(self):
        s = parse_call('p.add_argument("tasks_file")')
        self.assertEqual(s["options"], [])
        self.assertEqual(s["positional"], "tasks_file")


class TestDynamicIsRefused(unittest.TestCase):
    def test_non_literal_choices(self):
        s = parse_call('p.add_argument("--m", choices=VALID)')
        self.assertIn("non-literal choices=", s["dynamic"])

    def test_non_literal_type(self):
        s = parse_call('p.add_argument("--n", type=lambda v: int(v))')
        self.assertIn("non-literal type=", s["dynamic"])

    def test_non_literal_action(self):
        s = parse_call('p.add_argument("--x", action=SOME_ACTION)')
        self.assertIn("non-literal action=", s["dynamic"])

    def test_non_literal_option_string(self):
        s = parse_call('p.add_argument(*FLAGS)')
        self.assertIn("non-literal option string", s["dynamic"])

    def test_kwargs_expansion(self):
        s = parse_call('p.add_argument("--x", **opts)')
        self.assertIn("**kwargs expansion", s["dynamic"])

    def test_resolvable_call_has_no_reasons(self):
        s = parse_call('p.add_argument("--x", type=int, choices=[1, 2])')
        self.assertEqual(s["dynamic"], [])

    def test_action_resolved_through_a_module_constant(self):
        tree = ast.parse('p.add_argument("--x", action=A)')
        call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
        s = oc.spec_from_call(call, {"A": "store_true"})
        self.assertEqual(s["action"], "store_true")
        self.assertFalse(s["takes_value"])
        self.assertEqual(s["dynamic"], [])


class TestLongOptionSelection(unittest.TestCase):
    def test_help_is_excluded(self):
        s = parse_call('p.add_argument("-h", "--help", action="help")')
        self.assertEqual(oc.long_options(s), [])

    def test_short_flags_are_not_grouping_keys(self):
        s = parse_call('p.add_argument("-o", "--output")')
        self.assertEqual(oc.long_options(s), ["--output"])

    def test_two_long_options_both_count(self):
        s = parse_call('p.add_argument("--out", "--output")')
        self.assertEqual(oc.long_options(s), ["--out", "--output"])


class FixtureRepo:
    """A tree with one match, one conflict, aliases and a dynamic option."""

    def __init__(self, base, conflict=True):
        self.root = os.path.join(base, "fixture-repo")
        os.makedirs(self.root)
        make_tool(self.root, "alpha", [
            'p.add_argument("--root", default="..")',
            'p.add_argument("-o", "--output")',
            'p.add_argument("--timeout", type=float)',
            'p.add_argument("--only-alpha", action="store_true")',
        ])
        make_tool(self.root, "beta", [
            'p.add_argument("--root", default=".")',
            'p.add_argument("-o", "--output")',
            'p.add_argument("--timeout", type=%s)' % ("int" if conflict
                                                      else "float"),
            'p.add_argument("--mode", choices=MODES)',
        ])

    def report(self, only_option=None):
        return oc.build_report(self.root, only_option)


class TestFixtureReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="optioncheck_fx_")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.fx = FixtureRepo(self.tmp)
        self.r = self.fx.report()

    def by_option(self, opt):
        return next(o for o in self.r["options"] if o["option"] == opt)

    def test_identical_options_are_a_match(self):
        self.assertEqual(self.by_option("--root")["state"], "match")
        self.assertEqual(self.by_option("--output")["state"], "match")

    def test_differing_type_is_a_conflict(self):
        o = self.by_option("--timeout")
        self.assertEqual(o["state"], "conflict")
        self.assertEqual(o["differing_dimensions"], ["type"])
        self.assertEqual(len(o["variants"]), 2)

    def test_conflict_names_both_tools(self):
        o = self.by_option("--timeout")
        self.assertEqual(o["tools"], ["alpha", "beta"])

    def test_option_used_by_one_tool_is_single_use(self):
        self.assertEqual(self.by_option("--only-alpha")["state"], "single_use")

    def test_aliases_are_reported(self):
        o = self.by_option("--output")
        self.assertEqual(o["variants"][0]["sites"][0]["aliases"], ["-o"])

    def test_dynamic_option_is_listed_and_not_compared(self):
        self.assertEqual(len(self.r["unsupported_dynamic"]), 1)
        self.assertEqual(self.r["unsupported_dynamic"][0]["options"], ["--mode"])
        self.assertNotIn("--mode", [o["option"] for o in self.r["options"]])

    def test_conflicts_list_matches_the_conflict_state(self):
        self.assertEqual([c["option"] for c in self.r["conflicts"]],
                         [o["option"] for o in self.r["options"]
                          if o["state"] == "conflict"])

    def test_counts_sum_to_total_options(self):
        self.assertEqual(sum(self.r["counts"].values()),
                         self.r["totals"]["options"])

    def test_no_conflict_variant_when_shapes_agree(self):
        tmp = tempfile.mkdtemp(prefix="optioncheck_ok_")
        self.addCleanup(shutil.rmtree, tmp)
        r = FixtureRepo(tmp, conflict=False).report()
        self.assertEqual(r["counts"]["conflict"], 0)
        self.assertEqual(
            next(o for o in r["options"] if o["option"] == "--timeout")["state"],
            "match")

    def test_option_filter_restricts_the_report(self):
        r = self.fx.report(only_option="--timeout")
        self.assertEqual([o["option"] for o in r["options"]], ["--timeout"])
        self.assertEqual(r["option_filter"], "--timeout")


class TestStableOrdering(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="optioncheck_sort_")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.fx = FixtureRepo(self.tmp)

    def test_options_are_sorted(self):
        names = [o["option"] for o in self.fx.report()["options"]]
        self.assertEqual(names, sorted(names))

    def test_tools_within_an_option_are_sorted(self):
        for o in self.fx.report()["options"]:
            with self.subTest(option=o["option"]):
                self.assertEqual(o["tools"], sorted(o["tools"]))

    def test_dynamic_entries_are_sorted_by_file_then_line(self):
        d = self.fx.report()["unsupported_dynamic"]
        keys = [(e["file"], e["line"]) for e in d]
        self.assertEqual(keys, sorted(keys))

    def test_two_runs_are_byte_identical(self):
        import docval
        a = docval.canonical_dumps(self.fx.report())
        b = docval.canonical_dumps(self.fx.report())
        self.assertEqual(a, b)


class TestRelocation(unittest.TestCase):
    #: Planted in the destination path so a location leak is unmistakable.
    MARKER = "kpr-optioncheck-marker-9c2d"

    def test_fixture_report_is_identical_from_a_renamed_path(self):
        import docval
        tmp = tempfile.mkdtemp(prefix="optioncheck_rel_")
        self.addCleanup(shutil.rmtree, tmp)
        fx = FixtureRepo(tmp)
        here = docval.canonical_dumps(fx.report())
        dest_parent = os.path.join(tmp, self.MARKER)
        os.makedirs(dest_parent)
        dest = os.path.join(dest_parent, "renamed-copy")
        shutil.copytree(fx.root, dest)
        there = docval.canonical_dumps(oc.build_report(dest))
        self.assertEqual(here, there)
        self.assertNotIn(self.MARKER, there)

    def test_real_tree_report_is_identical_from_a_renamed_path(self):
        import docval
        tmp = tempfile.mkdtemp(prefix="optioncheck_rel2_")
        try:
            dest = os.path.join(tmp, self.MARKER)
            shutil.copytree(REPO_ROOT, dest,
                            ignore=shutil.ignore_patterns(".git", "__pycache__"))
            there = docval.canonical_dumps(oc.build_report(dest))
        finally:
            # Only ever remove the directory this test created itself.
            shutil.rmtree(tmp)
        here = docval.canonical_dumps(oc.build_report(REPO_ROOT))
        self.assertEqual(here, there)
        self.assertNotIn(self.MARKER, there)

    def test_marker_occurs_nowhere_else_in_the_repository(self):
        """Negative control: the assertions above are not vacuous."""
        hits = []
        for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
            dirnames[:] = [d for d in dirnames
                           if d not in (".git", "__pycache__")]
            for fn in filenames:
                p = os.path.join(dirpath, fn)
                try:
                    with open(p, encoding="utf-8", errors="ignore") as fh:
                        if self.MARKER in fh.read():
                            hits.append(os.path.relpath(p, REPO_ROOT))
                except OSError:
                    continue
        self.assertEqual(hits, [os.path.join("doc-validator",
                                             "test_optioncheck.py")])


class TestCli(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, os.path.join(THIS_DIR, "optioncheck.py")]
            + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=THIS_DIR)

    def test_bad_root_exits_2(self):
        p = self.run_cli("--root", os.path.join(THIS_DIR, "no_such_dir_xyz"))
        self.assertEqual(p.returncode, 2)
        self.assertIn(b"not a directory", p.stderr)

    def test_conflicting_fixture_exits_1(self):
        tmp = tempfile.mkdtemp(prefix="optioncheck_cli_")
        self.addCleanup(shutil.rmtree, tmp)
        fx = FixtureRepo(tmp)
        self.assertEqual(self.run_cli("--root", fx.root).returncode, 1)

    def test_clean_fixture_exits_0(self):
        tmp = tempfile.mkdtemp(prefix="optioncheck_cli_ok_")
        self.addCleanup(shutil.rmtree, tmp)
        fx = FixtureRepo(tmp, conflict=False)
        self.assertEqual(self.run_cli("--root", fx.root).returncode, 0)

    def test_output_file_matches_stdout(self):
        tmp = tempfile.mkdtemp(prefix="optioncheck_cli_o_")
        self.addCleanup(shutil.rmtree, tmp)
        fx = FixtureRepo(tmp)
        out = os.path.join(tmp, "r.json")
        self.run_cli("--root", fx.root, "-o", out)
        with open(out, encoding="utf-8") as fh:
            written = fh.read()
        self.assertEqual(written,
                         self.run_cli("--root", fx.root).stdout.decode())

    def test_output_is_canonical_json(self):
        p = self.run_cli("--root", REPO_ROOT)
        text = p.stdout.decode()
        import docval
        self.assertEqual(text, docval.canonical_dumps(json.loads(text)))


class TestCommittedReport(unittest.TestCase):
    def test_committed_report_exists(self):
        self.assertTrue(os.path.isfile(REPORT))

    def test_committed_report_matches_a_live_rescan(self):
        import docval
        with open(REPORT, encoding="utf-8") as fh:
            committed = fh.read()
        self.assertEqual(committed,
                         docval.canonical_dumps(oc.build_report(REPO_ROOT)))

    def test_the_repository_still_has_the_documented_conflict(self):
        """The README states a specific finding; if the tree is fixed or
        changes shape, this fails rather than letting the prose go stale."""
        with open(REPORT, encoding="utf-8") as fh:
            r = json.load(fh)
        opts = [c["option"] for c in r["conflicts"]]
        self.assertIn("--timeout", opts)
        t = next(c for c in r["conflicts"] if c["option"] == "--timeout")
        self.assertEqual(t["differing_dimensions"], ["type"])
        self.assertEqual(sorted(v["type"] for v in t["variants"]),
                         ["float", "int"])

    def test_report_contains_no_absolute_paths(self):
        with open(REPORT, encoding="utf-8") as fh:
            r = json.load(fh)
        for u in r["options"]:
            for v in u["variants"]:
                for s in v["sites"]:
                    with self.subTest(file=s["file"]):
                        self.assertFalse(os.path.isabs(s["file"]))


if __name__ == "__main__":
    unittest.main()
