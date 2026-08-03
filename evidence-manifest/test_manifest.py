#!/usr/bin/env python3
"""Tests for the Deterministic Batch Evidence Manifest CLI."""
import copy, hashlib, json, os, subprocess, sys, tempfile, unittest
import manifest

HERE = os.path.dirname(os.path.abspath(__file__))


def rec(sid="sub_1", cid="QmAbc", note="hello world"):
    return {"submission_id": sid, "cid": cid, "note": note}


class TestCanonicalization(unittest.TestCase):
    def test_key_order_irrelevant(self):
        a = {"b": 1, "a": 2}
        b = {"a": 2, "b": 1}
        self.assertEqual(manifest.canonical_bytes(a), manifest.canonical_bytes(b))
        self.assertEqual(manifest.leaf_digest(a), manifest.leaf_digest(b))

    def test_nested_key_order_irrelevant(self):
        a = {"x": {"q": 1, "p": 2}, "y": [{"n": 1, "m": 2}]}
        b = {"y": [{"m": 2, "n": 1}], "x": {"p": 2, "q": 1}}
        self.assertEqual(manifest.leaf_digest(a), manifest.leaf_digest(b))

    def test_whitespace_normalized(self):
        a = {"note": "  hello   world  "}
        b = {"note": "hello world"}
        self.assertEqual(manifest.leaf_digest(a), manifest.leaf_digest(b))

    def test_newlines_and_tabs_collapse(self):
        self.assertEqual(manifest.leaf_digest({"n": "a\n\t b"}),
                         manifest.leaf_digest({"n": "a b"}))

    def test_distinct_content_distinct_digest(self):
        self.assertNotEqual(manifest.leaf_digest(rec(sid="a")),
                            manifest.leaf_digest(rec(sid="b")))

    def test_non_string_scalars_untouched(self):
        c = manifest.canonicalize({"n": 5, "f": True, "z": None})
        self.assertEqual(c, {"n": 5, "f": True, "z": None})


class TestMerkleStructure(unittest.TestCase):
    def test_empty_batch_root_constant(self):
        self.assertEqual(manifest.merkle_root([]),
                         hashlib.sha256(b"empty:").hexdigest())

    def test_single_record_root_is_the_leaf(self):
        leaf = manifest.leaf_digest(rec())
        self.assertEqual(manifest.merkle_root([leaf]), leaf)

    def test_two_records_parent_hash(self):
        l1, l2 = manifest.leaf_digest(rec("a")), manifest.leaf_digest(rec("b"))
        expect = hashlib.sha256(b"node:" + bytes.fromhex(l1) + bytes.fromhex(l2)).hexdigest()
        self.assertEqual(manifest.merkle_root([l1, l2]), expect)

    def test_odd_count_promotes_not_duplicates(self):
        ls = [manifest.leaf_digest(rec(s)) for s in ("a", "b", "c")]
        parent = hashlib.sha256(b"node:" + bytes.fromhex(ls[0]) + bytes.fromhex(ls[1])).digest()
        expect = hashlib.sha256(b"node:" + parent + bytes.fromhex(ls[2])).hexdigest()
        self.assertEqual(manifest.merkle_root(ls), expect)

    def test_odd_promotion_not_equal_to_duplication(self):
        ls = [manifest.leaf_digest(rec(s)) for s in ("a", "b", "c")]
        dup = manifest.merkle_root(ls + [ls[2]])
        self.assertNotEqual(manifest.merkle_root(ls), dup)

    def test_order_changes_root(self):
        a = manifest.build_manifest([rec("a"), rec("b")])
        b = manifest.build_manifest([rec("b"), rec("a")])
        self.assertNotEqual(a["batch_root"], b["batch_root"])

    def test_four_records_balanced(self):
        ls = [manifest.leaf_digest(rec(s)) for s in "abcd"]
        p1 = hashlib.sha256(b"node:" + bytes.fromhex(ls[0]) + bytes.fromhex(ls[1])).digest()
        p2 = hashlib.sha256(b"node:" + bytes.fromhex(ls[2]) + bytes.fromhex(ls[3])).digest()
        expect = hashlib.sha256(b"node:" + p1 + p2).hexdigest()
        self.assertEqual(manifest.merkle_root(ls), expect)


class TestRepeatability(unittest.TestCase):
    def test_serialize_repeatable(self):
        m = manifest.build_manifest([rec("a"), rec("b"), rec("c")])
        self.assertEqual(manifest.serialize(m), manifest.serialize(m))

    def test_rebuild_same_bytes(self):
        recs = [rec("a"), rec("b"), rec("c")]
        self.assertEqual(manifest.serialize(manifest.build_manifest(recs)),
                         manifest.serialize(manifest.build_manifest(recs)))

    def test_reordered_keys_same_manifest_bytes(self):
        r1 = [{"submission_id": "a", "cid": "Q1"}]
        r2 = [{"cid": "Q1", "submission_id": "a"}]
        self.assertEqual(manifest.serialize(manifest.build_manifest(r1)),
                         manifest.serialize(manifest.build_manifest(r2)))


class TestVerification(unittest.TestCase):
    def test_clean_manifest_verifies(self):
        m = manifest.build_manifest([rec("a"), rec("b"), rec("c")])
        self.assertEqual(manifest.verify_manifest(m), [])

    def test_tampered_record_detected(self):
        m = manifest.build_manifest([rec("a"), rec("b")])
        m["entries"][0]["canonical"]["submission_id"] = "evil"
        problems = manifest.verify_manifest(m)
        self.assertTrue(any("leaf_digest drift" in p for p in problems))
        self.assertTrue(any("batch_root drift" in p for p in problems))

    def test_tampered_digest_detected(self):
        m = manifest.build_manifest([rec("a"), rec("b")])
        m["entries"][1]["leaf_digest"] = "0" * 64
        self.assertTrue(any("leaf_digest drift" in p for p in manifest.verify_manifest(m)))

    def test_tampered_root_detected(self):
        m = manifest.build_manifest([rec("a")])
        m["batch_root"] = "f" * 64
        self.assertTrue(any("batch_root drift" in p for p in manifest.verify_manifest(m)))

    def test_record_count_mismatch_detected(self):
        m = manifest.build_manifest([rec("a"), rec("b")])
        m["record_count"] = 99
        self.assertTrue(any("record_count mismatch" in p for p in manifest.verify_manifest(m)))

    def test_dropped_entry_detected(self):
        m = manifest.build_manifest([rec("a"), rec("b"), rec("c")])
        del m["entries"][1]
        self.assertTrue(manifest.verify_manifest(m))

    def test_malformed_entry_raises(self):
        with self.assertRaises(manifest.InputError):
            manifest.verify_manifest({"entries": [{"canonical": {}}]})


class TestCli(unittest.TestCase):
    def _cli(self, *args):
        return subprocess.run([sys.executable, os.path.join(HERE, "manifest.py"), *args],
                              capture_output=True, text=True)

    def _tmp(self, obj):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(obj, fh); fh.close()
        return fh.name

    def test_build_then_verify_ok(self):
        recs = self._tmp([rec("a"), rec("b"), rec("c")])
        out = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name
        try:
            b = self._cli("build", recs, "-o", out)
            self.assertEqual(b.returncode, 0)
            v = self._cli("verify", out)
            self.assertEqual(v.returncode, 0)
            self.assertIn("VERIFIED", v.stdout)
        finally:
            os.unlink(recs); os.unlink(out)

    def test_verify_tampered_exits_one(self):
        recs = self._tmp([rec("a"), rec("b")])
        out = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name
        try:
            self._cli("build", recs, "-o", out)
            with open(out) as fh:
                m = json.load(fh)
            m["entries"][0]["canonical"]["cid"] = "QmTAMPERED"
            with open(out, "w") as fh:
                json.dump(m, fh)
            v = self._cli("verify", out)
            self.assertEqual(v.returncode, 1)
            self.assertIn("VERIFICATION FAILED", v.stderr)
        finally:
            os.unlink(recs); os.unlink(out)

    def test_build_stdout_repeatable(self):
        recs = self._tmp([rec("a"), rec("b")])
        try:
            self.assertEqual(self._cli("build", recs).stdout, self._cli("build", recs).stdout)
        finally:
            os.unlink(recs)

    def test_invalid_json_exits_two(self):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        fh.write("{nope"); fh.close()
        try:
            p = self._cli("build", fh.name)
            self.assertEqual(p.returncode, 2)
            self.assertIn("INVALID_INPUT", p.stderr)
        finally:
            os.unlink(fh.name)

    def test_non_array_exits_two(self):
        p = self._cli("build", self._tmp({"a": 1}))
        self.assertEqual(p.returncode, 2)

    def test_missing_file_exits_two(self):
        self.assertEqual(self._cli("verify", "/nonexistent.json").returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
