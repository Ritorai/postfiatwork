#!/usr/bin/env python3
"""Test suite for crosspath.py. Stdlib-only.

Every scenario builds a throwaway tree of tiny synthetic "tools" under
tempfile.TemporaryDirectory(), so the suite never depends on the sibling
tool directories existing on disk.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crosspath  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CROSSPATH_PY = os.path.join(HERE, "crosspath.py")
PY = sys.executable or "python3"

# Writes its own working directory into the report: the classic leak.
LEAKY = '''\
import json, os, sys
report = {"tool": "leaky", "cwd": os.getcwd(), "findings": 0}
out = sys.argv[sys.argv.index("-o") + 1] if "-o" in sys.argv else None
text = json.dumps(report, sort_keys=True, separators=(",", ":")) + "\\n"
open(out, "w", encoding="utf-8", newline="\\n").write(text) if out else sys.stdout.write(text)
'''

CLEAN = '''\
import json, sys
report = {"tool": "clean", "findings": 0}
out = sys.argv[sys.argv.index("-o") + 1] if "-o" in sys.argv else None
text = json.dumps(report, sort_keys=True, separators=(",", ":")) + "\\n"
open(out, "w", encoding="utf-8", newline="\\n").write(text) if out else sys.stdout.write(text)
'''

# Same JSON meaning, different byte layout depending on path length.
FORMAT_UNSTABLE = '''\
import json, os, sys
indent = len(os.getcwd()) % 3
report = {"tool": "unstable", "findings": 0}
out = sys.argv[sys.argv.index("-o") + 1] if "-o" in sys.argv else None
text = json.dumps(report, sort_keys=True, indent=indent) + "\\n"
open(out, "w", encoding="utf-8", newline="\\n").write(text) if out else sys.stdout.write(text)
'''

# Exit code depends on how long the absolute path is.
EXIT_UNSTABLE = '''\
import json, os, sys
out = sys.argv[sys.argv.index("-o") + 1] if "-o" in sys.argv else None
text = json.dumps({"tool": "exitdep"}, sort_keys=True, separators=(",", ":")) + "\\n"
open(out, "w", encoding="utf-8", newline="\\n").write(text) if out else sys.stdout.write(text)
sys.exit(0 if len(os.getcwd()) % 2 == 0 else 1)
'''

NOT_JSON = '''\
import sys
out = sys.argv[sys.argv.index("-o") + 1] if "-o" in sys.argv else None
text = "plain text report, not JSON\\n"
open(out, "w", encoding="utf-8", newline="\\n").write(text) if out else sys.stdout.write(text)
'''

CRASHER = '''\
import sys
sys.exit(3)
'''

FILE_CMD = ["python3", "tool.py", "-o", "{REPORT}"]
STDOUT_CMD = ["python3", "tool.py"]


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


class TreeMixin:
    def tmp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        return td.name

    def make_tree(self, tools, report_mode="file"):
        """tools: {name: source}. Returns (root, manifest_path)."""
        root = os.path.join(self.tmp(), "tree")
        os.makedirs(root)
        entries = {}
        for name, source in tools.items():
            write(os.path.join(root, name, "tool.py"), source)
            entries[name] = {
                "status": "baselined",
                "command": list(FILE_CMD if report_mode == "file" else STDOUT_CMD),
                "report_mode": report_mode,
                "expected_exit_code": 0,
                "expected_report_sha256": None,
            }
        manifest = os.path.join(root, "manifest.json")
        write(manifest, json.dumps({"tools": entries}, sort_keys=True, indent=2))
        return root, manifest

    def run_cli(self, args):
        proc = subprocess.run([PY, CROSSPATH_PY] + args, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=300)
        return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")

    def run_tree(self, tools, report_mode="file", extra=None):
        root, manifest = self.make_tree(tools, report_mode)
        code, out, err = self.run_cli(
            ["--root", root, "--manifest", manifest] + (extra or []))
        return code, json.loads(out) if out.strip() else None, err

    def by_tool(self, report):
        return {r["tool"]: r for r in report["results"]}


# ---------------------------------------------------------------------------
# canonical_json / hashing
# ---------------------------------------------------------------------------

class TestCanonicalJson(unittest.TestCase):
    def test_single_trailing_newline(self):
        out = crosspath.canonical_json({"a": 1})
        self.assertTrue(out.endswith("\n"))
        self.assertFalse(out.endswith("\n\n"))

    def test_sorted_keys_tight_separators(self):
        self.assertEqual(crosspath.canonical_json({"b": 1, "a": 2}), '{"a":2,"b":1}\n')

    def test_ascii_only(self):
        self.assertIn("\\u00e9", crosspath.canonical_json({"p": "café"}))

    def test_sha256_matches_hashlib(self):
        self.assertEqual(crosspath.sha256_hex(b"abc"), hashlib.sha256(b"abc").hexdigest())

    def test_sha256_accepts_str_and_bytes_identically(self):
        self.assertEqual(crosspath.sha256_hex("abc"), crosspath.sha256_hex(b"abc"))


class TestCanonicalise(unittest.TestCase):
    def test_json_is_canonicalised(self):
        text, is_json = crosspath.canonicalise(b'{"b":1,  "a":2}')
        self.assertTrue(is_json)
        self.assertEqual(text, '{"a":2,"b":1}\n')

    def test_differently_formatted_same_json_canonicalises_equal(self):
        a, _ = crosspath.canonicalise(b'{"a":1,"b":2}')
        b, _ = crosspath.canonicalise(b'{\n  "b": 2,\n  "a": 1\n}\n')
        self.assertEqual(a, b)

    def test_non_json_is_flagged(self):
        text, is_json = crosspath.canonicalise(b"not json")
        self.assertFalse(is_json)
        self.assertIsNone(text)

    def test_invalid_utf8_is_flagged_not_raised(self):
        text, is_json = crosspath.canonicalise(b"\xff\xfe")
        self.assertFalse(is_json)


# ---------------------------------------------------------------------------
# Redactor
# ---------------------------------------------------------------------------

class TestRedactor(unittest.TestCase):
    def test_replaces_both_roots(self):
        r = crosspath.Redactor("/x/aaa", "/x/bbbbbb")
        self.assertEqual(r.text("/x/aaa/t and /x/bbbbbb/t"), "<ROOT_A>/t and <ROOT_B>/t")

    def test_records_that_it_fired(self):
        r = crosspath.Redactor("/x/aaa", "/x/bbbbbb")
        self.assertFalse(r.hit)
        r.text("nothing here")
        self.assertFalse(r.hit)
        r.text("/x/aaa")
        self.assertTrue(r.hit)

    def test_longest_root_replaced_first(self):
        # A nested pair must not be half-replaced.
        r = crosspath.Redactor("/x/a", "/x/a_longer")
        self.assertEqual(r.text("/x/a_longer/t"), "<ROOT_B>/t")

    def test_non_strings_pass_through(self):
        r = crosspath.Redactor("/x/a", "/x/bb")
        self.assertEqual(r.text(7), 7)
        self.assertIsNone(r.text(None))

    def test_scan_accepts_bytes(self):
        r = crosspath.Redactor("/x/aaa", "/x/bbbbbb")
        self.assertTrue(r.scan(b"prefix /x/aaa suffix"))
        self.assertFalse(r.scan(b"nothing"))

    def test_scan_survives_invalid_utf8(self):
        r = crosspath.Redactor("/x/aaa", "/x/bbbbbb")
        self.assertFalse(r.scan(b"\xff\xfe"))


class TestClip(unittest.TestCase):
    def test_short_value_unchanged(self):
        self.assertEqual(crosspath.clip("abc"), "abc")

    def test_long_value_clipped_and_marked(self):
        out = crosspath.clip("x" * 500)
        self.assertTrue(out.endswith("...[clipped]"))
        self.assertLess(len(out), 500)

    def test_non_string_serialised(self):
        self.assertEqual(crosspath.clip({"b": 1, "a": 2}), '{"a": 2, "b": 1}')


# ---------------------------------------------------------------------------
# json_pointer_diffs
# ---------------------------------------------------------------------------

class TestJsonPointerDiffs(unittest.TestCase):
    def test_identical_gives_no_diffs(self):
        self.assertEqual(crosspath.json_pointer_diffs({"a": 1}, {"a": 1}), [])

    def test_scalar_difference(self):
        self.assertEqual(crosspath.json_pointer_diffs({"a": 1}, {"a": 2}), [("/a", 1, 2)])

    def test_nested_pointer(self):
        d = crosspath.json_pointer_diffs({"a": {"b": 1}}, {"a": {"b": 2}})
        self.assertEqual(d[0][0], "/a/b")

    def test_missing_key_on_each_side(self):
        d = dict((p, (x, y)) for p, x, y in
                 crosspath.json_pointer_diffs({"a": 1}, {"b": 2}))
        self.assertEqual(d["/a"], (1, None))
        self.assertEqual(d["/b"], (None, 2))

    def test_list_length_difference_reported_once(self):
        d = crosspath.json_pointer_diffs({"a": [1, 2]}, {"a": [1]})
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0][0], "/a")

    def test_list_element_pointer(self):
        d = crosspath.json_pointer_diffs({"a": [1, 2]}, {"a": [1, 3]})
        self.assertEqual(d[0][0], "/a/1")

    def test_type_change_reported_at_the_node(self):
        d = crosspath.json_pointer_diffs({"a": 1}, {"a": "1"})
        self.assertEqual(d[0][0], "/a")

    def test_keys_visited_in_sorted_order(self):
        d = crosspath.json_pointer_diffs({"b": 1, "a": 1}, {"b": 2, "a": 2})
        self.assertEqual([p for p, _, _ in d], ["/a", "/b"])

    def test_slash_and_tilde_in_keys_are_escaped(self):
        d = crosspath.json_pointer_diffs({"a/b": 1}, {"a/b": 2})
        self.assertEqual(d[0][0], "/a~1b")
        d = crosspath.json_pointer_diffs({"a~b": 1}, {"a~b": 2})
        self.assertEqual(d[0][0], "/a~0b")


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

class TestManifest(TreeMixin, unittest.TestCase):
    def path_with(self, obj):
        p = os.path.join(self.tmp(), "m.json")
        write(p, json.dumps(obj))
        return p

    def test_missing_file(self):
        with self.assertRaises(crosspath.SetupError):
            crosspath.load_manifest(os.path.join(self.tmp(), "nope.json"))

    def test_invalid_json(self):
        p = os.path.join(self.tmp(), "m.json")
        write(p, "{not json")
        with self.assertRaises(crosspath.SetupError):
            crosspath.load_manifest(p)

    def test_missing_tools_object(self):
        with self.assertRaises(crosspath.SetupError):
            crosspath.load_manifest(self.path_with({"nope": {}}))

    def test_entry_missing_command(self):
        with self.assertRaises(crosspath.SetupError):
            crosspath.load_manifest(self.path_with(
                {"tools": {"t": {"report_mode": "stdout"}}}))

    def test_bad_report_mode(self):
        with self.assertRaises(crosspath.SetupError):
            crosspath.load_manifest(self.path_with(
                {"tools": {"t": {"command": ["x"], "report_mode": "pipe"}}}))

    def test_command_must_be_list_of_strings(self):
        with self.assertRaises(crosspath.SetupError):
            crosspath.load_manifest(self.path_with(
                {"tools": {"t": {"command": "x", "report_mode": "stdout"}}}))

    def test_unbaselineable_entry_is_kept_as_not_runnable(self):
        tools = crosspath.load_manifest(self.path_with(
            {"tools": {"t": {"status": "unbaselineable", "reason": "no command"}}}))
        self.assertFalse(tools["t"]["runnable"])

    def test_accepts_a_regression_checker_style_entry_unchanged(self):
        tools = crosspath.load_manifest(self.path_with({"tools": {"t": {
            "status": "baselined",
            "command": ["python3", "tool.py", "-o", "{REPORT}"],
            "report_mode": "file",
            "expected_exit_code": 1,
            "expected_report_sha256": "0" * 64,
        }}}))
        self.assertTrue(tools["t"]["runnable"])
        self.assertEqual(tools["t"]["report_mode"], "file")


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

class TestIdenticalTools(TreeMixin, unittest.TestCase):
    def test_clean_tool_exits_zero(self):
        code, report, err = self.run_tree({"clean": CLEAN})
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(report["status"], "identical")

    def test_clean_tool_has_no_codes(self):
        _, report, _ = self.run_tree({"clean": CLEAN})
        self.assertEqual(self.by_tool(report)["clean"]["codes"], [])

    def test_canonical_and_raw_hashes_both_recorded(self):
        _, report, _ = self.run_tree({"clean": CLEAN})
        d = self.by_tool(report)["clean"]["detail"]
        for key in ("canonical_sha256_a", "canonical_sha256_b",
                    "raw_sha256_a", "raw_sha256_b"):
            self.assertIn(key, d)

    def test_stdout_report_mode_also_works(self):
        code, report, err = self.run_tree({"clean": CLEAN}, report_mode="stdout")
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(self.by_tool(report)["clean"]["status"], "identical")


class TestPathDependentTools(TreeMixin, unittest.TestCase):
    def test_leaky_tool_diverges(self):
        code, report, err = self.run_tree({"leaky": LEAKY})
        self.assertEqual(code, 1, msg=err)
        self.assertEqual(report["status"], "divergent")

    def test_leaky_tool_reports_both_leak_and_hash_codes(self):
        _, report, _ = self.run_tree({"leaky": LEAKY})
        self.assertEqual(self.by_tool(report)["leaky"]["codes"],
                         [crosspath.C_LEAK, crosspath.C_HASH])

    def test_leaky_tool_difference_is_pinpointed_by_pointer(self):
        _, report, _ = self.run_tree({"leaky": LEAKY})
        diffs = self.by_tool(report)["leaky"]["detail"]["differences"]
        self.assertEqual([d["pointer"] for d in diffs], ["/cwd"])

    def test_leaked_paths_are_redacted_in_the_report(self):
        _, report, _ = self.run_tree({"leaky": LEAKY})
        diff = self.by_tool(report)["leaky"]["detail"]["differences"][0]
        self.assertIn("<ROOT_A>", diff["a"])
        self.assertIn("<ROOT_B>", diff["b"])

    def test_no_absolute_temp_path_anywhere_in_the_report(self):
        _, report, _ = self.run_tree({"leaky": LEAKY, "clean": CLEAN})
        text = json.dumps(report)
        self.assertNotIn("/tmp/", text)
        self.assertNotIn(crosspath.DIR_A, text)
        self.assertNotIn(crosspath.DIR_B, text)

    def test_clean_and_leaky_together_isolate_the_culprit(self):
        _, report, _ = self.run_tree({"leaky": LEAKY, "clean": CLEAN})
        by = self.by_tool(report)
        self.assertEqual(by["clean"]["status"], "identical")
        self.assertEqual(by["leaky"]["status"], "divergent")
        self.assertEqual(report["summary"], {"identical": 1, "divergent": 1,
                                             "error": 0, "skipped": 0})

    def test_exit_code_divergence_is_its_own_code(self):
        _, report, _ = self.run_tree({"exitdep": EXIT_UNSTABLE})
        codes = self.by_tool(report)["exitdep"]["codes"]
        self.assertIn(crosspath.C_EXIT, codes)

    def test_formatting_instability_caught_even_though_meaning_matches(self):
        # canonical JSON is equal; the raw bytes are not. A byte-hash
        # baseline would call this drift; this runner names it precisely.
        _, report, _ = self.run_tree({"unstable": FORMAT_UNSTABLE})
        r = self.by_tool(report)["unstable"]
        self.assertIn(crosspath.C_NONJSON, r["codes"])
        self.assertEqual(r["detail"]["canonical_sha256_a"],
                         r["detail"]["canonical_sha256_b"])
        self.assertNotEqual(r["detail"]["raw_sha256_a"], r["detail"]["raw_sha256_b"])


class TestNonJsonAndErrors(TreeMixin, unittest.TestCase):
    def test_non_json_identical_output_is_flagged_but_not_divergent_on_hash(self):
        _, report, _ = self.run_tree({"txt": NOT_JSON})
        r = self.by_tool(report)["txt"]
        self.assertIn(crosspath.C_NONJSON, r["codes"])
        self.assertNotIn(crosspath.C_HASH, r["codes"])

    def test_tool_that_writes_no_report_is_an_execution_error(self):
        code, report, err = self.run_tree({"crash": CRASHER})
        self.assertEqual(code, 2, msg=err)
        self.assertEqual(self.by_tool(report)["crash"]["codes"], [crosspath.C_ERROR])

    def test_execution_error_dominates_exit_code(self):
        # One broken tool must not be reported as "no divergence found".
        code, report, _ = self.run_tree({"crash": CRASHER, "clean": CLEAN})
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "error")

    def test_error_detail_is_redacted(self):
        _, report, _ = self.run_tree({"crash": CRASHER})
        text = json.dumps(self.by_tool(report)["crash"]["detail"])
        self.assertNotIn("/tmp/", text)


class TestCliArguments(TreeMixin, unittest.TestCase):
    def test_missing_manifest_exits_two(self):
        code, out, _ = self.run_cli(["--root", self.tmp(),
                                     "--manifest", "/nonexistent.json"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["status"], "error")

    def test_only_selects_a_subset(self):
        root, manifest = self.make_tree({"leaky": LEAKY, "clean": CLEAN})
        code, out, _ = self.run_cli(["--root", root, "--manifest", manifest,
                                     "--only", "clean"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["tools_compared"], 1)

    def test_only_with_unknown_name_exits_two(self):
        root, manifest = self.make_tree({"clean": CLEAN})
        code, out, _ = self.run_cli(["--root", root, "--manifest", manifest,
                                     "--only", "nope"])
        self.assertEqual(code, 2)

    def test_path_a_without_path_b_exits_two(self):
        root, manifest = self.make_tree({"clean": CLEAN})
        code, _, _ = self.run_cli(["--root", root, "--manifest", manifest,
                                   "--path-a", root])
        self.assertEqual(code, 2)

    def test_identical_given_paths_are_refused(self):
        root, manifest = self.make_tree({"clean": CLEAN})
        code, out, _ = self.run_cli(["--manifest", manifest,
                                     "--path-a", root, "--path-b", root])
        self.assertEqual(code, 2)
        self.assertIn("prove nothing", json.loads(out)["error"])

    def test_equal_length_given_paths_are_refused(self):
        base = self.tmp()
        one, two = os.path.join(base, "aaaa"), os.path.join(base, "bbbb")
        for p in (one, two):
            write(os.path.join(p, "clean", "tool.py"), CLEAN)
        manifest = os.path.join(base, "m.json")
        write(manifest, json.dumps({"tools": {"clean": {
            "command": FILE_CMD, "report_mode": "file"}}}))
        code, out, _ = self.run_cli(["--manifest", manifest,
                                     "--path-a", one, "--path-b", two])
        self.assertEqual(code, 2)
        self.assertIn("same length", json.loads(out)["error"])

    def test_given_paths_mode_works_and_is_labelled(self):
        base = self.tmp()
        one, two = os.path.join(base, "short"), os.path.join(base, "a_much_longer_name")
        for p in (one, two):
            write(os.path.join(p, "leaky", "tool.py"), LEAKY)
        manifest = os.path.join(base, "m.json")
        write(manifest, json.dumps({"tools": {"leaky": {
            "command": FILE_CMD, "report_mode": "file"}}}))
        code, out, err = self.run_cli(["--manifest", manifest,
                                       "--path-a", one, "--path-b", two])
        self.assertEqual(code, 1, msg=err)
        report = json.loads(out)
        self.assertEqual(report["mode"], "given-paths")
        self.assertIn(crosspath.C_LEAK, report["results"][0]["codes"])

    def test_output_flag_writes_file_and_silences_stdout(self):
        root, manifest = self.make_tree({"clean": CLEAN})
        out_path = os.path.join(self.tmp(), "r.json")
        code, out, _ = self.run_cli(["--root", root, "--manifest", manifest,
                                     "-o", out_path])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")
        with open(out_path, "rb") as fh:
            data = fh.read()
        self.assertTrue(data.endswith(b"\n"))
        self.assertFalse(data.endswith(b"\n\n"))

    def test_unwritable_output_falls_back_to_stderr(self):
        root, manifest = self.make_tree({"clean": CLEAN})
        code, out, err = self.run_cli(["--root", root, "--manifest", manifest,
                                       "-o", "/no/such/dir/r.json"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["status"], "error")


class TestReportShape(TreeMixin, unittest.TestCase):
    def test_results_sorted_by_tool_name(self):
        _, report, _ = self.run_tree({"zeta": CLEAN, "alpha": CLEAN, "mid": CLEAN})
        names = [r["tool"] for r in report["results"]]
        self.assertEqual(names, sorted(names))

    def test_code_counts_cover_every_code(self):
        _, report, _ = self.run_tree({"clean": CLEAN})
        self.assertEqual(set(report["code_counts"]), set(crosspath.ALL_CODES))

    def test_code_counts_match_the_results(self):
        _, report, _ = self.run_tree({"leaky": LEAKY, "clean": CLEAN})
        counted = {}
        for r in report["results"]:
            for c in r["codes"]:
                counted[c] = counted.get(c, 0) + 1
        for code, n in report["code_counts"].items():
            self.assertEqual(n, counted.get(code, 0), code)

    def test_schema_version_tool_and_mode(self):
        _, report, _ = self.run_tree({"clean": CLEAN})
        self.assertEqual(report["schema_version"], crosspath.SCHEMA_VERSION)
        self.assertEqual(report["tool"], "crosspath-runner")
        self.assertEqual(report["mode"], "copied")

    def test_unbaselineable_tool_is_skipped_not_silently_dropped(self):
        root = os.path.join(self.tmp(), "tree")
        os.makedirs(root)
        write(os.path.join(root, "clean", "tool.py"), CLEAN)
        manifest = os.path.join(root, "m.json")
        write(manifest, json.dumps({"tools": {
            "clean": {"command": FILE_CMD, "report_mode": "file"},
            "ghost": {"status": "unbaselineable", "reason": "no documented command"},
        }}))
        code, out, _ = self.run_cli(["--root", root, "--manifest", manifest])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["summary"]["skipped"], 1)
        self.assertEqual(self.by_tool(report)["ghost"]["status"], "skipped")

    def test_no_timestamps_or_durations_in_the_report(self):
        _, report, _ = self.run_tree({"clean": CLEAN})
        text = json.dumps(report).lower()
        for banned in ("timestamp", "duration", "elapsed", "started_at"):
            self.assertNotIn(banned, text)

    def test_two_invocations_agree_on_a_clean_tree(self):
        root, manifest = self.make_tree({"clean": CLEAN, "other": CLEAN})
        _, a, _ = self.run_cli(["--root", root, "--manifest", manifest])
        _, b, _ = self.run_cli(["--root", root, "--manifest", manifest])
        self.assertEqual(a, b)


class TestCopyNames(unittest.TestCase):
    def test_the_two_copy_names_differ_in_length(self):
        # Equal-length names cannot expose a length-dependent artefact.
        self.assertNotEqual(len(crosspath.DIR_A), len(crosspath.DIR_B))

    def test_the_two_copy_names_differ_in_spelling(self):
        self.assertNotEqual(crosspath.DIR_A, crosspath.DIR_B)

    def test_the_length_difference_is_prime(self):
        # An artefact of the form f(len(path)) % N cancels whenever N divides
        # the length difference. A difference of 30 hid `% 2` and `% 3`
        # artefacts from two of the tests below until this was fixed. A prime
        # difference exposes every period from 2 up to itself.
        diff = abs(len(crosspath.DIR_B) - len(crosspath.DIR_A))
        self.assertGreater(diff, 2)
        self.assertEqual([n for n in range(2, diff) if diff % n == 0], [],
                         "length difference %d is not prime" % diff)

    def test_the_length_difference_is_large_enough_to_matter(self):
        diff = abs(len(crosspath.DIR_B) - len(crosspath.DIR_A))
        self.assertGreaterEqual(diff, 17)


class TestStdlibOnlyImports(unittest.TestCase):
    ALLOWED = {"argparse", "hashlib", "json", "os", "shutil", "subprocess", "sys", "tempfile"}

    def test_only_allow_listed_imports(self):
        import ast
        with open(CROSSPATH_PY, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module.split(".")[0])
        self.assertEqual(found - self.ALLOWED, set())


if __name__ == "__main__":
    unittest.main()
