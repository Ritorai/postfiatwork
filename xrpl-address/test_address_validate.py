#!/usr/bin/env python3
"""Tests for the XRPL Classic and X-Address Validator."""
import json, os, subprocess, sys, tempfile, unittest
import address_validate as A

HERE = os.path.dirname(os.path.abspath(__file__))
META = json.load(open(os.path.join(HERE, "_fixture_meta.json")))


def encode(payload):
    raw = payload + A.double_sha256(payload)[:4]
    num = int.from_bytes(raw, "big"); out = ""
    while num:
        num, rem = divmod(num, 58); out = A.ALPHABET[rem] + out
    pad = 0
    for b in raw:
        if b == 0: pad += 1
        else: break
    return A.ALPHABET[0] * pad + out


class TestAlphabet(unittest.TestCase):
    def test_xrpl_alphabet_is_not_bitcoin(self):
        BITCOIN = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
        self.assertNotEqual(A.ALPHABET, BITCOIN)
        self.assertEqual(A.ALPHABET[0], "r")

    def test_alphabet_is_58_unique_chars(self):
        self.assertEqual(len(A.ALPHABET), 58)
        self.assertEqual(len(set(A.ALPHABET)), 58)

    def test_bitcoin_only_char_rejected(self):
        """'0' and 'l' are absent from XRPL's alphabet."""
        issues, _ = A.validate_address("r0000000000000000000000000000000", set())
        self.assertEqual(issues, [A.BAD_ALPHABET])

    def test_punctuation_rejected(self):
        issues, _ = A.validate_address("rNOT!VALID#CHARS", set())
        self.assertEqual(issues, [A.BAD_ALPHABET])


class TestValidAddresses(unittest.TestCase):
    def test_classic_valid(self):
        issues, kind = A.validate_address(META["classic"], set())
        self.assertEqual(issues, [])
        self.assertEqual(kind, "classic")

    def test_xaddress_mainnet_valid(self):
        issues, kind = A.validate_address(META["xmain"], set())
        self.assertEqual(issues, [])
        self.assertEqual(kind, "xaddress-main")

    def test_xaddress_testnet_valid(self):
        issues, kind = A.validate_address(META["xtest"], set())
        self.assertEqual(issues, [])
        self.assertEqual(kind, "xaddress-test")

    def test_classic_starts_with_r(self):
        self.assertTrue(META["classic"].startswith("r"))

    def test_xmain_starts_with_X(self):
        self.assertTrue(META["xmain"].startswith("X"))

    def test_xtest_starts_with_T(self):
        self.assertTrue(META["xtest"].startswith("T"))


class TestChecksum(unittest.TestCase):
    def test_corrupted_char_fails_checksum(self):
        issues, _ = A.validate_address(META["badsum"], set())
        self.assertIn(A.BAD_CHECKSUM, issues)

    def test_checksum_is_double_sha256_prefix(self):
        payload = bytes([0x00]) + bytes(range(20))
        self.assertEqual(A.double_sha256(payload)[:4],
                         A.b58decode(META["classic"])[-4:])

    def test_every_single_char_mutation_detected(self):
        """Flip each position to a different alphabet char; all must fail."""
        base = META["classic"]
        for i in range(len(base)):
            alt = A.ALPHABET[(A.ALPHABET_MAP[base[i]] + 1) % 58]
            mutated = base[:i] + alt + base[i + 1:]
            issues, _ = A.validate_address(mutated, set())
            self.assertTrue(issues, f"mutation at {i} was not detected")


class TestPrefixAndLength(unittest.TestCase):
    def test_unknown_prefix(self):
        issues, kind = A.validate_address(META["badpref"], set())
        self.assertIn(A.BAD_PREFIX, issues)
        self.assertIsNone(kind)

    def test_short_payload(self):
        issues, _ = A.validate_address(META["badlen"], set())
        self.assertTrue(issues)

    def test_too_short_overall(self):
        issues, _ = A.validate_address("rrr", set())
        self.assertEqual(issues, [A.BAD_LENGTH])

    def test_classic_wrong_payload_length(self):
        bad = encode(bytes([0x00]) + bytes(range(19)))
        issues, _ = A.validate_address(bad, set())
        self.assertIn(A.BAD_LENGTH, issues)


class TestDenylist(unittest.TestCase):
    def test_denylisted_flagged(self):
        issues, _ = A.validate_address(META["denied"], {META["denied"]})
        self.assertEqual(issues, [A.DENYLISTED])

    def test_not_denylisted_clean(self):
        issues, _ = A.validate_address(META["denied"], set())
        self.assertEqual(issues, [])

    def test_denylist_only_applies_to_structurally_valid(self):
        """A malformed address is reported as malformed, not as denylisted."""
        issues, _ = A.validate_address("rNOT!VALID", {"rNOT!VALID"})
        self.assertEqual(issues, [A.BAD_ALPHABET])
        self.assertNotIn(A.DENYLISTED, issues)

    def test_denylist_loader_rejects_non_array(self):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"a": 1}, fh); fh.close()
        try:
            with self.assertRaises(A.InputError):
                A.load_denylist(fh.name)
        finally:
            os.unlink(fh.name)


class TestMalformedRecords(unittest.TestCase):
    def test_non_string_element(self):
        rep = A.audit([12345], set())
        self.assertEqual(rep["addresses"][0]["issues"], [A.MALFORMED_RECORD])

    def test_empty_string(self):
        rep = A.audit([""], set())
        self.assertEqual(rep["addresses"][0]["issues"], [A.MALFORMED_RECORD])

    def test_malformed_does_not_abort(self):
        rep = A.audit([META["classic"], 999], set())
        self.assertEqual(rep["totals"]["addresses"], 2)
        self.assertEqual(rep["totals"]["valid"], 1)

    def test_non_array_input_raises(self):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"a": 1}, fh); fh.close()
        try:
            with self.assertRaises(A.InputError):
                A.load_addresses(fh.name)
        finally:
            os.unlink(fh.name)


class TestReport(unittest.TestCase):
    def test_serialize_repeatable(self):
        rep = A.audit([META["classic"], "bad!"], set())
        self.assertEqual(A.serialize(rep), A.serialize(rep))

    def test_status_clean_when_all_valid(self):
        rep = A.audit([META["classic"], META["xmain"]], set())
        self.assertEqual(rep["status"], "clean")

    def test_issue_counts_aggregate(self):
        rep = A.audit(["bad!one", "bad!two"], set())
        self.assertEqual(rep["issue_counts"][A.BAD_ALPHABET], 2)


class TestCli(unittest.TestCase):
    def _cli(self, *a):
        return subprocess.run([sys.executable, os.path.join(HERE, "address_validate.py"), *a],
                              capture_output=True, text=True)

    def test_valid_fixture_exit_zero(self):
        p = self._cli(os.path.join(HERE, "addresses_valid.json"))
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["status"], "clean")

    def test_invalid_fixture_exit_one(self):
        p = self._cli(os.path.join(HERE, "addresses_invalid.json"),
                      "-d", os.path.join(HERE, "denylist.json"))
        self.assertEqual(p.returncode, 1)

    def test_missing_file_exit_two(self):
        p = self._cli("/nonexistent.json")
        self.assertEqual(p.returncode, 2)
        self.assertIn("UNREADABLE_INPUT", p.stderr)

    def test_repeated_runs_identical(self):
        f = os.path.join(HERE, "addresses_invalid.json")
        d = os.path.join(HERE, "denylist.json")
        self.assertEqual(self._cli(f, "-d", d).stdout, self._cli(f, "-d", d).stdout)

    def test_invalid_fixture_covers_all_codes(self):
        p = self._cli(os.path.join(HERE, "addresses_invalid.json"),
                      "-d", os.path.join(HERE, "denylist.json"))
        got = set(json.loads(p.stdout)["issue_counts"])
        for c in (A.BAD_ALPHABET, A.BAD_CHECKSUM, A.BAD_PREFIX,
                  A.DENYLISTED, A.MALFORMED_RECORD):
            self.assertIn(c, got, f"{c} not exercised")

    def test_denylist_absent_makes_that_address_pass(self):
        p = self._cli(os.path.join(HERE, "addresses_invalid.json"))
        addrs = {a["address"]: a for a in json.loads(p.stdout)["addresses"]}
        self.assertEqual(addrs[META["denied"]]["issues"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
