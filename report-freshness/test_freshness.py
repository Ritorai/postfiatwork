#!/usr/bin/env python3
"""test_freshness.py -- unittest suite for freshness.py.

Deliberately does NOT hardcode any fact about the surrounding repository
(directory counts, specific committed byte content, current tool states)
except the fixed, author-controlled shape of report-freshness/'s own
manifest.json (its entry count and the regenerable/pinned split are
things we authored, not things we observed by scanning the repository).
Every test either:

  (a) exercises freshness.py against synthetic, self-contained fixture
      repositories built fresh in a tempdir per test (the large majority
      of tests below), or
  (b) exercises it against the REAL manifest.json/repository but only
      asserts *invariants* ("at least 3 entries", "every state is a
      valid state", "the two locations agree", "no pinned entry ever
      invokes its generator") rather than specific state values that
      could legitimately change as the repository evolves. That is the
      whole point of this tool -- state changing IS the signal, not a
      test failure.

Run with:  python3 -m unittest test_freshness -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import freshness  # noqa: E402


THIS_DIR = os.path.dirname(os.path.abspath(__file__))
FRESHNESS_PY = os.path.join(THIS_DIR, "freshness.py")
REAL_MANIFEST = os.path.join(THIS_DIR, "manifest.json")
REAL_REPO_ROOT = os.path.dirname(THIS_DIR)


def run_cli(args, cwd=None, timeout=60):
    proc = subprocess.run(
        [sys.executable, FRESHNESS_PY] + args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def write_bytes(path, data):
    with open(path, "wb") as fh:
        fh.write(data)


# GEN_SCRIPT modes:
#   write   -- write ctrl["content"] (latin-1 decoded) to -o, exit ctrl["exit_code"]
#   nowrite -- exit ctrl["exit_code"] without writing anything
#   sleep   -- sleep ctrl["seconds"], exit 0
#   poison  -- write a marker file INSIDE the tool directory (a real,
#              detectable side effect outside any scratch dir) and exit
#              77 ("fails loudly"). Used to prove a pinned entry's
#              generator is never invoked.
GEN_SCRIPT = '''\
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "ctrl.json"), "r", encoding="utf-8") as fh:
    ctrl = json.load(fh)

mode = ctrl["mode"]

if mode == "sleep":
    time.sleep(ctrl["seconds"])
    sys.exit(0)

if mode == "poison":
    with open(os.path.join(HERE, "INVOKED.marker"), "w", encoding="utf-8") as fh:
        fh.write("this generator was invoked and it should not have been\\n")
    sys.exit(77)

out_path = sys.argv[sys.argv.index("-o") + 1]

if mode == "write":
    with open(out_path, "wb") as fh:
        fh.write(ctrl["content"].encode("latin-1"))
    sys.exit(ctrl["exit_code"])

if mode == "nowrite":
    sys.exit(ctrl["exit_code"])

raise SystemExit("unknown mode: %r" % mode)
'''


def make_tool(root, tool_name, mode, exit_code=0, content="", seconds=0):
    """Create a synthetic tool directory under `root` with a controllable
    gen.py generator. Returns the tool directory path."""
    tool_dir = os.path.join(root, tool_name)
    os.makedirs(tool_dir, exist_ok=True)
    with open(os.path.join(tool_dir, "gen.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(GEN_SCRIPT)
    write_json(
        os.path.join(tool_dir, "ctrl.json"),
        {"mode": mode, "exit_code": exit_code, "content": content, "seconds": seconds},
    )
    return tool_dir


def make_regenerable_entry(tool_name, committed_report="report.json", expected_exit_code=0, inputs=None, argv=None):
    return {
        "id": "%s:%s" % (tool_name, committed_report),
        "tool": tool_name,
        "kind": "regenerable",
        "generation": {"argv": argv or ["python3", "gen.py", "-o", "{OUT}"], "cwd": tool_name},
        "committed_report": "%s/%s" % (tool_name, committed_report),
        "expected_exit_code": expected_exit_code,
        "inputs": inputs or [],
    }


# Backwards-compatible short alias used throughout this file.
make_entry = make_regenerable_entry


def make_pinned_entry(tool_name, committed_report="report.json", inputs=None, argv=None, expected_exit_code=None):
    entry = {
        "id": "%s:%s" % (tool_name, committed_report),
        "tool": tool_name,
        "kind": "pinned",
        "committed_report": "%s/%s" % (tool_name, committed_report),
        "inputs": inputs or [],
    }
    if argv is not None:
        entry["generation"] = {"argv": argv, "cwd": tool_name}
    if expected_exit_code is not None:
        entry["expected_exit_code"] = expected_exit_code
    return entry


def make_repo(root, entries, manifest_name="manifest.json"):
    manifest_path = os.path.join(root, manifest_name)
    write_json(manifest_path, {"schema_version": 2, "entries": entries})
    return manifest_path


def path_leak_markers():
    return [tempfile.gettempdir(), os.path.expanduser("~"), REAL_REPO_ROOT]


# ==========================================================================
# canonical_json / sha256 / is_exit_code
# ==========================================================================

class TestCanonicalJson(unittest.TestCase):
    def test_sorted_keys(self):
        text = freshness.canonical_json({"b": 1, "a": 2})
        self.assertTrue(text.index('"a"') < text.index('"b"'))

    def test_tight_separators(self):
        text = freshness.canonical_json({"a": [1, 2], "b": {"c": 1}})
        self.assertNotIn(", ", text)
        self.assertNotIn(": ", text)

    def test_trailing_newline(self):
        text = freshness.canonical_json({"a": 1})
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(text.count("\n"), 1)

    def test_ascii_only(self):
        text = freshness.canonical_json({"a": "café"})
        self.assertTrue(all(ord(ch) < 128 for ch in text))

    def test_round_trip(self):
        obj = {"z": [1, 2, {"y": None}], "a": True}
        parsed = json.loads(freshness.canonical_json(obj))
        self.assertEqual(parsed, obj)

    def test_deterministic_across_calls(self):
        obj = {"k" + str(i): i for i in range(20)}
        self.assertEqual(freshness.canonical_json(obj), freshness.canonical_json(dict(reversed(list(obj.items())))))


class TestShaHelpers(unittest.TestCase):
    def test_known_vector_empty(self):
        self.assertEqual(
            freshness.sha256_hex(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )

    def test_known_vector_abc(self):
        self.assertEqual(
            freshness.sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )

    def test_different_bytes_different_hash(self):
        self.assertNotEqual(freshness.sha256_hex(b"a"), freshness.sha256_hex(b"b"))

    def test_binary_safe(self):
        data = bytes(range(256))
        self.assertEqual(len(freshness.sha256_hex(data)), 64)


class TestIsExitCode(unittest.TestCase):
    def test_int_zero_is_valid(self):
        self.assertTrue(freshness.is_exit_code(0))

    def test_negative_int_is_valid(self):
        self.assertTrue(freshness.is_exit_code(-9))

    def test_bool_true_rejected(self):
        self.assertFalse(freshness.is_exit_code(True))

    def test_bool_false_rejected(self):
        self.assertFalse(freshness.is_exit_code(False))

    def test_string_rejected(self):
        self.assertFalse(freshness.is_exit_code("1"))

    def test_float_rejected(self):
        self.assertFalse(freshness.is_exit_code(1.0))

    def test_none_rejected(self):
        self.assertFalse(freshness.is_exit_code(None))


# ==========================================================================
# Manifest validation -- malformed manifests (dynamically generated)
# ==========================================================================

def _base_entry():
    return {
        "id": "toolA:report.json",
        "tool": "toolA",
        "kind": "regenerable",
        "generation": {"argv": ["python3", "gen.py", "-o", "{OUT}"], "cwd": "toolA"},
        "committed_report": "toolA/report.json",
        "expected_exit_code": 0,
    }


def _base_pinned_entry():
    return {
        "id": "toolA:report.json",
        "tool": "toolA",
        "kind": "pinned",
        "committed_report": "toolA/report.json",
    }


def _base_doc():
    return {"schema_version": 2, "entries": [_base_entry()]}


class TestManifestValidationErrors(unittest.TestCase):
    def _expect_setup_error(self, doc_or_text, is_raw_text=False):
        with tempfile.TemporaryDirectory(prefix="freshness_manifest_") as tmp:
            path = os.path.join(tmp, "manifest.json")
            if is_raw_text:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(doc_or_text)
            else:
                write_json(path, doc_or_text)
            with self.assertRaises(freshness.SetupError):
                freshness.load_manifest(path)


MALFORMED_CASES = {}


def _reg(name, mutate):
    MALFORMED_CASES[name] = mutate


_reg("top_level_not_object", lambda: [])
_reg("top_level_is_string", lambda: "nope")
_reg("missing_entries_key", lambda: {"schema_version": 1})
_reg("entries_not_a_list", lambda: {"entries": {}})
_reg("entries_empty_list", lambda: {"entries": []})
_reg("entry_not_object", lambda: {"entries": ["nope"]})
_reg("entry_missing_id", lambda: {"entries": [{k: v for k, v in _base_entry().items() if k != "id"}]})
_reg("entry_missing_tool", lambda: {"entries": [{k: v for k, v in _base_entry().items() if k != "tool"}]})
_reg("entry_missing_kind", lambda: {"entries": [{k: v for k, v in _base_entry().items() if k != "kind"}]})
_reg(
    "entry_missing_committed_report",
    lambda: {"entries": [{k: v for k, v in _base_entry().items() if k != "committed_report"}]},
)
_reg(
    "regenerable_missing_generation",
    lambda: {"entries": [{k: v for k, v in _base_entry().items() if k != "generation"}]},
)
_reg(
    "regenerable_missing_expected_exit_code",
    lambda: {"entries": [{k: v for k, v in _base_entry().items() if k != "expected_exit_code"}]},
)


def _with_entry_field(field, value):
    e = _base_entry()
    e[field] = value
    return {"entries": [e]}


def _with_pinned_field(field, value):
    e = _base_pinned_entry()
    e[field] = value
    return {"entries": [e]}


_reg("id_not_string", lambda: _with_entry_field("id", 5))
_reg("id_empty_string", lambda: _with_entry_field("id", ""))
_reg("tool_not_string", lambda: _with_entry_field("tool", 5))
_reg("tool_empty_string", lambda: _with_entry_field("tool", ""))
_reg("tool_contains_forward_slash", lambda: _with_entry_field("tool", "a/b"))
_reg("tool_contains_backslash", lambda: _with_entry_field("tool", "a\\b"))
_reg("kind_not_string", lambda: _with_entry_field("kind", 5))
_reg("kind_invalid_value", lambda: _with_entry_field("kind", "sometimes"))
_reg("kind_empty_string", lambda: _with_entry_field("kind", ""))
_reg("kind_null", lambda: _with_entry_field("kind", None))
_reg("generation_not_object", lambda: _with_entry_field("generation", ["x"]))
_reg("generation_missing_argv", lambda: _with_entry_field("generation", {"cwd": "toolA"}))
_reg("generation_missing_cwd", lambda: _with_entry_field("generation", {"argv": ["python3", "gen.py", "-o", "{OUT}"]}))
_reg(
    "argv_not_a_list",
    lambda: _with_entry_field("generation", {"argv": "python3 gen.py", "cwd": "toolA"}),
)
_reg("argv_empty_list", lambda: _with_entry_field("generation", {"argv": [], "cwd": "toolA"}))
_reg(
    "argv_contains_non_string",
    lambda: _with_entry_field("generation", {"argv": ["python3", 5, "-o", "{OUT}"], "cwd": "toolA"}),
)
_reg(
    "argv_missing_out_placeholder",
    lambda: _with_entry_field("generation", {"argv": ["python3", "gen.py", "-o", "fixed.json"], "cwd": "toolA"}),
)
_reg("cwd_not_string", lambda: _with_entry_field("generation", {"argv": ["python3", "gen.py", "-o", "{OUT}"], "cwd": 5}))
_reg("cwd_empty_string", lambda: _with_entry_field("generation", {"argv": ["python3", "gen.py", "-o", "{OUT}"], "cwd": ""}))
_reg(
    "cwd_absolute_path",
    lambda: _with_entry_field("generation", {"argv": ["python3", "gen.py", "-o", "{OUT}"], "cwd": "/etc"}),
)
_reg("committed_report_not_string", lambda: _with_entry_field("committed_report", 5))
_reg("committed_report_empty_string", lambda: _with_entry_field("committed_report", ""))
_reg("committed_report_absolute_path", lambda: _with_entry_field("committed_report", "/etc/passwd"))
_reg("expected_exit_code_string", lambda: _with_entry_field("expected_exit_code", "0"))
_reg("expected_exit_code_bool_true", lambda: _with_entry_field("expected_exit_code", True))
_reg("expected_exit_code_bool_false", lambda: _with_entry_field("expected_exit_code", False))
_reg("expected_exit_code_float", lambda: _with_entry_field("expected_exit_code", 1.5))
_reg("inputs_not_a_list", lambda: _with_entry_field("inputs", "a.json"))
_reg("inputs_contains_non_string", lambda: _with_entry_field("inputs", [5]))
_reg("description_not_a_string", lambda: _with_entry_field("description", 5))

# pinned-entry-specific malformed cases: generation/expected_exit_code are
# optional for a pinned entry, but if present they are still validated --
# provenance metadata that lies about its own shape is still a malformed
# manifest.
_reg("pinned_generation_not_object", lambda: _with_pinned_field("generation", ["x"]))
_reg("pinned_generation_missing_argv", lambda: _with_pinned_field("generation", {"cwd": "toolA"}))
_reg("pinned_generation_missing_cwd", lambda: _with_pinned_field("generation", {"argv": ["python3", "gen.py"]}))
_reg("pinned_generation_argv_not_a_list", lambda: _with_pinned_field("generation", {"argv": "x", "cwd": "toolA"}))
_reg("pinned_generation_cwd_absolute_path", lambda: _with_pinned_field("generation", {"argv": ["x"], "cwd": "/etc"}))
_reg("pinned_expected_exit_code_bool", lambda: _with_pinned_field("expected_exit_code", True))
_reg("pinned_expected_exit_code_string", lambda: _with_pinned_field("expected_exit_code", "1"))
_reg("pinned_committed_report_absolute_path", lambda: _with_pinned_field("committed_report", "/etc/passwd"))
_reg("pinned_tool_empty_string", lambda: _with_pinned_field("tool", ""))
_reg("pinned_id_empty_string", lambda: _with_pinned_field("id", ""))


def _make_duplicate_id_doc():
    e1 = _base_entry()
    e2 = _base_entry()
    e2["tool"] = "toolB"
    e2["committed_report"] = "toolB/report.json"
    e2["generation"] = {"argv": ["python3", "gen.py", "-o", "{OUT}"], "cwd": "toolB"}
    # same id as e1 on purpose
    return {"entries": [e1, e2]}


_reg("duplicate_id", _make_duplicate_id_doc)


def _make_duplicate_id_across_kinds_doc():
    e1 = _base_entry()
    e2 = _base_pinned_entry()
    # e2 keeps e1's id on purpose -- duplicate detection must not care about kind
    return {"entries": [e1, e2]}


_reg("duplicate_id_across_kinds", _make_duplicate_id_across_kinds_doc)


def _install_malformed_tests():
    for case_name, factory in MALFORMED_CASES.items():
        def _test(self, factory=factory):
            self._expect_setup_error(factory())

        _test.__name__ = "test_malformed_%s" % case_name
        setattr(TestManifestValidationErrors, _test.__name__, _test)


_install_malformed_tests()


class TestManifestValidationErrorsExtra(unittest.TestCase):
    def test_manifest_file_missing(self):
        with self.assertRaises(freshness.SetupError):
            freshness.load_manifest("/nonexistent/path/manifest.json")

    def test_manifest_file_invalid_json(self):
        with tempfile.TemporaryDirectory(prefix="freshness_manifest_") as tmp:
            path = os.path.join(tmp, "manifest.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            with self.assertRaises(freshness.SetupError):
                freshness.load_manifest(path)


# ==========================================================================
# Manifest validation -- valid manifests
# ==========================================================================

class TestManifestValidationValid(unittest.TestCase):
    def test_loads_single_regenerable_entry(self):
        with tempfile.TemporaryDirectory(prefix="freshness_manifest_") as tmp:
            path = os.path.join(tmp, "manifest.json")
            write_json(path, _base_doc())
            entries = freshness.load_manifest(path)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["id"], "toolA:report.json")
            self.assertEqual(entries[0]["kind"], "regenerable")

    def test_description_defaults_to_none(self):
        with tempfile.TemporaryDirectory(prefix="freshness_manifest_") as tmp:
            path = os.path.join(tmp, "manifest.json")
            write_json(path, _base_doc())
            entries = freshness.load_manifest(path)
            self.assertIsNone(entries[0]["description"])

    def test_inputs_defaults_to_empty_list(self):
        with tempfile.TemporaryDirectory(prefix="freshness_manifest_") as tmp:
            path = os.path.join(tmp, "manifest.json")
            write_json(path, _base_doc())
            entries = freshness.load_manifest(path)
            self.assertEqual(entries[0]["inputs"], [])

    def test_two_entries_unique_ids_ok(self):
        doc = _base_doc()
        e2 = _base_entry()
        e2["id"] = "toolB:report.json"
        e2["tool"] = "toolB"
        doc["entries"].append(e2)
        with tempfile.TemporaryDirectory(prefix="freshness_manifest_") as tmp:
            path = os.path.join(tmp, "manifest.json")
            write_json(path, doc)
            entries = freshness.load_manifest(path)
            self.assertEqual(len(entries), 2)

    def test_explicit_description_and_inputs_preserved(self):
        doc = _base_doc()
        doc["entries"][0]["description"] = "hello"
        doc["entries"][0]["inputs"] = ["a.json", "b.json"]
        with tempfile.TemporaryDirectory(prefix="freshness_manifest_") as tmp:
            path = os.path.join(tmp, "manifest.json")
            write_json(path, doc)
            entries = freshness.load_manifest(path)
            self.assertEqual(entries[0]["description"], "hello")
            self.assertEqual(entries[0]["inputs"], ["a.json", "b.json"])

    def test_loads_pinned_entry_with_no_generation(self):
        with tempfile.TemporaryDirectory(prefix="freshness_manifest_") as tmp:
            path = os.path.join(tmp, "manifest.json")
            write_json(path, {"entries": [_base_pinned_entry()]})
            entries = freshness.load_manifest(path)
            self.assertEqual(entries[0]["kind"], "pinned")
            self.assertIsNone(entries[0]["generation"])
            self.assertIsNone(entries[0]["expected_exit_code"])

    def test_loads_pinned_entry_with_generation_but_no_exit_code(self):
        e = _base_pinned_entry()
        e["generation"] = {"argv": ["python3", "gen.py", "-o", "out.json"], "cwd": "toolA"}
        with tempfile.TemporaryDirectory(prefix="freshness_manifest_") as tmp:
            path = os.path.join(tmp, "manifest.json")
            write_json(path, {"entries": [e]})
            entries = freshness.load_manifest(path)
            self.assertEqual(entries[0]["generation"]["cwd"], "toolA")
            self.assertIsNone(entries[0]["expected_exit_code"])

    def test_loads_pinned_entry_with_generation_and_exit_code(self):
        e = _base_pinned_entry()
        e["generation"] = {"argv": ["python3", "gen.py", "-o", "out.json"], "cwd": "toolA"}
        e["expected_exit_code"] = 1
        with tempfile.TemporaryDirectory(prefix="freshness_manifest_") as tmp:
            path = os.path.join(tmp, "manifest.json")
            write_json(path, {"entries": [e]})
            entries = freshness.load_manifest(path)
            self.assertEqual(entries[0]["expected_exit_code"], 1)

    def test_pinned_generation_argv_does_not_require_out_placeholder(self):
        e = _base_pinned_entry()
        e["generation"] = {"argv": ["python3", "gen.py", "-o", "fixed_name.json"], "cwd": "toolA"}
        with tempfile.TemporaryDirectory(prefix="freshness_manifest_") as tmp:
            path = os.path.join(tmp, "manifest.json")
            write_json(path, {"entries": [e]})
            entries = freshness.load_manifest(path)  # must not raise
            self.assertNotIn("{OUT}", "".join(entries[0]["generation"]["argv"]))

    def test_mixed_regenerable_and_pinned_entries_load(self):
        doc = {"entries": [_base_entry(), _base_pinned_entry()]}
        doc["entries"][1]["id"] = "toolB:report.json"
        doc["entries"][1]["tool"] = "toolB"
        doc["entries"][1]["committed_report"] = "toolB/report.json"
        with tempfile.TemporaryDirectory(prefix="freshness_manifest_") as tmp:
            path = os.path.join(tmp, "manifest.json")
            write_json(path, doc)
            entries = freshness.load_manifest(path)
            kinds = {e["kind"] for e in entries}
            self.assertEqual(kinds, {"regenerable", "pinned"})


# ==========================================================================
# classify_regenerable() / classify_pinned() -- pure decision functions
# ==========================================================================

def _gen_result(launch_error_kind=None, timed_out=False, exit_code=None, output_bytes=None):
    return {
        "launch_error_kind": launch_error_kind,
        "timed_out": timed_out,
        "exit_code": exit_code,
        "output_bytes": output_bytes,
    }


class TestClassifyRegenerablePure(unittest.TestCase):
    def _entry(self, expected=0):
        return {"expected_exit_code": expected}

    def test_tool_missing_wins_over_everything_committed_absent(self):
        state, reason = freshness.classify_regenerable(
            self._entry(0), "tool dir absent", _gen_result(), None, 10
        )
        self.assertEqual(state, freshness.STATE_TOOL_MISSING)
        self.assertIn("tool dir absent", reason)

    def test_tool_missing_wins_over_everything_committed_present(self):
        state, reason = freshness.classify_regenerable(
            self._entry(0), "script absent", _gen_result(), b"x", 10
        )
        self.assertEqual(state, freshness.STATE_TOOL_MISSING)

    def test_launch_error_committed_absent(self):
        state, _ = freshness.classify_regenerable(
            self._entry(0), None, _gen_result(launch_error_kind="FileNotFoundError"), None, 10
        )
        self.assertEqual(state, freshness.STATE_GENERATION_FAILED)

    def test_launch_error_committed_present(self):
        state, reason = freshness.classify_regenerable(
            self._entry(0), None, _gen_result(launch_error_kind="PermissionError"), b"x", 10
        )
        self.assertEqual(state, freshness.STATE_GENERATION_FAILED)
        self.assertIn("PermissionError", reason)

    def test_timed_out_committed_absent(self):
        state, _ = freshness.classify_regenerable(self._entry(0), None, _gen_result(timed_out=True), None, 10)
        self.assertEqual(state, freshness.STATE_GENERATION_FAILED)

    def test_timed_out_committed_present(self):
        state, reason = freshness.classify_regenerable(self._entry(0), None, _gen_result(timed_out=True), b"x", 30)
        self.assertEqual(state, freshness.STATE_GENERATION_FAILED)
        self.assertIn("30", reason)

    def test_exit_mismatch_0_expected_1_actual(self):
        state, _ = freshness.classify_regenerable(self._entry(0), None, _gen_result(exit_code=1, output_bytes=b"x"), b"x", 10)
        self.assertEqual(state, freshness.STATE_GENERATION_FAILED)

    def test_exit_mismatch_1_expected_0_actual(self):
        state, _ = freshness.classify_regenerable(self._entry(1), None, _gen_result(exit_code=0, output_bytes=b"x"), b"x", 10)
        self.assertEqual(state, freshness.STATE_GENERATION_FAILED)

    def test_exit_mismatch_1_expected_2_actual(self):
        state, _ = freshness.classify_regenerable(self._entry(1), None, _gen_result(exit_code=2, output_bytes=b"x"), None, 10)
        self.assertEqual(state, freshness.STATE_GENERATION_FAILED)

    def test_exit_mismatch_signal_negative(self):
        state, _ = freshness.classify_regenerable(self._entry(0), None, _gen_result(exit_code=-9, output_bytes=None), None, 10)
        self.assertEqual(state, freshness.STATE_GENERATION_FAILED)

    def test_exit_mismatch_none_actual_treated_as_mismatch(self):
        state, _ = freshness.classify_regenerable(self._entry(0), None, _gen_result(exit_code=None), None, 10)
        self.assertEqual(state, freshness.STATE_GENERATION_FAILED)

    def test_exit_match_no_output_committed_absent_expected_0(self):
        state, reason = freshness.classify_regenerable(self._entry(0), None, _gen_result(exit_code=0, output_bytes=None), None, 10)
        self.assertEqual(state, freshness.STATE_GENERATION_FAILED)
        self.assertIn("no output", reason)

    def test_exit_match_no_output_committed_present_expected_0(self):
        state, _ = freshness.classify_regenerable(self._entry(0), None, _gen_result(exit_code=0, output_bytes=None), b"x", 10)
        self.assertEqual(state, freshness.STATE_GENERATION_FAILED)

    def test_exit_match_no_output_committed_absent_expected_1(self):
        state, _ = freshness.classify_regenerable(self._entry(1), None, _gen_result(exit_code=1, output_bytes=None), None, 10)
        self.assertEqual(state, freshness.STATE_GENERATION_FAILED)

    def test_exit_match_no_output_committed_present_expected_1(self):
        state, _ = freshness.classify_regenerable(self._entry(1), None, _gen_result(exit_code=1, output_bytes=None), b"x", 10)
        self.assertEqual(state, freshness.STATE_GENERATION_FAILED)

    def test_missing_expected_0(self):
        state, reason = freshness.classify_regenerable(self._entry(0), None, _gen_result(exit_code=0, output_bytes=b"hi"), None, 10)
        self.assertEqual(state, freshness.STATE_MISSING)
        self.assertIn("committed", reason)

    def test_missing_expected_1(self):
        state, _ = freshness.classify_regenerable(self._entry(1), None, _gen_result(exit_code=1, output_bytes=b"hi"), None, 10)
        self.assertEqual(state, freshness.STATE_MISSING)

    def test_match_expected_0(self):
        state, reason = freshness.classify_regenerable(self._entry(0), None, _gen_result(exit_code=0, output_bytes=b"same"), b"same", 10)
        self.assertEqual(state, freshness.STATE_MATCH)
        self.assertIsNone(reason)

    def test_match_expected_1(self):
        state, _ = freshness.classify_regenerable(self._entry(1), None, _gen_result(exit_code=1, output_bytes=b"same"), b"same", 10)
        self.assertEqual(state, freshness.STATE_MATCH)

    def test_stale_expected_0_single_byte_diff(self):
        state, reason = freshness.classify_regenerable(self._entry(0), None, _gen_result(exit_code=0, output_bytes=b"aax"), b"aay", 10)
        self.assertEqual(state, freshness.STATE_STALE)
        self.assertIn("differ", reason)

    def test_stale_expected_1_length_diff(self):
        state, _ = freshness.classify_regenerable(self._entry(1), None, _gen_result(exit_code=1, output_bytes=b"short"), b"much longer content", 10)
        self.assertEqual(state, freshness.STATE_STALE)

    def test_stale_binary_content(self):
        committed = bytes([0, 1, 2, 3, 255])
        regenerated = bytes([0, 1, 2, 3, 254])
        state, _ = freshness.classify_regenerable(self._entry(0), None, _gen_result(exit_code=0, output_bytes=regenerated), committed, 10)
        self.assertEqual(state, freshness.STATE_STALE)


class TestClassifyPinnedPure(unittest.TestCase):
    def test_pinned_present_when_bytes_exist(self):
        state, reason = freshness.classify_pinned(b"some evidence bytes")
        self.assertEqual(state, freshness.STATE_PINNED_PRESENT)
        self.assertIsNone(reason)

    def test_pinned_present_empty_bytes_still_present(self):
        # b"" is falsy but not None -- the file exists and is empty, which
        # is "present", not "missing". Only None (file absent) is missing.
        state, reason = freshness.classify_pinned(b"")
        self.assertEqual(state, freshness.STATE_PINNED_PRESENT)

    def test_pinned_missing_when_bytes_none(self):
        state, reason = freshness.classify_pinned(None)
        self.assertEqual(state, freshness.STATE_PINNED_MISSING)
        self.assertIn("missing", reason)

    def test_pinned_missing_reason_mentions_evidence(self):
        _, reason = freshness.classify_pinned(None)
        self.assertIn("evidence", reason)


# ==========================================================================
# tool_missing_reason()
# ==========================================================================

class TestToolMissingReason(unittest.TestCase):
    def test_cwd_missing(self):
        with tempfile.TemporaryDirectory(prefix="freshness_tmr_") as root:
            entry = make_entry("nope")
            reason = freshness.tool_missing_reason(root, entry)
            self.assertIsNotNone(reason)
            self.assertIn("does not exist", reason)

    def test_cwd_exists_script_missing(self):
        with tempfile.TemporaryDirectory(prefix="freshness_tmr_") as root:
            os.makedirs(os.path.join(root, "toolA"))
            entry = make_entry("toolA")
            reason = freshness.tool_missing_reason(root, entry)
            self.assertIsNotNone(reason)
            self.assertIn("not found", reason)

    def test_cwd_exists_script_present(self):
        with tempfile.TemporaryDirectory(prefix="freshness_tmr_") as root:
            make_tool(root, "toolA", "write", exit_code=0, content="x")
            entry = make_entry("toolA")
            reason = freshness.tool_missing_reason(root, entry)
            self.assertIsNone(reason)

    def test_argv_without_py_token_skips_script_check(self):
        with tempfile.TemporaryDirectory(prefix="freshness_tmr_") as root:
            os.makedirs(os.path.join(root, "toolA"))
            entry = make_entry("toolA", argv=["echo", "{OUT}"])
            reason = freshness.tool_missing_reason(root, entry)
            self.assertIsNone(reason)


# ==========================================================================
# run_generation() -- real subprocess execution
# ==========================================================================

class TestRunGeneration(unittest.TestCase):
    def test_write_mode_exit_0(self):
        with tempfile.TemporaryDirectory(prefix="freshness_rg_") as root:
            make_tool(root, "toolA", "write", exit_code=0, content="hello")
            entry = make_entry("toolA")
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.run_generation(root, entry, temp_root, 30)
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["output_bytes"], b"hello")
            self.assertIsNone(result["launch_error_kind"])
            self.assertFalse(result["timed_out"])

    def test_write_mode_exit_1(self):
        with tempfile.TemporaryDirectory(prefix="freshness_rg_") as root:
            make_tool(root, "toolA", "write", exit_code=1, content="hi")
            entry = make_entry("toolA", expected_exit_code=1)
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.run_generation(root, entry, temp_root, 30)
            self.assertEqual(result["exit_code"], 1)
            self.assertEqual(result["output_bytes"], b"hi")

    def test_nowrite_mode(self):
        with tempfile.TemporaryDirectory(prefix="freshness_rg_") as root:
            make_tool(root, "toolA", "nowrite", exit_code=0)
            entry = make_entry("toolA")
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.run_generation(root, entry, temp_root, 30)
            self.assertIsNone(result["output_bytes"])
            self.assertEqual(result["exit_code"], 0)

    def test_launch_error_bad_interpreter(self):
        with tempfile.TemporaryDirectory(prefix="freshness_rg_") as root:
            make_tool(root, "toolA", "write", exit_code=0, content="x")
            entry = make_entry("toolA", argv=["definitely-not-a-real-interpreter-xyz", "gen.py", "-o", "{OUT}"])
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.run_generation(root, entry, temp_root, 30)
            self.assertIsNotNone(result["launch_error_kind"])
            self.assertIsNone(result["output_bytes"])

    def test_timeout(self):
        with tempfile.TemporaryDirectory(prefix="freshness_rg_") as root:
            make_tool(root, "toolA", "sleep", seconds=5)
            entry = make_entry("toolA")
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.run_generation(root, entry, temp_root, 1)
            self.assertTrue(result["timed_out"])
            self.assertIsNone(result["output_bytes"])

    def test_binary_content_roundtrip(self):
        raw = "".join(chr(b) for b in [0, 1, 2, 250, 251, 255])
        with tempfile.TemporaryDirectory(prefix="freshness_rg_") as root:
            make_tool(root, "toolA", "write", exit_code=0, content=raw)
            entry = make_entry("toolA")
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.run_generation(root, entry, temp_root, 30)
            self.assertEqual(result["output_bytes"], raw.encode("latin-1"))


# ==========================================================================
# evaluate_entry() -- full per-entry integration
# ==========================================================================

class TestEvaluateEntry(unittest.TestCase):
    def test_state_match(self):
        with tempfile.TemporaryDirectory(prefix="freshness_ee_") as root:
            make_tool(root, "toolA", "write", exit_code=0, content="same content")
            write_bytes(os.path.join(root, "toolA", "report.json"), b"same content")
            entry = make_entry("toolA")
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.evaluate_entry(root, entry, temp_root, 30)
            self.assertEqual(result["state"], freshness.STATE_MATCH)
            self.assertEqual(result["kind"], "regenerable")
            self.assertTrue(result["generator_invoked"])
            self.assertEqual(result["committed_sha256"], result["regenerated_sha256"])
            self.assertEqual(result["committed_bytes"], result["regenerated_bytes"])

    def test_state_stale_modified(self):
        with tempfile.TemporaryDirectory(prefix="freshness_ee_") as root:
            make_tool(root, "toolA", "write", exit_code=0, content="new content")
            write_bytes(os.path.join(root, "toolA", "report.json"), b"old content")
            entry = make_entry("toolA")
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.evaluate_entry(root, entry, temp_root, 30)
            self.assertEqual(result["state"], freshness.STATE_STALE)
            self.assertNotEqual(result["committed_sha256"], result["regenerated_sha256"])

    def test_regenerable_entry_still_detects_modification_after_kind_split(self):
        # Explicit re-check of the brief's "modified" case after adding
        # kind -- a regenerable entry that no longer reproduces must
        # still show "stale", exactly like before pinned existed.
        with tempfile.TemporaryDirectory(prefix="freshness_ee_") as root:
            make_tool(root, "toolA", "write", exit_code=0, content="edited-after-commit")
            write_bytes(os.path.join(root, "toolA", "report.json"), b"original-committed-bytes")
            entry = make_entry("toolA")
            self.assertEqual(entry["kind"], "regenerable")
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.evaluate_entry(root, entry, temp_root, 30)
            self.assertEqual(result["state"], freshness.STATE_STALE)
            self.assertTrue(result["generator_invoked"])

    def test_state_missing(self):
        with tempfile.TemporaryDirectory(prefix="freshness_ee_") as root:
            make_tool(root, "toolA", "write", exit_code=0, content="hi")
            entry = make_entry("toolA")
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.evaluate_entry(root, entry, temp_root, 30)
            self.assertEqual(result["state"], freshness.STATE_MISSING)
            self.assertFalse(result["committed_present"])
            self.assertIsNotNone(result["regenerated_sha256"])

    def test_state_generation_failed_unexpected_exit(self):
        with tempfile.TemporaryDirectory(prefix="freshness_ee_") as root:
            make_tool(root, "toolA", "write", exit_code=1, content="hi")
            write_bytes(os.path.join(root, "toolA", "report.json"), b"hi")
            entry = make_entry("toolA", expected_exit_code=0)
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.evaluate_entry(root, entry, temp_root, 30)
            self.assertEqual(result["state"], freshness.STATE_GENERATION_FAILED)
            self.assertEqual(result["actual_exit_code"], 1)

    def test_state_generation_failed_writes_nothing(self):
        with tempfile.TemporaryDirectory(prefix="freshness_ee_") as root:
            make_tool(root, "toolA", "nowrite", exit_code=0)
            write_bytes(os.path.join(root, "toolA", "report.json"), b"hi")
            entry = make_entry("toolA")
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.evaluate_entry(root, entry, temp_root, 30)
            self.assertEqual(result["state"], freshness.STATE_GENERATION_FAILED)
            self.assertIsNone(result["regenerated_sha256"])

    def test_state_generation_failed_launch_error(self):
        with tempfile.TemporaryDirectory(prefix="freshness_ee_") as root:
            make_tool(root, "toolA", "write", exit_code=0, content="hi")
            entry = make_entry("toolA", argv=["not-a-real-interpreter-abc", "gen.py", "-o", "{OUT}"])
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.evaluate_entry(root, entry, temp_root, 30)
            self.assertEqual(result["state"], freshness.STATE_GENERATION_FAILED)
            self.assertIn("launched", result["reason"])

    def test_state_generation_failed_timeout(self):
        with tempfile.TemporaryDirectory(prefix="freshness_ee_") as root:
            make_tool(root, "toolA", "sleep", seconds=5)
            entry = make_entry("toolA")
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.evaluate_entry(root, entry, temp_root, 1)
            self.assertEqual(result["state"], freshness.STATE_GENERATION_FAILED)
            self.assertIn("timed out", result["reason"])

    def test_state_tool_missing_cwd_absent(self):
        with tempfile.TemporaryDirectory(prefix="freshness_ee_") as root:
            entry = make_entry("nope")
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.evaluate_entry(root, entry, temp_root, 30)
            self.assertEqual(result["state"], freshness.STATE_TOOL_MISSING)
            self.assertIsNone(result["actual_exit_code"])
            self.assertFalse(result["generator_invoked"])

    def test_state_tool_missing_script_absent(self):
        with tempfile.TemporaryDirectory(prefix="freshness_ee_") as root:
            os.makedirs(os.path.join(root, "toolA"))
            entry = make_entry("toolA")
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.evaluate_entry(root, entry, temp_root, 30)
            self.assertEqual(result["state"], freshness.STATE_TOOL_MISSING)

    def test_expected_nonzero_exit_treated_as_legitimate_when_match(self):
        with tempfile.TemporaryDirectory(prefix="freshness_ee_") as root:
            make_tool(root, "toolA", "write", exit_code=1, content="content")
            write_bytes(os.path.join(root, "toolA", "report.json"), b"content")
            entry = make_entry("toolA", expected_exit_code=1)
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.evaluate_entry(root, entry, temp_root, 30)
            self.assertEqual(result["state"], freshness.STATE_MATCH)
            self.assertEqual(result["actual_exit_code"], 1)

    def test_expected_nonzero_exit_treated_as_legitimate_when_stale(self):
        with tempfile.TemporaryDirectory(prefix="freshness_ee_") as root:
            make_tool(root, "toolA", "write", exit_code=1, content="new")
            write_bytes(os.path.join(root, "toolA", "report.json"), b"old")
            entry = make_entry("toolA", expected_exit_code=1)
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.evaluate_entry(root, entry, temp_root, 30)
            self.assertEqual(result["state"], freshness.STATE_STALE)
            self.assertEqual(result["actual_exit_code"], 1)

    # -- pinned entries --------------------------------------------------

    def test_state_pinned_present(self):
        with tempfile.TemporaryDirectory(prefix="freshness_ee_") as root:
            os.makedirs(os.path.join(root, "toolA"))
            write_bytes(os.path.join(root, "toolA", "report.json"), b"frozen evidence")
            entry = make_pinned_entry("toolA")
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.evaluate_entry(root, entry, temp_root, 30)
            self.assertEqual(result["state"], freshness.STATE_PINNED_PRESENT)
            self.assertEqual(result["kind"], "pinned")
            self.assertIsNone(result["reason"])
            self.assertFalse(result["generator_invoked"])
            self.assertIsNone(result["actual_exit_code"])
            self.assertIsNone(result["regenerated_sha256"])
            self.assertIsNone(result["regenerated_bytes"])
            self.assertEqual(result["committed_bytes"], len(b"frozen evidence"))

    def test_state_pinned_missing(self):
        with tempfile.TemporaryDirectory(prefix="freshness_ee_") as root:
            os.makedirs(os.path.join(root, "toolA"))
            entry = make_pinned_entry("toolA")
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.evaluate_entry(root, entry, temp_root, 30)
            self.assertEqual(result["state"], freshness.STATE_PINNED_MISSING)
            self.assertIsNotNone(result["reason"])
            self.assertFalse(result["committed_present"])

    def test_pinned_entry_generator_never_invoked_even_when_tool_and_script_exist(self):
        # The "poison" generator writes a marker file into its own tool
        # directory and would exit loudly (77) if run. A pinned entry
        # must leave it untouched.
        with tempfile.TemporaryDirectory(prefix="freshness_ee_") as root:
            tool_dir = make_tool(root, "toolA", "poison")
            write_bytes(os.path.join(root, "toolA", "report.json"), b"frozen evidence, do not touch")
            entry = make_pinned_entry("toolA", argv=["python3", "gen.py", "-o", "{OUT}"])
            marker_path = os.path.join(tool_dir, "INVOKED.marker")
            self.assertFalse(os.path.exists(marker_path))
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.evaluate_entry(root, entry, temp_root, 30)
            self.assertEqual(result["state"], freshness.STATE_PINNED_PRESENT)
            self.assertFalse(result["generator_invoked"])
            self.assertFalse(
                os.path.exists(marker_path),
                "pinned entry's generator was invoked -- it must never run",
            )

    def test_pinned_entry_generator_never_invoked_when_missing_too(self):
        with tempfile.TemporaryDirectory(prefix="freshness_ee_") as root:
            tool_dir = make_tool(root, "toolA", "poison")
            entry = make_pinned_entry("toolA", argv=["python3", "gen.py", "-o", "{OUT}"])
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.evaluate_entry(root, entry, temp_root, 30)
            self.assertEqual(result["state"], freshness.STATE_PINNED_MISSING)
            self.assertFalse(result["generator_invoked"])
            self.assertFalse(os.path.exists(os.path.join(tool_dir, "INVOKED.marker")))

    def test_pinned_entry_with_no_generation_field_at_all(self):
        with tempfile.TemporaryDirectory(prefix="freshness_ee_") as root:
            os.makedirs(os.path.join(root, "toolA"))
            write_bytes(os.path.join(root, "toolA", "report.json"), b"evidence")
            entry = make_pinned_entry("toolA")  # no argv -> no "generation" key at all
            self.assertNotIn("generation", entry)
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.evaluate_entry(root, entry, temp_root, 30)
            self.assertEqual(result["state"], freshness.STATE_PINNED_PRESENT)
            self.assertIsNone(result["generation_argv"])
            self.assertIsNone(result["generation_cwd"])

    def test_pinned_entry_records_committed_hash_and_length(self):
        with tempfile.TemporaryDirectory(prefix="freshness_ee_") as root:
            os.makedirs(os.path.join(root, "toolA"))
            data = b"exact evidence bytes, byte for byte"
            write_bytes(os.path.join(root, "toolA", "report.json"), data)
            entry = make_pinned_entry("toolA")
            with tempfile.TemporaryDirectory(prefix="freshness_out_") as temp_root:
                result = freshness.evaluate_entry(root, entry, temp_root, 30)
            self.assertEqual(result["committed_bytes"], len(data))
            self.assertEqual(result["committed_sha256"], freshness.sha256_hex(data))


# ==========================================================================
# CLI (subprocess) tests
# ==========================================================================

class TestCLI(unittest.TestCase):
    def _repo_all_match(self):
        tmp = tempfile.mkdtemp(prefix="freshness_cli_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        make_tool(tmp, "toolA", "write", exit_code=0, content="hello")
        write_bytes(os.path.join(tmp, "toolA", "report.json"), b"hello")
        make_tool(tmp, "toolB", "write", exit_code=1, content="world")
        write_bytes(os.path.join(tmp, "toolB", "report.json"), b"world")
        entries = [make_entry("toolA"), make_entry("toolB", expected_exit_code=1)]
        manifest_path = make_repo(tmp, entries)
        return tmp, manifest_path

    def _repo_all_match_with_pinned(self):
        tmp, manifest_path = self._repo_all_match()
        os.makedirs(os.path.join(tmp, "toolC"))
        write_bytes(os.path.join(tmp, "toolC", "evidence.json"), b"pinned evidence")
        entries = json.load(open(manifest_path, encoding="utf-8"))["entries"]
        entries.append(make_pinned_entry("toolC", committed_report="evidence.json"))
        make_repo(tmp, entries)
        return tmp, manifest_path

    def test_exit_0_all_match(self):
        tmp, manifest_path = self._repo_all_match()
        rc, out, err = run_cli(["--manifest", manifest_path, "--root", tmp])
        self.assertEqual(rc, 0)
        report = json.loads(out.decode("utf-8"))
        self.assertEqual(report["counts"]["match"], 2)

    def test_exit_0_all_match_and_pinned_present(self):
        tmp, manifest_path = self._repo_all_match_with_pinned()
        rc, out, err = run_cli(["--manifest", manifest_path, "--root", tmp])
        self.assertEqual(rc, 0)
        report = json.loads(out.decode("utf-8"))
        self.assertEqual(report["counts"]["match"], 2)
        self.assertEqual(report["counts"]["pinned_present"], 1)

    def test_exit_1_stale(self):
        tmp = tempfile.mkdtemp(prefix="freshness_cli_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        make_tool(tmp, "toolA", "write", exit_code=0, content="new")
        write_bytes(os.path.join(tmp, "toolA", "report.json"), b"old")
        manifest_path = make_repo(tmp, [make_entry("toolA")])
        rc, out, err = run_cli(["--manifest", manifest_path, "--root", tmp])
        self.assertEqual(rc, 1)
        report = json.loads(out.decode("utf-8"))
        self.assertEqual(report["counts"]["stale"], 1)

    def test_exit_1_missing(self):
        tmp = tempfile.mkdtemp(prefix="freshness_cli_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        make_tool(tmp, "toolA", "write", exit_code=0, content="hi")
        manifest_path = make_repo(tmp, [make_entry("toolA")])
        rc, out, err = run_cli(["--manifest", manifest_path, "--root", tmp])
        self.assertEqual(rc, 1)
        report = json.loads(out.decode("utf-8"))
        self.assertEqual(report["counts"]["missing"], 1)

    def test_exit_2_malformed_manifest(self):
        tmp = tempfile.mkdtemp(prefix="freshness_cli_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        manifest_path = os.path.join(tmp, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        rc, out, err = run_cli(["--manifest", manifest_path, "--root", tmp])
        self.assertEqual(rc, 2)
        report = json.loads(out.decode("utf-8"))
        self.assertEqual(report["status"], "error")

    def test_exit_2_root_not_a_directory(self):
        tmp, manifest_path = self._repo_all_match()
        rc, out, err = run_cli(["--manifest", manifest_path, "--root", os.path.join(tmp, "nonexistent")])
        self.assertEqual(rc, 2)

    def test_exit_2_output_unwritable(self):
        tmp, manifest_path = self._repo_all_match()
        bad_output = os.path.join(tmp, "nonexistent-dir", "out.json")
        rc, out, err = run_cli(["--manifest", manifest_path, "--root", tmp, "-o", bad_output])
        self.assertEqual(rc, 2)

    def test_exit_3_generation_failed(self):
        tmp = tempfile.mkdtemp(prefix="freshness_cli_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        make_tool(tmp, "toolA", "write", exit_code=1, content="hi")
        write_bytes(os.path.join(tmp, "toolA", "report.json"), b"hi")
        manifest_path = make_repo(tmp, [make_entry("toolA", expected_exit_code=0)])
        rc, out, err = run_cli(["--manifest", manifest_path, "--root", tmp])
        self.assertEqual(rc, 3)
        report = json.loads(out.decode("utf-8"))
        self.assertEqual(report["counts"]["generation_failed"], 1)

    def test_exit_3_tool_missing(self):
        tmp = tempfile.mkdtemp(prefix="freshness_cli_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        manifest_path = make_repo(tmp, [make_entry("nope")])
        rc, out, err = run_cli(["--manifest", manifest_path, "--root", tmp])
        self.assertEqual(rc, 3)
        report = json.loads(out.decode("utf-8"))
        self.assertEqual(report["counts"]["tool_missing"], 1)

    def test_exit_3_pinned_missing(self):
        tmp = tempfile.mkdtemp(prefix="freshness_cli_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        os.makedirs(os.path.join(tmp, "toolA"))
        manifest_path = make_repo(tmp, [make_pinned_entry("toolA")])
        rc, out, err = run_cli(["--manifest", manifest_path, "--root", tmp])
        self.assertEqual(rc, 3)
        report = json.loads(out.decode("utf-8"))
        self.assertEqual(report["counts"]["pinned_missing"], 1)
        self.assertEqual(report["entries"][0]["state"], "pinned_missing")

    def test_exit_3_pinned_missing_outranks_stale(self):
        tmp = tempfile.mkdtemp(prefix="freshness_cli_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        make_tool(tmp, "toolA", "write", exit_code=0, content="new")
        write_bytes(os.path.join(tmp, "toolA", "report.json"), b"old")
        os.makedirs(os.path.join(tmp, "toolB"))
        manifest_path = make_repo(tmp, [make_entry("toolA"), make_pinned_entry("toolB")])
        rc, out, err = run_cli(["--manifest", manifest_path, "--root", tmp])
        self.assertEqual(rc, 3)

    def test_exit_3_takes_priority_over_stale(self):
        tmp = tempfile.mkdtemp(prefix="freshness_cli_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        make_tool(tmp, "toolA", "write", exit_code=0, content="new")
        write_bytes(os.path.join(tmp, "toolA", "report.json"), b"old")
        make_tool(tmp, "toolB", "write", exit_code=1, content="hi")
        write_bytes(os.path.join(tmp, "toolB", "report.json"), b"hi")
        manifest_path = make_repo(tmp, [make_entry("toolA"), make_entry("toolB", expected_exit_code=0)])
        rc, out, err = run_cli(["--manifest", manifest_path, "--root", tmp])
        self.assertEqual(rc, 3)

    def test_json_top_level_keys(self):
        tmp, manifest_path = self._repo_all_match()
        rc, out, err = run_cli(["--manifest", manifest_path, "--root", tmp])
        report = json.loads(out.decode("utf-8"))
        self.assertEqual(set(report.keys()), {"schema_version", "tool", "manifest_path", "counts", "entries"})

    def test_entries_sorted_by_id(self):
        tmp = tempfile.mkdtemp(prefix="freshness_cli_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for name in ["zzz", "aaa", "mmm"]:
            make_tool(tmp, name, "write", exit_code=0, content=name)
            write_bytes(os.path.join(tmp, name, "report.json"), name.encode())
        manifest_path = make_repo(tmp, [make_entry("zzz"), make_entry("aaa"), make_entry("mmm")])
        rc, out, err = run_cli(["--manifest", manifest_path, "--root", tmp])
        report = json.loads(out.decode("utf-8"))
        ids = [e["id"] for e in report["entries"]]
        self.assertEqual(ids, sorted(ids))

    def test_output_written_to_file(self):
        tmp, manifest_path = self._repo_all_match()
        out_path = os.path.join(tmp, "report.json")
        rc, out, err = run_cli(["--manifest", manifest_path, "--root", tmp, "-o", out_path])
        self.assertEqual(rc, 0)
        self.assertEqual(out, b"")
        self.assertTrue(os.path.isfile(out_path))
        with open(out_path, "rb") as fh:
            report = json.loads(fh.read().decode("utf-8"))
        self.assertEqual(report["counts"]["match"], 2)

    def test_output_defaults_to_stdout(self):
        tmp, manifest_path = self._repo_all_match()
        rc, out, err = run_cli(["--manifest", manifest_path, "--root", tmp])
        self.assertTrue(len(out) > 0)

    def test_counts_total_matches_entries_length(self):
        tmp, manifest_path = self._repo_all_match()
        rc, out, err = run_cli(["--manifest", manifest_path, "--root", tmp])
        report = json.loads(out.decode("utf-8"))
        self.assertEqual(report["counts"]["total"], len(report["entries"]))

    def test_counts_sum_of_states_equals_total(self):
        tmp, manifest_path = self._repo_all_match_with_pinned()
        rc, out, err = run_cli(["--manifest", manifest_path, "--root", tmp])
        report = json.loads(out.decode("utf-8"))
        state_sum = sum(report["counts"][s] for s in freshness.ALL_STATES)
        self.assertEqual(state_sum, report["counts"]["total"])

    def test_help_exits_0(self):
        rc, out, err = run_cli(["--help"])
        self.assertEqual(rc, 0)
        self.assertIn(b"usage", out.lower())

    def test_unknown_argument_exits_2(self):
        rc, out, err = run_cli(["--not-a-real-flag"])
        self.assertEqual(rc, 2)

    def test_custom_timeout_causes_timeout_state(self):
        tmp = tempfile.mkdtemp(prefix="freshness_cli_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        make_tool(tmp, "toolA", "sleep", seconds=5)
        manifest_path = make_repo(tmp, [make_entry("toolA")])
        rc, out, err = run_cli(["--manifest", manifest_path, "--root", tmp, "--timeout", "1"], timeout=30)
        self.assertEqual(rc, 3)
        report = json.loads(out.decode("utf-8"))
        self.assertIn("timed out", report["entries"][0]["reason"])

    def test_no_absolute_path_markers_in_output(self):
        tmp, manifest_path = self._repo_all_match_with_pinned()
        rc, out, err = run_cli(["--manifest", manifest_path, "--root", tmp])
        text = out.decode("utf-8")
        for marker in [tmp, tempfile.gettempdir()]:
            self.assertNotIn(marker, text)

    def test_pinned_entry_generator_never_invoked_end_to_end(self):
        tmp = tempfile.mkdtemp(prefix="freshness_cli_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        tool_dir = make_tool(tmp, "toolC", "poison")
        write_bytes(os.path.join(tmp, "toolC", "evidence.json"), b"pinned, frozen")
        manifest_path = make_repo(
            tmp, [make_pinned_entry("toolC", committed_report="evidence.json", argv=["python3", "gen.py", "-o", "{OUT}"])]
        )
        rc, out, err = run_cli(["--manifest", manifest_path, "--root", tmp])
        self.assertEqual(rc, 0)
        report = json.loads(out.decode("utf-8"))
        self.assertEqual(report["entries"][0]["state"], "pinned_present")
        self.assertFalse(report["entries"][0]["generator_invoked"])
        self.assertFalse(os.path.exists(os.path.join(tool_dir, "INVOKED.marker")))


# ==========================================================================
# Working-tree-untouched
# ==========================================================================

class TestWorkingTreeUntouched(unittest.TestCase):
    def _repo(self):
        tmp = tempfile.mkdtemp(prefix="freshness_wt_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        make_tool(tmp, "toolA", "write", exit_code=0, content="hello")
        write_bytes(os.path.join(tmp, "toolA", "report.json"), b"hello")
        manifest_path = make_repo(tmp, [make_entry("toolA")])
        return tmp, manifest_path

    def test_committed_report_bytes_unchanged(self):
        tmp, manifest_path = self._repo()
        report_path = os.path.join(tmp, "toolA", "report.json")
        with open(report_path, "rb") as fh:
            before = fh.read()
        run_cli(["--manifest", manifest_path, "--root", tmp])
        with open(report_path, "rb") as fh:
            after = fh.read()
        self.assertEqual(before, after)

    def test_no_new_files_in_tool_directory(self):
        tmp, manifest_path = self._repo()
        tool_dir = os.path.join(tmp, "toolA")
        before = set(os.listdir(tool_dir))
        run_cli(["--manifest", manifest_path, "--root", tmp])
        after = set(os.listdir(tool_dir))
        self.assertEqual(before, after)

    def test_no_new_files_in_repo_root_without_output_flag(self):
        tmp, manifest_path = self._repo()
        before = set(os.listdir(tmp))
        run_cli(["--manifest", manifest_path, "--root", tmp])
        after = set(os.listdir(tmp))
        self.assertEqual(before, after)

    def test_only_declared_output_file_created(self):
        tmp, manifest_path = self._repo()
        before = set(os.listdir(tmp))
        out_path = os.path.join(tmp, "my_report.json")
        run_cli(["--manifest", manifest_path, "--root", tmp, "-o", out_path])
        after = set(os.listdir(tmp))
        self.assertEqual(after - before, {"my_report.json"})

    def test_no_leftover_temp_directories(self):
        tmp, manifest_path = self._repo()
        sys_temp = tempfile.gettempdir()
        before = {n for n in os.listdir(sys_temp) if n.startswith("report_freshness_")}
        run_cli(["--manifest", manifest_path, "--root", tmp])
        after = {n for n in os.listdir(sys_temp) if n.startswith("report_freshness_")}
        self.assertEqual(before, after)

    def test_pinned_tool_directory_untouched(self):
        tmp = tempfile.mkdtemp(prefix="freshness_wt_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        tool_dir = make_tool(tmp, "toolC", "poison")
        write_bytes(os.path.join(tmp, "toolC", "evidence.json"), b"frozen")
        before = set(os.listdir(tool_dir))
        manifest_path = make_repo(
            tmp, [make_pinned_entry("toolC", committed_report="evidence.json", argv=["python3", "gen.py", "-o", "{OUT}"])]
        )
        run_cli(["--manifest", manifest_path, "--root", tmp])
        after = set(os.listdir(tool_dir))
        self.assertEqual(before, after)


# ==========================================================================
# Relocated-repository proof
# ==========================================================================

class TestRelocatedRepository(unittest.TestCase):
    def _build_repo(self, root):
        make_tool(root, "toolA", "write", exit_code=0, content="alpha content")
        write_bytes(os.path.join(root, "toolA", "report.json"), b"alpha content")
        make_tool(root, "toolB", "write", exit_code=1, content="beta content NEW")
        write_bytes(os.path.join(root, "toolB", "report.json"), b"beta content OLD")
        make_tool(root, "toolC", "write", exit_code=0, content="gamma")
        os.makedirs(os.path.join(root, "toolD"))
        write_bytes(os.path.join(root, "toolD", "evidence.json"), b"pinned evidence, frozen forever")
        entries = [
            make_entry("toolA"),
            make_entry("toolB", expected_exit_code=1),
            make_entry("toolC"),
            make_pinned_entry("toolD", committed_report="evidence.json"),
        ]
        return make_repo(root, entries)

    def test_two_locations_produce_byte_identical_reports(self):
        loc_a = tempfile.mkdtemp(prefix="freshness_reloc_alpha_")
        self.addCleanup(shutil.rmtree, loc_a, ignore_errors=True)
        self._build_repo(loc_a)

        loc_b = tempfile.mkdtemp(prefix="freshness_relocated_completely_different_name_")
        self.addCleanup(shutil.rmtree, loc_b, ignore_errors=True)
        shutil.rmtree(loc_b)
        shutil.copytree(loc_a, loc_b)

        manifest_a = os.path.join(loc_a, "manifest.json")
        manifest_b = os.path.join(loc_b, "manifest.json")

        rc_a, out_a, _ = run_cli(["--manifest", manifest_a, "--root", loc_a])
        rc_b, out_b, _ = run_cli(["--manifest", manifest_b, "--root", loc_b])

        self.assertEqual(rc_a, rc_b)
        self.assertEqual(out_a, out_b)

    def test_two_locations_hashes_match(self):
        loc_a = tempfile.mkdtemp(prefix="freshness_reloc_h1_")
        self.addCleanup(shutil.rmtree, loc_a, ignore_errors=True)
        self._build_repo(loc_a)
        loc_b = tempfile.mkdtemp(prefix="freshness_reloc_h2_totally_renamed_")
        self.addCleanup(shutil.rmtree, loc_b, ignore_errors=True)
        shutil.rmtree(loc_b)
        shutil.copytree(loc_a, loc_b)

        _, out_a, _ = run_cli(["--manifest", os.path.join(loc_a, "manifest.json"), "--root", loc_a])
        _, out_b, _ = run_cli(["--manifest", os.path.join(loc_b, "manifest.json"), "--root", loc_b])

        self.assertEqual(freshness.sha256_hex(out_a), freshness.sha256_hex(out_b))

    def test_output_never_contains_either_location_path(self):
        loc_a = tempfile.mkdtemp(prefix="freshness_reloc_p1_")
        self.addCleanup(shutil.rmtree, loc_a, ignore_errors=True)
        self._build_repo(loc_a)
        loc_b = tempfile.mkdtemp(prefix="freshness_reloc_p2_differently_named_")
        self.addCleanup(shutil.rmtree, loc_b, ignore_errors=True)
        shutil.rmtree(loc_b)
        shutil.copytree(loc_a, loc_b)

        _, out_a, _ = run_cli(["--manifest", os.path.join(loc_a, "manifest.json"), "--root", loc_a])
        _, out_b, _ = run_cli(["--manifest", os.path.join(loc_b, "manifest.json"), "--root", loc_b])

        text_a = out_a.decode("utf-8")
        text_b = out_b.decode("utf-8")
        self.assertNotIn(loc_a, text_a)
        self.assertNotIn(loc_b, text_a)
        self.assertNotIn(loc_a, text_b)
        self.assertNotIn(loc_b, text_b)

    def test_relocated_report_reflects_real_states(self):
        loc_a = tempfile.mkdtemp(prefix="freshness_reloc_states_")
        self.addCleanup(shutil.rmtree, loc_a, ignore_errors=True)
        self._build_repo(loc_a)
        rc, out, _ = run_cli(["--manifest", os.path.join(loc_a, "manifest.json"), "--root", loc_a])
        report = json.loads(out.decode("utf-8"))
        states = {e["id"]: e["state"] for e in report["entries"]}
        self.assertEqual(states["toolA:report.json"], freshness.STATE_MATCH)
        self.assertEqual(states["toolB:report.json"], freshness.STATE_STALE)
        self.assertEqual(states["toolC:report.json"], freshness.STATE_MISSING)
        self.assertEqual(states["toolD:evidence.json"], freshness.STATE_PINNED_PRESENT)

    def test_relocated_manifest_entry_order_does_not_affect_output(self):
        loc_a = tempfile.mkdtemp(prefix="freshness_reloc_order1_")
        self.addCleanup(shutil.rmtree, loc_a, ignore_errors=True)
        make_tool(loc_a, "toolA", "write", exit_code=0, content="x")
        write_bytes(os.path.join(loc_a, "toolA", "report.json"), b"x")
        make_tool(loc_a, "toolB", "write", exit_code=0, content="y")
        write_bytes(os.path.join(loc_a, "toolB", "report.json"), b"y")
        make_repo(loc_a, [make_entry("toolB"), make_entry("toolA")])

        loc_b = tempfile.mkdtemp(prefix="freshness_reloc_order2_renamed_")
        self.addCleanup(shutil.rmtree, loc_b, ignore_errors=True)
        make_tool(loc_b, "toolA", "write", exit_code=0, content="x")
        write_bytes(os.path.join(loc_b, "toolA", "report.json"), b"x")
        make_tool(loc_b, "toolB", "write", exit_code=0, content="y")
        write_bytes(os.path.join(loc_b, "toolB", "report.json"), b"y")
        make_repo(loc_b, [make_entry("toolA"), make_entry("toolB")])

        _, out_a, _ = run_cli(["--manifest", os.path.join(loc_a, "manifest.json"), "--root", loc_a])
        _, out_b, _ = run_cli(["--manifest", os.path.join(loc_b, "manifest.json"), "--root", loc_b])
        self.assertEqual(out_a, out_b)


# ==========================================================================
# Dogfood: the real manifest.json against the real repository
# ==========================================================================

class TestRealManifestDogfood(unittest.TestCase):
    def test_manifest_file_is_valid_json(self):
        with open(REAL_MANIFEST, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertIn("entries", doc)

    def test_loads_at_least_three_entries(self):
        entries = freshness.load_manifest(REAL_MANIFEST)
        self.assertGreaterEqual(len(entries), 3)

    def test_manifest_has_at_least_three_regenerable_entries(self):
        entries = freshness.load_manifest(REAL_MANIFEST)
        regenerable = [e for e in entries if e["kind"] == "regenerable"]
        self.assertGreaterEqual(len(regenerable), 3)

    def test_manifest_has_at_least_two_pinned_entries(self):
        entries = freshness.load_manifest(REAL_MANIFEST)
        pinned = [e for e in entries if e["kind"] == "pinned"]
        self.assertGreaterEqual(len(pinned), 2)

    def test_all_ids_unique(self):
        entries = freshness.load_manifest(REAL_MANIFEST)
        ids = [e["id"] for e in entries]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_entry_tool_directory_exists(self):
        entries = freshness.load_manifest(REAL_MANIFEST)
        for e in entries:
            tool_dir_abs = os.path.join(REAL_REPO_ROOT, e["tool"])
            self.assertTrue(os.path.isdir(tool_dir_abs), "missing tool dir for %s" % e["id"])

    def test_every_regenerable_entry_generation_cwd_exists(self):
        entries = freshness.load_manifest(REAL_MANIFEST)
        for e in entries:
            if e["kind"] != "regenerable":
                continue
            cwd_abs = os.path.join(REAL_REPO_ROOT, e["generation"]["cwd"])
            self.assertTrue(os.path.isdir(cwd_abs), "missing generation cwd for %s" % e["id"])

    def test_build_report_real_repo_exit_code_not_setup_error(self):
        report, exit_code = freshness.build_report(REAL_MANIFEST, REAL_REPO_ROOT, 120)
        self.assertIn(exit_code, (0, 1, 3))

    def test_build_report_real_repo_counts_total(self):
        report, _ = freshness.build_report(REAL_MANIFEST, REAL_REPO_ROOT, 120)
        entries = freshness.load_manifest(REAL_MANIFEST)
        self.assertEqual(report["counts"]["total"], len(entries))

    def test_build_report_real_repo_every_state_valid(self):
        report, _ = freshness.build_report(REAL_MANIFEST, REAL_REPO_ROOT, 120)
        for e in report["entries"]:
            self.assertIn(e["state"], freshness.ALL_STATES)

    def test_build_report_real_repo_entries_sorted(self):
        report, _ = freshness.build_report(REAL_MANIFEST, REAL_REPO_ROOT, 120)
        ids = [e["id"] for e in report["entries"]]
        self.assertEqual(ids, sorted(ids))

    def test_build_report_real_repo_json_round_trips(self):
        report, _ = freshness.build_report(REAL_MANIFEST, REAL_REPO_ROOT, 120)
        text = freshness.canonical_json(report)
        self.assertEqual(json.loads(text), report)

    def test_build_report_real_repo_no_path_leak_markers(self):
        report, _ = freshness.build_report(REAL_MANIFEST, REAL_REPO_ROOT, 120)
        text = freshness.canonical_json(report)
        for marker in path_leak_markers():
            self.assertNotIn(marker, text)

    def test_build_report_real_repo_no_tmp_slash_substring(self):
        report, _ = freshness.build_report(REAL_MANIFEST, REAL_REPO_ROOT, 120)
        text = freshness.canonical_json(report)
        self.assertNotIn("/tmp", text)
        self.assertNotIn("/root", text)
        self.assertNotIn("/home", text)

    def test_build_report_real_repo_deterministic_across_runs(self):
        report1, _ = freshness.build_report(REAL_MANIFEST, REAL_REPO_ROOT, 120)
        report2, _ = freshness.build_report(REAL_MANIFEST, REAL_REPO_ROOT, 120)
        self.assertEqual(freshness.canonical_json(report1), freshness.canonical_json(report2))

    def test_build_report_real_repo_observed_states_are_valid(self):
        # This tool is worthless if it can only ever say "all fresh" --
        # assert it actually classifies entries into real states, without
        # asserting *which* states (that would be the mutable-repo-state
        # anti-pattern this tool exists to avoid).
        report, _ = freshness.build_report(REAL_MANIFEST, REAL_REPO_ROOT, 120)
        observed = {e["state"] for e in report["entries"]}
        self.assertTrue(observed.issubset(set(freshness.ALL_STATES)))
        self.assertGreaterEqual(len(observed), 1)

    def test_each_entry_has_full_field_set(self):
        report, _ = freshness.build_report(REAL_MANIFEST, REAL_REPO_ROOT, 120)
        expected_fields = {
            "id", "tool", "kind", "description", "state", "reason", "committed_report",
            "committed_present", "committed_sha256", "committed_bytes", "generator_invoked",
            "regenerated_sha256", "regenerated_bytes", "expected_exit_code",
            "actual_exit_code", "generation_argv", "generation_cwd", "inputs",
        }
        for e in report["entries"]:
            self.assertEqual(set(e.keys()), expected_fields)

    def test_pinned_entries_in_real_manifest_never_invoke_generator(self):
        report, _ = freshness.build_report(REAL_MANIFEST, REAL_REPO_ROOT, 120)
        for e in report["entries"]:
            if e["kind"] == "pinned":
                self.assertFalse(e["generator_invoked"], "%s must never invoke its generator" % e["id"])
                self.assertIsNone(e["actual_exit_code"])
                self.assertIsNone(e["regenerated_bytes"])
                self.assertIsNone(e["regenerated_sha256"])
                self.assertIn(e["state"], (freshness.STATE_PINNED_PRESENT, freshness.STATE_PINNED_MISSING))

    def test_regenerable_entries_in_real_manifest_have_expected_exit_code(self):
        report, _ = freshness.build_report(REAL_MANIFEST, REAL_REPO_ROOT, 120)
        for e in report["entries"]:
            if e["kind"] == "regenerable":
                self.assertIsNotNone(e["expected_exit_code"])

    def test_env_leak_scanner_entry_is_pinned_not_regenerable(self):
        # The specific regression this whole design change guards against:
        # a dated, point-in-time scan must never be marked regenerable.
        entries = freshness.load_manifest(REAL_MANIFEST)
        by_id = {e["id"]: e for e in entries}
        entry = by_id["env-leak-scanner:leak_report_2026-08-04.json"]
        self.assertEqual(entry["kind"], "pinned")

    def test_transcript_drift_after_migration_entry_is_pinned_not_regenerable(self):
        entries = freshness.load_manifest(REAL_MANIFEST)
        by_id = {e["id"]: e for e in entries}
        entry = by_id["transcript-drift:drift_report_after_migration.json"]
        self.assertEqual(entry["kind"], "pinned")

    def test_committed_freshness_report_json_not_modified_by_dogfood_tests(self):
        # build_report() above must only ever read from REAL_REPO_ROOT and
        # write into its own tempfile.TemporaryDirectory -- prove the
        # committed freshness_report.json in this directory is untouched
        # by the dogfood tests running in this same process.
        report_path = os.path.join(THIS_DIR, "freshness_report.json")
        if not os.path.isfile(report_path):
            self.skipTest("freshness_report.json not present in this checkout")
        with open(report_path, "rb") as fh:
            before = fh.read()
        freshness.build_report(REAL_MANIFEST, REAL_REPO_ROOT, 120)
        with open(report_path, "rb") as fh:
            after = fh.read()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
