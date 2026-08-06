#!/usr/bin/env python3
"""test_sortdetect.py -- test suite for sortdetect.py.

Real-tool-dependent tests (the "load-bearing set": all three current tools
reported STABLE, all three pre-fix controls reported per their real,
verified behaviour) locate the three real target tools via environment
variables, defaulting to the paths they live at in this build environment:

    SORTDETECT_CONSOLIDATE_PATH   (default: /mnt/user-data/outputs/fixes/consolidate/consolidate.py)
    SORTDETECT_SCHEMA_CHECK_PATH  (default: /mnt/user-data/outputs/fixes/schema-checker/schema_check.py)
    SORTDETECT_NDSCAN_PATH        (default: /mnt/user-data/outputs/fixes/nondeterminism-scanner/ndscan.py)

If a given path does not exist, the tests that need it are skipped (not
failed) so the rest of the suite (controls/, toy_tool/, and every unit-level
test) still runs standalone using only what this package ships. See
README.md "How to point it at any tool".

Every test in this file does REAL work: real subprocess calls, real file
I/O, real hashing. Nothing here reads sortdetect's target tools' source
code to predict output.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_DIR)

import sortdetect  # noqa: E402

PY = sys.executable
SORTDETECT_PY = os.path.join(THIS_DIR, "sortdetect.py")
FIXTURES_DIR = os.path.join(THIS_DIR, "fixtures")
CONTROLS_DIR = os.path.join(THIS_DIR, "controls")
TOY_DIR = os.path.join(THIS_DIR, "toy_tool")
MAKE_FIXTURES_PY = os.path.join(THIS_DIR, "make_fixtures.py")

CONSOLIDATE_PATH = os.environ.get(
    "SORTDETECT_CONSOLIDATE_PATH", "/mnt/user-data/outputs/fixes/consolidate/consolidate.py")
SCHEMA_CHECK_PATH = os.environ.get(
    "SORTDETECT_SCHEMA_CHECK_PATH", "/mnt/user-data/outputs/fixes/schema-checker/schema_check.py")
NDSCAN_PATH = os.environ.get(
    "SORTDETECT_NDSCAN_PATH", "/mnt/user-data/outputs/fixes/nondeterminism-scanner/ndscan.py")

HAVE_CONSOLIDATE = os.path.isfile(CONSOLIDATE_PATH)
HAVE_SCHEMA_CHECK = os.path.isfile(SCHEMA_CHECK_PATH)
HAVE_NDSCAN = os.path.isfile(NDSCAN_PATH)

skip_no_consolidate = unittest.skipUnless(HAVE_CONSOLIDATE, "real consolidate.py not found at %s" % CONSOLIDATE_PATH)
skip_no_schema_check = unittest.skipUnless(HAVE_SCHEMA_CHECK, "real schema_check.py not found at %s" % SCHEMA_CHECK_PATH)
skip_no_ndscan = unittest.skipUnless(HAVE_NDSCAN, "real ndscan.py not found at %s" % NDSCAN_PATH)


def run_cli(args, timeout=60):
    proc = subprocess.run(
        [PY, SORTDETECT_PY] + args,
        cwd=THIS_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


def run_cli_json(args, timeout=60):
    code, out, err = run_cli(args, timeout=timeout)
    try:
        doc = json.loads(out.decode("utf-8"))
    except ValueError:
        doc = None
    return code, doc, out, err


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    with open(path, "rb") as fh:
        return sha256_bytes(fh.read())


def tree_sha256(root: str) -> str:
    """sha256 over (relpath, content) of every file under root, order-independent
    of physical filesystem listing (we sort relpaths ourselves)."""
    h = hashlib.sha256()
    rels = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rels.append(os.path.relpath(full, root).replace(os.sep, "/"))
    for rel in sorted(rels):
        with open(os.path.join(root, rel), "rb") as fh:
            data = fh.read()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(hashlib.sha256(data).digest())
        h.update(b"\0")
    return h.hexdigest()


# ---------------------------------------------------------------------------
# A. canonical JSON helpers
# ---------------------------------------------------------------------------

class TestCanonicalJson(unittest.TestCase):
    def test_canonical_dumps_ends_with_single_newline(self):
        text = sortdetect.canonical_dumps({"a": 1})
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))

    def test_canonical_dumps_sorts_keys(self):
        text = sortdetect.canonical_dumps({"b": 1, "a": 2})
        self.assertTrue(text.startswith('{"a":2,"b":1}'))

    def test_canonical_dumps_compact_separators(self):
        text = sortdetect.canonical_dumps({"a": [1, 2], "b": 3})
        self.assertNotIn(", ", text)
        self.assertNotIn(": ", text)

    def test_canonical_dumps_ascii_only(self):
        text = sortdetect.canonical_dumps({"a": "é"})
        self.assertTrue(all(ord(c) < 128 for c in text))

    def test_canonical_text_has_no_trailing_newline(self):
        text = sortdetect.canonical_text({"a": 1})
        self.assertFalse(text.endswith("\n"))

    def test_canonical_text_matches_canonical_dumps_minus_newline(self):
        obj = {"z": 1, "a": [3, 2, 1], "m": None}
        self.assertEqual(sortdetect.canonical_text(obj) + "\n", sortdetect.canonical_dumps(obj))

    def test_canonical_dumps_deterministic_across_calls(self):
        obj = {"x": 1, "y": [1, 2, 3], "z": {"nested": True}}
        self.assertEqual(sortdetect.canonical_dumps(obj), sortdetect.canonical_dumps(obj))

    def test_canonical_dumps_distinguishes_int_and_float(self):
        self.assertNotEqual(sortdetect.canonical_text(1), sortdetect.canonical_text(1.0))

    def test_canonical_dumps_distinguishes_bool_and_int(self):
        self.assertNotEqual(sortdetect.canonical_text(True), sortdetect.canonical_text(1))

    def test_write_canonical_uses_unix_newlines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.json")
            sortdetect.write_canonical(path, {"a": 1})
            with open(path, "rb") as fh:
                data = fh.read()
            self.assertNotIn(b"\r\n", data)
            self.assertTrue(data.endswith(b"\n"))


# ---------------------------------------------------------------------------
# B. JSON Pointer utilities
# ---------------------------------------------------------------------------

class TestJsonPointer(unittest.TestCase):
    def test_pointer_tokens_root_is_empty_list(self):
        self.assertEqual(sortdetect.pointer_tokens(""), [])

    def test_pointer_tokens_none_is_empty_list(self):
        self.assertEqual(sortdetect.pointer_tokens(None), [])

    def test_pointer_tokens_single_segment(self):
        self.assertEqual(sortdetect.pointer_tokens("/foo"), ["foo"])

    def test_pointer_tokens_multi_segment(self):
        self.assertEqual(sortdetect.pointer_tokens("/foo/bar/2"), ["foo", "bar", "2"])

    def test_pointer_tokens_unescapes_tilde(self):
        self.assertEqual(sortdetect.pointer_tokens("/a~0b"), ["a~b"])

    def test_pointer_tokens_unescapes_slash(self):
        self.assertEqual(sortdetect.pointer_tokens("/a~1b"), ["a/b"])

    def test_pointer_tokens_rejects_missing_leading_slash(self):
        with self.assertRaises(ValueError):
            sortdetect.pointer_tokens("foo")

    def test_pointer_get_root(self):
        doc = {"a": 1}
        self.assertEqual(sortdetect.pointer_get(doc, ""), doc)

    def test_pointer_get_object_key(self):
        doc = {"issues": [1, 2, 3]}
        self.assertEqual(sortdetect.pointer_get(doc, "/issues"), [1, 2, 3])

    def test_pointer_get_array_index(self):
        doc = {"issues": [10, 20, 30]}
        self.assertEqual(sortdetect.pointer_get(doc, "/issues/1"), 20)

    def test_pointer_get_nested(self):
        doc = {"a": {"b": [{"c": 5}]}}
        self.assertEqual(sortdetect.pointer_get(doc, "/a/b/0/c"), 5)

    def test_pointer_get_missing_key_raises(self):
        with self.assertRaises(KeyError):
            sortdetect.pointer_get({"a": 1}, "/b")

    def test_pointer_get_out_of_range_raises(self):
        with self.assertRaises(IndexError):
            sortdetect.pointer_get([1, 2], "/5")

    def test_pointer_set_object_key(self):
        doc = {"issues": [1, 2, 3]}
        sortdetect.pointer_set(doc, "/issues", [9])
        self.assertEqual(doc, {"issues": [9]})

    def test_pointer_set_array_index(self):
        doc = {"a": [1, 2, 3]}
        sortdetect.pointer_set(doc, "/a/1", 99)
        self.assertEqual(doc["a"], [1, 99, 3])

    def test_pointer_set_root_raises(self):
        with self.assertRaises(ValueError):
            sortdetect.pointer_set({"a": 1}, "", {"b": 2})


# ---------------------------------------------------------------------------
# C. deterministic permutation generation
# ---------------------------------------------------------------------------

class TestPermutationGenerator(unittest.TestCase):
    def test_generate_returns_requested_count(self):
        for k in (2, 3, 4, 5, 6, 7):
            for n in (1, 2, 4, 6, 10):
                with self.subTest(k=k, n=n):
                    perms = sortdetect.generate_index_permutations(k, n)
                    self.assertEqual(len(perms), n)

    def test_every_output_is_a_valid_permutation(self):
        for k in (2, 3, 4, 5, 8):
            perms = sortdetect.generate_index_permutations(k, 6)
            for p in perms:
                with self.subTest(k=k, p=p):
                    self.assertEqual(sorted(p), list(range(k)))

    def test_covers_full_record_set_each_time(self):
        for k in (3, 5):
            for p in sortdetect.generate_index_permutations(k, 6):
                self.assertEqual(len(p), k)
                self.assertEqual(len(set(p)), k)

    def test_deterministic_across_repeated_calls(self):
        for _ in range(5):
            a = sortdetect.generate_index_permutations(5, 6)
            b = sortdetect.generate_index_permutations(5, 6)
            self.assertEqual(a, b)

    def test_deterministic_across_fresh_interpreter(self):
        code = "import sortdetect, json, sys; sys.path.insert(0, %r); print(json.dumps(sortdetect.generate_index_permutations(5, 6)))" % THIS_DIR
        out1 = subprocess.run([PY, "-c", code], cwd=THIS_DIR, stdout=subprocess.PIPE).stdout
        out2 = subprocess.run([PY, "-c", code], cwd=THIS_DIR, stdout=subprocess.PIPE).stdout
        self.assertEqual(out1, out2)

    def test_includes_identity_permutation(self):
        perms = sortdetect.generate_index_permutations(4, 6)
        self.assertIn(list(range(4)), perms)

    def test_includes_full_reversal(self):
        perms = sortdetect.generate_index_permutations(4, 6)
        self.assertIn(list(reversed(range(4))), perms)

    def test_k_zero_returns_empty_lists(self):
        perms = sortdetect.generate_index_permutations(0, 3)
        self.assertEqual(perms, [[], [], []])

    def test_k_one_returns_single_index_lists(self):
        perms = sortdetect.generate_index_permutations(1, 3)
        self.assertEqual(perms, [[0], [0], [0]])

    def test_n_larger_than_distinct_permutations_pads_deterministically(self):
        perms = sortdetect.generate_index_permutations(2, 6)
        self.assertEqual(len(perms), 6)
        for p in perms:
            self.assertEqual(sorted(p), [0, 1])

    def test_apply_index_permutation_basic(self):
        self.assertEqual(sortdetect.apply_index_permutation(["a", "b", "c"], [2, 0, 1]), ["c", "a", "b"])

    def test_apply_index_permutation_identity(self):
        items = ["x", "y", "z"]
        self.assertEqual(sortdetect.apply_index_permutation(items, [0, 1, 2]), items)

    def test_apply_index_permutation_reversal(self):
        self.assertEqual(sortdetect.apply_index_permutation([1, 2, 3], [2, 1, 0]), [3, 2, 1])

    def test_apply_index_permutation_preserves_length(self):
        items = list(range(9))
        for p in sortdetect.generate_index_permutations(9, 4):
            self.assertEqual(len(sortdetect.apply_index_permutation(items, p)), 9)

    def test_generate_no_two_adjacent_requested_permutations_required_equal(self):
        # not a hard requirement, but for k >= 3 with n <= number of distinct
        # rotations we should not be trivially repeating every single time
        perms = sortdetect.generate_index_permutations(4, 4)
        self.assertEqual(len(set(tuple(p) for p in perms)), 4)


for _k in (2, 3, 4, 5, 6, 7, 8):
    def _make_full_coverage_test(k):
        def _test(self):
            perms = sortdetect.generate_index_permutations(k, 6)
            for p in perms:
                self.assertEqual(set(p), set(range(k)))
        return _test
    setattr(TestPermutationGenerator, "test_full_coverage_k_%d" % _k, _make_full_coverage_test(_k))


# ---------------------------------------------------------------------------
# D/E/F. fixture materialisation, list-reorder, dict-key-reorder, file-order
# ---------------------------------------------------------------------------

class TestFixtureMaterialisation(unittest.TestCase):
    def test_materialise_copies_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = os.path.join(tmp, "copy")
            os.makedirs(dst)
            sortdetect.materialise_fixture_copy(os.path.join(FIXTURES_DIR, "consolidate"), dst)
            self.assertTrue(os.path.isfile(os.path.join(dst, "reports", "tied.json")))

    def test_materialise_preserves_empty_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = os.path.join(tmp, "copy")
            os.makedirs(dst)
            sortdetect.materialise_fixture_copy(os.path.join(FIXTURES_DIR, "consolidate"), dst)
            self.assertTrue(os.path.isdir(os.path.join(dst, "reports", "empty_subdir")))

    def test_materialise_ndscan_preserves_empty_pkg(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = os.path.join(tmp, "copy")
            os.makedirs(dst)
            sortdetect.materialise_fixture_copy(os.path.join(FIXTURES_DIR, "ndscan"), dst)
            self.assertTrue(os.path.isdir(os.path.join(dst, "src", "empty_pkg")))

    def test_materialise_does_not_mutate_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = os.path.join(tmp, "copy")
            os.makedirs(dst)
            src = os.path.join(FIXTURES_DIR, "schema_check")
            before = tree_sha256(src)
            sortdetect.materialise_fixture_copy(src, dst)
            after = tree_sha256(src)
            self.assertEqual(before, after)


class TestApplyListReorder(unittest.TestCase):
    def _copy_consolidate(self, tmp):
        dst = os.path.join(tmp, "copy")
        os.makedirs(dst)
        sortdetect.materialise_fixture_copy(os.path.join(FIXTURES_DIR, "consolidate"), dst)
        return dst

    def test_reorders_issues_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = self._copy_consolidate(tmp)
            n = sortdetect.apply_list_reorder(dst, "reports/tied.json", "/issues", [5, 4, 3, 2, 1, 0])
            self.assertEqual(n, 6)
            with open(os.path.join(dst, "reports", "tied.json")) as fh:
                doc = json.load(fh)
            self.assertEqual(doc["issues"][0]["issue"], 0)

    def test_reorder_preserves_record_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = self._copy_consolidate(tmp)
            sortdetect.apply_list_reorder(dst, "reports/tied.json", "/issues", [1, 0, 2, 3, 4, 5])
            with open(os.path.join(dst, "reports", "tied.json")) as fh:
                doc = json.load(fh)
            self.assertEqual(len(doc["issues"]), 6)

    def test_reorder_preserves_other_top_level_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = self._copy_consolidate(tmp)
            sortdetect.apply_list_reorder(dst, "reports/tied.json", "/issues", [1, 0, 2, 3, 4, 5])
            with open(os.path.join(dst, "reports", "tied.json")) as fh:
                doc = json.load(fh)
            self.assertIn("totals", doc)
            self.assertEqual(doc["totals"]["roster_tasks"], 6)

    def test_reorder_root_list_replaces_whole_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = os.path.join(tmp, "copy")
            os.makedirs(dst)
            sortdetect.materialise_fixture_copy(os.path.join(FIXTURES_DIR, "schema_check"), dst)
            sortdetect.apply_list_reorder(dst, "payload.json", "", [4, 3, 2, 1, 0])
            with open(os.path.join(dst, "payload.json")) as fh:
                doc = json.load(fh)
            self.assertEqual(doc, ["echo", "delta", "charlie", "bravo", "alpha"])

    def test_reorder_wrong_length_permutation_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = self._copy_consolidate(tmp)
            with self.assertRaises(ValueError):
                sortdetect.apply_list_reorder(dst, "reports/tied.json", "/issues", [0, 1])

    def test_reorder_non_list_pointer_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = self._copy_consolidate(tmp)
            with self.assertRaises(ValueError):
                sortdetect.apply_list_reorder(dst, "reports/tied.json", "/totals", [0])


class TestApplyDictKeyReorder(unittest.TestCase):
    def test_reorders_object_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "obj.json")
            with open(path, "w") as fh:
                json.dump({"a": 1, "b": 2, "c": 3}, fh)
            n = sortdetect.apply_dict_key_reorder(tmp, "obj.json", [2, 0, 1])
            self.assertEqual(n, 3)
            with open(path) as fh:
                text = fh.read()
            self.assertLess(text.index('"c"'), text.index('"a"'))
            self.assertLess(text.index('"a"'), text.index('"b"'))

    def test_reorder_preserves_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "obj.json")
            with open(path, "w") as fh:
                json.dump({"a": 1, "b": 2}, fh)
            sortdetect.apply_dict_key_reorder(tmp, "obj.json", [1, 0])
            with open(path) as fh:
                doc = json.load(fh)
            self.assertEqual(doc, {"a": 1, "b": 2})


class TestApplyFileCreationOrder(unittest.TestCase):
    def _copy_ndscan(self, tmp):
        dst = os.path.join(tmp, "copy")
        os.makedirs(dst)
        sortdetect.materialise_fixture_copy(os.path.join(FIXTURES_DIR, "ndscan"), dst)
        return dst

    def _read_all(self, dst, filenames):
        result = {}
        for n in filenames:
            with open(os.path.join(dst, "src", n), "rb") as fh:
                result[n] = fh.read()
        return result

    def test_recreates_all_files_with_same_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = self._copy_ndscan(tmp)
            filenames = ["a_wallclock.py", "b_listdir.py", "c_random.py"]
            before = self._read_all(dst, filenames)
            sortdetect.apply_file_creation_order(dst, "src", filenames, [2, 0, 1])
            after = self._read_all(dst, filenames)
            self.assertEqual(before, after)

    def test_preserves_empty_pkg_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = self._copy_ndscan(tmp)
            filenames = ["a_wallclock.py", "b_listdir.py", "c_random.py"]
            sortdetect.apply_file_creation_order(dst, "src", filenames, [1, 2, 0])
            self.assertTrue(os.path.isdir(os.path.join(dst, "src", "empty_pkg")))

    def test_returns_file_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = self._copy_ndscan(tmp)
            filenames = ["a_wallclock.py", "b_listdir.py", "c_random.py"]
            n = sortdetect.apply_file_creation_order(dst, "src", filenames, [0, 1, 2])
            self.assertEqual(n, 3)

    def test_list_empty_dirs_finds_truly_empty_subtree(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "a", "b"))
            os.makedirs(os.path.join(tmp, "c"))
            with open(os.path.join(tmp, "c", "f.txt"), "w") as fh:
                fh.write("x")
            empties = sortdetect._list_empty_dirs(tmp)
            self.assertIn("a", empties)
            self.assertIn("a/b", empties)
            self.assertNotIn("c", empties)


# ---------------------------------------------------------------------------
# G. fixture content validation -- fixtures actually tie on every field
# ---------------------------------------------------------------------------

class TestFixtureContentTies(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(FIXTURES_DIR, "consolidate", "reports", "tied.json")) as fh:
            self.consolidate_doc = json.load(fh)

    def test_consolidate_fixture_has_six_issues(self):
        self.assertEqual(len(self.consolidate_doc["issues"]), 6)

    def test_consolidate_fixture_all_task_ids_none(self):
        for item in self.consolidate_doc["issues"]:
            self.assertIsNone(item["task_id"])

    def test_consolidate_fixture_all_details_identical(self):
        details = {item["detail"] for item in self.consolidate_doc["issues"]}
        self.assertEqual(details, {"d"})

    def test_consolidate_fixture_two_true_tie_groups_by_python_equality(self):
        codes = [item["issue"] for item in self.consolidate_doc["issues"]]
        group_a = [c for c in codes if c == 1]
        group_b = [c for c in codes if c == 0]
        self.assertEqual(len(group_a), 3)
        self.assertEqual(len(group_b), 3)

    def test_consolidate_fixture_group_a_members_distinguishable_by_canonical_dump(self):
        group_a = [c for c in (item["issue"] for item in self.consolidate_doc["issues"]) if c == 1]
        dumps = {json.dumps(c) for c in group_a}
        self.assertEqual(len(dumps), 3)

    def test_consolidate_fixture_group_b_members_distinguishable_by_canonical_dump(self):
        group_b = [c for c in (item["issue"] for item in self.consolidate_doc["issues"]) if c == 0]
        dumps = {json.dumps(c) for c in group_b}
        self.assertEqual(len(dumps), 3)

    def test_consolidate_fixture_totals_present_for_adapter_match(self):
        self.assertIn("roster_tasks", self.consolidate_doc["totals"])
        self.assertIn("well_formed_payouts", self.consolidate_doc["totals"])

    def test_schema_check_fixture_payload_is_five_wrong_typed_strings(self):
        with open(os.path.join(FIXTURES_DIR, "schema_check", "payload.json")) as fh:
            payload = json.load(fh)
        self.assertEqual(len(payload), 5)
        self.assertTrue(all(isinstance(x, str) for x in payload))

    def test_schema_check_fixture_schema_requires_integer(self):
        with open(os.path.join(FIXTURES_DIR, "schema_check", "schema.json")) as fh:
            schema = json.load(fh)
        self.assertEqual(schema["root"]["items"]["type"], "integer")

    @skip_no_schema_check
    def test_schema_check_fixture_all_slots_produce_identical_violation_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy2(os.path.join(FIXTURES_DIR, "schema_check", "schema.json"), tmp)
            shutil.copy2(os.path.join(FIXTURES_DIR, "schema_check", "payload.json"), tmp)
            out = os.path.join(tmp, "out.json")
            subprocess.run([PY, SCHEMA_CHECK_PATH, "schema.json", "payload.json", "-o", "out.json"],
                            cwd=tmp, check=False)
            with open(out) as fh:
                report = json.load(fh)
            messages = {v["message"] for v in report["violations"]}
            self.assertEqual(messages, {"expected type integer, got string"})

    def test_ndscan_fixture_has_three_source_files(self):
        py_files = [f for f in os.listdir(os.path.join(FIXTURES_DIR, "ndscan", "src")) if f.endswith(".py")]
        self.assertEqual(len(py_files), 3)

    def test_ndscan_fixture_wallclock_file_contains_time_call(self):
        with open(os.path.join(FIXTURES_DIR, "ndscan", "src", "a_wallclock.py")) as fh:
            self.assertIn("time.time()", fh.read())

    def test_ndscan_fixture_listdir_file_contains_unsorted_listdir(self):
        with open(os.path.join(FIXTURES_DIR, "ndscan", "src", "b_listdir.py")) as fh:
            self.assertIn("os.listdir(", fh.read())

    def test_ndscan_fixture_random_file_contains_random_call(self):
        with open(os.path.join(FIXTURES_DIR, "ndscan", "src", "c_random.py")) as fh:
            self.assertIn("random.random()", fh.read())

    def test_toy_fixture_all_keys_none(self):
        with open(os.path.join(FIXTURES_DIR, "toy", "input.json")) as fh:
            doc = json.load(fh)
        self.assertTrue(all(item["key"] is None for item in doc["items"]))

    def test_toy_fixture_has_four_distinct_labels(self):
        with open(os.path.join(FIXTURES_DIR, "toy", "input.json")) as fh:
            doc = json.load(fh)
        labels = {item["label"] for item in doc["items"]}
        self.assertEqual(len(labels), 4)


# ---------------------------------------------------------------------------
# H. end-to-end CLI: the load-bearing stable/unstable set
# ---------------------------------------------------------------------------

class TestEndToEndLoadBearing(unittest.TestCase):

    @skip_no_consolidate
    def test_consolidate_real_is_stable_exit0(self):
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", CONSOLIDATE_PATH,
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"), "--permutations", "6",
        ])
        self.assertEqual(code, 0, err)
        self.assertTrue(doc["stable"])

    def test_consolidate_control_is_unstable_exit1(self):
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"), "--permutations", "6",
        ])
        self.assertEqual(code, 1, err)
        self.assertFalse(doc["stable"])

    def test_consolidate_control_diffs_are_nonempty(self):
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"), "--permutations", "6",
        ])
        self.assertGreater(len(doc["diffs"]), 0)

    def test_consolidate_control_reports_concrete_swapped_records(self):
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"), "--permutations", "6",
        ])
        moves = doc["distinct_record_moves"]
        self.assertGreater(len(moves), 0)
        for m in moves:
            self.assertIn("baseline_record", m)
            self.assertIn("permutation_record", m)
            self.assertNotEqual(m["baseline_record"], m["permutation_record"])

    @skip_no_schema_check
    def test_schema_check_real_is_stable_exit0(self):
        code, doc, out, err = run_cli_json([
            "--tool", "schema_check", "--tool-path", SCHEMA_CHECK_PATH,
            "--fixture", os.path.join(FIXTURES_DIR, "schema_check"), "--permutations", "6",
        ])
        self.assertEqual(code, 0, err)
        self.assertTrue(doc["stable"])

    def test_schema_check_control_is_stable_exit0(self):
        # Verified true fact (see README): schema_check's violation entries
        # (pointer, code, message) already fully and uniquely describe the
        # record -- no fixture can make the trailing dump tiebreak matter.
        code, doc, out, err = run_cli_json([
            "--tool", "schema_check", "--tool-path", os.path.join(CONTROLS_DIR, "schema_check_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "schema_check"), "--permutations", "6",
        ])
        self.assertEqual(code, 0, err)
        self.assertTrue(doc["stable"])

    @skip_no_ndscan
    def test_ndscan_real_is_stable_exit0(self):
        code, doc, out, err = run_cli_json([
            "--tool", "ndscan", "--tool-path", NDSCAN_PATH,
            "--fixture", os.path.join(FIXTURES_DIR, "ndscan"), "--permutations", "6",
        ])
        self.assertEqual(code, 0, err)
        self.assertTrue(doc["stable"])

    def test_ndscan_control_is_stable_exit0(self):
        # Verified true fact (see README): ndscan's Finding is always fully
        # described by (rule_id, path, line, col, detail) -- distinct AST
        # nodes cannot share (line, col), so no fixture can make the
        # trailing dump tiebreak matter.
        code, doc, out, err = run_cli_json([
            "--tool", "ndscan", "--tool-path", os.path.join(CONTROLS_DIR, "ndscan_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "ndscan"), "--permutations", "6",
        ])
        self.assertEqual(code, 0, err)
        self.assertTrue(doc["stable"])

    def test_toy_fixed_generic_cmd_is_stable_exit0(self):
        code, doc, out, err = run_cli_json([
            "--tool-path", os.path.join(TOY_DIR, "toy_tool.py"),
            "--cmd", "{tool_path} input.json -o {output}",
            "--fixture", os.path.join(FIXTURES_DIR, "toy"),
            "--record-file", "input.json", "--record-pointer", "/items",
            "--output-file", "out.json", "--output-list-pointer", "/sorted",
            "--permute-mode", "list-reorder", "--permutations", "6",
        ])
        self.assertEqual(code, 0, err)
        self.assertTrue(doc["stable"])

    def test_toy_broken_generic_cmd_is_unstable_exit1(self):
        code, doc, out, err = run_cli_json([
            "--tool-path", os.path.join(TOY_DIR, "toy_tool_broken.py"),
            "--cmd", "{tool_path} input.json -o {output}",
            "--fixture", os.path.join(FIXTURES_DIR, "toy"),
            "--record-file", "input.json", "--record-pointer", "/items",
            "--output-file", "out.json", "--output-list-pointer", "/sorted",
            "--permute-mode", "list-reorder", "--permutations", "6",
        ])
        self.assertEqual(code, 1, err)
        self.assertFalse(doc["stable"])

    def test_toy_broken_generic_cmd_diffs_nonempty(self):
        code, doc, out, err = run_cli_json([
            "--tool-path", os.path.join(TOY_DIR, "toy_tool_broken.py"),
            "--cmd", "{tool_path} input.json -o {output}",
            "--fixture", os.path.join(FIXTURES_DIR, "toy"),
            "--record-file", "input.json", "--record-pointer", "/items",
            "--output-file", "out.json", "--output-list-pointer", "/sorted",
            "--permute-mode", "list-reorder", "--permutations", "6",
        ])
        self.assertGreater(len(doc["diffs"]), 0)


_PERM_COUNTS = (2, 3, 6, 9)


def _make_permcount_test(target, expect_stable):
    def _test(self):
        if target == "consolidate_real" and not HAVE_CONSOLIDATE:
            self.skipTest("real consolidate.py not available")
        if target == "schema_check_real" and not HAVE_SCHEMA_CHECK:
            self.skipTest("real schema_check.py not available")
        if target == "ndscan_real" and not HAVE_NDSCAN:
            self.skipTest("real ndscan.py not available")

        table = {
            "consolidate_real": ("consolidate", CONSOLIDATE_PATH, "consolidate"),
            "consolidate_control": ("consolidate", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"), "consolidate"),
            "schema_check_real": ("schema_check", SCHEMA_CHECK_PATH, "schema_check"),
            "schema_check_control": ("schema_check", os.path.join(CONTROLS_DIR, "schema_check_prefix.py"), "schema_check"),
            "ndscan_real": ("ndscan", NDSCAN_PATH, "ndscan"),
            "ndscan_control": ("ndscan", os.path.join(CONTROLS_DIR, "ndscan_prefix.py"), "ndscan"),
        }
        tool, tool_path, fixture = table[target]
        for n in _PERM_COUNTS:
            with self.subTest(n=n):
                code, doc, out, err = run_cli_json([
                    "--tool", tool, "--tool-path", tool_path,
                    "--fixture", os.path.join(FIXTURES_DIR, fixture), "--permutations", str(n),
                ])
                self.assertEqual(doc["stable"], expect_stable, (target, n, err))
    return _test


for _target, _expect in (
    ("consolidate_real", True),
    ("consolidate_control", False),
    ("schema_check_real", True),
    ("schema_check_control", True),
    ("ndscan_real", True),
    ("ndscan_control", True),
):
    setattr(TestEndToEndLoadBearing, "test_permcounts_%s" % _target, _make_permcount_test(_target, _expect))


# ---------------------------------------------------------------------------
# I. exit code 2 -- error conditions never crash
# ---------------------------------------------------------------------------

class TestExitCodeTwo(unittest.TestCase):
    def test_missing_tool_path(self):
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", "/definitely/does/not/exist.py",
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"),
        ])
        self.assertEqual(code, 2)
        self.assertIsNotNone(doc["error"])

    def test_tool_path_is_a_directory(self):
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", FIXTURES_DIR,
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"),
        ])
        self.assertEqual(code, 2)

    def test_missing_fixture_dir(self):
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
            "--fixture", "/definitely/does/not/exist",
        ])
        self.assertEqual(code, 2)

    def test_fixture_is_a_file_not_a_directory(self):
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate", "reports", "tied.json"),
        ])
        self.assertEqual(code, 2)

    def test_neither_tool_nor_cmd(self):
        code, doc, out, err = run_cli_json([
            "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"),
        ])
        self.assertEqual(code, 2)

    def test_permutations_zero(self):
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"), "--permutations", "0",
        ])
        self.assertEqual(code, 2)

    def test_permutations_negative(self):
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"), "--permutations", "-4",
        ])
        self.assertEqual(code, 2)

    def test_malformed_record_file_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = os.path.join(tmp, "reports")
            os.makedirs(reports)
            with open(os.path.join(reports, "tied.json"), "w") as fh:
                fh.write("{not valid json")
            code, doc, out, err = run_cli_json([
                "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
                "--fixture", tmp,
            ])
            self.assertEqual(code, 2)

    def test_record_pointer_not_found(self):
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"),
            "--record-pointer", "/does_not_exist",
        ])
        self.assertEqual(code, 2)

    def test_output_list_pointer_resolves_to_non_list(self):
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"),
            "--output-list-pointer", "/totals",
        ])
        self.assertEqual(code, 2)

    def test_output_list_pointer_not_found(self):
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"),
            "--output-list-pointer", "/nope",
        ])
        self.assertEqual(code, 2)

    def test_crashing_target_nonzero_unexpected_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            crasher = os.path.join(tmp, "crasher.py")
            with open(crasher, "w") as fh:
                fh.write("import sys\nsys.exit(17)\n")
            code, doc, out, err = run_cli_json([
                "--tool", "consolidate", "--tool-path", crasher,
                "--fixture", os.path.join(FIXTURES_DIR, "consolidate"),
            ])
            self.assertEqual(code, 2)

    def test_target_that_does_not_exist_as_python_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "not_python.py")
            with open(fake, "w") as fh:
                fh.write("this is not valid python syntax {{{\n")
            code, doc, out, err = run_cli_json([
                "--tool", "consolidate", "--tool-path", fake,
                "--fixture", os.path.join(FIXTURES_DIR, "consolidate"),
            ])
            self.assertEqual(code, 2)

    def test_target_output_not_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "writes_garbage.py")
            with open(script, "w") as fh:
                fh.write(
                    "import argparse\n"
                    "p = argparse.ArgumentParser()\n"
                    "p.add_argument('root')\n"
                    "p.add_argument('-o', '--output')\n"
                    "a = p.parse_args()\n"
                    "open(a.output, 'w').write('not json at all')\n"
                )
            code, doc, out, err = run_cli_json([
                "--tool", "consolidate", "--tool-path", script,
                "--fixture", os.path.join(FIXTURES_DIR, "consolidate"),
            ])
            self.assertEqual(code, 2)

    def test_target_does_not_write_output_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "writes_nothing.py")
            with open(script, "w") as fh:
                fh.write(
                    "import argparse\n"
                    "p = argparse.ArgumentParser()\n"
                    "p.add_argument('root')\n"
                    "p.add_argument('-o', '--output')\n"
                    "p.parse_args()\n"
                )
            code, doc, out, err = run_cli_json([
                "--tool", "consolidate", "--tool-path", script,
                "--fixture", os.path.join(FIXTURES_DIR, "consolidate"),
            ])
            self.assertEqual(code, 2)

    def test_generic_cmd_missing_required_flags(self):
        code, doc, out, err = run_cli_json([
            "--tool-path", os.path.join(TOY_DIR, "toy_tool.py"),
            "--cmd", "{tool_path} input.json -o {output}",
            "--fixture", os.path.join(FIXTURES_DIR, "toy"),
        ])
        self.assertEqual(code, 2)

    def test_record_file_missing_from_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, doc, out, err = run_cli_json([
                "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
                "--fixture", tmp,
            ])
            self.assertEqual(code, 2)

    def test_fewer_than_two_records_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = os.path.join(tmp, "reports")
            os.makedirs(reports)
            with open(os.path.join(reports, "tied.json"), "w") as fh:
                json.dump({"issues": [{"task_id": None, "issue": 1, "detail": "d"}],
                           "totals": {"roster_tasks": 1, "well_formed_payouts": 0}}, fh)
            code, doc, out, err = run_cli_json([
                "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
                "--fixture", tmp,
            ])
            self.assertEqual(code, 2)


# ---------------------------------------------------------------------------
# J. determinism -- byte-stable report across two runs
# ---------------------------------------------------------------------------

class TestByteStability(unittest.TestCase):
    def test_consolidate_control_two_runs_identical_bytes(self):
        args = [
            "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"), "--permutations", "6",
        ]
        _, out1, _ = run_cli(args)
        _, out2, _ = run_cli(args)
        self.assertEqual(out1, out2)
        self.assertEqual(sha256_bytes(out1), sha256_bytes(out2))

    @skip_no_consolidate
    def test_consolidate_real_two_runs_identical_bytes(self):
        args = [
            "--tool", "consolidate", "--tool-path", CONSOLIDATE_PATH,
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"), "--permutations", "6",
        ]
        _, out1, _ = run_cli(args)
        _, out2, _ = run_cli(args)
        self.assertEqual(sha256_bytes(out1), sha256_bytes(out2))

    def test_toy_broken_two_runs_identical_bytes(self):
        args = [
            "--tool-path", os.path.join(TOY_DIR, "toy_tool_broken.py"),
            "--cmd", "{tool_path} input.json -o {output}",
            "--fixture", os.path.join(FIXTURES_DIR, "toy"),
            "--record-file", "input.json", "--record-pointer", "/items",
            "--output-file", "out.json", "--output-list-pointer", "/sorted",
            "--permute-mode", "list-reorder", "--permutations", "6",
        ]
        _, out1, _ = run_cli(args)
        _, out2, _ = run_cli(args)
        self.assertEqual(sha256_bytes(out1), sha256_bytes(out2))

    def test_schema_check_control_two_runs_identical_bytes(self):
        args = [
            "--tool", "schema_check", "--tool-path", os.path.join(CONTROLS_DIR, "schema_check_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "schema_check"), "--permutations", "6",
        ]
        _, out1, _ = run_cli(args)
        _, out2, _ = run_cli(args)
        self.assertEqual(sha256_bytes(out1), sha256_bytes(out2))

    def test_ndscan_control_two_runs_identical_bytes(self):
        args = [
            "--tool", "ndscan", "--tool-path", os.path.join(CONTROLS_DIR, "ndscan_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "ndscan"), "--permutations", "6",
        ]
        _, out1, _ = run_cli(args)
        _, out2, _ = run_cli(args)
        self.assertEqual(sha256_bytes(out1), sha256_bytes(out2))


# ---------------------------------------------------------------------------
# K. relocation independence
# ---------------------------------------------------------------------------

class TestRelocationIndependence(unittest.TestCase):
    def _relocate_and_run(self, tool, tool_path_rel, fixture_rel, expect_stable):
        with tempfile.TemporaryDirectory() as tmp:
            new_root = os.path.join(tmp, "totally-renamed-copy")
            shutil.copytree(THIS_DIR, new_root)
            new_tool_path = os.path.join(new_root, tool_path_rel)
            new_fixture = os.path.join(new_root, fixture_rel)
            new_sortdetect = os.path.join(new_root, "sortdetect.py")
            proc = subprocess.run(
                [PY, new_sortdetect, "--tool", tool, "--tool-path", new_tool_path,
                 "--fixture", new_fixture, "--permutations", "6"],
                cwd=new_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            doc = json.loads(proc.stdout.decode("utf-8"))
            self.assertEqual(doc["stable"], expect_stable)
            return proc.stdout

    def test_consolidate_control_relocated_same_result(self):
        original_args = [
            "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"), "--permutations", "6",
        ]
        _, out_original, _ = run_cli(original_args)
        out_relocated = self._relocate_and_run(
            "consolidate", os.path.join("controls", "consolidate_prefix.py"),
            os.path.join("fixtures", "consolidate"), False)
        self.assertEqual(sha256_bytes(out_original), sha256_bytes(out_relocated))

    def test_schema_check_control_relocated_same_result(self):
        original_args = [
            "--tool", "schema_check", "--tool-path", os.path.join(CONTROLS_DIR, "schema_check_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "schema_check"), "--permutations", "6",
        ]
        _, out_original, _ = run_cli(original_args)
        out_relocated = self._relocate_and_run(
            "schema_check", os.path.join("controls", "schema_check_prefix.py"),
            os.path.join("fixtures", "schema_check"), True)
        self.assertEqual(sha256_bytes(out_original), sha256_bytes(out_relocated))

    def test_relocated_report_has_no_absolute_paths_from_new_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            new_root = os.path.join(tmp, "another-name-entirely")
            shutil.copytree(THIS_DIR, new_root)
            proc = subprocess.run(
                [PY, os.path.join(new_root, "sortdetect.py"),
                 "--tool", "consolidate", "--tool-path",
                 os.path.join(new_root, "controls", "consolidate_prefix.py"),
                 "--fixture", os.path.join(new_root, "fixtures", "consolidate"),
                 "--permutations", "6"],
                cwd=new_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertNotIn(new_root.encode("utf-8"), proc.stdout)
            self.assertNotIn(tmp.encode("utf-8"), proc.stdout)


# ---------------------------------------------------------------------------
# L. make_fixtures.py regeneration
# ---------------------------------------------------------------------------

class TestMakeFixtures(unittest.TestCase):
    def test_regenerates_fixtures_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([PY, MAKE_FIXTURES_PY, "--out", tmp], cwd=THIS_DIR, check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(tree_sha256(FIXTURES_DIR), tree_sha256(os.path.join(tmp, "fixtures")))

    def test_regenerates_controls_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([PY, MAKE_FIXTURES_PY, "--out", tmp], cwd=THIS_DIR, check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(tree_sha256(CONTROLS_DIR), tree_sha256(os.path.join(tmp, "controls")))

    def test_regenerates_toy_tool_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([PY, MAKE_FIXTURES_PY, "--out", tmp], cwd=THIS_DIR, check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(tree_sha256(TOY_DIR), tree_sha256(os.path.join(tmp, "toy_tool")))

    def test_regenerates_empty_subdir_under_consolidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([PY, MAKE_FIXTURES_PY, "--out", tmp], cwd=THIS_DIR, check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertTrue(os.path.isdir(os.path.join(tmp, "fixtures", "consolidate", "reports", "empty_subdir")))

    def test_regenerates_empty_pkg_under_ndscan(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([PY, MAKE_FIXTURES_PY, "--out", tmp], cwd=THIS_DIR, check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertTrue(os.path.isdir(os.path.join(tmp, "fixtures", "ndscan", "src", "empty_pkg")))

    def test_regenerated_tree_produces_same_detector_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([PY, MAKE_FIXTURES_PY, "--out", tmp], cwd=THIS_DIR, check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            proc = subprocess.run(
                [PY, SORTDETECT_PY, "--tool", "consolidate",
                 "--tool-path", os.path.join(tmp, "controls", "consolidate_prefix.py"),
                 "--fixture", os.path.join(tmp, "fixtures", "consolidate"), "--permutations", "6"],
                cwd=THIS_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            doc = json.loads(proc.stdout.decode("utf-8"))
            self.assertFalse(doc["stable"])

    def test_make_fixtures_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([PY, MAKE_FIXTURES_PY, "--out", tmp], cwd=THIS_DIR, check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            first = tree_sha256(os.path.join(tmp, "fixtures"))
            subprocess.run([PY, MAKE_FIXTURES_PY, "--out", tmp], cwd=THIS_DIR, check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            second = tree_sha256(os.path.join(tmp, "fixtures"))
            self.assertEqual(first, second)


# ---------------------------------------------------------------------------
# M. no clock substrings / no absolute paths in our own source or output
# ---------------------------------------------------------------------------

class TestNoForbiddenSubstrings(unittest.TestCase):
    # Deliberately excludes test_sortdetect.py itself and fixtures/ -- this
    # test's own FORBIDDEN literal below necessarily mentions these tokens,
    # and the ndscan fixture legitimately contains a real time.time() call
    # as sample input for ndscan's ND001_WALL_CLOCK rule to detect. The
    # constraint is on the DETECTOR's own logic, not on text that quotes or
    # exercises the concept.
    OWN_SOURCE_FILES = ["sortdetect.py", "make_fixtures.py"]
    FORBIDDEN = ["time.time", "utcnow", "datetime.now", ".now()"]

    def test_own_source_has_no_wallclock_substrings(self):
        for name in self.OWN_SOURCE_FILES:
            path = os.path.join(THIS_DIR, name)
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            for token in self.FORBIDDEN:
                with self.subTest(file=name, token=token):
                    self.assertNotIn(token, text)

    def test_own_source_never_imports_random_module_for_logic(self):
        for name in ["sortdetect.py", "make_fixtures.py"]:
            path = os.path.join(THIS_DIR, name)
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("import random", text)

    def test_own_source_never_reads_pid_or_hostname(self):
        for name in self.OWN_SOURCE_FILES:
            path = os.path.join(THIS_DIR, name)
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("os.getpid", text)
            self.assertNotIn("socket.gethostname", text)

    def test_stable_report_has_no_absolute_paths(self):
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"), "--permutations", "6",
        ])
        self.assertNotIn(THIS_DIR.encode("utf-8"), out)
        self.assertNotIn(b"/tmp/", out)

    def test_error_report_has_no_absolute_paths_of_tempdirs(self):
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", "/definitely/does/not/exist.py",
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"),
        ])
        self.assertNotIn(b"/tmp/sortdetect-", out)

    def test_report_contains_no_iso_timestamp_pattern(self):
        import re
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"), "--permutations", "6",
        ])
        self.assertIsNone(re.search(rb"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", out))


# ---------------------------------------------------------------------------
# N. the detector's own output list uses a tiebreak that breaks real ties
# ---------------------------------------------------------------------------

class TestOwnOutputTiebreak(unittest.TestCase):
    def test_summarise_record_moves_deduplicates(self):
        diffs = [
            {"permutation_index": 1, "changed_positions": [
                {"position": 0, "baseline_record": '{"code":1}', "permutation_record": '{"code":true}'},
            ]},
            {"permutation_index": 2, "changed_positions": [
                {"position": 0, "baseline_record": '{"code":1}', "permutation_record": '{"code":true}'},
            ]},
        ]
        moves = sortdetect.summarise_record_moves(diffs)
        self.assertEqual(len(moves), 1)

    def test_summarise_record_moves_ties_on_baseline_record_broken_by_dump(self):
        # Two moves share the SAME baseline_record but pair with different
        # permutation_records -- a genuine tie on the leading sort field.
        diffs_order_a = [
            {"permutation_index": 1, "changed_positions": [
                {"position": 0, "baseline_record": '{"code":1}', "permutation_record": '{"code":true}'},
            ]},
            {"permutation_index": 2, "changed_positions": [
                {"position": 0, "baseline_record": '{"code":1}', "permutation_record": '{"code":1.0}'},
            ]},
        ]
        diffs_order_b = list(reversed(diffs_order_a))

        moves_a = sortdetect.summarise_record_moves(diffs_order_a)
        moves_b = sortdetect.summarise_record_moves(diffs_order_b)
        self.assertEqual(moves_a, moves_b)

    def test_summarise_record_moves_without_tiebreak_would_be_order_dependent(self):
        # Directly demonstrates WHY the tiebreak is needed: sorting by
        # baseline_record alone (no dump) is not a total order when two
        # pairs share a baseline_record, so a stable sort would preserve
        # whatever order the (deduplicated) dict happened to iterate in.
        pairs_a = {
            ('{"code":1}', '{"code":true}'): {"baseline_record": '{"code":1}', "permutation_record": '{"code":true}'},
            ('{"code":1}', '{"code":1.0}'): {"baseline_record": '{"code":1}', "permutation_record": '{"code":1.0}'},
        }
        pairs_b = {
            ('{"code":1}', '{"code":1.0}'): {"baseline_record": '{"code":1}', "permutation_record": '{"code":1.0}'},
            ('{"code":1}', '{"code":true}'): {"baseline_record": '{"code":1}', "permutation_record": '{"code":true}'},
        }
        leading_only_a = sorted(pairs_a.values(), key=lambda p: p["baseline_record"])
        leading_only_b = sorted(pairs_b.values(), key=lambda p: p["baseline_record"])
        # With only the leading field, a stable sort preserves insertion
        # order among ties, so the two dict-insertion-orders can disagree.
        self.assertNotEqual(leading_only_a, leading_only_b)

        with_tiebreak_a = sorted(pairs_a.values(), key=lambda p: (p["baseline_record"], sortdetect.canonical_text(p)))
        with_tiebreak_b = sorted(pairs_b.values(), key=lambda p: (p["baseline_record"], sortdetect.canonical_text(p)))
        self.assertEqual(with_tiebreak_a, with_tiebreak_b)

    def test_distinct_record_moves_present_in_real_control_output(self):
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"), "--permutations", "6",
        ])
        self.assertIn("distinct_record_moves", doc)
        self.assertGreater(len(doc["distinct_record_moves"]), 0)

    def test_distinct_record_moves_has_a_genuine_tie_in_practice(self):
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"), "--permutations", "6",
        ])
        by_baseline = {}
        for m in doc["distinct_record_moves"]:
            by_baseline.setdefault(m["baseline_record"], []).append(m["permutation_record"])
        self.assertTrue(any(len(v) > 1 for v in by_baseline.values()))

    def test_distinct_record_moves_is_itself_sorted(self):
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"), "--permutations", "6",
        ])
        keys = [(m["baseline_record"], sortdetect.canonical_text(m)) for m in doc["distinct_record_moves"]]
        self.assertEqual(keys, sorted(keys))


# ---------------------------------------------------------------------------
# O. CLI argument behaviour
# ---------------------------------------------------------------------------

class TestCliArgumentBehaviour(unittest.TestCase):
    def test_output_flag_writes_file_not_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "report.json")
            code, out, err = run_cli([
                "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
                "--fixture", os.path.join(FIXTURES_DIR, "consolidate"), "--permutations", "6",
                "-o", out_path,
            ])
            self.assertEqual(out, b"")
            self.assertTrue(os.path.isfile(out_path))

    def test_output_file_and_stdout_produce_same_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "report.json")
            run_cli([
                "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
                "--fixture", os.path.join(FIXTURES_DIR, "consolidate"), "--permutations", "6",
                "-o", out_path,
            ])
            _, out, _ = run_cli([
                "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
                "--fixture", os.path.join(FIXTURES_DIR, "consolidate"), "--permutations", "6",
            ])
            with open(out_path, "rb") as fh:
                file_bytes = fh.read()
            self.assertEqual(file_bytes, out)

    def test_python_flag_overrides_interpreter(self):
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"), "--permutations", "6",
            "--python", PY,
        ])
        self.assertEqual(code, 1)

    def test_permutations_flag_respected_in_report(self):
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"), "--permutations", "9",
        ])
        self.assertEqual(doc["permutations_requested"], 9)
        self.assertEqual(doc["permutations_run"], 9)

    def test_default_permutations_is_six(self):
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"),
        ])
        self.assertEqual(doc["permutations_requested"], 6)

    def test_help_flag_exits_cleanly(self):
        proc = subprocess.run([PY, SORTDETECT_PY, "--help"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"sortdetect", proc.stdout.lower())

    def test_relative_tool_path_resolved_against_cwd(self):
        proc = subprocess.run(
            [PY, "sortdetect.py", "--tool", "consolidate",
             "--tool-path", os.path.join("controls", "consolidate_prefix.py"),
             "--fixture", os.path.join("fixtures", "consolidate"), "--permutations", "6"],
            cwd=THIS_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        doc = json.loads(proc.stdout.decode("utf-8"))
        self.assertFalse(doc["stable"])


# ---------------------------------------------------------------------------
# P. concrete diff reporting
# ---------------------------------------------------------------------------

class TestConcreteDiffReporting(unittest.TestCase):
    def setUp(self):
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"), "--permutations", "6",
        ])
        self.doc = doc

    def test_diffs_reference_valid_permutation_indices(self):
        for d in self.doc["diffs"]:
            self.assertGreaterEqual(d["permutation_index"], 1)
            self.assertLess(d["permutation_index"], self.doc["permutations_run"])

    def test_diffs_changed_positions_have_distinct_records(self):
        for d in self.doc["diffs"]:
            for m in d["changed_positions"]:
                self.assertNotEqual(m["baseline_record"], m["permutation_record"])

    def test_diffs_are_not_just_a_boolean(self):
        # i.e. the report doesn't just say "differs" -- concrete positions
        # and the two records at each position are present.
        self.assertTrue(any(d["changed_positions"] for d in self.doc["diffs"]))
        for d in self.doc["diffs"]:
            for m in d["changed_positions"]:
                self.assertIn("position", m)


# ---------------------------------------------------------------------------
# Q. generic --cmd misconfiguration
# ---------------------------------------------------------------------------

class TestGenericCmdMisconfiguration(unittest.TestCase):
    def test_cmd_with_unknown_permute_mode_rejected_by_argparse(self):
        proc = subprocess.run(
            [PY, SORTDETECT_PY, "--tool-path", os.path.join(TOY_DIR, "toy_tool.py"),
             "--cmd", "{tool_path} input.json -o {output}",
             "--fixture", os.path.join(FIXTURES_DIR, "toy"),
             "--record-file", "input.json", "--record-pointer", "/items",
             "--output-file", "out.json", "--output-list-pointer", "/sorted",
             "--permute-mode", "not-a-real-mode", "--permutations", "6"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(proc.returncode, 2)

    def test_cmd_missing_tool_path_placeholder_still_runs(self):
        # {tool_path} is optional in the template; the tool can be invoked
        # via a fixed path baked into --cmd instead.
        code, doc, out, err = run_cli_json([
            "--tool-path", os.path.join(TOY_DIR, "toy_tool.py"),
            "--cmd", "%s input.json -o {output}" % os.path.join(TOY_DIR, "toy_tool.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "toy"),
            "--record-file", "input.json", "--record-pointer", "/items",
            "--output-file", "out.json", "--output-list-pointer", "/sorted",
            "--permute-mode", "list-reorder", "--permutations", "6",
        ])
        self.assertEqual(code, 0, err)

    def test_custom_tool_ok_exit_codes_accepted(self):
        code, doc, out, err = run_cli_json([
            "--tool-path", os.path.join(TOY_DIR, "toy_tool.py"),
            "--cmd", "{tool_path} input.json -o {output}",
            "--fixture", os.path.join(FIXTURES_DIR, "toy"),
            "--record-file", "input.json", "--record-pointer", "/items",
            "--output-file", "out.json", "--output-list-pointer", "/sorted",
            "--permute-mode", "list-reorder", "--permutations", "6",
            "--tool-ok-exit-codes", "0,3,4",
        ])
        self.assertEqual(code, 0, err)

    def test_builtin_tool_accepts_overrides(self):
        code, doc, out, err = run_cli_json([
            "--tool", "consolidate", "--tool-path", os.path.join(CONTROLS_DIR, "consolidate_prefix.py"),
            "--fixture", os.path.join(FIXTURES_DIR, "consolidate"), "--permutations", "6",
            "--output-list-pointer", "/ungrouped_findings",
        ])
        self.assertEqual(code, 1)

    def test_dict_key_reorder_mode_selectable_via_generic_cmd(self):
        # Uses schema_check's payload only to exercise the dict-key-reorder
        # code path end to end via the generic --cmd interface (not the
        # built-in --tool schema_check adapter, which uses list-reorder).
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy2(os.path.join(FIXTURES_DIR, "schema_check", "schema.json"), tmp)
            with open(os.path.join(tmp, "payload.json"), "w") as fh:
                json.dump({"a": 1, "b": 2, "c": 3}, fh)
            with open(os.path.join(tmp, "schema.json"), "w") as fh:
                json.dump({"root": {"type": "object", "properties": {
                    "a": {"type": "integer"}, "b": {"type": "integer"}, "c": {"type": "integer"}}}}, fh)
            code, doc, out, err = run_cli_json([
                "--tool-path", os.path.join(CONTROLS_DIR, "schema_check_prefix.py"),
                "--cmd", "{tool_path} schema.json payload.json -o {output}",
                "--fixture", tmp,
                "--record-file", "payload.json", "--permute-mode", "dict-key-reorder",
                "--output-file", "out.json", "--output-list-pointer", "/violations",
                "--permutations", "6",
            ])
            self.assertEqual(code, 0, err)
            self.assertTrue(doc["stable"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
