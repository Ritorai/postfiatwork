#!/usr/bin/env python3
"""Test suite for regress.py. Stdlib-only (unittest, tempfile, subprocess,
json, os, sys, hashlib). Does NOT depend on the sibling tool repositories
existing on disk -- every scenario is built from small synthetic fixtures
created on the fly, or from the generated fixtures/ directory, which
setUpModule() builds via make_fixtures.py if it is not already present)."""

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import regress  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REGRESS_PY = os.path.join(HERE, "regress.py")
FIXTURES_DIR = os.path.join(HERE, "fixtures")
PY = sys.executable or "python3"


def setUpModule():
    """`fixtures/` is generated, not committed -- the same convention as
    `bundle-verifier/`, `exit-harness/` and `tamper-runner/`, which all ship a
    `make_fixtures.py` and commit no generated tree. Build it on demand so the
    CLI-level tests below are runnable from a fresh clone."""
    if not os.path.isdir(FIXTURES_DIR):
        import make_fixtures
        make_fixtures.build(FIXTURES_DIR)



def write(path, content):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def make_tool_script(dir_path, filename, body):
    os.makedirs(dir_path, exist_ok=True)
    write(os.path.join(dir_path, filename), body)


TOOL_TEMPLATE = """#!/usr/bin/env python3
import argparse, json, sys
ap = argparse.ArgumentParser()
ap.add_argument("-o", "--output")
args = ap.parse_args()
report = {report!r}
text = json.dumps(report, sort_keys=True, separators=(",", ":")) + "\\n"
if args.output:
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(text)
else:
    sys.stdout.write(text)
sys.exit({exit_code})
"""


def make_simple_tool(dir_path, report_obj, exit_code=0):
    body = TOOL_TEMPLATE.format(report=report_obj, exit_code=exit_code)
    make_tool_script(dir_path, "tool.py", body)


def sha256_of(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_report_hash(report_obj):
    text = json.dumps(report_obj, sort_keys=True, separators=(",", ":")) + "\n"
    return sha256_of(text), text


def run_cli(args, cwd=None):
    proc = subprocess.run(
        [PY, REGRESS_PY] + args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


class TempRootMixin:
    def make_root(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        return td.name

    def write_baselines(self, root, tools):
        path = os.path.join(root, "baselines.json")
        write(path, json.dumps({"tools": tools}))
        return path


# ---------------------------------------------------------------------------
# canonical_json
# ---------------------------------------------------------------------------

class TestCanonicalJson(unittest.TestCase):
    def test_ends_with_single_newline(self):
        out = regress.canonical_json({"a": 1})
        self.assertTrue(out.endswith("\n"))
        self.assertFalse(out.endswith("\n\n"))

    def test_sorted_keys(self):
        out = regress.canonical_json({"b": 1, "a": 2})
        self.assertEqual(out, '{"a":2,"b":1}\n')

    def test_tight_separators_no_spaces(self):
        out = regress.canonical_json({"a": [1, 2, 3], "b": {"c": 1}})
        self.assertNotIn(", ", out)
        self.assertNotIn(": ", out)

    def test_ascii_only_escapes_unicode(self):
        out = regress.canonical_json({"a": "café"})
        self.assertIn("\\u00e9", out)
        self.assertTrue(all(ord(c) < 128 for c in out))

    def test_deterministic_across_calls(self):
        obj = {"z": 1, "a": {"y": 2, "x": [3, 2, 1]}}
        self.assertEqual(regress.canonical_json(obj), regress.canonical_json(obj))

    def test_bool_and_null_preserved(self):
        out = regress.canonical_json({"t": True, "f": False, "n": None})
        self.assertEqual(out, '{"f":false,"n":null,"t":true}\n')

    def test_list_order_preserved_not_sorted(self):
        out = regress.canonical_json({"a": [3, 1, 2]})
        self.assertEqual(out, '{"a":[3,1,2]}\n')

    def test_empty_dict(self):
        self.assertEqual(regress.canonical_json({}), "{}\n")

    def test_nested_dict_keys_sorted_at_every_level(self):
        out = regress.canonical_json({"outer": {"z": 1, "a": 2}})
        self.assertEqual(out, '{"outer":{"a":2,"z":1}}\n')

    def test_integer_not_rendered_as_float(self):
        out = regress.canonical_json({"n": 5})
        self.assertIn('"n":5', out)
        self.assertNotIn("5.0", out)

    def test_returns_str_type(self):
        self.assertIsInstance(regress.canonical_json({"a": 1}), str)

    def test_string_with_quotes_escaped(self):
        out = regress.canonical_json({"a": 'he said "hi"'})
        self.assertIn('\\"hi\\"', out)


# ---------------------------------------------------------------------------
# sha256_hex
# ---------------------------------------------------------------------------

class TestSha256Hex(unittest.TestCase):
    def test_empty_bytes(self):
        self.assertEqual(
            regress.sha256_hex(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )

    def test_known_vector_abc(self):
        self.assertEqual(
            regress.sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )

    def test_length_is_64(self):
        self.assertEqual(len(regress.sha256_hex(b"hello")), 64)

    def test_lowercase_hex(self):
        h = regress.sha256_hex(b"hello world")
        self.assertEqual(h, h.lower())

    def test_different_input_different_hash(self):
        self.assertNotEqual(regress.sha256_hex(b"a"), regress.sha256_hex(b"b"))

    def test_matches_hashlib_directly(self):
        data = b"some report bytes \x00\xff"
        self.assertEqual(regress.sha256_hex(data), hashlib.sha256(data).hexdigest())


# ---------------------------------------------------------------------------
# load_baselines
# ---------------------------------------------------------------------------

class TestLoadBaselines(TempRootMixin, unittest.TestCase):
    def test_missing_file_raises(self):
        with self.assertRaises(regress.SetupError):
            regress.load_baselines("/nonexistent/path/baselines.json")

    def test_valid_minimal(self):
        root = self.make_root()
        path = self.write_baselines(root, {
            "t1": {"command": ["python3", "x.py"], "report_mode": "stdout", "expected_exit_code": 0}
        })
        tools = regress.load_baselines(path)
        self.assertIn("t1", tools)
        self.assertEqual(tools["t1"]["status"], "baselined")
        self.assertIsNone(tools["t1"]["expected_report_sha256"])

    def test_invalid_json_raises(self):
        root = self.make_root()
        path = os.path.join(root, "b.json")
        write(path, "{not json")
        with self.assertRaises(regress.SetupError):
            regress.load_baselines(path)

    def test_top_level_not_object_raises(self):
        root = self.make_root()
        path = os.path.join(root, "b.json")
        write(path, "[1,2,3]")
        with self.assertRaises(regress.SetupError):
            regress.load_baselines(path)

    def test_missing_tools_key_raises(self):
        root = self.make_root()
        path = os.path.join(root, "b.json")
        write(path, json.dumps({"nope": {}}))
        with self.assertRaises(regress.SetupError):
            regress.load_baselines(path)

    def test_tools_not_object_raises(self):
        root = self.make_root()
        path = os.path.join(root, "b.json")
        write(path, json.dumps({"tools": []}))
        with self.assertRaises(regress.SetupError):
            regress.load_baselines(path)

    def test_entry_not_object_raises(self):
        root = self.make_root()
        path = self.write_baselines(root, {"t1": "not-an-object"})
        with self.assertRaises(regress.SetupError):
            regress.load_baselines(path)

    def test_unknown_status_raises(self):
        root = self.make_root()
        path = self.write_baselines(root, {"t1": {"status": "weird"}})
        with self.assertRaises(regress.SetupError):
            regress.load_baselines(path)

    def test_unbaselineable_missing_reason_raises(self):
        root = self.make_root()
        path = self.write_baselines(root, {"t1": {"status": "unbaselineable"}})
        with self.assertRaises(regress.SetupError):
            regress.load_baselines(path)

    def test_unbaselineable_reason_not_string_raises(self):
        root = self.make_root()
        path = self.write_baselines(root, {"t1": {"status": "unbaselineable", "reason": 5}})
        with self.assertRaises(regress.SetupError):
            regress.load_baselines(path)

    def test_unbaselineable_valid(self):
        root = self.make_root()
        path = self.write_baselines(root, {"t1": {"status": "unbaselineable", "reason": "no deterministic output"}})
        tools = regress.load_baselines(path)
        self.assertEqual(tools["t1"], {"status": "unbaselineable", "reason": "no deterministic output"})

    def test_missing_command_field_raises(self):
        root = self.make_root()
        path = self.write_baselines(root, {"t1": {"report_mode": "stdout", "expected_exit_code": 0}})
        with self.assertRaises(regress.SetupError):
            regress.load_baselines(path)

    def test_missing_report_mode_field_raises(self):
        root = self.make_root()
        path = self.write_baselines(root, {"t1": {"command": ["a"], "expected_exit_code": 0}})
        with self.assertRaises(regress.SetupError):
            regress.load_baselines(path)

    def test_missing_expected_exit_code_field_raises(self):
        root = self.make_root()
        path = self.write_baselines(root, {"t1": {"command": ["a"], "report_mode": "stdout"}})
        with self.assertRaises(regress.SetupError):
            regress.load_baselines(path)

    def test_command_not_list_raises(self):
        root = self.make_root()
        path = self.write_baselines(root, {"t1": {"command": "python3 x.py", "report_mode": "stdout", "expected_exit_code": 0}})
        with self.assertRaises(regress.SetupError):
            regress.load_baselines(path)

    def test_command_with_non_string_element_raises(self):
        root = self.make_root()
        path = self.write_baselines(root, {"t1": {"command": ["python3", 5], "report_mode": "stdout", "expected_exit_code": 0}})
        with self.assertRaises(regress.SetupError):
            regress.load_baselines(path)

    def test_invalid_report_mode_raises(self):
        root = self.make_root()
        path = self.write_baselines(root, {"t1": {"command": ["a"], "report_mode": "network", "expected_exit_code": 0}})
        with self.assertRaises(regress.SetupError):
            regress.load_baselines(path)

    def test_expected_exit_code_not_int_raises(self):
        root = self.make_root()
        path = self.write_baselines(root, {"t1": {"command": ["a"], "report_mode": "stdout", "expected_exit_code": "0"}})
        with self.assertRaises(regress.SetupError):
            regress.load_baselines(path)

    def test_expected_exit_code_bool_false_rejected(self):
        # bool is a subclass of int in Python. Accepting `false` here used to
        # make `0 == False` compare as a match, silently blessing any tool
        # that exited 0. It is now a setup error.
        root = self.make_root()
        path = self.write_baselines(root, {"t1": {"command": ["a"], "report_mode": "stdout", "expected_exit_code": False}})
        with self.assertRaises(regress.SetupError):
            regress.load_baselines(path)

    def test_expected_exit_code_bool_true_rejected(self):
        root = self.make_root()
        path = self.write_baselines(root, {"t1": {"command": ["a"], "report_mode": "stdout", "expected_exit_code": True}})
        with self.assertRaises(regress.SetupError):
            regress.load_baselines(path)

    def test_null_hash_accepted(self):
        root = self.make_root()
        path = self.write_baselines(root, {"t1": {"command": ["a"], "report_mode": "stdout", "expected_exit_code": 0, "expected_report_sha256": None}})
        tools = regress.load_baselines(path)
        self.assertIsNone(tools["t1"]["expected_report_sha256"])

    def test_short_hash_rejected(self):
        root = self.make_root()
        path = self.write_baselines(root, {"t1": {"command": ["a"], "report_mode": "stdout", "expected_exit_code": 0, "expected_report_sha256": "abc123"}})
        with self.assertRaises(regress.SetupError):
            regress.load_baselines(path)

    def test_non_string_hash_rejected(self):
        root = self.make_root()
        path = self.write_baselines(root, {"t1": {"command": ["a"], "report_mode": "stdout", "expected_exit_code": 0, "expected_report_sha256": 12345}})
        with self.assertRaises(regress.SetupError):
            regress.load_baselines(path)

    def test_multiple_mixed_entries(self):
        root = self.make_root()
        path = self.write_baselines(root, {
            "a": {"command": ["x"], "report_mode": "file", "expected_exit_code": 0, "expected_report_sha256": "0" * 64},
            "b": {"status": "unbaselineable", "reason": "needs network"},
        })
        tools = regress.load_baselines(path)
        self.assertEqual(set(tools.keys()), {"a", "b"})
        self.assertEqual(tools["a"]["status"], "baselined")
        self.assertEqual(tools["b"]["status"], "unbaselineable")

    def test_valid_64_char_hash_accepted(self):
        root = self.make_root()
        h = "a" * 64
        path = self.write_baselines(root, {"t1": {"command": ["a"], "report_mode": "stdout", "expected_exit_code": 0, "expected_report_sha256": h}})
        tools = regress.load_baselines(path)
        self.assertEqual(tools["t1"]["expected_report_sha256"], h)


# ---------------------------------------------------------------------------
# discover_tool_dirs
# ---------------------------------------------------------------------------

class TestDiscoverToolDirs(TempRootMixin, unittest.TestCase):
    def test_root_not_a_directory_raises(self):
        root = self.make_root()
        f = os.path.join(root, "afile")
        write(f, "x")
        with self.assertRaises(regress.SetupError):
            regress.discover_tool_dirs(f)

    def test_root_missing_raises(self):
        with self.assertRaises(regress.SetupError):
            regress.discover_tool_dirs("/definitely/not/there/xyz")

    def test_empty_root_returns_empty_list(self):
        root = self.make_root()
        self.assertEqual(regress.discover_tool_dirs(root), [])

    def test_sorted_order(self):
        root = self.make_root()
        for name in ["zeta", "alpha", "mu"]:
            os.makedirs(os.path.join(root, name))
        self.assertEqual(regress.discover_tool_dirs(root), ["alpha", "mu", "zeta"])

    def test_skips_dotdirs(self):
        root = self.make_root()
        os.makedirs(os.path.join(root, ".git"))
        os.makedirs(os.path.join(root, "visible"))
        self.assertEqual(regress.discover_tool_dirs(root), ["visible"])

    def test_skips_pycache(self):
        root = self.make_root()
        os.makedirs(os.path.join(root, "__pycache__"))
        os.makedirs(os.path.join(root, "visible"))
        self.assertEqual(regress.discover_tool_dirs(root), ["visible"])

    def test_excludes_plain_files(self):
        root = self.make_root()
        write(os.path.join(root, "README.md"), "hi")
        os.makedirs(os.path.join(root, "tool_a"))
        self.assertEqual(regress.discover_tool_dirs(root), ["tool_a"])

    def test_output_file_living_in_root_is_not_a_tool(self):
        # Edge case called out in the spec: the checker's own -o output
        # file might live inside --root. Since discovery only looks at
        # directories, a JSON file there is never mistaken for a tool.
        root = self.make_root()
        os.makedirs(os.path.join(root, "tool_a"))
        write(os.path.join(root, "report.json"), '{"drift":false}')
        self.assertEqual(regress.discover_tool_dirs(root), ["tool_a"])

    def test_nested_subdirectories_not_recursed(self):
        root = self.make_root()
        os.makedirs(os.path.join(root, "tool_a", "nested"))
        self.assertEqual(regress.discover_tool_dirs(root), ["tool_a"])


# ---------------------------------------------------------------------------
# run_tool
# ---------------------------------------------------------------------------

class TestRunTool(TempRootMixin, unittest.TestCase):
    def test_stdout_mode_success(self):
        root = self.make_root()
        make_simple_tool(root, {"ok": True})
        entry = {"command": [PY, "tool.py"], "report_mode": "stdout"}
        result = regress.run_tool(root, entry, timeout=10)
        self.assertTrue(result["ok"])
        self.assertEqual(result["actual_exit_code"], 0)
        expected_hash, _ = canonical_report_hash({"ok": True})
        self.assertEqual(result["actual_report_sha256"], expected_hash)

    def test_file_mode_success(self):
        root = self.make_root()
        make_simple_tool(root, {"ok": True, "n": 3})
        entry = {"command": [PY, "tool.py", "-o", "{REPORT}"], "report_mode": "file"}
        result = regress.run_tool(root, entry, timeout=10)
        self.assertTrue(result["ok"])
        expected_hash, _ = canonical_report_hash({"ok": True, "n": 3})
        self.assertEqual(result["actual_report_sha256"], expected_hash)

    def test_file_mode_report_not_created(self):
        root = self.make_root()
        # tool.py does not exist at all
        entry = {"command": [PY, "tool.py", "-o", "{REPORT}"], "report_mode": "file"}
        result = regress.run_tool(root, entry, timeout=10)
        self.assertFalse(result["ok"])
        self.assertIn("report file was not created", result["error"])
        self.assertIsNotNone(result["actual_exit_code"])

    def test_command_not_found(self):
        root = self.make_root()
        entry = {"command": ["no_such_interpreter_xyz_123", "tool.py"], "report_mode": "stdout"}
        result = regress.run_tool(root, entry, timeout=10)
        self.assertFalse(result["ok"])
        self.assertIn("command not found", result["error"])
        self.assertIsNone(result["actual_exit_code"])

    def test_timeout(self):
        root = self.make_root()
        make_tool_script(root, "tool.py", "import time\ntime.sleep(5)\n")
        entry = {"command": [PY, "tool.py"], "report_mode": "stdout"}
        result = regress.run_tool(root, entry, timeout=1)
        self.assertFalse(result["ok"])
        self.assertIn("timeout", result["error"])

    def test_nonzero_exit_still_captured_ok(self):
        root = self.make_root()
        make_simple_tool(root, {"ok": False}, exit_code=1)
        entry = {"command": [PY, "tool.py"], "report_mode": "stdout"}
        result = regress.run_tool(root, entry, timeout=10)
        self.assertTrue(result["ok"])
        self.assertEqual(result["actual_exit_code"], 1)

    def test_report_bytes_length_recorded(self):
        root = self.make_root()
        make_simple_tool(root, {"x": 1})
        entry = {"command": [PY, "tool.py"], "report_mode": "stdout"}
        result = regress.run_tool(root, entry, timeout=10)
        _, text = canonical_report_hash({"x": 1})
        self.assertEqual(result["report_bytes_length"], len(text.encode("utf-8")))

    def test_placeholder_substitution_only_replaces_report_token(self):
        root = self.make_root()
        make_simple_tool(root, {"x": 1})
        entry = {"command": [PY, "tool.py", "-o", "{REPORT}"], "report_mode": "file"}
        # If substitution were broken, the report path would literally be
        # "{REPORT}" and the file would never be created relative to root.
        result = regress.run_tool(root, entry, timeout=10)
        self.assertTrue(result["ok"])
        self.assertFalse(os.path.exists(os.path.join(root, "{REPORT}")))

    def test_temp_report_file_cleaned_up(self):
        root = self.make_root()
        make_simple_tool(root, {"x": 1})
        entry = {"command": [PY, "tool.py", "-o", "{REPORT}"], "report_mode": "file"}
        # Just verify run_tool doesn't raise / leaves no trace in root.
        regress.run_tool(root, entry, timeout=10)
        self.assertEqual(sorted(os.listdir(root)), ["tool.py"])

    def test_stdin_not_required_argv_passed_literally(self):
        root = self.make_root()
        body = (
            "#!/usr/bin/env python3\n"
            "import sys, json\n"
            "print(json.dumps({'argv1': sys.argv[1]}, sort_keys=True, separators=(',', ':')))\n"
        )
        make_tool_script(root, "tool.py", body)
        payload = "a && b; $(whoami) | c"
        entry = {"command": [PY, "tool.py", payload], "report_mode": "stdout"}
        result = regress.run_tool(root, entry, timeout=10)
        self.assertTrue(result["ok"])
        expected_hash, _ = canonical_report_hash({"argv1": payload})
        self.assertEqual(result["actual_report_sha256"], expected_hash)


# ---------------------------------------------------------------------------
# evaluate_tool
# ---------------------------------------------------------------------------

class TestEvaluateTool(TempRootMixin, unittest.TestCase):
    def _entry(self, exit_code=0, sha=None, mode="stdout", extra_argv=None):
        cmd = [PY, "tool.py"] + (extra_argv or [])
        if mode == "file":
            cmd += ["-o", "{REPORT}"]
        return {
            "status": "baselined",
            "command": cmd,
            "report_mode": mode,
            "expected_exit_code": exit_code,
            "expected_report_sha256": sha,
        }

    def test_clean(self):
        root = self.make_root()
        os.makedirs(os.path.join(root, "toolA"))
        make_simple_tool(os.path.join(root, "toolA"), {"ok": True})
        h, _ = canonical_report_hash({"ok": True})
        entry = self._entry(exit_code=0, sha=h)
        result = regress.evaluate_tool("toolA", root, entry, {"toolA"}, timeout=10)
        self.assertEqual(result["status"], "clean")
        self.assertEqual(result["drift_codes"], [])

    def test_exit_code_drift_only(self):
        root = self.make_root()
        os.makedirs(os.path.join(root, "toolA"))
        make_simple_tool(os.path.join(root, "toolA"), {"ok": True})
        h, _ = canonical_report_hash({"ok": True})
        entry = self._entry(exit_code=9, sha=h)
        result = regress.evaluate_tool("toolA", root, entry, {"toolA"}, timeout=10)
        self.assertEqual(result["drift_codes"], [regress.DRIFT_EXIT_CODE])

    def test_hash_drift_only(self):
        root = self.make_root()
        os.makedirs(os.path.join(root, "toolA"))
        make_simple_tool(os.path.join(root, "toolA"), {"ok": True})
        entry = self._entry(exit_code=0, sha="0" * 64)
        result = regress.evaluate_tool("toolA", root, entry, {"toolA"}, timeout=10)
        self.assertEqual(result["drift_codes"], [regress.DRIFT_HASH])

    def test_both_exit_and_hash_drift(self):
        root = self.make_root()
        os.makedirs(os.path.join(root, "toolA"))
        make_simple_tool(os.path.join(root, "toolA"), {"ok": True})
        entry = self._entry(exit_code=9, sha="0" * 64)
        result = regress.evaluate_tool("toolA", root, entry, {"toolA"}, timeout=10)
        self.assertEqual(set(result["drift_codes"]), {regress.DRIFT_EXIT_CODE, regress.DRIFT_HASH})

    def test_null_hash_always_drifts(self):
        root = self.make_root()
        os.makedirs(os.path.join(root, "toolA"))
        make_simple_tool(os.path.join(root, "toolA"), {"ok": True})
        entry = self._entry(exit_code=0, sha=None)
        result = regress.evaluate_tool("toolA", root, entry, {"toolA"}, timeout=10)
        self.assertEqual(result["drift_codes"], [regress.DRIFT_HASH])

    def test_tool_missing(self):
        root = self.make_root()
        entry = self._entry(exit_code=0, sha="0" * 64)
        result = regress.evaluate_tool("ghost", root, entry, set(), timeout=10)
        self.assertEqual(result["drift_codes"], [regress.DRIFT_TOOL_MISSING])
        self.assertEqual(result["status"], "drift")

    def test_unbaselined_tool(self):
        root = self.make_root()
        result = regress.evaluate_tool("newtool", root, None, {"newtool"}, timeout=10)
        self.assertEqual(result["drift_codes"], [regress.DRIFT_UNBASELINED])

    def test_unbaselineable_present_is_skipped_not_drift(self):
        root = self.make_root()
        entry = {"status": "unbaselineable", "reason": "no deterministic report"}
        result = regress.evaluate_tool("t", root, entry, {"t"}, timeout=10)
        self.assertEqual(result["status"], "skipped_unbaselineable")
        self.assertEqual(result["drift_codes"], [])

    def test_unbaselineable_missing_dir_is_tool_missing(self):
        root = self.make_root()
        entry = {"status": "unbaselineable", "reason": "no deterministic report"}
        result = regress.evaluate_tool("t", root, entry, set(), timeout=10)
        self.assertEqual(result["drift_codes"], [regress.DRIFT_TOOL_MISSING])
        self.assertEqual(result["status"], "drift")

    def test_execution_error_command_not_found(self):
        root = self.make_root()
        os.makedirs(os.path.join(root, "toolA"))
        entry = self._entry(exit_code=0, sha="0" * 64)
        entry["command"] = ["no_such_binary_zzz", "tool.py"]
        result = regress.evaluate_tool("toolA", root, entry, {"toolA"}, timeout=10)
        self.assertEqual(result["drift_codes"], [regress.DRIFT_EXEC_ERROR])

    def test_execution_error_report_missing_also_flags_exit_drift_if_differs(self):
        root = self.make_root()
        os.makedirs(os.path.join(root, "toolA"))
        entry = self._entry(exit_code=0, sha="0" * 64, mode="file")
        # tool.py deliberately absent -> python3 exits 2, no report created
        result = regress.evaluate_tool("toolA", root, entry, {"toolA"}, timeout=10)
        self.assertIn(regress.DRIFT_EXEC_ERROR, result["drift_codes"])
        self.assertIn(regress.DRIFT_EXIT_CODE, result["drift_codes"])

    def test_stdout_mode_clean(self):
        root = self.make_root()
        os.makedirs(os.path.join(root, "toolA"))
        make_simple_tool(os.path.join(root, "toolA"), {"m": "stdout"})
        h, _ = canonical_report_hash({"m": "stdout"})
        entry = self._entry(exit_code=0, sha=h, mode="stdout")
        result = regress.evaluate_tool("toolA", root, entry, {"toolA"}, timeout=10)
        self.assertEqual(result["status"], "clean")

    def test_result_has_no_absolute_path_fields(self):
        root = self.make_root()
        os.makedirs(os.path.join(root, "toolA"))
        make_simple_tool(os.path.join(root, "toolA"), {"ok": True})
        h, _ = canonical_report_hash({"ok": True})
        entry = self._entry(exit_code=0, sha=h)
        result = regress.evaluate_tool("toolA", root, entry, {"toolA"}, timeout=10)
        blob = json.dumps(result)
        self.assertNotIn(root, blob)


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------

class TestBuildReport(TempRootMixin, unittest.TestCase):
    def test_empty_root_empty_baselines(self):
        root = self.make_root()
        report, any_drift = regress.build_report(root, {}, timeout=10)
        self.assertFalse(any_drift)
        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["tools_checked"], 0)
        self.assertEqual(report["results"], [])

    def test_results_sorted_by_tool_name(self):
        root = self.make_root()
        for name in ["zeta", "alpha", "mid"]:
            d = os.path.join(root, name)
            os.makedirs(d)
            make_simple_tool(d, {"n": name})
        baselines = {}
        for name in ["zeta", "alpha", "mid"]:
            h, _ = canonical_report_hash({"n": name})
            baselines[name] = {"status": "baselined", "command": [PY, "tool.py"], "report_mode": "stdout", "expected_exit_code": 0, "expected_report_sha256": h}
        report, any_drift = regress.build_report(root, baselines, timeout=10)
        self.assertFalse(any_drift)
        self.assertEqual([r["tool"] for r in report["results"]], ["alpha", "mid", "zeta"])

    def test_summary_counts(self):
        root = self.make_root()
        for name in ["clean_tool", "drift_tool"]:
            d = os.path.join(root, name)
            os.makedirs(d)
            make_simple_tool(d, {"n": name})
        h_clean, _ = canonical_report_hash({"n": "clean_tool"})
        baselines = {
            "clean_tool": {"status": "baselined", "command": [PY, "tool.py"], "report_mode": "stdout", "expected_exit_code": 0, "expected_report_sha256": h_clean},
            "drift_tool": {"status": "baselined", "command": [PY, "tool.py"], "report_mode": "stdout", "expected_exit_code": 0, "expected_report_sha256": "0" * 64},
        }
        report, any_drift = regress.build_report(root, baselines, timeout=10)
        self.assertTrue(any_drift)
        self.assertEqual(report["summary"]["clean"], 1)
        self.assertEqual(report["summary"]["drift"], 1)
        self.assertEqual(report["drift_counts"][regress.DRIFT_HASH], 1)

    def test_tools_checked_is_union_of_baseline_and_present(self):
        root = self.make_root()
        d = os.path.join(root, "present_only")
        os.makedirs(d)
        make_simple_tool(d, {"x": 1})
        baselines = {
            "baseline_only": {"status": "baselined", "command": [PY, "tool.py"], "report_mode": "stdout", "expected_exit_code": 0, "expected_report_sha256": "0" * 64},
        }
        report, any_drift = regress.build_report(root, baselines, timeout=10)
        self.assertEqual(report["tools_checked"], 2)
        names = {r["tool"] for r in report["results"]}
        self.assertEqual(names, {"present_only", "baseline_only"})

    def test_all_clean_status_clean(self):
        root = self.make_root()
        d = os.path.join(root, "t")
        os.makedirs(d)
        make_simple_tool(d, {"a": 1})
        h, _ = canonical_report_hash({"a": 1})
        baselines = {"t": {"status": "baselined", "command": [PY, "tool.py"], "report_mode": "stdout", "expected_exit_code": 0, "expected_report_sha256": h}}
        report, any_drift = regress.build_report(root, baselines, timeout=10)
        self.assertEqual(report["status"], "clean")
        self.assertFalse(any_drift)

    def test_report_is_json_serializable(self):
        root = self.make_root()
        report, _ = regress.build_report(root, {}, timeout=10)
        dumped = json.dumps(report)
        round_tripped = json.loads(dumped)
        self.assertEqual(round_tripped, report)
        self.assertEqual(round_tripped["status"], "clean")
        self.assertEqual(round_tripped["tools_checked"], 0)
        self.assertEqual(round_tripped["results"], [])

    def test_report_has_schema_version_and_tool_name(self):
        root = self.make_root()
        report, _ = regress.build_report(root, {}, timeout=10)
        self.assertEqual(report["schema_version"], regress.SCHEMA_VERSION)
        self.assertEqual(report["tool"], regress.TOOL_NAME)

    def test_no_duration_or_timestamp_keys_anywhere(self):
        root = self.make_root()
        d = os.path.join(root, "t")
        os.makedirs(d)
        make_simple_tool(d, {"a": 1})
        h, _ = canonical_report_hash({"a": 1})
        baselines = {"t": {"status": "baselined", "command": [PY, "tool.py"], "report_mode": "stdout", "expected_exit_code": 0, "expected_report_sha256": h}}
        report, _ = regress.build_report(root, baselines, timeout=10)
        blob = json.dumps(report).lower()
        for banned in ("duration", "elapsed", "timestamp", "wall_clock", "started_at", "finished_at"):
            self.assertNotIn(banned, blob)

    def test_no_absolute_root_path_in_report(self):
        root = self.make_root()
        d = os.path.join(root, "t")
        os.makedirs(d)
        make_simple_tool(d, {"a": 1})
        h, _ = canonical_report_hash({"a": 1})
        baselines = {"t": {"status": "baselined", "command": [PY, "tool.py"], "report_mode": "stdout", "expected_exit_code": 0, "expected_report_sha256": h}}
        report, _ = regress.build_report(root, baselines, timeout=10)
        blob = json.dumps(report)
        self.assertNotIn(root, blob)

    def test_drift_counts_keys_cover_all_codes(self):
        root = self.make_root()
        report, _ = regress.build_report(root, {}, timeout=10)
        self.assertEqual(set(report["drift_counts"].keys()), regress.ALL_DRIFT_CODES)


# ---------------------------------------------------------------------------
# CLI end-to-end, against the generated fixtures/ directory
# ---------------------------------------------------------------------------

class TestCLIFixturesOk(unittest.TestCase):
    def test_ok_baselines_exit_zero(self):
        code, out, err = run_cli(["--root", "fixtures", "--baselines", "fixtures/baselines_ok.json"], cwd=HERE)
        self.assertEqual(code, 0, msg=err + out)

    def test_ok_baselines_status_clean(self):
        code, out, _ = run_cli(["--root", "fixtures", "--baselines", "fixtures/baselines_ok.json"], cwd=HERE)
        report = json.loads(out)
        self.assertEqual(report["status"], "clean")

    def test_ok_baselines_reports_skipped_unbaselineable(self):
        code, out, _ = run_cli(["--root", "fixtures", "--baselines", "fixtures/baselines_ok.json"], cwd=HERE)
        report = json.loads(out)
        self.assertEqual(report["summary"]["skipped_unbaselineable"], 2)


class TestCLIFixturesDrift(unittest.TestCase):
    def test_drift_exit_one(self):
        code, out, err = run_cli(["--root", "fixtures", "--baselines", "fixtures/baselines_drift.json"], cwd=HERE)
        self.assertEqual(code, 1, msg=err + out)

    def test_drift_status_drift(self):
        _, out, _ = run_cli(["--root", "fixtures", "--baselines", "fixtures/baselines_drift.json"], cwd=HERE)
        report = json.loads(out)
        self.assertEqual(report["status"], "drift")

    def test_drift_identifies_exit_code_drift_tool(self):
        _, out, _ = run_cli(["--root", "fixtures", "--baselines", "fixtures/baselines_drift.json"], cwd=HERE)
        report = json.loads(out)
        by_name = {r["tool"]: r for r in report["results"]}
        self.assertIn(regress.DRIFT_EXIT_CODE, by_name["tool_exit_drift"]["drift_codes"])

    def test_drift_identifies_hash_drift_tool(self):
        _, out, _ = run_cli(["--root", "fixtures", "--baselines", "fixtures/baselines_drift.json"], cwd=HERE)
        report = json.loads(out)
        by_name = {r["tool"]: r for r in report["results"]}
        self.assertIn(regress.DRIFT_HASH, by_name["tool_hash_drift"]["drift_codes"])

    def test_drift_identifies_execution_error_tool(self):
        _, out, _ = run_cli(["--root", "fixtures", "--baselines", "fixtures/baselines_drift.json"], cwd=HERE)
        report = json.loads(out)
        by_name = {r["tool"]: r for r in report["results"]}
        self.assertIn(regress.DRIFT_EXEC_ERROR, by_name["tool_error"]["drift_codes"])

    def test_drift_identifies_null_hash_tool(self):
        _, out, _ = run_cli(["--root", "fixtures", "--baselines", "fixtures/baselines_drift.json"], cwd=HERE)
        report = json.loads(out)
        by_name = {r["tool"]: r for r in report["results"]}
        self.assertIn(regress.DRIFT_HASH, by_name["tool_null_hash_demo"]["drift_codes"])

    def test_drift_identifies_tool_missing(self):
        _, out, _ = run_cli(["--root", "fixtures", "--baselines", "fixtures/baselines_drift.json"], cwd=HERE)
        report = json.loads(out)
        by_name = {r["tool"]: r for r in report["results"]}
        self.assertIn(regress.DRIFT_TOOL_MISSING, by_name["ghost_tool"]["drift_codes"])

    def test_clean_tools_still_reported_clean(self):
        _, out, _ = run_cli(["--root", "fixtures", "--baselines", "fixtures/baselines_drift.json"], cwd=HERE)
        report = json.loads(out)
        by_name = {r["tool"]: r for r in report["results"]}
        self.assertEqual(by_name["tool_ok"]["status"], "clean")
        self.assertEqual(by_name["tool_shell_meta"]["status"], "clean")
        self.assertEqual(by_name["tool_stdout"]["status"], "clean")

    def test_repeated_runs_byte_identical_via_output_file(self):
        with tempfile.TemporaryDirectory() as td:
            out1 = os.path.join(td, "r1.json")
            out2 = os.path.join(td, "r2.json")
            c1, _, _ = run_cli(["--root", "fixtures", "--baselines", "fixtures/baselines_drift.json", "-o", out1], cwd=HERE)
            c2, _, _ = run_cli(["--root", "fixtures", "--baselines", "fixtures/baselines_drift.json", "-o", out2], cwd=HERE)
            self.assertEqual(c1, 1)
            self.assertEqual(c2, 1)
            with open(out1, "rb") as f1, open(out2, "rb") as f2:
                self.assertEqual(f1.read(), f2.read())

    def test_output_report_has_no_absolute_paths(self):
        with tempfile.TemporaryDirectory() as td:
            out1 = os.path.join(td, "r1.json")
            run_cli(["--root", "fixtures", "--baselines", "fixtures/baselines_drift.json", "-o", out1], cwd=HERE)
            with open(out1, "r", encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("/tmp", text)
            self.assertNotIn("/home", text)
            self.assertNotIn(HERE, text)

    def test_output_file_ends_with_single_trailing_newline(self):
        with tempfile.TemporaryDirectory() as td:
            out1 = os.path.join(td, "r1.json")
            run_cli(["--root", "fixtures", "--baselines", "fixtures/baselines_drift.json", "-o", out1], cwd=HERE)
            with open(out1, "rb") as fh:
                data = fh.read()
            self.assertTrue(data.endswith(b"\n"))
            self.assertFalse(data.endswith(b"\n\n"))


class TestCLISetupErrors(unittest.TestCase):
    def test_nonexistent_baselines_exit_two(self):
        code, out, err = run_cli(["--root", "fixtures", "--baselines", "/nonexistent.json"], cwd=HERE)
        self.assertEqual(code, 2)
        report = json.loads(out)
        self.assertEqual(report["status"], "error")

    def test_malformed_baselines_json_exit_two(self):
        with tempfile.TemporaryDirectory() as td:
            bpath = os.path.join(td, "b.json")
            write(bpath, "{not valid json")
            code, out, err = run_cli(["--root", "fixtures", "--baselines", bpath], cwd=HERE)
            self.assertEqual(code, 2)

    def test_baselines_missing_tools_key_exit_two(self):
        with tempfile.TemporaryDirectory() as td:
            bpath = os.path.join(td, "b.json")
            write(bpath, json.dumps({"nope": {}}))
            code, out, err = run_cli(["--root", "fixtures", "--baselines", bpath], cwd=HERE)
            self.assertEqual(code, 2)

    def test_nonexistent_root_exit_two(self):
        with tempfile.TemporaryDirectory() as td:
            bpath = os.path.join(td, "b.json")
            write(bpath, json.dumps({"tools": {}}))
            code, out, err = run_cli(["--root", "/no/such/dir/xyz", "--baselines", bpath], cwd=HERE)
            self.assertEqual(code, 2)

    def test_root_is_a_file_not_dir_exit_two(self):
        with tempfile.TemporaryDirectory() as td:
            bpath = os.path.join(td, "b.json")
            write(bpath, json.dumps({"tools": {}}))
            fpath = os.path.join(td, "afile")
            write(fpath, "x")
            code, out, err = run_cli(["--root", fpath, "--baselines", bpath], cwd=HERE)
            self.assertEqual(code, 2)

    def test_baselines_is_a_directory_not_file_exit_two(self):
        with tempfile.TemporaryDirectory() as td:
            dpath = os.path.join(td, "adir")
            os.makedirs(dpath)
            code, out, err = run_cli(["--root", "fixtures", "--baselines", dpath], cwd=HERE)
            self.assertEqual(code, 2)

    def test_output_directory_does_not_exist_exit_two(self):
        code, out, err = run_cli(["--root", "fixtures", "--baselines", "fixtures/baselines_ok.json", "-o", "/no/such/dir/report.json"], cwd=HERE)
        self.assertEqual(code, 2)

    def test_setup_error_report_is_canonical_json(self):
        code, out, err = run_cli(["--root", "fixtures", "--baselines", "/nonexistent.json"], cwd=HERE)
        self.assertTrue(out.endswith("\n"))
        self.assertFalse(out.endswith("\n\n"))
        report = json.loads(out)
        self.assertIn("error", report)

    def test_entry_with_bad_report_mode_exit_two(self):
        with tempfile.TemporaryDirectory() as td:
            bpath = os.path.join(td, "b.json")
            write(bpath, json.dumps({"tools": {"x": {"command": ["a"], "report_mode": "carrier_pigeon", "expected_exit_code": 0}}}))
            code, out, err = run_cli(["--root", "fixtures", "--baselines", bpath], cwd=HERE)
            self.assertEqual(code, 2)


class TestCLIOutputHandling(unittest.TestCase):
    def test_default_writes_to_stdout_when_no_output_flag(self):
        code, out, err = run_cli(["--root", "fixtures", "--baselines", "fixtures/baselines_ok.json"], cwd=HERE)
        self.assertTrue(len(out) > 0)
        json.loads(out)

    def test_output_flag_short_form(self):
        with tempfile.TemporaryDirectory() as td:
            out1 = os.path.join(td, "r.json")
            code, out, err = run_cli(["--root", "fixtures", "--baselines", "fixtures/baselines_ok.json", "-o", out1], cwd=HERE)
            self.assertEqual(out.strip(), "")
            with open(out1) as fh:
                json.load(fh)

    def test_output_flag_long_form(self):
        with tempfile.TemporaryDirectory() as td:
            out1 = os.path.join(td, "r.json")
            code, out, err = run_cli(["--root", "fixtures", "--baselines", "fixtures/baselines_ok.json", "--output", out1], cwd=HERE)
            self.assertEqual(out.strip(), "")
            with open(out1) as fh:
                report = json.load(fh)
            self.assertEqual(report["status"], "clean")
            self.assertEqual(code, 0)

    def test_output_file_living_inside_root_not_picked_up_next_run(self):
        # Edge case: -o writes its report file *inside* --root. Verify that
        # doing so does not turn the report into a phantom "tool" on a
        # subsequent run (discovery only considers directories).
        with tempfile.TemporaryDirectory() as td:
            tool_dir = os.path.join(td, "tool_a")
            os.makedirs(tool_dir)
            make_simple_tool(tool_dir, {"ok": True})
            h, _ = canonical_report_hash({"ok": True})
            bpath = os.path.join(td, "b.json")
            write(bpath, json.dumps({"tools": {"tool_a": {
                "command": [PY, "tool.py"], "report_mode": "stdout",
                "expected_exit_code": 0, "expected_report_sha256": h,
            }}}))
            report_inside_root = os.path.join(td, "report.json")
            code, _, _ = run_cli(["--root", td, "--baselines", bpath, "-o", report_inside_root], cwd=HERE)
            self.assertEqual(code, 0)
            # second run, with the report file now sitting inside --root
            code2, out2, _ = run_cli(["--root", td, "--baselines", bpath], cwd=HERE)
            self.assertEqual(code2, 0)
            report = json.loads(out2)
            self.assertEqual(report["tools_checked"], 1)


class TestCLITimeout(unittest.TestCase):
    def test_timeout_flag_triggers_execution_error(self):
        with tempfile.TemporaryDirectory() as td:
            tool_dir = os.path.join(td, "slow_tool")
            os.makedirs(tool_dir)
            make_tool_script(tool_dir, "tool.py", "import time\ntime.sleep(5)\n")
            bpath = os.path.join(td, "b.json")
            write(bpath, json.dumps({"tools": {"slow_tool": {
                "command": [PY, "tool.py"], "report_mode": "stdout",
                "expected_exit_code": 0, "expected_report_sha256": "0" * 64,
            }}}))
            code, out, err = run_cli(["--root", td, "--baselines", bpath, "--timeout", "1"], cwd=HERE)
            self.assertEqual(code, 1)
            report = json.loads(out)
            by_name = {r["tool"]: r for r in report["results"]}
            self.assertIn(regress.DRIFT_EXEC_ERROR, by_name["slow_tool"]["drift_codes"])
            self.assertIn("timeout", by_name["slow_tool"]["detail"]["execution_error"])

    def test_default_timeout_is_120(self):
        parser = regress.build_arg_parser()
        args = parser.parse_args(["--root", "x"])
        self.assertEqual(args.timeout, 120)


class TestCLIArgParsing(unittest.TestCase):
    def test_default_root_is_dot(self):
        parser = regress.build_arg_parser()
        args = parser.parse_args([])
        self.assertEqual(args.root, ".")

    def test_default_baselines_is_baselines_json(self):
        parser = regress.build_arg_parser()
        args = parser.parse_args([])
        self.assertEqual(args.baselines, "baselines.json")

    def test_default_output_is_none(self):
        parser = regress.build_arg_parser()
        args = parser.parse_args([])
        self.assertIsNone(args.output)

    def test_update_baselines_default_false(self):
        parser = regress.build_arg_parser()
        args = parser.parse_args([])
        self.assertFalse(args.update_baselines)

    def test_update_baselines_flag_true_when_passed(self):
        parser = regress.build_arg_parser()
        args = parser.parse_args(["--update-baselines"])
        self.assertTrue(args.update_baselines)

    def test_custom_root_and_baselines(self):
        parser = regress.build_arg_parser()
        args = parser.parse_args(["--root", "/x", "--baselines", "/y.json"])
        self.assertEqual(args.root, "/x")
        self.assertEqual(args.baselines, "/y.json")


class TestUpdateBaselines(unittest.TestCase):
    def test_update_baselines_rewrites_hash_and_exit_code(self):
        with tempfile.TemporaryDirectory() as td:
            tool_dir = os.path.join(td, "t")
            os.makedirs(tool_dir)
            make_simple_tool(tool_dir, {"v": 1})
            bpath = os.path.join(td, "b.json")
            write(bpath, json.dumps({"tools": {"t": {
                "command": [PY, "tool.py"], "report_mode": "stdout",
                "expected_exit_code": 99, "expected_report_sha256": "0" * 64,
            }}}))
            code, out, err = run_cli(["--root", td, "--baselines", bpath, "--update-baselines"], cwd=HERE)
            self.assertEqual(code, 0)
            with open(bpath) as fh:
                data = json.load(fh)
            self.assertEqual(data["tools"]["t"]["expected_exit_code"], 0)
            h, _ = canonical_report_hash({"v": 1})
            self.assertEqual(data["tools"]["t"]["expected_report_sha256"], h)

    def test_update_baselines_prints_loud_warning_to_stderr(self):
        with tempfile.TemporaryDirectory() as td:
            bpath = os.path.join(td, "b.json")
            write(bpath, json.dumps({"tools": {}}))
            code, out, err = run_cli(["--root", td, "--baselines", bpath, "--update-baselines"], cwd=HERE)
            self.assertIn("WARNING", err)
            self.assertIn("whitewash", err.lower())

    def test_update_baselines_never_runs_without_the_flag(self):
        with tempfile.TemporaryDirectory() as td:
            tool_dir = os.path.join(td, "t")
            os.makedirs(tool_dir)
            make_simple_tool(tool_dir, {"v": 1})
            bpath = os.path.join(td, "b.json")
            original = {"tools": {"t": {
                "command": [PY, "tool.py"], "report_mode": "stdout",
                "expected_exit_code": 99, "expected_report_sha256": "0" * 64,
            }}}
            write(bpath, json.dumps(original))
            run_cli(["--root", td, "--baselines", bpath], cwd=HERE)  # plain regression check
            with open(bpath) as fh:
                data = json.load(fh)
            self.assertEqual(data, original)  # untouched

    def test_update_baselines_skips_unbaselineable_entries(self):
        with tempfile.TemporaryDirectory() as td:
            bpath = os.path.join(td, "b.json")
            write(bpath, json.dumps({"tools": {"t": {"status": "unbaselineable", "reason": "no report"}}}))
            code, out, err = run_cli(["--root", td, "--baselines", bpath, "--update-baselines"], cwd=HERE)
            self.assertEqual(code, 0)
            with open(bpath) as fh:
                data = json.load(fh)
            self.assertEqual(data["tools"]["t"]["status"], "unbaselineable")

    def test_update_baselines_missing_tool_dir_reports_failure_exit_two(self):
        with tempfile.TemporaryDirectory() as td:
            bpath = os.path.join(td, "b.json")
            write(bpath, json.dumps({"tools": {"ghost": {
                "command": [PY, "tool.py"], "report_mode": "stdout",
                "expected_exit_code": 0, "expected_report_sha256": "0" * 64,
            }}}))
            code, out, err = run_cli(["--root", td, "--baselines", bpath, "--update-baselines"], cwd=HERE)
            self.assertEqual(code, 2)
            self.assertIn("FAILED", err)

    def test_update_baselines_output_is_pretty_printed_and_sorted(self):
        with tempfile.TemporaryDirectory() as td:
            tool_dir_b = os.path.join(td, "b_tool")
            tool_dir_a = os.path.join(td, "a_tool")
            os.makedirs(tool_dir_a)
            os.makedirs(tool_dir_b)
            make_simple_tool(tool_dir_a, {"v": 1})
            make_simple_tool(tool_dir_b, {"v": 2})
            bpath = os.path.join(td, "b.json")
            write(bpath, json.dumps({"tools": {
                "b_tool": {"command": [PY, "tool.py"], "report_mode": "stdout", "expected_exit_code": 0, "expected_report_sha256": None},
                "a_tool": {"command": [PY, "tool.py"], "report_mode": "stdout", "expected_exit_code": 0, "expected_report_sha256": None},
            }}))
            run_cli(["--root", td, "--baselines", bpath, "--update-baselines"], cwd=HERE)
            with open(bpath) as fh:
                text = fh.read()
            self.assertIn("\n", text)  # pretty-printed, not single-line
            data = json.loads(text)
            self.assertIsNotNone(data["tools"]["a_tool"]["expected_report_sha256"])
            self.assertIsNotNone(data["tools"]["b_tool"]["expected_report_sha256"])

    def test_update_baselines_then_regression_check_is_clean(self):
        with tempfile.TemporaryDirectory() as td:
            tool_dir = os.path.join(td, "t")
            os.makedirs(tool_dir)
            make_simple_tool(tool_dir, {"v": 1})
            bpath = os.path.join(td, "b.json")
            write(bpath, json.dumps({"tools": {"t": {
                "command": [PY, "tool.py"], "report_mode": "stdout",
                "expected_exit_code": 99, "expected_report_sha256": "0" * 64,
            }}}))
            code1, _, _ = run_cli(["--root", td, "--baselines", bpath, "--update-baselines"], cwd=HERE)
            self.assertEqual(code1, 0)
            code2, out2, _ = run_cli(["--root", td, "--baselines", bpath], cwd=HERE)
            self.assertEqual(code2, 0)
            self.assertEqual(json.loads(out2)["status"], "clean")


class TestSortedDiscoveryEndToEnd(unittest.TestCase):
    def test_results_order_matches_sorted_tool_names_regardless_of_fs_order(self):
        with tempfile.TemporaryDirectory() as td:
            names = ["mango", "apple", "zebra", "kiwi"]
            baselines = {"tools": {}}
            for n in names:
                d = os.path.join(td, n)
                os.makedirs(d)
                make_simple_tool(d, {"n": n})
                h, _ = canonical_report_hash({"n": n})
                baselines["tools"][n] = {
                    "command": [PY, "tool.py"], "report_mode": "stdout",
                    "expected_exit_code": 0, "expected_report_sha256": h,
                }
            bpath = os.path.join(td, "b.json")
            write(bpath, json.dumps(baselines))
            code, out, err = run_cli(["--root", td, "--baselines", bpath], cwd=HERE)
            self.assertEqual(code, 0, msg=err)
            report = json.loads(out)
            self.assertEqual([r["tool"] for r in report["results"]], sorted(names))


class TestMainFunctionDirectly(unittest.TestCase):
    def test_main_returns_int(self):
        code = regress.main(["--root", "fixtures", "--baselines", "fixtures/baselines_ok.json"])
        self.assertIsInstance(code, int)

    def test_main_zero_on_ok_fixture(self):
        cwd = os.getcwd()
        try:
            os.chdir(HERE)
            code = regress.main(["--root", "fixtures", "--baselines", "fixtures/baselines_ok.json"])
            self.assertEqual(code, 0)
        finally:
            os.chdir(cwd)


class TestStdlibOnlyImports(unittest.TestCase):
    def test_no_third_party_imports_in_regress_module(self):
        with open(os.path.join(HERE, "regress.py"), encoding="utf-8") as fh:
            src = fh.read()
        stdlib_allowed = {"argparse", "hashlib", "json", "os", "shlex", "subprocess", "sys", "tempfile"}
        import ast
        tree = ast.parse(src)
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module.split(".")[0])
        self.assertTrue(found.issubset(stdlib_allowed), found - stdlib_allowed)


if __name__ == "__main__":
    unittest.main()
