#!/usr/bin/env python3
"""Test suite for coverage_audit.py. Stdlib-only (unittest, tempfile,
subprocess, json, os, sys, hashlib, stat). Every scenario below is built
from small synthetic fixtures created on the fly under a per-test
TemporaryDirectory -- nothing here depends on, or mutates, the real
regression-checker/baselines.json or any sibling tool directory.

Naming convention: TestXxx classes group by the property under test, not
by which internal function happens to implement it, so a reviewer can
find "every state" or "the bool-coercion fix" or "the stale-flip positive
control" by class name alone.
"""

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coverage_audit  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
COVERAGE_AUDIT_PY = os.path.join(HERE, "coverage_audit.py")
REPO_ROOT = os.path.dirname(HERE)
REAL_BASELINES = os.path.join(HERE, "baselines.json")
PY = sys.executable or "python3"


# --------------------------------------------------------------------------
# Fixture helpers
# --------------------------------------------------------------------------

TOOL_TEMPLATE = """#!/usr/bin/env python3
import argparse, json, sys, time
ap = argparse.ArgumentParser()
ap.add_argument("-o", "--output")
args = ap.parse_args()
{sleep}
report = {report!r}
text = json.dumps(report, sort_keys=True, separators=(",", ":")) + "\\n"
if args.output:
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(text)
else:
    sys.stdout.write(text)
sys.exit({exit_code})
"""

NO_WRITE_TEMPLATE = """#!/usr/bin/env python3
# Deliberately never writes -o, to exercise report_mode=file + no report.
import sys
sys.exit({exit_code})
"""


def write(path, content):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def make_script(tool_dir, filename, report, exit_code, sleep_seconds=None):
    os.makedirs(tool_dir, exist_ok=True)
    sleep = "time.sleep(%r)" % sleep_seconds if sleep_seconds else ""
    write(
        os.path.join(tool_dir, filename),
        TOOL_TEMPLATE.format(report=report, exit_code=exit_code, sleep=sleep),
    )


def make_no_write_script(tool_dir, filename, exit_code):
    os.makedirs(tool_dir, exist_ok=True)
    write(os.path.join(tool_dir, filename), NO_WRITE_TEMPLATE.format(exit_code=exit_code))


def report_hash_for(report_obj):
    text = json.dumps(report_obj, sort_keys=True, separators=(",", ":")) + "\n"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def baseline_entry(script, report_obj, exit_code, report_mode="file", expected_hash=None):
    return {
        "command": [PY, script, "-o", "{REPORT}"] if report_mode == "file" else [PY, script],
        "expected_exit_code": exit_code,
        "expected_report_sha256": (
            expected_hash if expected_hash is not None else report_hash_for(report_obj)
        ),
        "report_mode": report_mode,
        "status": "baselined",
    }


def write_baselines(path, tools):
    write(path, json.dumps({"tools": tools}, indent=2, sort_keys=True) + "\n")


class TempRepoTestCase(unittest.TestCase):
    """A fresh, empty tool-root + baselines.json per test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="coverage_audit_test_")
        self.root = os.path.join(self.tmp.name, "root")
        os.makedirs(self.root, exist_ok=True)
        self.baselines_path = os.path.join(self.tmp.name, "baselines.json")

    def tearDown(self):
        self.tmp.cleanup()

    def tool_dir(self, name):
        return os.path.join(self.root, name)


# ==========================================================================
# is_exit_code / bool-coercion rejection
# ==========================================================================

class TestIsExitCode(unittest.TestCase):
    def test_plain_int_zero_is_exit_code(self):
        self.assertTrue(coverage_audit.is_exit_code(0))

    def test_plain_int_positive_is_exit_code(self):
        self.assertTrue(coverage_audit.is_exit_code(1))

    def test_plain_int_negative_is_exit_code(self):
        self.assertTrue(coverage_audit.is_exit_code(-9))

    def test_bool_true_is_not_exit_code(self):
        self.assertFalse(coverage_audit.is_exit_code(True))

    def test_bool_false_is_not_exit_code(self):
        self.assertFalse(coverage_audit.is_exit_code(False))

    def test_float_is_not_exit_code(self):
        self.assertFalse(coverage_audit.is_exit_code(0.0))

    def test_string_is_not_exit_code(self):
        self.assertFalse(coverage_audit.is_exit_code("0"))

    def test_none_is_not_exit_code(self):
        self.assertFalse(coverage_audit.is_exit_code(None))

    def test_python_native_bool_int_equality_sanity(self):
        # Documents exactly why the naive isinstance(x, int) check is
        # dangerous: it is the premise the fix defends against.
        self.assertTrue(isinstance(False, int))
        self.assertEqual(False, 0)


# ==========================================================================
# load_baselines: structural validation
# ==========================================================================

class TestLoadBaselinesValid(TempRepoTestCase):
    def test_single_valid_entry_loads(self):
        write_baselines(self.baselines_path, {"foo": baseline_entry("foo.py", {"ok": True}, 0)})
        loaded = coverage_audit.load_baselines(self.baselines_path)
        self.assertIn("foo", loaded)
        self.assertEqual(loaded["foo"]["expected_exit_code"], 0)

    def test_null_expected_hash_accepted(self):
        entry = baseline_entry("foo.py", {"ok": True}, 0)
        entry["expected_report_sha256"] = None
        write_baselines(self.baselines_path, {"foo": entry})
        loaded = coverage_audit.load_baselines(self.baselines_path)
        self.assertIsNone(loaded["foo"]["expected_report_sha256"])

    def test_multiple_entries_all_loaded(self):
        tools = {
            "a": baseline_entry("a.py", {"x": 1}, 0),
            "b": baseline_entry("b.py", {"x": 2}, 1),
        }
        write_baselines(self.baselines_path, tools)
        loaded = coverage_audit.load_baselines(self.baselines_path)
        self.assertEqual(set(loaded.keys()), {"a", "b"})

    def test_returns_plain_dict_not_original_object_identity(self):
        entry = baseline_entry("a.py", {"x": 1}, 0)
        write_baselines(self.baselines_path, {"a": entry})
        loaded = coverage_audit.load_baselines(self.baselines_path)
        loaded["a"]["command"].append("mutated")
        loaded2 = coverage_audit.load_baselines(self.baselines_path)
        self.assertNotIn("mutated", loaded2["a"]["command"])


class TestLoadBaselinesMissingOrUnreadable(TempRepoTestCase):
    def test_missing_file_raises_setup_error(self):
        missing = os.path.join(self.tmp.name, "does_not_exist.json")
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.load_baselines(missing)

    def test_missing_file_message_names_path(self):
        missing = os.path.join(self.tmp.name, "does_not_exist.json")
        try:
            coverage_audit.load_baselines(missing)
            self.fail("expected SetupError")
        except coverage_audit.SetupError as exc:
            self.assertIn("does_not_exist.json", str(exc))

    def test_directory_as_baselines_path_raises(self):
        d = os.path.join(self.tmp.name, "a_directory")
        os.makedirs(d)
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.load_baselines(d)

    def test_unreadable_file_raises_setup_error(self):
        write(self.baselines_path, "{}")
        os.chmod(self.baselines_path, 0o000)
        try:
            if os.geteuid() == 0:
                self.skipTest("running as root: chmod 000 does not block reads")
        except AttributeError:
            pass
        try:
            with self.assertRaises(coverage_audit.SetupError):
                coverage_audit.load_baselines(self.baselines_path)
        finally:
            os.chmod(self.baselines_path, 0o644)


class TestLoadBaselinesMalformedJSON(TempRepoTestCase):
    def test_invalid_json_raises(self):
        write(self.baselines_path, "{not valid json")
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.load_baselines(self.baselines_path)

    def test_empty_file_raises(self):
        write(self.baselines_path, "")
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.load_baselines(self.baselines_path)

    def test_top_level_list_instead_of_object_raises(self):
        write(self.baselines_path, "[]")
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.load_baselines(self.baselines_path)

    def test_top_level_object_without_tools_key_raises(self):
        write(self.baselines_path, json.dumps({"nope": {}}))
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.load_baselines(self.baselines_path)

    def test_tools_value_not_object_raises(self):
        write(self.baselines_path, json.dumps({"tools": []}))
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.load_baselines(self.baselines_path)

    def test_entry_not_object_raises(self):
        write(self.baselines_path, json.dumps({"tools": {"foo": "not an object"}}))
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.load_baselines(self.baselines_path)


class TestLoadBaselinesEntryValidation(TempRepoTestCase):
    def _write(self, entry):
        write_baselines(self.baselines_path, {"foo": entry})

    def test_missing_status_raises(self):
        entry = baseline_entry("foo.py", {"x": 1}, 0)
        del entry["status"]
        self._write(entry)
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.load_baselines(self.baselines_path)

    def test_status_unbaselineable_raises(self):
        # coverage_audit.py deliberately only supports status=="baselined".
        entry = baseline_entry("foo.py", {"x": 1}, 0)
        entry["status"] = "unbaselineable"
        self._write(entry)
        with self.assertRaises(coverage_audit.SetupError) as ctx:
            coverage_audit.load_baselines(self.baselines_path)
        self.assertIn("unbaselineable", str(ctx.exception))

    def test_status_unknown_string_raises(self):
        entry = baseline_entry("foo.py", {"x": 1}, 0)
        entry["status"] = "totally-made-up"
        self._write(entry)
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.load_baselines(self.baselines_path)

    def test_missing_command_raises(self):
        entry = baseline_entry("foo.py", {"x": 1}, 0)
        del entry["command"]
        self._write(entry)
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.load_baselines(self.baselines_path)

    def test_missing_report_mode_raises(self):
        entry = baseline_entry("foo.py", {"x": 1}, 0)
        del entry["report_mode"]
        self._write(entry)
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.load_baselines(self.baselines_path)

    def test_missing_expected_exit_code_raises(self):
        entry = baseline_entry("foo.py", {"x": 1}, 0)
        del entry["expected_exit_code"]
        self._write(entry)
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.load_baselines(self.baselines_path)

    def test_missing_expected_report_sha256_key_raises(self):
        entry = baseline_entry("foo.py", {"x": 1}, 0)
        del entry["expected_report_sha256"]
        self._write(entry)
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.load_baselines(self.baselines_path)

    def test_command_not_list_raises(self):
        entry = baseline_entry("foo.py", {"x": 1}, 0)
        entry["command"] = "python3 foo.py"
        self._write(entry)
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.load_baselines(self.baselines_path)

    def test_command_empty_list_raises(self):
        entry = baseline_entry("foo.py", {"x": 1}, 0)
        entry["command"] = []
        self._write(entry)
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.load_baselines(self.baselines_path)

    def test_command_with_non_string_token_raises(self):
        entry = baseline_entry("foo.py", {"x": 1}, 0)
        entry["command"] = [PY, "foo.py", 5]
        self._write(entry)
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.load_baselines(self.baselines_path)

    def test_invalid_report_mode_raises(self):
        entry = baseline_entry("foo.py", {"x": 1}, 0)
        entry["report_mode"] = "network"
        self._write(entry)
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.load_baselines(self.baselines_path)

    def test_expected_exit_code_bool_true_raises(self):
        entry = baseline_entry("foo.py", {"x": 1}, 0)
        entry["expected_exit_code"] = True
        self._write(entry)
        with self.assertRaises(coverage_audit.SetupError) as ctx:
            coverage_audit.load_baselines(self.baselines_path)
        self.assertIn("boolean", str(ctx.exception))

    def test_expected_exit_code_bool_false_raises_not_coerced(self):
        # THE bool-coercion fix: a naive isinstance(x, int) check would
        # accept `false` and later compare it `== 0`. It must be rejected
        # at load time instead, with SetupError (never silently -> 0).
        entry = baseline_entry("foo.py", {"x": 1}, 0)
        entry["expected_exit_code"] = False
        self._write(entry)
        with self.assertRaises(coverage_audit.SetupError) as ctx:
            coverage_audit.load_baselines(self.baselines_path)
        msg = str(ctx.exception)
        self.assertIn("boolean", msg)
        self.assertIn("false", msg)

    def test_expected_exit_code_string_raises(self):
        entry = baseline_entry("foo.py", {"x": 1}, 0)
        entry["expected_exit_code"] = "0"
        self._write(entry)
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.load_baselines(self.baselines_path)

    def test_expected_exit_code_float_raises(self):
        entry = baseline_entry("foo.py", {"x": 1}, 0)
        entry["expected_exit_code"] = 0.0
        self._write(entry)
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.load_baselines(self.baselines_path)

    def test_expected_hash_wrong_length_raises(self):
        entry = baseline_entry("foo.py", {"x": 1}, 0)
        entry["expected_report_sha256"] = "abc123"
        self._write(entry)
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.load_baselines(self.baselines_path)

    def test_expected_hash_non_hex_raises(self):
        entry = baseline_entry("foo.py", {"x": 1}, 0)
        entry["expected_report_sha256"] = "z" * 64
        self._write(entry)
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.load_baselines(self.baselines_path)

    def test_expected_hash_non_string_raises(self):
        entry = baseline_entry("foo.py", {"x": 1}, 0)
        entry["expected_report_sha256"] = 12345
        self._write(entry)
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.load_baselines(self.baselines_path)

    def test_valid_uppercase_hex_hash_accepted(self):
        entry = baseline_entry("foo.py", {"x": 1}, 0)
        entry["expected_report_sha256"] = entry["expected_report_sha256"].upper()
        self._write(entry)
        loaded = coverage_audit.load_baselines(self.baselines_path)
        self.assertEqual(len(loaded["foo"]["expected_report_sha256"]), 64)


# ==========================================================================
# discover_tool_dirs
# ==========================================================================

class TestDiscoverToolDirs(TempRepoTestCase):
    def test_empty_root_returns_empty_list(self):
        self.assertEqual(coverage_audit.discover_tool_dirs(self.root), [])

    def test_finds_subdirectories(self):
        os.makedirs(os.path.join(self.root, "zeta"))
        os.makedirs(os.path.join(self.root, "alpha"))
        self.assertEqual(coverage_audit.discover_tool_dirs(self.root), ["alpha", "zeta"])

    def test_result_is_sorted(self):
        for name in ["b", "a", "c"]:
            os.makedirs(os.path.join(self.root, name))
        self.assertEqual(coverage_audit.discover_tool_dirs(self.root), ["a", "b", "c"])

    def test_skips_dotdirs(self):
        os.makedirs(os.path.join(self.root, ".git"))
        os.makedirs(os.path.join(self.root, "real"))
        self.assertEqual(coverage_audit.discover_tool_dirs(self.root), ["real"])

    def test_skips_pycache(self):
        os.makedirs(os.path.join(self.root, "__pycache__"))
        os.makedirs(os.path.join(self.root, "real"))
        self.assertEqual(coverage_audit.discover_tool_dirs(self.root), ["real"])

    def test_skips_plain_files(self):
        write(os.path.join(self.root, "README.md"), "hi")
        os.makedirs(os.path.join(self.root, "real"))
        self.assertEqual(coverage_audit.discover_tool_dirs(self.root), ["real"])

    def test_nonexistent_root_raises(self):
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.discover_tool_dirs(os.path.join(self.root, "nope"))

    def test_file_as_root_raises(self):
        f = os.path.join(self.tmp.name, "a_file")
        write(f, "x")
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.discover_tool_dirs(f)


# ==========================================================================
# find_script_token
# ==========================================================================

class TestFindScriptToken(unittest.TestCase):
    def test_python3_interpreter(self):
        self.assertEqual(coverage_audit.find_script_token(["python3", "foo.py"]), "foo.py")

    def test_python_interpreter(self):
        self.assertEqual(coverage_audit.find_script_token(["python", "foo.py"]), "foo.py")

    def test_versioned_python_interpreter(self):
        self.assertEqual(coverage_audit.find_script_token(["python3.11", "foo.py"]), "foo.py")

    def test_versioned_python_interpreter_single_digit(self):
        self.assertEqual(coverage_audit.find_script_token(["python3.9", "bar.py", "-x"]), "bar.py")

    def test_extra_args_ignored(self):
        self.assertEqual(
            coverage_audit.find_script_token(["python3", "foo.py", "-o", "{REPORT}"]), "foo.py"
        )

    def test_non_interpreter_falls_back_to_py_scan(self):
        self.assertEqual(coverage_audit.find_script_token(["bash", "run.sh", "helper.py"]), "helper.py")

    def test_no_py_token_returns_none(self):
        self.assertIsNone(coverage_audit.find_script_token(["ls", "-la"]))

    def test_empty_command_returns_none(self):
        self.assertIsNone(coverage_audit.find_script_token([]))

    def test_interpreter_alone_no_script_returns_none(self):
        self.assertIsNone(coverage_audit.find_script_token(["python3"]))


# ==========================================================================
# run_command
# ==========================================================================

class TestRunCommand(TempRepoTestCase):
    def test_file_mode_success(self):
        d = self.tool_dir("t")
        make_script(d, "t.py", {"ok": True}, 0)
        result = coverage_audit.run_command(d, [PY, "t.py", "-o", "{REPORT}"], "file", 30)
        self.assertTrue(result["ok"])
        self.assertEqual(result["actual_exit_code"], 0)
        self.assertTrue(result["report_created"])
        self.assertEqual(result["actual_report_sha256"], report_hash_for({"ok": True}))

    def test_file_mode_nonzero_exit(self):
        d = self.tool_dir("t")
        make_script(d, "t.py", {"ok": False}, 1)
        result = coverage_audit.run_command(d, [PY, "t.py", "-o", "{REPORT}"], "file", 30)
        self.assertEqual(result["actual_exit_code"], 1)

    def test_file_mode_report_not_created(self):
        d = self.tool_dir("t")
        make_no_write_script(d, "t.py", 0)
        result = coverage_audit.run_command(d, [PY, "t.py", "-o", "{REPORT}"], "file", 30)
        self.assertTrue(result["ok"])
        self.assertFalse(result["report_created"])
        self.assertIsNone(result["actual_report_sha256"])

    def test_stdout_mode_success(self):
        d = self.tool_dir("t")
        make_script(d, "t.py", {"stdout": True}, 0)
        result = coverage_audit.run_command(d, [PY, "t.py"], "stdout", 30)
        self.assertTrue(result["ok"])
        self.assertTrue(result["report_created"])
        self.assertEqual(result["actual_report_sha256"], report_hash_for({"stdout": True}))

    def test_nonexistent_interpreter_is_unrunnable(self):
        d = self.tool_dir("t")
        make_script(d, "t.py", {"ok": True}, 0)
        result = coverage_audit.run_command(
            d, ["definitely-not-a-real-interpreter-xyz", "t.py"], "stdout", 5
        )
        self.assertFalse(result["ok"])
        self.assertIsNotNone(result["error"])

    def test_timeout_is_unrunnable(self):
        d = self.tool_dir("t")
        make_script(d, "t.py", {"ok": True}, 0, sleep_seconds=5)
        result = coverage_audit.run_command(d, [PY, "t.py"], "stdout", 1)
        self.assertFalse(result["ok"])
        self.assertIn("timeout", result["error"])

    def test_temp_report_dir_cleaned_up(self):
        d = self.tool_dir("t")
        make_script(d, "t.py", {"ok": True}, 0)
        before = set(os.listdir(tempfile.gettempdir()))
        coverage_audit.run_command(d, [PY, "t.py", "-o", "{REPORT}"], "file", 30)
        after = set(os.listdir(tempfile.gettempdir()))
        leaked = [n for n in (after - before) if n.startswith("coverage_audit_report_")]
        self.assertEqual(leaked, [])

    def test_report_bytes_length_recorded(self):
        d = self.tool_dir("t")
        make_script(d, "t.py", {"k": "v"}, 0)
        result = coverage_audit.run_command(d, [PY, "t.py", "-o", "{REPORT}"], "file", 30)
        expected_len = len(json.dumps({"k": "v"}, sort_keys=True, separators=(",", ":")) + "\n")
        self.assertEqual(result["report_bytes_length"], expected_len)


# ==========================================================================
# classify(): one test class per state
# ==========================================================================

class TestClassifyReproducing(TempRepoTestCase):
    def test_reproducing_when_exit_and_hash_match(self):
        d = self.tool_dir("t")
        make_script(d, "t.py", {"ok": True}, 0)
        entry = baseline_entry("t.py", {"ok": True}, 0)
        result = coverage_audit.classify("t", self.root, entry, {"t"}, 30)
        self.assertEqual(result["state"], coverage_audit.STATE_REPRODUCING)
        self.assertEqual(result["tool"], "t")

    def test_reproducing_nonzero_expected_exit(self):
        d = self.tool_dir("t")
        make_script(d, "t.py", {"bad": True}, 1)
        entry = baseline_entry("t.py", {"bad": True}, 1)
        result = coverage_audit.classify("t", self.root, entry, {"t"}, 30)
        self.assertEqual(result["state"], coverage_audit.STATE_REPRODUCING)

    def test_reproducing_stdout_mode(self):
        d = self.tool_dir("t")
        make_script(d, "t.py", {"ok": True}, 0)
        entry = baseline_entry("t.py", {"ok": True}, 0, report_mode="stdout")
        result = coverage_audit.classify("t", self.root, entry, {"t"}, 30)
        self.assertEqual(result["state"], coverage_audit.STATE_REPRODUCING)

    def test_reproducing_result_has_no_reasons_key(self):
        d = self.tool_dir("t")
        make_script(d, "t.py", {"ok": True}, 0)
        entry = baseline_entry("t.py", {"ok": True}, 0)
        result = coverage_audit.classify("t", self.root, entry, {"t"}, 30)
        self.assertNotIn("reasons", result)


class TestClassifyStale(TempRepoTestCase):
    def test_stale_on_exit_code_mismatch(self):
        d = self.tool_dir("t")
        report = {"ok": True}
        make_script(d, "t.py", report, 3)  # actually exits 3
        entry = baseline_entry("t.py", report, 0)  # baseline expects 0
        result = coverage_audit.classify("t", self.root, entry, {"t"}, 30)
        self.assertEqual(result["state"], coverage_audit.STATE_STALE)
        self.assertIn("exit_code_mismatch", result["reasons"])

    def test_stale_on_hash_mismatch(self):
        d = self.tool_dir("t")
        make_script(d, "t.py", {"now": "different"}, 0)
        entry = baseline_entry("t.py", {"now": "different"}, 0, expected_hash="0" * 64)
        result = coverage_audit.classify("t", self.root, entry, {"t"}, 30)
        self.assertEqual(result["state"], coverage_audit.STATE_STALE)
        self.assertIn("hash_mismatch", result["reasons"])

    def test_stale_on_report_not_created(self):
        d = self.tool_dir("t")
        make_no_write_script(d, "t.py", 0)
        entry = baseline_entry("t.py", {"whatever": 1}, 0)
        result = coverage_audit.classify("t", self.root, entry, {"t"}, 30)
        self.assertEqual(result["state"], coverage_audit.STATE_STALE)
        self.assertIn("report_not_created", result["reasons"])

    def test_stale_on_null_expected_hash(self):
        d = self.tool_dir("t")
        make_script(d, "t.py", {"ok": True}, 0)
        entry = baseline_entry("t.py", {"ok": True}, 0)
        entry["expected_report_sha256"] = None
        result = coverage_audit.classify("t", self.root, entry, {"t"}, 30)
        self.assertEqual(result["state"], coverage_audit.STATE_STALE)
        self.assertIn("expected_hash_missing", result["reasons"])

    def test_stale_with_both_exit_and_hash_mismatch(self):
        d = self.tool_dir("t")
        make_script(d, "t.py", {"a": 1}, 7)
        entry = baseline_entry("t.py", {"a": 1}, 0, expected_hash="1" * 64)
        result = coverage_audit.classify("t", self.root, entry, {"t"}, 30)
        self.assertEqual(result["state"], coverage_audit.STATE_STALE)
        self.assertIn("exit_code_mismatch", result["reasons"])
        self.assertIn("hash_mismatch", result["reasons"])

    def test_stale_never_equals_reproducing_state_string(self):
        self.assertNotEqual(coverage_audit.STATE_STALE, coverage_audit.STATE_REPRODUCING)


class TestClassifyNotBaselined(TempRepoTestCase):
    def test_directory_with_no_entry_is_not_baselined(self):
        os.makedirs(self.tool_dir("orphan-dir"))
        result = coverage_audit.classify("orphan-dir", self.root, None, {"orphan-dir"}, 30)
        self.assertEqual(result["state"], coverage_audit.STATE_NOT_BASELINED)

    def test_not_baselined_detail_mentions_no_entry(self):
        os.makedirs(self.tool_dir("orphan-dir"))
        result = coverage_audit.classify("orphan-dir", self.root, None, {"orphan-dir"}, 30)
        self.assertIn("no entry", result["detail"])


class TestClassifyOrphanedBaseline(TempRepoTestCase):
    def test_entry_without_directory_is_orphaned(self):
        entry = baseline_entry("ghost.py", {"x": 1}, 0)
        result = coverage_audit.classify("ghost-tool", self.root, entry, set(), 30)
        self.assertEqual(result["state"], coverage_audit.STATE_ORPHANED_BASELINE)

    def test_orphaned_detail_mentions_missing_directory(self):
        entry = baseline_entry("ghost.py", {"x": 1}, 0)
        result = coverage_audit.classify("ghost-tool", self.root, entry, set(), 30)
        self.assertIn("directory", result["detail"])

    def test_orphaned_never_attempts_execution(self):
        # No directory exists at all, so classify() must not raise trying
        # to cwd= into it or run anything.
        entry = baseline_entry("ghost.py", {"x": 1}, 0)
        try:
            result = coverage_audit.classify("ghost-tool", "/nonexistent/root/xyz", entry, set(), 30)
        except Exception as exc:  # noqa: BLE001
            self.fail("classify() raised for an orphaned baseline: %r" % exc)
        self.assertEqual(result["state"], coverage_audit.STATE_ORPHANED_BASELINE)


class TestClassifySourceMissing(TempRepoTestCase):
    def test_directory_exists_but_script_absent(self):
        d = self.tool_dir("t")
        os.makedirs(d)
        entry = baseline_entry("missing.py", {"x": 1}, 0)
        result = coverage_audit.classify("t", self.root, entry, {"t"}, 30)
        self.assertEqual(result["state"], coverage_audit.STATE_SOURCE_MISSING)
        self.assertEqual(result["script"], "missing.py")

    def test_source_missing_never_attempts_execution(self):
        # If classify() tried to run this it would raise FileNotFoundError
        # for the interpreter arg parsing missing.py; assert no exception
        # and the state is exactly source_missing, not unrunnable.
        d = self.tool_dir("t")
        os.makedirs(d)
        entry = baseline_entry("missing.py", {"x": 1}, 0)
        result = coverage_audit.classify("t", self.root, entry, {"t"}, 30)
        self.assertEqual(result["state"], coverage_audit.STATE_SOURCE_MISSING)
        self.assertNotEqual(result["state"], coverage_audit.STATE_UNRUNNABLE)

    def test_source_missing_rejects_path_traversal_script(self):
        d = self.tool_dir("t")
        os.makedirs(d)
        # Put the "real" script one level up, and have the command try to
        # reach it with a traversal token -- this must be source_missing,
        # never a successful run reading a file outside the tool dir.
        write(os.path.join(self.root, "escaped.py"), "import sys; sys.exit(0)")
        entry = baseline_entry("../escaped.py", {"x": 1}, 0)
        result = coverage_audit.classify("t", self.root, entry, {"t"}, 30)
        self.assertEqual(result["state"], coverage_audit.STATE_SOURCE_MISSING)

    def test_source_missing_rejects_absolute_path_script(self):
        d = self.tool_dir("t")
        os.makedirs(d)
        outside = os.path.join(self.tmp.name, "outside.py")
        write(outside, "import sys; sys.exit(0)")
        entry = baseline_entry(outside, {"x": 1}, 0)
        result = coverage_audit.classify("t", self.root, entry, {"t"}, 30)
        self.assertEqual(result["state"], coverage_audit.STATE_SOURCE_MISSING)

    def test_source_missing_detail_names_script(self):
        d = self.tool_dir("t")
        os.makedirs(d)
        entry = baseline_entry("nope.py", {"x": 1}, 0)
        result = coverage_audit.classify("t", self.root, entry, {"t"}, 30)
        self.assertIn("nope.py", result["detail"])


class TestClassifyUnrunnable(TempRepoTestCase):
    def test_nonexistent_interpreter_is_unrunnable(self):
        d = self.tool_dir("t")
        os.makedirs(d)
        write(os.path.join(d, "t.py"), "import sys; sys.exit(0)")
        entry = baseline_entry("t.py", {"x": 1}, 0)
        entry["command"] = ["definitely-not-a-real-interpreter-xyz", "t.py"]
        result = coverage_audit.classify("t", self.root, entry, {"t"}, 5)
        self.assertEqual(result["state"], coverage_audit.STATE_UNRUNNABLE)

    def test_timeout_is_unrunnable(self):
        d = self.tool_dir("t")
        make_script(d, "t.py", {"x": 1}, 0, sleep_seconds=5)
        entry = baseline_entry("t.py", {"x": 1}, 0)
        result = coverage_audit.classify("t", self.root, entry, {"t"}, 1)
        self.assertEqual(result["state"], coverage_audit.STATE_UNRUNNABLE)

    def test_unrunnable_never_reported_as_reproducing(self):
        d = self.tool_dir("t")
        make_script(d, "t.py", {"x": 1}, 0, sleep_seconds=5)
        entry = baseline_entry("t.py", {"x": 1}, 0)
        result = coverage_audit.classify("t", self.root, entry, {"t"}, 1)
        self.assertNotEqual(result["state"], coverage_audit.STATE_REPRODUCING)

    def test_unrunnable_has_detail_error_text(self):
        d = self.tool_dir("t")
        os.makedirs(d)
        write(os.path.join(d, "t.py"), "import sys; sys.exit(0)")
        entry = baseline_entry("t.py", {"x": 1}, 0)
        entry["command"] = ["definitely-not-a-real-interpreter-xyz", "t.py"]
        result = coverage_audit.classify("t", self.root, entry, {"t"}, 5)
        self.assertTrue(result["detail"])


# ==========================================================================
# build_report: end-to-end over a synthetic multi-tool tree, and totals
# arithmetic
# ==========================================================================

class TestBuildReportAllStates(TempRepoTestCase):
    """One synthetic tree exercising all six states at once, so the totals
    arithmetic check has something nontrivial to add up."""

    def setUp(self):
        super().setUp()
        # reproducing
        make_script(self.tool_dir("aaa-reproducing"), "run.py", {"ok": True}, 0)
        # stale
        make_script(self.tool_dir("bbb-stale"), "run.py", {"ok": True}, 3)
        # not_baselined: directory present, no entry
        os.makedirs(self.tool_dir("ccc-not-baselined"))
        # source_missing: dir present, entry present, script absent
        os.makedirs(self.tool_dir("ddd-source-missing"))
        # unrunnable: script exists but sleeps past timeout
        make_script(self.tool_dir("eee-unrunnable"), "run.py", {"ok": True}, 0, sleep_seconds=5)

        tools = {
            "aaa-reproducing": baseline_entry("run.py", {"ok": True}, 0),
            "bbb-stale": baseline_entry("run.py", {"ok": True}, 0),  # expects 0, script exits 3
            "ddd-source-missing": baseline_entry("run.py", {"x": 1}, 0),
            "eee-unrunnable": baseline_entry("run.py", {"ok": True}, 0),
            "fff-orphaned-baseline": baseline_entry("ghost.py", {"x": 1}, 0),  # no directory at all
        }
        write_baselines(self.baselines_path, tools)

    def test_totals_sum_equals_len_results(self):
        report, _exit = coverage_audit.build_report(self.root, self.baselines_path, timeout=1)
        counts = report["counts"]
        state_sum = sum(counts[s] for s in coverage_audit.ALL_STATES)
        self.assertEqual(state_sum, len(report["results"]))
        self.assertEqual(state_sum, counts["total_records"])

    def test_every_state_appears_exactly_once(self):
        report, _exit = coverage_audit.build_report(self.root, self.baselines_path, timeout=1)
        by_tool = {r["tool"]: r["state"] for r in report["results"]}
        self.assertEqual(by_tool["aaa-reproducing"], coverage_audit.STATE_REPRODUCING)
        self.assertEqual(by_tool["bbb-stale"], coverage_audit.STATE_STALE)
        self.assertEqual(by_tool["ccc-not-baselined"], coverage_audit.STATE_NOT_BASELINED)
        self.assertEqual(by_tool["ddd-source-missing"], coverage_audit.STATE_SOURCE_MISSING)
        self.assertEqual(by_tool["eee-unrunnable"], coverage_audit.STATE_UNRUNNABLE)
        self.assertEqual(by_tool["fff-orphaned-baseline"], coverage_audit.STATE_ORPHANED_BASELINE)

    def test_counts_match_hand_count(self):
        report, _exit = coverage_audit.build_report(self.root, self.baselines_path, timeout=1)
        counts = report["counts"]
        self.assertEqual(counts["reproducing"], 1)
        self.assertEqual(counts["stale"], 1)
        self.assertEqual(counts["not_baselined"], 1)
        self.assertEqual(counts["source_missing"], 1)
        self.assertEqual(counts["unrunnable"], 1)
        self.assertEqual(counts["orphaned_baseline"], 1)
        self.assertEqual(counts["total_records"], 6)

    def test_discovered_directories_count(self):
        report, _exit = coverage_audit.build_report(self.root, self.baselines_path, timeout=1)
        # 5 directories created in setUp: aaa, bbb, ccc, ddd, eee (fff has no dir)
        self.assertEqual(report["counts"]["discovered_directories"], 5)

    def test_baseline_entries_count(self):
        report, _exit = coverage_audit.build_report(self.root, self.baselines_path, timeout=1)
        self.assertEqual(report["counts"]["baseline_entries"], 5)

    def test_discovered_plus_baseline_minus_overlap_identity(self):
        # Every result is either "has a directory" or "has a baseline
        # entry" (or both); a directory-having result set and a
        # baseline-having result set partition-overlap exactly the way
        # discovered_directories / baseline_entries / total_records say.
        report, _exit = coverage_audit.build_report(self.root, self.baselines_path, timeout=1)
        counts = report["counts"]
        has_dir_states = {
            coverage_audit.STATE_REPRODUCING,
            coverage_audit.STATE_STALE,
            coverage_audit.STATE_NOT_BASELINED,
            coverage_audit.STATE_SOURCE_MISSING,
            coverage_audit.STATE_UNRUNNABLE,
        }
        has_baseline_states = {
            coverage_audit.STATE_REPRODUCING,
            coverage_audit.STATE_STALE,
            coverage_audit.STATE_SOURCE_MISSING,
            coverage_audit.STATE_UNRUNNABLE,
            coverage_audit.STATE_ORPHANED_BASELINE,
        }
        n_has_dir = sum(counts[s] for s in has_dir_states)
        n_has_baseline = sum(counts[s] for s in has_baseline_states)
        self.assertEqual(n_has_dir, counts["discovered_directories"])
        self.assertEqual(n_has_baseline, counts["baseline_entries"])

    def test_exit_code_is_nonzero_when_not_all_reproducing(self):
        _report, exit_code = coverage_audit.build_report(self.root, self.baselines_path, timeout=1)
        self.assertNotEqual(exit_code, 0)

    def test_results_sorted_by_tool_name(self):
        report, _exit = coverage_audit.build_report(self.root, self.baselines_path, timeout=1)
        names = [r["tool"] for r in report["results"]]
        self.assertEqual(names, sorted(names))

    def test_report_status_field_reflects_issues(self):
        report, _exit = coverage_audit.build_report(self.root, self.baselines_path, timeout=1)
        self.assertEqual(report["status"], "issues_found")

    def test_no_absolute_paths_in_report_text(self):
        report, _exit = coverage_audit.build_report(self.root, self.baselines_path, timeout=1)
        text = coverage_audit.canonical_json(report)
        self.assertNotIn(self.root, text)
        self.assertNotIn(self.tmp.name, text)

    def test_report_is_json_serializable_and_canonical(self):
        report, _exit = coverage_audit.build_report(self.root, self.baselines_path, timeout=1)
        text = coverage_audit.canonical_json(report)
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(text.count("\n"), 1)
        # sorted keys, no extra whitespace
        reparsed = json.loads(text)
        self.assertEqual(reparsed, report)


class TestBuildReportAllReproducing(TempRepoTestCase):
    def setUp(self):
        super().setUp()
        make_script(self.tool_dir("only-tool"), "run.py", {"ok": True}, 0)
        write_baselines(
            self.baselines_path, {"only-tool": baseline_entry("run.py", {"ok": True}, 0)}
        )

    def test_exit_code_zero_when_fully_reproducing(self):
        _report, exit_code = coverage_audit.build_report(self.root, self.baselines_path, timeout=5)
        self.assertEqual(exit_code, 0)

    def test_status_field_is_reproducing(self):
        report, _exit = coverage_audit.build_report(self.root, self.baselines_path, timeout=5)
        self.assertEqual(report["status"], coverage_audit.STATE_REPRODUCING)

    def test_totals_all_in_reproducing_bucket(self):
        report, _exit = coverage_audit.build_report(self.root, self.baselines_path, timeout=5)
        self.assertEqual(report["counts"]["reproducing"], 1)
        for state in coverage_audit.ALL_STATES:
            if state != coverage_audit.STATE_REPRODUCING:
                self.assertEqual(report["counts"][state], 0)


class TestBuildReportEmptyRepo(TempRepoTestCase):
    def setUp(self):
        super().setUp()
        write_baselines(self.baselines_path, {})

    def test_empty_repo_is_trivially_exit_zero(self):
        report, exit_code = coverage_audit.build_report(self.root, self.baselines_path, timeout=5)
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["counts"]["total_records"], 0)
        self.assertEqual(report["results"], [])


class TestBuildReportSetupErrors(TempRepoTestCase):
    def test_missing_baselines_raises(self):
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.build_report(self.root, os.path.join(self.tmp.name, "nope.json"), 5)

    def test_root_not_directory_raises(self):
        write_baselines(self.baselines_path, {})
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.build_report(os.path.join(self.root, "nope"), self.baselines_path, 5)

    def test_malformed_baselines_raises_before_touching_root(self):
        write(self.baselines_path, "not json")
        with self.assertRaises(coverage_audit.SetupError):
            coverage_audit.build_report(self.root, self.baselines_path, 5)


# ==========================================================================
# THE POSITIVE CONTROL: one valid baseline flipped to stale, in an
# isolated fixture -- never touches the real baselines.json.
# ==========================================================================

class TestPositiveControlStaleFlip(TempRepoTestCase):
    """Isolated fixture, built fresh in setUp(): a single tool with a
    genuinely valid baseline. First assert the whole audit is clean (exit
    0). Then mutate ONLY the copy of the baseline entry (never the
    original fixture files, never any real repo file) so it no longer
    matches what the tool actually produces, and assert the exit code
    flips from 0 to nonzero. This is the exact scenario the task brief
    calls out by name."""

    def setUp(self):
        super().setUp()
        self.report_obj = {"metric": "value", "n": 42}
        make_script(self.tool_dir("solo-tool"), "run.py", self.report_obj, 0)
        self.valid_entry = baseline_entry("run.py", self.report_obj, 0)

    def _write_and_run(self, entry):
        write_baselines(self.baselines_path, {"solo-tool": entry})
        return coverage_audit.build_report(self.root, self.baselines_path, timeout=10)

    def test_baseline_starts_valid_and_exit_is_zero(self):
        report, exit_code = self._write_and_run(dict(self.valid_entry))
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["results"][0]["state"], coverage_audit.STATE_REPRODUCING)

    def test_mutating_hash_flips_state_to_stale_and_exit_nonzero(self):
        # Baseline #1: valid -> exit 0.
        report1, exit1 = self._write_and_run(dict(self.valid_entry))
        self.assertEqual(exit1, 0)
        self.assertEqual(report1["results"][0]["state"], coverage_audit.STATE_REPRODUCING)

        # Baseline #2: SAME tool, SAME script, only the recorded hash in
        # our isolated copy of the entry is changed -- as if the tool's
        # output drifted since the baseline was recorded.
        stale_entry = dict(self.valid_entry)
        stale_entry["expected_report_sha256"] = "f" * 64
        report2, exit2 = self._write_and_run(stale_entry)

        self.assertEqual(report2["results"][0]["state"], coverage_audit.STATE_STALE)
        self.assertNotEqual(exit2, 0)

        # The flip itself, stated explicitly as the thing being proven:
        self.assertEqual(exit1, 0)
        self.assertNotEqual(exit2, 0)
        self.assertNotEqual(exit1, exit2)

    def test_mutating_expected_exit_code_flips_state_to_stale_and_exit_nonzero(self):
        report1, exit1 = self._write_and_run(dict(self.valid_entry))
        self.assertEqual(exit1, 0)

        stale_entry = dict(self.valid_entry)
        stale_entry["expected_exit_code"] = 9
        report2, exit2 = self._write_and_run(stale_entry)

        self.assertEqual(report2["results"][0]["state"], coverage_audit.STATE_STALE)
        self.assertIn("exit_code_mismatch", report2["results"][0]["reasons"])
        self.assertEqual(exit1, 0)
        self.assertNotEqual(exit2, 0)

    def test_reverting_the_mutation_flips_back_to_zero(self):
        # Confirms the flip is caused by the mutation, not by test order
        # or hidden state: valid -> stale -> valid again.
        _report1, exit1 = self._write_and_run(dict(self.valid_entry))
        stale_entry = dict(self.valid_entry)
        stale_entry["expected_report_sha256"] = "0" * 64
        _report2, exit2 = self._write_and_run(stale_entry)
        _report3, exit3 = self._write_and_run(dict(self.valid_entry))
        self.assertEqual((exit1, exit2, exit3), (0, 1, 0))

    def test_real_baselines_json_was_never_touched(self):
        # Sanity guard: this whole class must operate only on
        # self.baselines_path inside the per-test TemporaryDirectory.
        self.assertTrue(os.path.isfile(REAL_BASELINES))
        with open(REAL_BASELINES, "r", encoding="utf-8") as fh:
            before = fh.read()
        self._write_and_run(dict(self.valid_entry))
        stale_entry = dict(self.valid_entry)
        stale_entry["expected_report_sha256"] = "e" * 64
        self._write_and_run(stale_entry)
        with open(REAL_BASELINES, "r", encoding="utf-8") as fh:
            after = fh.read()
        self.assertEqual(before, after)


# ==========================================================================
# CLI-level (subprocess) tests
# ==========================================================================

class TestCLI(TempRepoTestCase):
    def run_cli(self, extra_args, cwd=None):
        cmd = [PY, COVERAGE_AUDIT_PY] + extra_args
        return subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)

    def test_cli_exit_zero_all_reproducing(self):
        make_script(self.tool_dir("only-tool"), "run.py", {"ok": True}, 0)
        write_baselines(self.baselines_path, {"only-tool": baseline_entry("run.py", {"ok": True}, 0)})
        proc = self.run_cli(["--root", self.root, "--baselines", self.baselines_path])
        self.assertEqual(proc.returncode, 0)

    def test_cli_exit_one_on_not_baselined(self):
        os.makedirs(self.tool_dir("stray"))
        write_baselines(self.baselines_path, {})
        proc = self.run_cli(["--root", self.root, "--baselines", self.baselines_path])
        self.assertEqual(proc.returncode, 1)

    def test_cli_exit_one_on_stale(self):
        make_script(self.tool_dir("t"), "run.py", {"a": 1}, 5)
        write_baselines(self.baselines_path, {"t": baseline_entry("run.py", {"a": 1}, 0)})
        proc = self.run_cli(["--root", self.root, "--baselines", self.baselines_path])
        self.assertEqual(proc.returncode, 1)

    def test_cli_exit_two_on_missing_baselines_file(self):
        proc = self.run_cli(
            ["--root", self.root, "--baselines", os.path.join(self.tmp.name, "nope.json")]
        )
        self.assertEqual(proc.returncode, 2)

    def test_cli_exit_two_on_bool_expected_exit_code(self):
        os.makedirs(self.tool_dir("t"))
        entry = baseline_entry("run.py", {"a": 1}, 0)
        entry["expected_exit_code"] = False
        write_baselines(self.baselines_path, {"t": entry})
        proc = self.run_cli(["--root", self.root, "--baselines", self.baselines_path])
        self.assertEqual(proc.returncode, 2)
        combined = (proc.stdout + proc.stderr).decode("utf-8")
        self.assertIn("boolean", combined)

    def test_cli_exit_two_on_malformed_json(self):
        write(self.baselines_path, "{broken")
        proc = self.run_cli(["--root", self.root, "--baselines", self.baselines_path])
        self.assertEqual(proc.returncode, 2)

    def test_cli_exit_two_on_root_not_directory(self):
        write_baselines(self.baselines_path, {})
        proc = self.run_cli(
            ["--root", os.path.join(self.root, "nope"), "--baselines", self.baselines_path]
        )
        self.assertEqual(proc.returncode, 2)

    def test_cli_writes_output_file(self):
        make_script(self.tool_dir("only-tool"), "run.py", {"ok": True}, 0)
        write_baselines(self.baselines_path, {"only-tool": baseline_entry("run.py", {"ok": True}, 0)})
        out_path = os.path.join(self.tmp.name, "out.json")
        proc = self.run_cli(
            ["--root", self.root, "--baselines", self.baselines_path, "-o", out_path]
        )
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(os.path.isfile(out_path))
        with open(out_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["counts"]["reproducing"], 1)

    # ----------------------------------------------------------------
    # The stdout contract on success.
    #
    # This block replaces a single test named
    # test_cli_stdout_has_no_extraneous_output_on_success whose entire
    # body was:
    #
    #     proc = self.run_cli([...])
    #     # stdout must be exactly one JSON document + trailing newline
    #     json.loads(proc.stdout.decode("utf-8"))
    #
    # No assertion, and the result thrown away. It passed if stdout
    # happened to parse, which is a weaker claim than any of the three
    # its name and comment make.
    #
    # stdout_contract_mutations.py measures this rather than asserting
    # it: five realistic regressions are applied to a copy of
    # coverage_audit.py one at a time. Run against the parent commit,
    # that ONE test caught 1 of 5 --
    #
    #   banner line before the JSON     caught
    #   leading blank line on stdout    MISSED -- json.loads skips
    #                                             leading whitespace
    #   no trailing newline             MISSED -- the comment claims
    #                                             this is checked
    #   exit code 3 instead of 0        MISSED -- "on success" was
    #                                             never verified here
    #   chatter written to stderr       MISSED -- "no extraneous
    #                                             output" was only ever
    #                                             about stdout
    #
    # -- and the whole TestCLI class caught 2 of 5, because
    # test_cli_exit_zero_all_reproducing and two siblings already
    # covered the exit code. That distinction matters: the exit-code
    # property was not unprotected, it was just not protected by the
    # test whose name claimed it. The other three were unprotected.
    # After this change the class catches 5 of 5.
    #
    # The properties are split into named tests so a future regression
    # reports which one broke rather than "that JSON test".
    # ----------------------------------------------------------------

    def _successful_run(self):
        """One reproducing tool, audited, exit 0 expected."""
        make_script(self.tool_dir("only-tool"), "run.py", {"ok": True}, 0)
        write_baselines(self.baselines_path,
                        {"only-tool": baseline_entry("run.py", {"ok": True}, 0)})
        return self.run_cli(["--root", self.root, "--baselines", self.baselines_path])

    def test_cli_exits_zero_on_a_fully_reproducing_run(self):
        """The 'on success' half of the old name, actually checked."""
        proc = self._successful_run()
        self.assertEqual(proc.returncode, 0,
                         "stderr was: %r" % proc.stderr[:400])

    def test_cli_stdout_is_exactly_one_json_document_and_nothing_else(self):
        """Not 'parses as JSON' -- nothing before it, nothing after it.

        ``json.loads`` tolerates surrounding whitespace, so it cannot
        distinguish a clean report from one preceded by a blank line.
        ``raw_decode`` reports where the document ended, which turns
        "and nothing else" into something checkable.
        """
        proc = self._successful_run()
        text = proc.stdout.decode("utf-8")
        self.assertTrue(text, "stdout was empty")
        self.assertFalse(text[:1].isspace(),
                         "stdout begins with whitespace: %r" % text[:20])
        _, end = json.JSONDecoder().raw_decode(text)
        self.assertEqual(text[end:], "\n",
                         "trailing bytes after the JSON document: %r"
                         % text[end:][:60])

    def test_cli_stdout_ends_with_exactly_one_newline(self):
        """The half of the old comment that was never verified."""
        proc = self._successful_run()
        text = proc.stdout.decode("utf-8")
        self.assertTrue(text.endswith("\n"), "stdout has no trailing newline")
        self.assertFalse(text.endswith("\n\n"),
                         "stdout ends with more than one newline")

    def test_cli_writes_nothing_to_stderr_on_success(self):
        """'No extraneous output' has to include the other stream."""
        proc = self._successful_run()
        self.assertEqual(proc.stderr, b"",
                         "stderr on a successful run: %r" % proc.stderr[:400])

    def test_cli_stdout_is_the_report_not_merely_valid_json(self):
        """A tool that printed `{}` would satisfy every test above."""
        proc = self._successful_run()
        report = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(report["tool"], coverage_audit.TOOL_NAME)
        self.assertEqual(report["counts"]["reproducing"], 1)

    def test_cli_unwritable_output_is_setup_error(self):
        write_baselines(self.baselines_path, {})
        bad_dir = os.path.join(self.tmp.name, "no_such_dir", "out.json")
        proc = self.run_cli(["--root", self.root, "--baselines", self.baselines_path, "-o", bad_dir])
        self.assertEqual(proc.returncode, 2)

    def test_cli_default_timeout_flag_accepted(self):
        make_script(self.tool_dir("only-tool"), "run.py", {"ok": True}, 0)
        write_baselines(self.baselines_path, {"only-tool": baseline_entry("run.py", {"ok": True}, 0)})
        proc = self.run_cli(
            ["--root", self.root, "--baselines", self.baselines_path, "--timeout", "10"]
        )
        self.assertEqual(proc.returncode, 0)

    def test_cli_help_exits_zero(self):
        proc = self.run_cli(["--help"])
        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"coverage_audit", proc.stdout)


# ==========================================================================
# canonical_json / sha256_hex determinism helpers
# ==========================================================================

class TestCanonicalJSON(unittest.TestCase):
    def test_sorted_keys(self):
        text = coverage_audit.canonical_json({"b": 1, "a": 2})
        self.assertTrue(text.startswith('{"a":2,"b":1}'))

    def test_single_trailing_newline(self):
        text = coverage_audit.canonical_json({"x": 1})
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))

    def test_no_extra_whitespace(self):
        text = coverage_audit.canonical_json({"a": [1, 2], "b": {"c": 1}})
        self.assertNotIn(", ", text)
        self.assertNotIn(": ", text)

    def test_deterministic_across_calls(self):
        obj = {"z": 1, "a": [3, 2, 1], "m": {"y": 1, "x": 2}}
        self.assertEqual(coverage_audit.canonical_json(obj), coverage_audit.canonical_json(obj))

    def test_ascii_only(self):
        text = coverage_audit.canonical_json({"name": "café"})
        self.assertNotIn("é", text)
        self.assertIn("\\u00e9", text)


class TestSha256Hex(unittest.TestCase):
    def test_known_value_empty_bytes(self):
        self.assertEqual(
            coverage_audit.sha256_hex(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )

    def test_hex_length_is_64(self):
        self.assertEqual(len(coverage_audit.sha256_hex(b"anything")), 64)

    def test_deterministic(self):
        self.assertEqual(coverage_audit.sha256_hex(b"x"), coverage_audit.sha256_hex(b"x"))

    def test_different_input_different_hash(self):
        self.assertNotEqual(coverage_audit.sha256_hex(b"x"), coverage_audit.sha256_hex(b"y"))


# ==========================================================================
# Determinism: two runs, same directory, byte-identical report
# ==========================================================================

class TestDeterminismSameDirectory(TempRepoTestCase):
    def setUp(self):
        super().setUp()
        make_script(self.tool_dir("only-tool"), "run.py", {"ok": True, "n": 7}, 0)
        write_baselines(self.baselines_path, {"only-tool": baseline_entry("run.py", {"ok": True, "n": 7}, 0)})

    def test_two_runs_same_directory_are_byte_identical(self):
        report1, _ = coverage_audit.build_report(self.root, self.baselines_path, timeout=10)
        report2, _ = coverage_audit.build_report(self.root, self.baselines_path, timeout=10)
        text1 = coverage_audit.canonical_json(report1)
        text2 = coverage_audit.canonical_json(report2)
        self.assertEqual(text1, text2)
        self.assertEqual(hashlib.sha256(text1.encode()).hexdigest(), hashlib.sha256(text2.encode()).hexdigest())


# ==========================================================================
# Real repository smoke test: the actual bug-hunt finding, pinned.
# ==========================================================================

@unittest.skipUnless(os.path.isfile(REAL_BASELINES), "real baselines.json not found")
class TestRealRepository(unittest.TestCase):
    """Runs the auditor against the ACTUAL repository (the one this file
    lives in), read-only: no baselines.json or tool source is modified.
    Pins the real, observed findings so a future regression in either the
    repo or the auditor is caught."""

    @classmethod
    def setUpClass(cls):
        cls.report, cls.exit_code = coverage_audit.build_report(
            REPO_ROOT, REAL_BASELINES, timeout=60
        )

    def test_exit_code_is_nonzero(self):
        # Many directories are not baselined at all; this can never be 0.
        self.assertNotEqual(self.exit_code, 0)

    def test_baseline_entries_count_is_23(self):
        self.assertEqual(self.report["counts"]["baseline_entries"], 23)

    def test_discovered_directories_matches_disk(self):
        # Derived, not pinned: a hardcoded repo size breaks every time a new
        # tool directory is added (it broke when claim-crosscheck landed).
        # The invariant that actually matters is that discovery agrees with
        # what is on disk.
        on_disk = coverage_audit.discover_tool_dirs(REPO_ROOT)
        self.assertEqual(
            self.report["counts"]["discovered_directories"], len(on_disk)
        )

    def test_not_baselined_equals_discovered_minus_baselined_present(self):
        counts = self.report["counts"]
        baselined_present = (
            counts["reproducing"]
            + counts["stale"]
            + counts["source_missing"]
            + counts["unrunnable"]
        )
        self.assertEqual(
            counts["not_baselined"],
            counts["discovered_directories"] - baselined_present,
        )

    def test_orphaned_baseline_count_is_zero(self):
        self.assertEqual(self.report["counts"]["orphaned_baseline"], 0)

    def test_source_missing_count_is_zero(self):
        self.assertEqual(self.report["counts"]["source_missing"], 0)

    def test_unrunnable_count_is_zero(self):
        self.assertEqual(self.report["counts"]["unrunnable"], 0)

    def test_totals_are_derivable_from_results(self):
        # Every total a reviewer reads must be recomputable by counting
        # results[]. This is the arithmetic check, stated without pinning
        # the repository's current size.
        counts = self.report["counts"]
        results = self.report["results"]
        self.assertEqual(sum(counts[s] for s in coverage_audit.ALL_STATES), len(results))
        self.assertEqual(counts["total_records"], len(results))
        self.assertEqual(counts["discovered_directories"], len(results))
        for state in coverage_audit.ALL_STATES:
            self.assertEqual(
                counts[state],
                sum(1 for r in results if r["state"] == state),
                "counts[%s] disagrees with results[]" % state,
            )

    def test_bug_hunt_finding_bundle_index_is_stale(self):
        # THE real finding: bundle-index's baseline command runs
        # `python3 bundle_index.py bundle_bad -o {REPORT}`, but only
        # `bundle_ok/` is committed under bundle-index/ -- `bundle_bad/`
        # does not exist. The baselined command exits 2 (input error)
        # instead of the baselined 1, and never writes a report.
        by_tool = {r["tool"]: r for r in self.report["results"]}
        self.assertIn("bundle-index", by_tool)
        self.assertEqual(by_tool["bundle-index"]["state"], coverage_audit.STATE_STALE)
        self.assertIn("exit_code_mismatch", by_tool["bundle-index"]["reasons"])
        self.assertIn("report_not_created", by_tool["bundle-index"]["reasons"])

    def test_all_other_baselined_tools_reproduce(self):
        by_tool = {r["tool"]: r for r in self.report["results"]}
        with open(REAL_BASELINES, "r", encoding="utf-8") as fh:
            baselined_names = json.load(fh)["tools"].keys()
        non_reproducing = [
            name
            for name in baselined_names
            if name != "bundle-index" and by_tool[name]["state"] != coverage_audit.STATE_REPRODUCING
        ]
        self.assertEqual(non_reproducing, [])

    def test_reproducing_count_is_22(self):
        self.assertEqual(self.report["counts"]["reproducing"], 22)

    def test_stale_count_is_1(self):
        self.assertEqual(self.report["counts"]["stale"], 1)


if __name__ == "__main__":
    unittest.main()
