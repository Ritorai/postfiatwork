#!/usr/bin/env python3
"""Test suite for tamperrun.py.

Run with:
    python3 -m unittest -v test_tamperrun

All temporary workspaces are created with tempfile.TemporaryDirectory() (or
tempfile.mkdtemp() paired with an explicit shutil.rmtree of that exact
returned path) and cleaned up automatically -- never any directory this
suite did not itself create. The shared fixture at fixtures/valid_bundle is
never opened for writing by any test; every test that needs a mutable copy
makes one with shutil.copytree(..., symlinks=True) into a fresh temp dir.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import tamperrun as tr

HERE = os.path.dirname(os.path.realpath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "valid_bundle")
VERIFIERS = os.path.join(HERE, "verifiers")
PY = sys.executable

STRICT_VERIFIER = f"{PY} {os.path.join(VERIFIERS, 'bundleverify.py')} --bundle {{bundle}}"
WEAK_VERIFIER = f"{PY} {os.path.join(VERIFIERS, 'weak_verifier.py')} --bundle {{bundle}}"
ALWAYS_REJECT_VERIFIER = (
    f"{PY} {os.path.join(VERIFIERS, 'always_reject_verifier.py')} --bundle {{bundle}}"
)
CRASHING_VERIFIER = f"{PY} {os.path.join(VERIFIERS, 'crashing_verifier.py')} --bundle {{bundle}}"
UNCAUGHT_CRASH_VERIFIER = (
    f"{PY} {os.path.join(VERIFIERS, 'uncaught_crash_verifier.py')} --bundle {{bundle}}"
)
EXIT2_VERIFIER = f"{PY} {os.path.join(VERIFIERS, 'exit2_verifier.py')} --bundle {{bundle}}"
FLAKY_VERIFIER = f"{PY} {os.path.join(VERIFIERS, 'flaky_verifier.py')} --bundle {{bundle}}"
SLOW_VERIFIER = f"{PY} {os.path.join(VERIFIERS, 'slow_verifier.py')} --bundle {{bundle}}"
NONEXISTENT_VERIFIER = "/no/such/binary_xyz_never_exists --bundle {bundle}"

TAMPER_CASE_IDS = list(tr.DEFAULT_CASE_ORDER)
ALL_CASE_IDS = [tr.CONTROL_CASE_ID] + TAMPER_CASE_IDS


def tree_hash(root: str) -> str:
    """Independent (does not reuse tamperrun's own code) content hash of a
    directory tree: sorted relative paths, file bytes, and which
    directories exist (so a dropped empty directory changes the hash)."""
    h = hashlib.sha256()
    entries = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        entries.append(("D", rel_dir))
        for name in sorted(filenames):
            rel = os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/")
            with open(os.path.join(dirpath, name), "rb") as fh:
                data = fh.read()
            entries.append(("F", rel, hashlib.sha256(data).hexdigest(), len(data)))
    for entry in sorted(entries):
        h.update(repr(entry).encode("utf-8"))
    return h.hexdigest()


def make_copy(dest_parent: str, name: str = "copy") -> str:
    dest = os.path.join(dest_parent, name)
    shutil.copytree(FIXTURE, dest, symlinks=True)
    return dest


def run_cli(args: list[str], cwd: str | None = None):
    return subprocess.run(
        [PY, os.path.join(HERE, "tamperrun.py")] + args,
        cwd=cwd or HERE,
        capture_output=True,
        text=True,
        timeout=60,
    )


# --------------------------------------------------------------------------
# A. canonical JSON
# --------------------------------------------------------------------------


class TestCanonicalJson(unittest.TestCase):
    def test_keys_sorted(self):
        text = tr.canonical_dumps({"b": 1, "a": 2})
        self.assertEqual(text, '{"a":2,"b":1}')

    def test_compact_separators(self):
        text = tr.canonical_dumps({"a": [1, 2], "b": {"c": 3}})
        self.assertNotIn(" ", text)
        self.assertNotIn("\n", text)

    def test_ensure_ascii(self):
        text = tr.canonical_dumps({"a": "é"})
        self.assertIn("\\u00e9", text)
        self.assertNotIn("é", text)

    def test_no_trailing_newline(self):
        text = tr.canonical_dumps({"a": 1})
        self.assertFalse(text.endswith("\n"))

    def test_write_canonical_json_exact_bytes(self):
        with tempfile.TemporaryDirectory(prefix="tamperrun_test_") as d:
            path = os.path.join(d, "out.json")
            tr.write_canonical_json(path, {"z": 1, "a": 2})
            with open(path, "rb") as fh:
                raw = fh.read()
            self.assertEqual(raw, b'{"a":2,"z":1}\n')

    def test_write_canonical_json_roundtrip(self):
        obj = {"list": [3, 1, 2], "nested": {"x": True, "y": None}}
        with tempfile.TemporaryDirectory(prefix="tamperrun_test_") as d:
            path = os.path.join(d, "out.json")
            tr.write_canonical_json(path, obj)
            with open(path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            self.assertEqual(loaded, obj)


# --------------------------------------------------------------------------
# B. sorted_with_tiebreak
# --------------------------------------------------------------------------


class TestSortedWithTiebreak(unittest.TestCase):
    def test_sorts_by_primary_key(self):
        items = [{"k": "b"}, {"k": "a"}]
        out = tr.sorted_with_tiebreak(items, primary_key=lambda i: i["k"])
        self.assertEqual([i["k"] for i in out], ["a", "b"])

    def test_tiebreak_breaks_a_tie(self):
        # Same primary key ("X"), different content -- must be ordered by
        # canonical JSON dump of the item, not by insertion order.
        items = [
            {"case_id": "X", "target": "zzz"},
            {"case_id": "X", "target": "aaa"},
        ]
        out = tr.sorted_with_tiebreak(items, primary_key=lambda i: i["case_id"])
        self.assertEqual([i["target"] for i in out], ["aaa", "zzz"])
        expected = sorted(items, key=lambda i: tr.canonical_dumps(i))
        self.assertEqual(out, expected)

    def test_order_independent_of_input_order(self):
        items = [{"case_id": "X", "target": "zzz"}, {"case_id": "X", "target": "aaa"}]
        reversed_items = list(reversed(items))
        out_a = tr.sorted_with_tiebreak(items, primary_key=lambda i: i["case_id"])
        out_b = tr.sorted_with_tiebreak(reversed_items, primary_key=lambda i: i["case_id"])
        self.assertEqual(out_a, out_b)

    def test_plain_string_list(self):
        out = tr.sorted_with_tiebreak(["c", "a", "b"], primary_key=lambda s: s)
        self.assertEqual(out, ["a", "b", "c"])


# --------------------------------------------------------------------------
# C. safe_join
# --------------------------------------------------------------------------


class TestSafeJoin(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="tamperrun_test_")
        self.base = self._tmp.name
        os.makedirs(os.path.join(self.base, "sub"))
        with open(os.path.join(self.base, "sub", "f.txt"), "w") as fh:
            fh.write("x")

    def tearDown(self):
        self._tmp.cleanup()

    def test_normal_relative_path(self):
        result = tr.safe_join(self.base, "sub/f.txt")
        self.assertTrue(os.path.isfile(result))

    def test_nested_relative_path(self):
        os.makedirs(os.path.join(self.base, "a", "b"))
        result = tr.safe_join(self.base, "a/b")
        self.assertTrue(os.path.isdir(result))

    def test_absolute_relpath_rejected(self):
        with self.assertRaises(tr.PathEscapeError):
            tr.safe_join(self.base, "/etc/passwd")

    def test_dotdot_component_rejected(self):
        with self.assertRaises(tr.PathEscapeError):
            tr.safe_join(self.base, "../escape.txt")

    def test_dot_component_rejected(self):
        with self.assertRaises(tr.PathEscapeError):
            tr.safe_join(self.base, "./f.txt")

    def test_empty_component_rejected(self):
        with self.assertRaises(tr.PathEscapeError):
            tr.safe_join(self.base, "sub//f.txt")

    def test_symlink_pointing_inside_allowed(self):
        link = os.path.join(self.base, "inside_link")
        os.symlink(os.path.join(self.base, "sub", "f.txt"), link)
        result = tr.safe_join(self.base, "inside_link")
        self.assertTrue(os.path.isfile(result))

    def test_symlink_pointing_outside_rejected(self):
        with tempfile.TemporaryDirectory(prefix="tamperrun_test_outside_") as outside:
            target = os.path.join(outside, "secret.txt")
            with open(target, "w") as fh:
                fh.write("secret")
            link = os.path.join(self.base, "escape_link")
            os.symlink(target, link)
            with self.assertRaises(tr.PathEscapeError):
                tr.safe_join(self.base, "escape_link")

    def test_returned_path_usable(self):
        result = tr.safe_join(self.base, "sub/f.txt")
        with open(result) as fh:
            self.assertEqual(fh.read(), "x")

    def test_base_is_realpathed(self):
        with tempfile.TemporaryDirectory(prefix="tamperrun_test_") as real_dir:
            link_to_base = os.path.join(real_dir, "link")
            os.symlink(self.base, link_to_base)
            result = tr.safe_join(link_to_base, "sub/f.txt")
            self.assertEqual(os.path.realpath(result), os.path.realpath(result))
            self.assertTrue(result.startswith(os.path.realpath(self.base)))


# --------------------------------------------------------------------------
# D. list_regular_files / list_json_files
# --------------------------------------------------------------------------


class TestListFiles(unittest.TestCase):
    def test_lists_all_fixture_files(self):
        files = set(tr.list_regular_files(FIXTURE))
        expected = {
            "binary.dat",
            "config.json",
            "data.txt",
            "manifest.json",
            "nested/more.txt",
            "notes.txt",
            "unicode.txt",
        }
        self.assertEqual(files, expected)

    def test_includes_nested_file(self):
        self.assertIn("nested/more.txt", tr.list_regular_files(FIXTURE))

    def test_json_files_subset(self):
        self.assertEqual(set(tr.list_json_files(FIXTURE)), {"config.json", "manifest.json"})

    def test_empty_dir_yields_no_files_no_crash(self):
        files = tr.list_regular_files(FIXTURE)
        self.assertNotIn("empty_dir", files)

    def test_broken_symlink_included_without_crash(self):
        with tempfile.TemporaryDirectory(prefix="tamperrun_test_") as d:
            copy = make_copy(d)
            os.symlink(
                os.path.join(copy, "does_not_exist.txt"),
                os.path.join(copy, "broken_link.txt"),
            )
            files = tr.list_regular_files(copy)
            self.assertIn("broken_link.txt", files)

    def test_sorted_order_stable(self):
        self.assertEqual(tr.list_regular_files(FIXTURE), sorted(tr.list_regular_files(FIXTURE)))


# --------------------------------------------------------------------------
# E. find_first_scalar
# --------------------------------------------------------------------------


class TestFindFirstScalar(unittest.TestCase):
    def test_simple_dict(self):
        self.assertEqual(tr.find_first_scalar({"b": 1, "a": 2}), (["a"], 2))

    def test_nested_dict_sorted_key(self):
        data = {"z": {"m": 1}, "a": {"n": 2}}
        self.assertEqual(tr.find_first_scalar(data), (["a", "n"], 2))

    def test_list_of_scalars(self):
        self.assertEqual(tr.find_first_scalar([9, 8]), ([0], 9))

    def test_nested_list_within_dict(self):
        data = {"a": [{"x": 1}]}
        self.assertEqual(tr.find_first_scalar(data), (["a", 0, "x"], 1))

    def test_finds_leaf_through_containers(self):
        data = {"a": {"b": {"c": [{"d": {}}, {"e": 5}]}}}
        self.assertEqual(tr.find_first_scalar(data), (["a", "b", "c", 1, "e"], 5))

    def test_empty_dict_returns_none(self):
        self.assertIsNone(tr.find_first_scalar({}))

    def test_empty_list_returns_none(self):
        self.assertIsNone(tr.find_first_scalar([]))

    def test_root_itself_scalar(self):
        self.assertEqual(tr.find_first_scalar(3), ([], 3))


# --------------------------------------------------------------------------
# F. find_first_hash_like
# --------------------------------------------------------------------------


class TestFindFirstHashLike(unittest.TestCase):
    def test_finds_sha256_length_string(self):
        h = "a" * 64
        self.assertEqual(tr.find_first_hash_like({"x": h}), (["x"], h))

    def test_skips_non_hex_scalar(self):
        h = "b" * 64
        data = {"a": "not-hex-at-all", "z": h}
        self.assertEqual(tr.find_first_hash_like(data), (["z"], h))

    def test_returns_none_when_nothing_matches(self):
        self.assertIsNone(tr.find_first_hash_like({"a": "widget", "b": 3}))

    def test_wrong_length_hex_not_matched(self):
        self.assertIsNone(tr.find_first_hash_like({"a": "abc123"}))

    def test_nested_dict_alphabetical_key(self):
        data = {"files": {"z.txt": "c" * 64, "a.txt": "d" * 64}}
        self.assertEqual(tr.find_first_hash_like(data), (["files", "a.txt"], "d" * 64))

    def test_uppercase_hex_matches(self):
        h = "F" * 64
        self.assertEqual(tr.find_first_hash_like({"x": h}), (["x"], h))


# --------------------------------------------------------------------------
# G. _mutate_scalar
# --------------------------------------------------------------------------


class TestMutateScalar(unittest.TestCase):
    def test_bool_flips_true(self):
        self.assertEqual(tr._mutate_scalar(True), False)

    def test_bool_flips_false(self):
        self.assertEqual(tr._mutate_scalar(False), True)

    def test_int_increments(self):
        self.assertEqual(tr._mutate_scalar(3), 4)

    def test_float_increments(self):
        self.assertEqual(tr._mutate_scalar(1.5), 2.5)

    def test_str_appends_marker(self):
        self.assertEqual(tr._mutate_scalar("widget"), "widget_TAMPERED")

    def test_str_already_tampered_appends_2(self):
        self.assertEqual(tr._mutate_scalar("widget_TAMPERED"), "widget_TAMPERED_2")

    def test_none_becomes_marker_string(self):
        self.assertEqual(tr._mutate_scalar(None), "TAMPERED_NULL")

    def test_container_raises(self):
        with self.assertRaises(tr.TamperCaseError):
            tr._mutate_scalar([1, 2])


# --------------------------------------------------------------------------
# H. _mutate_hex
# --------------------------------------------------------------------------


class TestMutateHex(unittest.TestCase):
    def test_same_length(self):
        h = "a" * 64
        self.assertEqual(len(tr._mutate_hex(h)), 64)

    def test_different_from_input(self):
        h = "a" * 64
        self.assertNotEqual(tr._mutate_hex(h).lower(), h.lower())

    def test_deterministic(self):
        h = "1234567890abcdef" * 4
        self.assertEqual(tr._mutate_hex(h), tr._mutate_hex(h))

    def test_all_hex_chars(self):
        h = "deadbeef" * 8
        mutated = tr._mutate_hex(h)
        int(mutated, 16)  # raises ValueError if not valid hex


# --------------------------------------------------------------------------
# I. Individual apply_* function behaviour
# --------------------------------------------------------------------------


class TestApplyFunctions(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="tamperrun_test_")
        self.copy = make_copy(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_op_changes_nothing(self):
        before = tree_hash(self.copy)
        target, desc = tr.apply_no_op(self.copy)
        after = tree_hash(self.copy)
        self.assertIsNone(target)
        self.assertEqual(before, after)
        self.assertIn("control", desc)

    def test_delete_file_removes_target(self):
        target, desc = tr.apply_delete_file(self.copy)
        self.assertEqual(target, "data.txt")
        self.assertFalse(os.path.exists(os.path.join(self.copy, "data.txt")))
        self.assertIn("data.txt", desc)

    def test_delete_file_leaves_others_untouched(self):
        tr.apply_delete_file(self.copy)
        for other in ("notes.txt", "binary.dat", "config.json", "manifest.json"):
            self.assertTrue(os.path.exists(os.path.join(self.copy, other)))

    def test_delete_file_never_targets_manifest(self):
        target, _ = tr.apply_delete_file(self.copy)
        self.assertNotEqual(target, "manifest.json")

    def test_truncate_file_halves_size(self):
        original_size = os.path.getsize(os.path.join(self.copy, "notes.txt"))
        target, desc = tr.apply_truncate_file(self.copy)
        self.assertEqual(target, "notes.txt")
        new_size = os.path.getsize(os.path.join(self.copy, "notes.txt"))
        self.assertEqual(new_size, original_size // 2)
        self.assertIn(str(new_size), desc)

    def test_truncate_file_is_prefix_of_original(self):
        with open(os.path.join(FIXTURE, "notes.txt"), "rb") as fh:
            original = fh.read()
        tr.apply_truncate_file(self.copy)
        with open(os.path.join(self.copy, "notes.txt"), "rb") as fh:
            truncated = fh.read()
        self.assertEqual(truncated, original[: len(truncated)])

    def test_truncate_file_leaves_others_untouched(self):
        before = os.path.getsize(os.path.join(self.copy, "binary.dat"))
        tr.apply_truncate_file(self.copy)
        after = os.path.getsize(os.path.join(self.copy, "binary.dat"))
        self.assertEqual(before, after)

    def test_mutate_byte_size_unchanged(self):
        before = os.path.getsize(os.path.join(self.copy, "binary.dat"))
        target, _ = tr.apply_mutate_byte(self.copy)
        after = os.path.getsize(os.path.join(self.copy, "binary.dat"))
        self.assertEqual(target, "binary.dat")
        self.assertEqual(before, after)

    def test_mutate_byte_exactly_one_byte_differs(self):
        with open(os.path.join(FIXTURE, "binary.dat"), "rb") as fh:
            original = fh.read()
        tr.apply_mutate_byte(self.copy)
        with open(os.path.join(self.copy, "binary.dat"), "rb") as fh:
            mutated = fh.read()
        diffs = [i for i in range(len(original)) if original[i] != mutated[i]]
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0], len(original) // 2)

    def test_mutate_byte_description_mentions_offset(self):
        _, desc = tr.apply_mutate_byte(self.copy)
        self.assertIn("offset", desc)

    def test_alter_json_field_changes_config_count(self):
        target, desc = tr.apply_alter_json_field(self.copy)
        self.assertEqual(target, "config.json")
        with open(os.path.join(self.copy, "config.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["count"], 4)
        self.assertIn("count", desc)

    def test_alter_json_field_still_valid_json(self):
        tr.apply_alter_json_field(self.copy)
        with open(os.path.join(self.copy, "config.json"), encoding="utf-8") as fh:
            json.load(fh)  # raises if invalid

    def test_alter_json_field_leaves_manifest_untouched(self):
        with open(os.path.join(FIXTURE, "manifest.json"), "rb") as fh:
            before_bytes = fh.read()
        tr.apply_alter_json_field(self.copy)
        with open(os.path.join(self.copy, "manifest.json"), "rb") as fh:
            after_bytes = fh.read()
        self.assertEqual(before_bytes, after_bytes)

    def test_stale_hash_changes_manifest_entry(self):
        with open(os.path.join(FIXTURE, "manifest.json"), encoding="utf-8") as fh:
            before = json.load(fh)
        target, desc = tr.apply_stale_hash(self.copy)
        self.assertEqual(target, "manifest.json")
        with open(os.path.join(self.copy, "manifest.json"), encoding="utf-8") as fh:
            after = json.load(fh)
        changed_keys = [
            k for k in before["files"] if before["files"][k] != after["files"].get(k)
        ]
        self.assertEqual(len(changed_keys), 1)
        self.assertIn("digest", desc)

    def test_stale_hash_result_still_hex_same_length(self):
        with open(os.path.join(FIXTURE, "manifest.json"), encoding="utf-8") as fh:
            before = json.load(fh)
        tr.apply_stale_hash(self.copy)
        with open(os.path.join(self.copy, "manifest.json"), encoding="utf-8") as fh:
            after = json.load(fh)
        for key, old_val in before["files"].items():
            new_val = after["files"][key]
            if new_val != old_val:
                self.assertEqual(len(new_val), len(old_val))
                int(new_val, 16)

    def test_stale_hash_leaves_config_untouched(self):
        with open(os.path.join(FIXTURE, "config.json"), "rb") as fh:
            before_bytes = fh.read()
        tr.apply_stale_hash(self.copy)
        with open(os.path.join(self.copy, "config.json"), "rb") as fh:
            after_bytes = fh.read()
        self.assertEqual(before_bytes, after_bytes)

    def test_add_unlisted_file_creates_new_file(self):
        target, desc = tr.apply_add_unlisted_file(self.copy)
        self.assertTrue(os.path.isfile(os.path.join(self.copy, target)))
        self.assertIn(target, desc)

    def test_add_unlisted_file_content_matches_marker(self):
        target, _ = tr.apply_add_unlisted_file(self.copy)
        with open(os.path.join(self.copy, target), "rb") as fh:
            self.assertIn(b"UNLISTED_TAMPER_MARKER", fh.read())

    def test_add_unlisted_file_does_not_overwrite_existing(self):
        before = set(tr.list_regular_files(self.copy))
        target, _ = tr.apply_add_unlisted_file(self.copy)
        after = set(tr.list_regular_files(self.copy))
        self.assertEqual(after - before, {target})


# --------------------------------------------------------------------------
# J. CASE_ERROR from apply functions on impoverished fixtures
# --------------------------------------------------------------------------


class TestApplyFunctionCaseErrors(unittest.TestCase):
    def _empty_copy(self, tmp):
        d = os.path.join(tmp, "empty_bundle")
        os.makedirs(d)
        return d

    def _manifest_only_copy(self, tmp):
        d = os.path.join(tmp, "manifest_only")
        os.makedirs(d)
        shutil.copy2(os.path.join(FIXTURE, "manifest.json"), os.path.join(d, "manifest.json"))
        return d

    def test_alter_json_field_no_json_files(self):
        with tempfile.TemporaryDirectory(prefix="tamperrun_test_") as tmp:
            d = self._empty_copy(tmp)
            with open(os.path.join(d, "plain.txt"), "w") as fh:
                fh.write("hello")
            with self.assertRaises(tr.TamperCaseError):
                tr.apply_alter_json_field(d)

    def test_stale_hash_no_hash_like_fields(self):
        with tempfile.TemporaryDirectory(prefix="tamperrun_test_") as tmp:
            d = self._empty_copy(tmp)
            with open(os.path.join(d, "config.json"), "w") as fh:
                json.dump({"name": "widget"}, fh)
            with self.assertRaises(tr.TamperCaseError):
                tr.apply_stale_hash(d)

    def test_delete_file_only_manifest_present(self):
        with tempfile.TemporaryDirectory(prefix="tamperrun_test_") as tmp:
            d = self._manifest_only_copy(tmp)
            with self.assertRaises(tr.TamperCaseError):
                tr.apply_delete_file(d)

    def test_truncate_file_all_too_small(self):
        with tempfile.TemporaryDirectory(prefix="tamperrun_test_") as tmp:
            d = self._empty_copy(tmp)
            with open(os.path.join(d, "one_byte.txt"), "wb") as fh:
                fh.write(b"x")
            with self.assertRaises(tr.TamperCaseError):
                tr.apply_truncate_file(d)

    def test_mutate_byte_all_too_small(self):
        with tempfile.TemporaryDirectory(prefix="tamperrun_test_") as tmp:
            d = self._empty_copy(tmp)
            with open(os.path.join(d, "one_byte.txt"), "wb") as fh:
                fh.write(b"x")
            with self.assertRaises(tr.TamperCaseError):
                tr.apply_mutate_byte(d)

    def test_add_unlisted_file_succeeds_on_empty_fixture(self):
        with tempfile.TemporaryDirectory(prefix="tamperrun_test_") as tmp:
            d = self._empty_copy(tmp)
            target, _ = tr.apply_add_unlisted_file(d)
            self.assertTrue(os.path.isfile(os.path.join(d, target)))


# --------------------------------------------------------------------------
# K. build_argv
# --------------------------------------------------------------------------


class TestBuildArgv(unittest.TestCase):
    def test_placeholder_replaced(self):
        argv = tr.build_argv(["python3", "v.py", "--bundle", "{bundle}"], "/tmp/x")
        self.assertEqual(argv, ["python3", "v.py", "--bundle", "/tmp/x"])

    def test_no_placeholder_appends(self):
        argv = tr.build_argv(["python3", "v.py"], "/tmp/x")
        self.assertEqual(argv, ["python3", "v.py", "/tmp/x"])

    def test_multiple_placeholders_all_replaced(self):
        argv = tr.build_argv(["cmd", "{bundle}/a", "{bundle}/b"], "/tmp/x")
        self.assertEqual(argv, ["cmd", "/tmp/x/a", "/tmp/x/b"])

    def test_template_tokens_not_mutated(self):
        tokens = ["cmd", "{bundle}"]
        tr.build_argv(tokens, "/tmp/x")
        self.assertEqual(tokens, ["cmd", "{bundle}"])


# --------------------------------------------------------------------------
# L. _classify
# --------------------------------------------------------------------------


class TestClassify(unittest.TestCase):
    def test_exit0_tamper_escaped(self):
        self.assertEqual(tr._classify("DELETE_FILE", 0), "ESCAPED")

    def test_exit0_control_ok(self):
        self.assertEqual(tr._classify(tr.CONTROL_CASE_ID, 0), "CONTROL_OK")

    def test_exit1_tamper_caught(self):
        self.assertEqual(tr._classify("DELETE_FILE", 1), "CAUGHT")

    def test_exit1_control_failed(self):
        self.assertEqual(tr._classify(tr.CONTROL_CASE_ID, 1), "CONTROL_FAILED")

    def test_exit2_tamper_case_error(self):
        self.assertEqual(tr._classify("DELETE_FILE", 2), "CASE_ERROR")

    def test_negative_exit_control_case_error(self):
        self.assertEqual(tr._classify(tr.CONTROL_CASE_ID, -9), "CASE_ERROR")


# --------------------------------------------------------------------------
# M. End-to-end run() with the strict verifier -- everything CAUGHT
# --------------------------------------------------------------------------


class TestRunStrictVerifier(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report, cls.exit_code = tr.run(FIXTURE, STRICT_VERIFIER)

    def test_overall_exit_code_zero(self):
        self.assertEqual(self.exit_code, 0)

    def test_control_ok(self):
        self.assertEqual(self.report["control"]["outcome"], "CONTROL_OK")

    def test_no_escaped_cases(self):
        self.assertEqual(self.report["escaped_cases"], [])

    def test_summary_counts(self):
        summary = self.report["summary"]
        self.assertEqual(summary["caught"], 6)
        self.assertEqual(summary["escaped"], 0)
        self.assertEqual(summary["case_errors"], 0)
        self.assertTrue(summary["control_ok"])
        self.assertEqual(summary["total_tamper_cases"], 6)


def _make_strict_case_test(case_id):
    def test(self):
        report, _ = tr.run(FIXTURE, STRICT_VERIFIER)
        by_id = {c["case_id"]: c for c in report["cases"]}
        self.assertEqual(by_id[case_id]["outcome"], "CAUGHT")

    test.__name__ = f"test_case_{case_id.lower()}_caught"
    return test


for _cid in TAMPER_CASE_IDS:
    setattr(TestRunStrictVerifier, f"test_case_{_cid.lower()}_caught", _make_strict_case_test(_cid))


# --------------------------------------------------------------------------
# N. End-to-end run() with the weak (rubber stamp) verifier -- everything ESCAPED
# --------------------------------------------------------------------------


class TestRunWeakVerifier(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report, cls.exit_code = tr.run(FIXTURE, WEAK_VERIFIER)

    def test_overall_exit_code_one(self):
        self.assertEqual(self.exit_code, 1)

    def test_control_still_ok(self):
        self.assertEqual(self.report["control"]["outcome"], "CONTROL_OK")

    def test_escaped_cases_lists_all_six(self):
        self.assertEqual(sorted(self.report["escaped_cases"]), sorted(TAMPER_CASE_IDS))

    def test_summary_counts(self):
        summary = self.report["summary"]
        self.assertEqual(summary["caught"], 0)
        self.assertEqual(summary["escaped"], 6)
        self.assertEqual(summary["case_errors"], 0)


def _make_weak_case_test(case_id):
    def test(self):
        report, _ = tr.run(FIXTURE, WEAK_VERIFIER)
        by_id = {c["case_id"]: c for c in report["cases"]}
        self.assertEqual(by_id[case_id]["outcome"], "ESCAPED")

    return test


for _cid in TAMPER_CASE_IDS:
    setattr(TestRunWeakVerifier, f"test_case_{_cid.lower()}_escaped", _make_weak_case_test(_cid))


# --------------------------------------------------------------------------
# O. NO_OP control failing
# --------------------------------------------------------------------------


class TestControlFailed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report, cls.exit_code = tr.run(FIXTURE, ALWAYS_REJECT_VERIFIER)

    def test_control_outcome_failed(self):
        self.assertEqual(self.report["control"]["outcome"], "CONTROL_FAILED")

    def test_exit_code_one_despite_all_tampers_caught(self):
        summary = self.report["summary"]
        self.assertEqual(summary["caught"], 6)
        self.assertEqual(self.exit_code, 1)

    def test_summary_control_ok_false(self):
        self.assertFalse(self.report["summary"]["control_ok"])


# --------------------------------------------------------------------------
# P. CASE_ERROR scenarios
# --------------------------------------------------------------------------


class TestCaseErrors(unittest.TestCase):
    def test_nonexistent_verifier_all_case_error(self):
        report, exit_code = tr.run(FIXTURE, NONEXISTENT_VERIFIER)
        self.assertEqual(exit_code, 1)
        for case in report["cases"]:
            self.assertEqual(case["outcome"], "CASE_ERROR")
            self.assertIn("not found", case["error"])
        self.assertEqual(report["control"]["outcome"], "CASE_ERROR")

    def test_timeout_produces_case_error(self):
        report, exit_code = tr.run(FIXTURE, SLOW_VERIFIER, timeout=0.5)
        self.assertEqual(exit_code, 1)
        self.assertEqual(report["control"]["outcome"], "CASE_ERROR")
        self.assertIn("timed out", report["control"]["error"])

    def test_wrapped_crash_is_case_error(self):
        report, exit_code = tr.run(FIXTURE, CRASHING_VERIFIER)
        self.assertEqual(report["control"]["outcome"], "CASE_ERROR")
        for case in report["cases"]:
            self.assertEqual(case["outcome"], "CASE_ERROR")

    def test_uncaught_crash_documented_limitation(self):
        # Pinning test for a documented limitation: CPython's default exit
        # code for an unhandled exception is 1, which is indistinguishable
        # from a legitimate "flagged a problem" result. See README.md.
        report, _ = tr.run(FIXTURE, UNCAUGHT_CRASH_VERIFIER)
        self.assertEqual(report["control"]["outcome"], "CONTROL_FAILED")
        for case in report["cases"]:
            self.assertEqual(case["outcome"], "CAUGHT")

    def test_exit2_stub_is_case_error(self):
        report, exit_code = tr.run(FIXTURE, EXIT2_VERIFIER)
        self.assertEqual(exit_code, 1)
        self.assertEqual(report["control"]["outcome"], "CASE_ERROR")
        for case in report["cases"]:
            self.assertEqual(case["outcome"], "CASE_ERROR")

    def test_single_failing_case_does_not_abort_run(self):
        report, exit_code = tr.run(FIXTURE, FLAKY_VERIFIER)
        by_id = {c["case_id"]: c for c in report["cases"]}
        self.assertEqual(by_id["DELETE_FILE"]["outcome"], "CASE_ERROR")
        # every other case still got a real result from the real verifier
        for cid in TAMPER_CASE_IDS:
            if cid != "DELETE_FILE":
                self.assertEqual(by_id[cid]["outcome"], "CAUGHT")
        self.assertEqual(report["control"]["outcome"], "CONTROL_OK")
        self.assertEqual(exit_code, 1)


# --------------------------------------------------------------------------
# Q. Exit codes via real subprocess
# --------------------------------------------------------------------------


class TestExitCodesViaSubprocess(unittest.TestCase):
    def test_exit_zero_strict_verifier(self):
        proc = run_cli(
            [
                "--fixture",
                os.path.relpath(FIXTURE, HERE),
                "--verifier",
                STRICT_VERIFIER,
            ]
        )
        self.assertEqual(proc.returncode, 0)

    def test_exit_one_weak_verifier(self):
        proc = run_cli(
            [
                "--fixture",
                os.path.relpath(FIXTURE, HERE),
                "--verifier",
                WEAK_VERIFIER,
            ]
        )
        self.assertEqual(proc.returncode, 1)

    def test_exit_two_bad_fixture(self):
        proc = run_cli(
            [
                "--fixture",
                "no_such_fixture_dir_xyz",
                "--verifier",
                STRICT_VERIFIER,
            ]
        )
        self.assertEqual(proc.returncode, 2)


# --------------------------------------------------------------------------
# R. Original fixture never modified
# --------------------------------------------------------------------------


class TestFixtureNeverModified(unittest.TestCase):
    def test_unmodified_after_strict_run(self):
        before = tree_hash(FIXTURE)
        tr.run(FIXTURE, STRICT_VERIFIER)
        after = tree_hash(FIXTURE)
        self.assertEqual(before, after)

    def test_unmodified_after_weak_run(self):
        before = tree_hash(FIXTURE)
        tr.run(FIXTURE, WEAK_VERIFIER)
        after = tree_hash(FIXTURE)
        self.assertEqual(before, after)

    def test_unmodified_after_case_error_run(self):
        before = tree_hash(FIXTURE)
        tr.run(FIXTURE, NONEXISTENT_VERIFIER)
        after = tree_hash(FIXTURE)
        self.assertEqual(before, after)


# --------------------------------------------------------------------------
# S. Byte-stable report across two runs
# --------------------------------------------------------------------------


class TestByteStability(unittest.TestCase):
    def test_two_in_process_runs_identical(self):
        report_a, _ = tr.run(FIXTURE, STRICT_VERIFIER)
        report_b, _ = tr.run(FIXTURE, STRICT_VERIFIER)
        self.assertEqual(tr.canonical_dumps(report_a), tr.canonical_dumps(report_b))

    def test_two_subprocess_runs_identical_stdout(self):
        args = [
            "--fixture",
            os.path.relpath(FIXTURE, HERE),
            "--verifier",
            STRICT_VERIFIER,
        ]
        proc_a = run_cli(args)
        proc_b = run_cli(args)
        self.assertEqual(proc_a.stdout, proc_b.stdout)


# --------------------------------------------------------------------------
# T. Permuted case order -> identical output
# --------------------------------------------------------------------------


class TestPermutedCaseOrder(unittest.TestCase):
    def test_reversed_order_identical_output(self):
        report_a, _ = tr.run(FIXTURE, STRICT_VERIFIER, case_order=TAMPER_CASE_IDS)
        report_b, _ = tr.run(FIXTURE, STRICT_VERIFIER, case_order=list(reversed(TAMPER_CASE_IDS)))
        self.assertEqual(tr.canonical_dumps(report_a), tr.canonical_dumps(report_b))

    def test_arbitrary_shuffle_identical_output(self):
        shuffled = [
            "STALE_HASH",
            "ADD_UNLISTED_FILE",
            "DELETE_FILE",
            "ALTER_JSON_FIELD",
            "TRUNCATE_FILE",
            "MUTATE_BYTE",
        ]
        self.assertEqual(sorted(shuffled), sorted(TAMPER_CASE_IDS))
        report_a, _ = tr.run(FIXTURE, STRICT_VERIFIER, case_order=TAMPER_CASE_IDS)
        report_b, _ = tr.run(FIXTURE, STRICT_VERIFIER, case_order=shuffled)
        self.assertEqual(tr.canonical_dumps(report_a), tr.canonical_dumps(report_b))


# --------------------------------------------------------------------------
# U. Tiebreak breaking a tie (report-shaped synthetic data)
# --------------------------------------------------------------------------


class TestTiebreakOnReportShapedData(unittest.TestCase):
    def test_two_case_error_entries_same_case_id_ordered_by_dump(self):
        items = [
            {"case_id": "DELETE_FILE", "target": "z.txt", "error": "zzz"},
            {"case_id": "DELETE_FILE", "target": "a.txt", "error": "aaa"},
        ]
        out = tr.sorted_with_tiebreak(items, primary_key=lambda i: i["case_id"])
        self.assertEqual(out[0]["target"], "a.txt")

    def test_escaped_case_string_list_tie_is_a_no_op(self):
        out = tr.sorted_with_tiebreak(["DELETE_FILE", "DELETE_FILE"], primary_key=lambda s: s)
        self.assertEqual(out, ["DELETE_FILE", "DELETE_FILE"])


# --------------------------------------------------------------------------
# V. Binary and unicode file handling
# --------------------------------------------------------------------------


class TestBinaryAndUnicodeFiles(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="tamperrun_test_")

    def tearDown(self):
        self._tmp.cleanup()

    def test_mutate_byte_targets_binary_file_by_default(self):
        copy = make_copy(self._tmp.name)
        target, _ = tr.apply_mutate_byte(copy)
        self.assertEqual(target, "binary.dat")

    def test_mutate_byte_falls_back_to_unicode_file(self):
        copy = make_copy(self._tmp.name)
        os.remove(os.path.join(copy, "binary.dat"))
        os.remove(os.path.join(copy, "data.txt"))
        target, _ = tr.apply_mutate_byte(copy)
        self.assertEqual(target, "unicode.txt")
        self.assertTrue(os.path.isfile(os.path.join(copy, "unicode.txt")))

    def test_mutate_byte_on_unicode_file_size_unchanged(self):
        copy = make_copy(self._tmp.name)
        before_size = os.path.getsize(os.path.join(copy, "unicode.txt"))
        os.remove(os.path.join(copy, "binary.dat"))
        os.remove(os.path.join(copy, "data.txt"))
        tr.apply_mutate_byte(copy)
        after_size = os.path.getsize(os.path.join(copy, "unicode.txt"))
        self.assertEqual(before_size, after_size)

    def test_truncate_unicode_file_does_not_crash(self):
        copy = make_copy(self._tmp.name)
        for name in ("data.txt", "binary.dat", "notes.txt", "config.json"):
            os.remove(os.path.join(copy, name))
        os.remove(os.path.join(copy, "nested", "more.txt"))
        target, _ = tr.apply_truncate_file(copy)
        self.assertEqual(target, "unicode.txt")


# --------------------------------------------------------------------------
# V2. Bug-hunt regression: an unrelated broken/escaping symlink elsewhere in
# the fixture must not poison candidate selection for cases that have a
# perfectly good target. See README.md "Bug found during the bug hunt".
# --------------------------------------------------------------------------


class TestBrokenSymlinkDoesNotPoisonUnrelatedCases(unittest.TestCase):
    def _fixture_with_escaping_symlink(self, tmp):
        variant = os.path.join(tmp, "variant_with_symlink")
        shutil.copytree(FIXTURE, variant, symlinks=True)
        # An absolute-target symlink: after this directory is itself copied
        # again (as every tamper case's isolated workspace is), the link
        # still points at *this* variant directory, which is outside the
        # new copy -- i.e. it "escapes" the copy, exactly like a fixture
        # symlink pointing outside the fixture entirely would.
        os.symlink(
            os.path.join(variant, "nonexistent_target.txt"),
            os.path.join(variant, "broken_link.txt"),
        )
        return variant

    def test_mutate_byte_unaffected_by_unrelated_broken_symlink(self):
        with tempfile.TemporaryDirectory(prefix="tamperrun_test_") as tmp:
            variant = self._fixture_with_escaping_symlink(tmp)
            case_dir = os.path.join(tmp, "case_copy")
            shutil.copytree(variant, case_dir, symlinks=True)
            target, _ = tr.apply_mutate_byte(case_dir)
            self.assertEqual(target, "binary.dat")

    def test_truncate_file_unaffected_by_unrelated_broken_symlink(self):
        with tempfile.TemporaryDirectory(prefix="tamperrun_test_") as tmp:
            variant = self._fixture_with_escaping_symlink(tmp)
            case_dir = os.path.join(tmp, "case_copy")
            shutil.copytree(variant, case_dir, symlinks=True)
            target, _ = tr.apply_truncate_file(case_dir)
            self.assertEqual(target, "notes.txt")

    def test_file_size_or_none_returns_none_for_escaping_symlink(self):
        with tempfile.TemporaryDirectory(prefix="tamperrun_test_") as tmp:
            variant = self._fixture_with_escaping_symlink(tmp)
            case_dir = os.path.join(tmp, "case_copy")
            shutil.copytree(variant, case_dir, symlinks=True)
            self.assertIsNone(tr._file_size_or_none(case_dir, "broken_link.txt"))

    def test_alter_json_field_unaffected_by_unrelated_broken_symlink(self):
        with tempfile.TemporaryDirectory(prefix="tamperrun_test_") as tmp:
            variant = self._fixture_with_escaping_symlink(tmp)
            case_dir = os.path.join(tmp, "case_copy")
            shutil.copytree(variant, case_dir, symlinks=True)
            target, _ = tr.apply_alter_json_field(case_dir)
            self.assertEqual(target, "config.json")

    def test_stale_hash_unaffected_by_unrelated_broken_symlink(self):
        with tempfile.TemporaryDirectory(prefix="tamperrun_test_") as tmp:
            variant = self._fixture_with_escaping_symlink(tmp)
            case_dir = os.path.join(tmp, "case_copy")
            shutil.copytree(variant, case_dir, symlinks=True)
            target, _ = tr.apply_stale_hash(case_dir)
            self.assertEqual(target, "manifest.json")


# --------------------------------------------------------------------------
# W. Malformed CLI args -> exit 2
# --------------------------------------------------------------------------


class TestMalformedArgs(unittest.TestCase):
    def test_missing_fixture_flag(self):
        proc = run_cli(["--verifier", STRICT_VERIFIER])
        self.assertEqual(proc.returncode, 2)

    def test_missing_verifier_flag(self):
        proc = run_cli(["--fixture", os.path.relpath(FIXTURE, HERE)])
        self.assertEqual(proc.returncode, 2)

    def test_unknown_flag(self):
        proc = run_cli(
            [
                "--fixture",
                os.path.relpath(FIXTURE, HERE),
                "--verifier",
                STRICT_VERIFIER,
                "--not-a-real-flag",
            ]
        )
        self.assertEqual(proc.returncode, 2)

    def test_empty_verifier_string(self):
        proc = run_cli(["--fixture", os.path.relpath(FIXTURE, HERE), "--verifier", ""])
        self.assertEqual(proc.returncode, 2)

    def test_unwritable_output_path(self):
        proc = run_cli(
            [
                "--fixture",
                os.path.relpath(FIXTURE, HERE),
                "--verifier",
                STRICT_VERIFIER,
                "-o",
                "/root/definitely_unwritable/out.json",
            ]
        )
        self.assertEqual(proc.returncode, 2)


# --------------------------------------------------------------------------
# X. Determinism + relocation (in-suite; the full real-run record with sha256
# values lives in captured_output.txt as required by the spec)
# --------------------------------------------------------------------------


class TestRelocation(unittest.TestCase):
    def test_relocated_and_renamed_fixture_same_report(self):
        report_original, _ = tr.run(FIXTURE, STRICT_VERIFIER)
        with tempfile.TemporaryDirectory(prefix="tamperrun_reloc_") as tmp:
            relocated = os.path.join(tmp, "totally_different_name_98765")
            shutil.copytree(FIXTURE, relocated, symlinks=True)
            report_relocated, _ = tr.run(relocated, STRICT_VERIFIER)
        self.assertEqual(tr.canonical_dumps(report_original), tr.canonical_dumps(report_relocated))

    def test_relocated_fixture_hash_matches_original(self):
        with tempfile.TemporaryDirectory(prefix="tamperrun_reloc_") as tmp:
            relocated = os.path.join(tmp, "another_different_name")
            shutil.copytree(FIXTURE, relocated, symlinks=True)
            self.assertEqual(tree_hash(FIXTURE), tree_hash(relocated))


if __name__ == "__main__":
    unittest.main(verbosity=2)
