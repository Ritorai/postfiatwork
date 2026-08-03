#!/usr/bin/env python3
"""Tests for payload_validate.py (stdlib-only Post Fiat payload validator)."""
import decimal
import json
import os
import subprocess
import sys
import tempfile
import unittest

import payload_validate as P

HERE = os.path.dirname(os.path.abspath(__file__))
D = decimal.Decimal

# Addresses reused (as data) from the sibling xrpl-address fixture set.
CLASSIC = "rrpDp2dLMs7KyhZhg5RbReRagjWuvH7qB"
CLASSIC2 = "raLnyR4PTuc5SgXGHqYA894a4eoKqoFwu"
XMAIN = "X7TYt4nPauxSispXtYbecsfAHuA4ciFXGkeY167vGiZVMX6"
XTEST = "T7PnJ7NMx1W7HYo9wMLRWp1qmM7B3S7ZDvcrB45S1PG4mYX"
BADSUM = "raLnyS4PTuc5SgXGHqYA894a4eoKqoFwu"       # CLASSIC2 with one char flipped
BADPREF = "hdsTnrGD5yDMg1KLHT5HxZaVmNhrHatzfM"
BADLEN = "rrn1Pz6Lrou64NjgqMJ"
BADALPHA = "rNOT!VALID#CHARS"


def hx(s):
    return s.encode("utf-8").hex()


def base(**overrides):
    rec = {
        "payload_id": "p1",
        "memo_hex": hx("hello"),
        "account": CLASSIC,
        "destination": CLASSIC2,
        "amount_drops": 100,
    }
    rec.update(overrides)
    return rec


def codes(findings):
    return sorted(f["code"] for f in findings)


def run_cli(args, input_text=None):
    cmd = [sys.executable, os.path.join(HERE, "payload_validate.py")] + args
    return subprocess.run(cmd, input=input_text, capture_output=True, text=True)


# ============================================================================
# Reused address logic (ported from xrpl-address/address_validate.py)
# ============================================================================
class TestReusedAddressLogic(unittest.TestCase):
    def test_xrpl_alphabet_is_not_bitcoin(self):
        BITCOIN = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
        self.assertNotEqual(P.ALPHABET, BITCOIN)
        self.assertEqual(P.ALPHABET[0], "r")

    def test_alphabet_is_58_unique_chars(self):
        self.assertEqual(len(P.ALPHABET), 58)
        self.assertEqual(len(set(P.ALPHABET)), 58)

    def test_bitcoin_only_char_rejected(self):
        issues = P.address_subissues("r0000000000000000000000000000000")
        self.assertEqual(issues, [P.BAD_ALPHABET])

    def test_punctuation_rejected(self):
        issues = P.address_subissues(BADALPHA)
        self.assertEqual(issues, [P.BAD_ALPHABET])

    def test_classic_valid(self):
        self.assertEqual(P.address_subissues(CLASSIC), [])

    def test_classic2_valid(self):
        self.assertEqual(P.address_subissues(CLASSIC2), [])

    def test_xaddress_mainnet_valid(self):
        self.assertEqual(P.address_subissues(XMAIN), [])

    def test_xaddress_testnet_valid(self):
        self.assertEqual(P.address_subissues(XTEST), [])

    def test_corrupted_checksum(self):
        self.assertEqual(P.address_subissues(BADSUM), [P.BAD_CHECKSUM])

    def test_unknown_prefix(self):
        self.assertIn(P.BAD_PREFIX, P.address_subissues(BADPREF))

    def test_short_payload(self):
        self.assertTrue(P.address_subissues(BADLEN))

    def test_too_short_overall(self):
        self.assertEqual(P.address_subissues("rrr"), [P.BAD_LENGTH])

    def test_checksum_is_double_sha256_prefix(self):
        payload = bytes([0x00]) + bytes(range(20))
        self.assertEqual(P.double_sha256(payload)[:4], P.b58decode(CLASSIC)[-4:])

    def test_every_single_char_mutation_of_classic_detected(self):
        base_addr = CLASSIC
        for i in range(len(base_addr)):
            alt = P.ALPHABET[(P.ALPHABET_MAP[base_addr[i]] + 1) % 58]
            mutated = base_addr[:i] + alt + base_addr[i + 1:]
            self.assertTrue(P.address_subissues(mutated),
                             f"mutation at {i} was not detected")

    def test_flipped_char_address_is_correct_length_and_alphabet(self):
        """The BADSUM demo address differs from CLASSIC2 by exactly one
        in-alphabet character at the same length -- only the checksum
        step catches it."""
        self.assertEqual(len(BADSUM), len(CLASSIC2))
        self.assertTrue(all(c in P.ALPHABET_MAP for c in BADSUM))
        diffs = [i for i, (a, b) in enumerate(zip(BADSUM, CLASSIC2)) if a != b]
        self.assertEqual(len(diffs), 1)
        self.assertEqual(P.address_subissues(BADSUM), [P.BAD_CHECKSUM])


# ============================================================================
# memo_hex decoding
# ============================================================================
class TestMemoHex(unittest.TestCase):
    def test_valid_lowercase_hex(self):
        findings, decoded = P.validate_memo_hex("68656c6c6f", 1024)
        self.assertEqual(findings, [])
        self.assertEqual(decoded, b"hello")

    def test_valid_uppercase_hex(self):
        findings, decoded = P.validate_memo_hex("68656C6C6F", 1024)
        self.assertEqual(findings, [])
        self.assertEqual(decoded, b"hello")

    def test_mixed_case_hex_decodes_same_as_lower_and_upper(self):
        lo = P.validate_memo_hex("deadbeef", 1024)[1]
        up = P.validate_memo_hex("DEADBEEF", 1024)[1]
        mixed = P.validate_memo_hex("DeAdBeEf", 1024)[1]
        self.assertEqual(lo, up)
        self.assertEqual(lo, mixed)

    def test_empty_memo_hex_is_valid_zero_bytes(self):
        findings, decoded = P.validate_memo_hex("", 1024)
        self.assertEqual(findings, [])
        self.assertEqual(decoded, b"")

    def test_odd_length_rejected(self):
        findings, decoded = P.validate_memo_hex("abc", 1024)
        self.assertEqual(codes(findings), [P.INVALID_HEX])
        self.assertIsNone(decoded)

    def test_single_char_rejected_odd_length(self):
        findings, _ = P.validate_memo_hex("a", 1024)
        self.assertEqual(codes(findings), [P.INVALID_HEX])

    def test_non_hex_chars_rejected(self):
        findings, decoded = P.validate_memo_hex("zzzz", 1024)
        self.assertEqual(codes(findings), [P.INVALID_HEX])
        self.assertIsNone(decoded)

    def test_internal_whitespace_rejected_not_silently_tolerated(self):
        # bytes.fromhex() alone would accept "de ad"; we must not.
        findings, decoded = P.validate_memo_hex("de ad", 1024)
        self.assertEqual(codes(findings), [P.INVALID_HEX])
        self.assertIsNone(decoded)

    def test_non_string_memo_hex_rejected(self):
        findings, decoded = P.validate_memo_hex(1234, 1024)
        self.assertEqual(codes(findings), [P.INVALID_HEX])
        self.assertIsNone(decoded)

    def test_none_memo_hex_rejected(self):
        findings, decoded = P.validate_memo_hex(None, 1024)
        self.assertEqual(codes(findings), [P.INVALID_HEX])

    def test_list_memo_hex_rejected(self):
        findings, decoded = P.validate_memo_hex(["ab"], 1024)
        self.assertEqual(codes(findings), [P.INVALID_HEX])

    def test_memo_exactly_at_limit_is_ok(self):
        findings, decoded = P.validate_memo_hex("41" * 1024, 1024)
        self.assertEqual(len(decoded), 1024)
        self.assertNotIn(P.MEMO_TOO_LARGE, codes(findings))

    def test_memo_one_byte_over_limit_flagged(self):
        findings, decoded = P.validate_memo_hex("41" * 1025, 1024)
        self.assertEqual(len(decoded), 1025)
        self.assertIn(P.MEMO_TOO_LARGE, codes(findings))

    def test_memo_one_byte_under_limit_ok(self):
        findings, decoded = P.validate_memo_hex("41" * 1023, 1024)
        self.assertNotIn(P.MEMO_TOO_LARGE, codes(findings))

    def test_limit_boundary_is_strictly_greater_than_not_gte(self):
        # This test pins the > vs >= decision documented in the README.
        at_limit, _ = P.validate_memo_hex("41" * 10, 10)
        over_limit, _ = P.validate_memo_hex("41" * 11, 10)
        self.assertEqual(codes(at_limit), [])
        self.assertEqual(codes(over_limit), [P.MEMO_TOO_LARGE])

    def test_custom_max_memo_bytes_changes_verdict(self):
        big = "41" * 2000
        small_limit = P.validate_memo_hex(big, 1024)[0]
        big_limit = P.validate_memo_hex(big, 5000)[0]
        self.assertIn(P.MEMO_TOO_LARGE, codes(small_limit))
        self.assertNotIn(P.MEMO_TOO_LARGE, codes(big_limit))

    def test_max_memo_bytes_zero_flags_any_nonempty_memo(self):
        findings, _ = P.validate_memo_hex("41", 0)
        self.assertIn(P.MEMO_TOO_LARGE, codes(findings))

    def test_max_memo_bytes_zero_allows_empty_memo(self):
        findings, _ = P.validate_memo_hex("", 0)
        self.assertEqual(findings, [])

    def test_valid_utf8_multibyte_memo(self):
        findings, decoded = P.validate_memo_hex(hx("café ✅"), 1024)
        self.assertEqual(findings, [])
        self.assertEqual(decoded.decode("utf-8"), "café ✅")

    def test_invalid_utf8_lone_continuation_byte(self):
        findings, decoded = P.validate_memo_hex("80", 1024)
        self.assertIn(P.MEMO_NOT_UTF8, codes(findings))

    def test_invalid_utf8_ff_byte(self):
        findings, decoded = P.validate_memo_hex("ff", 1024)
        self.assertIn(P.MEMO_NOT_UTF8, codes(findings))

    def test_too_large_and_not_utf8_can_co_occur(self):
        payload = "ff" * 2000
        findings, decoded = P.validate_memo_hex(payload, 1024)
        c = codes(findings)
        self.assertIn(P.MEMO_TOO_LARGE, c)
        self.assertIn(P.MEMO_NOT_UTF8, c)

    def test_ascii_memo_is_valid_utf8_subset(self):
        findings, _ = P.validate_memo_hex(hx("plain ascii text"), 1024)
        self.assertEqual(findings, [])

    def test_emoji_memo_decodes_correctly(self):
        findings, decoded = P.validate_memo_hex(hx("\U0001F680 launch"), 1024)
        self.assertEqual(findings, [])
        self.assertEqual(decoded.decode("utf-8"), "\U0001F680 launch")


# ============================================================================
# Amount handling (Decimal-based)
# ============================================================================
class TestAmountDrops(unittest.TestCase):
    def test_int_valid(self):
        findings, drops = P.validate_amount_drops(100)
        self.assertEqual(findings, [])
        self.assertEqual(drops, D(100))

    def test_zero_valid(self):
        findings, drops = P.validate_amount_drops(0)
        self.assertEqual(findings, [])
        self.assertEqual(drops, D(0))

    def test_string_integer_valid(self):
        findings, drops = P.validate_amount_drops("100")
        self.assertEqual(findings, [])
        self.assertEqual(drops, D(100))

    def test_string_with_whitespace_valid(self):
        findings, drops = P.validate_amount_drops("  100  ")
        self.assertEqual(findings, [])
        self.assertEqual(drops, D(100))

    def test_negative_int_rejected(self):
        findings, drops = P.validate_amount_drops(-1)
        self.assertEqual(codes(findings), [P.INVALID_AMOUNT])
        self.assertIsNone(drops)

    def test_negative_string_rejected(self):
        findings, drops = P.validate_amount_drops("-1")
        self.assertEqual(codes(findings), [P.INVALID_AMOUNT])

    def test_fractional_decimal_rejected(self):
        # JSON float literal 12.5 arrives as Decimal('12.5') via parse_float.
        findings, drops = P.validate_amount_drops(D("12.5"))
        self.assertEqual(codes(findings), [P.INVALID_AMOUNT])
        self.assertIsNone(drops)

    def test_whole_number_decimal_accepted(self):
        # 12.0 has zero fractional part -> valid, drops == 12.
        findings, drops = P.validate_amount_drops(D("12.0"))
        self.assertEqual(findings, [])
        self.assertEqual(drops, D(12))

    def test_nan_string_rejected(self):
        findings, drops = P.validate_amount_drops("NaN")
        self.assertEqual(codes(findings), [P.INVALID_AMOUNT])
        self.assertIsNone(drops)

    def test_infinity_string_rejected(self):
        findings, drops = P.validate_amount_drops("Infinity")
        self.assertEqual(codes(findings), [P.INVALID_AMOUNT])

    def test_negative_infinity_string_rejected(self):
        findings, drops = P.validate_amount_drops("-Infinity")
        self.assertEqual(codes(findings), [P.INVALID_AMOUNT])

    def test_non_numeric_string_rejected(self):
        findings, drops = P.validate_amount_drops("abc")
        self.assertEqual(codes(findings), [P.INVALID_AMOUNT])

    def test_empty_string_rejected(self):
        findings, drops = P.validate_amount_drops("")
        self.assertEqual(codes(findings), [P.INVALID_AMOUNT])

    def test_bool_true_rejected(self):
        findings, drops = P.validate_amount_drops(True)
        self.assertEqual(codes(findings), [P.INVALID_AMOUNT])
        self.assertIsNone(drops)

    def test_bool_false_rejected(self):
        findings, drops = P.validate_amount_drops(False)
        self.assertEqual(codes(findings), [P.INVALID_AMOUNT])

    def test_list_rejected(self):
        findings, drops = P.validate_amount_drops([1, 2])
        self.assertEqual(codes(findings), [P.INVALID_AMOUNT])

    def test_dict_rejected(self):
        findings, drops = P.validate_amount_drops({"a": 1})
        self.assertEqual(codes(findings), [P.INVALID_AMOUNT])

    def test_none_rejected(self):
        findings, drops = P.validate_amount_drops(None)
        self.assertEqual(codes(findings), [P.INVALID_AMOUNT])

    def test_exactly_max_drops_accepted(self):
        findings, drops = P.validate_amount_drops(P.MAX_DROPS)
        self.assertEqual(findings, [])
        self.assertEqual(drops, D(P.MAX_DROPS))

    def test_max_drops_plus_one_out_of_range(self):
        findings, drops = P.validate_amount_drops(P.MAX_DROPS + 1)
        self.assertEqual(codes(findings), [P.AMOUNT_OUT_OF_RANGE])

    def test_huge_string_drops_out_of_range(self):
        findings, drops = P.validate_amount_drops(str(P.MAX_DROPS + 10 ** 10))
        self.assertEqual(codes(findings), [P.AMOUNT_OUT_OF_RANGE])

    def test_scientific_notation_string_accepted(self):
        findings, drops = P.validate_amount_drops("1E2")
        self.assertEqual(findings, [])
        self.assertEqual(drops, D(100))

    def test_leading_plus_sign_accepted(self):
        findings, drops = P.validate_amount_drops("+42")
        self.assertEqual(findings, [])
        self.assertEqual(drops, D(42))


class TestAmountPft(unittest.TestCase):
    def test_int_valid(self):
        findings, pft = P.validate_amount_pft(5)
        self.assertEqual(findings, [])
        self.assertEqual(pft, D(5))

    def test_fractional_string_valid(self):
        findings, pft = P.validate_amount_pft("12.345")
        self.assertEqual(findings, [])
        self.assertEqual(pft, D("12.345"))

    def test_fractional_decimal_valid(self):
        findings, pft = P.validate_amount_pft(D("0.000001"))
        self.assertEqual(findings, [])

    def test_zero_valid(self):
        findings, pft = P.validate_amount_pft(0)
        self.assertEqual(findings, [])

    def test_negative_rejected(self):
        findings, pft = P.validate_amount_pft(-0.5) if False else P.validate_amount_pft(D("-0.5"))
        self.assertEqual(codes(findings), [P.INVALID_AMOUNT])

    def test_negative_string_rejected(self):
        findings, pft = P.validate_amount_pft("-3.14")
        self.assertEqual(codes(findings), [P.INVALID_AMOUNT])

    def test_nan_string_rejected(self):
        findings, pft = P.validate_amount_pft("NaN")
        self.assertEqual(codes(findings), [P.INVALID_AMOUNT])

    def test_infinity_rejected(self):
        findings, pft = P.validate_amount_pft("Infinity")
        self.assertEqual(codes(findings), [P.INVALID_AMOUNT])

    def test_non_numeric_rejected(self):
        findings, pft = P.validate_amount_pft("lots")
        self.assertEqual(codes(findings), [P.INVALID_AMOUNT])

    def test_bool_rejected(self):
        findings, pft = P.validate_amount_pft(True)
        self.assertEqual(codes(findings), [P.INVALID_AMOUNT])

    def test_large_pft_has_no_range_check(self):
        # By design: AMOUNT_OUT_OF_RANGE is defined only for drops vs the
        # XRP supply ceiling. See README limitations.
        findings, pft = P.validate_amount_pft(10 ** 30)
        self.assertEqual(findings, [])


# ============================================================================
# fmt_decimal / canonical JSON helpers
# ============================================================================
class TestFmtDecimal(unittest.TestCase):
    def test_integer_no_exponent(self):
        self.assertEqual(P.fmt_decimal(D(100)), "100")

    def test_large_integer_no_scientific_notation(self):
        self.assertEqual(P.fmt_decimal(D(P.MAX_DROPS)), "100000000000000000")

    def test_fraction_preserved(self):
        self.assertEqual(P.fmt_decimal(D("12.500")), "12.500")

    def test_scientific_input_rendered_fixed(self):
        self.assertEqual(P.fmt_decimal(D("1E2")), "100")


class TestCanonicalJson(unittest.TestCase):
    def test_sorted_keys_compact_separators_trailing_newline(self):
        obj = {"b": 1, "a": 2}
        text = P.canonical_json(obj)
        self.assertEqual(text, '{"a":2,"b":1}\n')

    def test_no_spaces_in_output(self):
        text = P.canonical_json({"x": [1, 2, 3]})
        self.assertNotIn(" ", text.rstrip("\n"))

    def test_ensure_ascii_escapes_non_ascii(self):
        text = P.canonical_json({"memo": "café"})
        self.assertNotIn("é", text)
        self.assertIn("\\u00e9", text)

    def test_report_is_byte_identical_across_two_builds(self):
        data = [base(payload_id="a"), base(payload_id="b", memo_hex=hx("x"))]
        r1 = P.canonical_json(P.build_report(data, 1024))
        r2 = P.canonical_json(P.build_report(data, 1024))
        self.assertEqual(r1, r2)


# ============================================================================
# Required fields / record-level validation
# ============================================================================
class TestRequiredFields(unittest.TestCase):
    def test_fully_valid_record_has_no_findings(self):
        r = P.validate_record(base(), 0, 1024)
        self.assertEqual(r["findings"], [])

    def test_missing_payload_id(self):
        rec = base(); del rec["payload_id"]
        r = P.validate_record(rec, 0, 1024)
        self.assertIn(P.MISSING_REQUIRED_FIELD, codes(r["findings"]))
        self.assertIsNone(r["payload_id"])

    def test_empty_string_payload_id(self):
        r = P.validate_record(base(payload_id=""), 0, 1024)
        self.assertIn(P.MISSING_REQUIRED_FIELD, codes(r["findings"]))
        self.assertIsNone(r["payload_id"])

    def test_non_string_payload_id_int(self):
        r = P.validate_record(base(payload_id=123), 0, 1024)
        self.assertIn(P.MISSING_REQUIRED_FIELD, codes(r["findings"]))

    def test_non_string_payload_id_null(self):
        r = P.validate_record(base(payload_id=None), 0, 1024)
        self.assertIn(P.MISSING_REQUIRED_FIELD, codes(r["findings"]))

    def test_non_string_payload_id_list(self):
        r = P.validate_record(base(payload_id=["x"]), 0, 1024)
        self.assertIn(P.MISSING_REQUIRED_FIELD, codes(r["findings"]))

    def test_missing_memo_hex(self):
        rec = base(); del rec["memo_hex"]
        r = P.validate_record(rec, 0, 1024)
        self.assertIn(P.MISSING_REQUIRED_FIELD, codes(r["findings"]))

    def test_missing_account(self):
        rec = base(); del rec["account"]
        r = P.validate_record(rec, 0, 1024)
        self.assertIn(P.MISSING_REQUIRED_FIELD, codes(r["findings"]))

    def test_missing_destination(self):
        rec = base(); del rec["destination"]
        r = P.validate_record(rec, 0, 1024)
        self.assertIn(P.MISSING_REQUIRED_FIELD, codes(r["findings"]))

    def test_empty_string_account_treated_as_missing(self):
        r = P.validate_record(base(account=""), 0, 1024)
        findings = [f for f in r["findings"] if f["field"] == "account"]
        self.assertEqual([f["code"] for f in findings], [P.MISSING_REQUIRED_FIELD])

    def test_empty_string_destination_treated_as_missing(self):
        r = P.validate_record(base(destination=""), 0, 1024)
        findings = [f for f in r["findings"] if f["field"] == "destination"]
        self.assertEqual([f["code"] for f in findings], [P.MISSING_REQUIRED_FIELD])

    def test_non_string_account_type(self):
        r = P.validate_record(base(account=12345), 0, 1024)
        findings = [f for f in r["findings"] if f["field"] == "account"]
        self.assertEqual([f["code"] for f in findings], [P.INVALID_ADDRESS])

    def test_non_string_destination_type(self):
        r = P.validate_record(base(destination=["x"]), 0, 1024)
        findings = [f for f in r["findings"] if f["field"] == "destination"]
        self.assertEqual([f["code"] for f in findings], [P.INVALID_ADDRESS])

    def test_missing_amount_both_absent(self):
        rec = base(); del rec["amount_drops"]
        r = P.validate_record(rec, 0, 1024)
        amt_findings = [f for f in r["findings"] if f["field"] == "amount"]
        self.assertEqual([f["code"] for f in amt_findings], [P.MISSING_REQUIRED_FIELD])

    def test_empty_object_record_reports_all_missing_fields(self):
        r = P.validate_record({}, 0, 1024)
        c = codes(r["findings"])
        self.assertEqual(c, sorted([P.MISSING_REQUIRED_FIELD] * 5))
        fields = sorted(f["field"] for f in r["findings"])
        self.assertEqual(fields, ["account", "amount", "destination", "memo_hex", "payload_id"])


# ============================================================================
# Address validation via full records
# ============================================================================
class TestAddressValidationInRecord(unittest.TestCase):
    def test_valid_classic_addresses_pass(self):
        r = P.validate_record(base(account=CLASSIC, destination=CLASSIC2), 0, 1024)
        self.assertEqual([f for f in r["findings"] if f["code"] == P.INVALID_ADDRESS], [])

    def test_valid_xaddress_destination_passes(self):
        r = P.validate_record(base(account=CLASSIC, destination=XMAIN), 0, 1024)
        self.assertEqual([f for f in r["findings"] if f["code"] == P.INVALID_ADDRESS], [])

    def test_valid_xaddress_testnet_account_passes(self):
        r = P.validate_record(base(account=XTEST, destination=CLASSIC2), 0, 1024)
        self.assertEqual([f for f in r["findings"] if f["code"] == P.INVALID_ADDRESS], [])

    def test_checksum_flip_on_account_caught(self):
        r = P.validate_record(base(account=BADSUM, destination=CLASSIC), 0, 1024)
        addr_findings = [f for f in r["findings"] if f["field"] == "account"]
        self.assertEqual([f["code"] for f in addr_findings], [P.INVALID_ADDRESS])
        self.assertIn("BAD_CHECKSUM", addr_findings[0]["detail"])

    def test_checksum_flip_on_destination_caught(self):
        r = P.validate_record(base(account=CLASSIC, destination=BADSUM), 0, 1024)
        addr_findings = [f for f in r["findings"] if f["field"] == "destination"]
        self.assertEqual([f["code"] for f in addr_findings], [P.INVALID_ADDRESS])

    def test_bad_alphabet_address_caught(self):
        r = P.validate_record(base(destination=BADALPHA), 0, 1024)
        addr_findings = [f for f in r["findings"] if f["field"] == "destination"]
        self.assertEqual([f["code"] for f in addr_findings], [P.INVALID_ADDRESS])
        self.assertIn("BAD_ALPHABET", addr_findings[0]["detail"])

    def test_bad_length_address_caught(self):
        r = P.validate_record(base(account=BADLEN), 0, 1024)
        addr_findings = [f for f in r["findings"] if f["field"] == "account"]
        self.assertEqual([f["code"] for f in addr_findings], [P.INVALID_ADDRESS])

    def test_bad_prefix_address_caught(self):
        r = P.validate_record(base(account=BADPREF), 0, 1024)
        addr_findings = [f for f in r["findings"] if f["field"] == "account"]
        self.assertEqual([f["code"] for f in addr_findings], [P.INVALID_ADDRESS])

    def test_both_account_and_destination_invalid_reports_both(self):
        r = P.validate_record(base(account=BADALPHA, destination=BADLEN), 0, 1024)
        fields = sorted(f["field"] for f in r["findings"] if f["code"] == P.INVALID_ADDRESS)
        self.assertEqual(fields, ["account", "destination"])


# ============================================================================
# Self payment
# ============================================================================
class TestSelfPayment(unittest.TestCase):
    def test_identical_valid_addresses_flagged(self):
        r = P.validate_record(base(account=CLASSIC, destination=CLASSIC), 0, 1024)
        self.assertIn(P.SELF_PAYMENT, codes(r["findings"]))

    def test_different_valid_addresses_not_flagged(self):
        r = P.validate_record(base(account=CLASSIC, destination=CLASSIC2), 0, 1024)
        self.assertNotIn(P.SELF_PAYMENT, codes(r["findings"]))

    def test_identical_invalid_addresses_flag_both_self_payment_and_invalid(self):
        r = P.validate_record(base(account=BADALPHA, destination=BADALPHA), 0, 1024)
        c = codes(r["findings"])
        self.assertIn(P.SELF_PAYMENT, c)
        self.assertIn(P.INVALID_ADDRESS, c)

    def test_self_payment_not_flagged_when_account_missing(self):
        rec = base(destination=CLASSIC); del rec["account"]
        r = P.validate_record(rec, 0, 1024)
        self.assertNotIn(P.SELF_PAYMENT, codes(r["findings"]))

    def test_self_payment_not_flagged_when_types_differ(self):
        r = P.validate_record(base(account=12345, destination=12345), 0, 1024)
        self.assertNotIn(P.SELF_PAYMENT, codes(r["findings"]))

    def test_xaddress_vs_classic_same_underlying_account_not_flagged(self):
        # Raw string equality only -- this tool does not decode X-addresses
        # to compare underlying account IDs. Documented limitation.
        r = P.validate_record(base(account=CLASSIC, destination=XMAIN), 0, 1024)
        self.assertNotIn(P.SELF_PAYMENT, codes(r["findings"]))


# ============================================================================
# Amount presence rules (drops vs pft)
# ============================================================================
class TestAmountFieldPresence(unittest.TestCase):
    def test_neither_amount_field_present(self):
        rec = base(); del rec["amount_drops"]
        r = P.validate_record(rec, 0, 1024)
        amt = [f for f in r["findings"] if f["field"] == "amount"]
        self.assertEqual([f["code"] for f in amt], [P.MISSING_REQUIRED_FIELD])

    def test_both_amount_fields_present(self):
        rec = base(amount_pft="5.5")  # base() already has amount_drops
        r = P.validate_record(rec, 0, 1024)
        amt = [f for f in r["findings"] if f["field"] == "amount"]
        self.assertEqual([f["code"] for f in amt], [P.INVALID_AMOUNT])

    def test_amount_drops_only_produces_amount_drops_output(self):
        r = P.validate_record(base(amount_drops=250), 0, 1024)
        self.assertEqual(r["amount_drops"], "250")
        self.assertIsNone(r["amount_pft"])

    def test_amount_pft_only_produces_amount_pft_output(self):
        rec = base(amount_pft="3.5"); del rec["amount_drops"]
        r = P.validate_record(rec, 0, 1024)
        self.assertEqual(r["amount_pft"], "3.5")
        self.assertIsNone(r["amount_drops"])

    def test_amount_drops_as_json_string_is_accepted(self):
        r = P.validate_record(base(amount_drops="12345"), 0, 1024)
        self.assertEqual(r["amount_drops"], "12345")
        self.assertEqual([f for f in r["findings"] if f["field"] == "amount_drops"], [])

    def test_invalid_amount_leaves_output_field_none(self):
        r = P.validate_record(base(amount_drops=-5), 0, 1024)
        self.assertIsNone(r["amount_drops"])


# ============================================================================
# Duplicate payload_id
# ============================================================================
class TestDuplicatePayloadId(unittest.TestCase):
    def test_two_records_same_id_second_flagged(self):
        data = [base(payload_id="dup"), base(payload_id="dup", memo_hex=hx("y"))]
        report = P.build_report(data, 1024)
        self.assertEqual(codes(report["results"][0]["findings"]), [])
        self.assertIn(P.DUPLICATE_PAYLOAD_ID, codes(report["results"][1]["findings"]))

    def test_three_records_same_id_only_first_clean(self):
        data = [base(payload_id="dup"),
                base(payload_id="dup", memo_hex=hx("y")),
                base(payload_id="dup", memo_hex=hx("z"))]
        report = P.build_report(data, 1024)
        self.assertTrue(report["results"][0]["ok"])
        self.assertIn(P.DUPLICATE_PAYLOAD_ID, codes(report["results"][1]["findings"]))
        self.assertIn(P.DUPLICATE_PAYLOAD_ID, codes(report["results"][2]["findings"]))

    def test_distinct_ids_not_flagged(self):
        data = [base(payload_id="a"), base(payload_id="b", memo_hex=hx("y"))]
        report = P.build_report(data, 1024)
        for r in report["results"]:
            self.assertNotIn(P.DUPLICATE_PAYLOAD_ID, codes(r["findings"]))

    def test_records_with_missing_payload_id_not_compared(self):
        rec1 = base(); del rec1["payload_id"]
        rec2 = base(); del rec2["payload_id"]
        report = P.build_report([rec1, rec2], 1024)
        for r in report["results"]:
            self.assertNotIn(P.DUPLICATE_PAYLOAD_ID, codes(r["findings"]))

    def test_duplicate_detail_references_first_index(self):
        data = [base(payload_id="dup"), base(payload_id="dup", memo_hex=hx("y"))]
        report = P.build_report(data, 1024)
        dupf = [f for f in report["results"][1]["findings"]
                if f["code"] == P.DUPLICATE_PAYLOAD_ID][0]
        self.assertIn("index 0", dupf["detail"])


# ============================================================================
# Malformed records
# ============================================================================
class TestMalformedRecord(unittest.TestCase):
    def test_string_element_malformed(self):
        r = P.validate_record("not-an-object", 0, 1024)
        self.assertEqual(codes(r["findings"]), [P.MALFORMED_RECORD])

    def test_number_element_malformed(self):
        r = P.validate_record(42, 0, 1024)
        self.assertEqual(codes(r["findings"]), [P.MALFORMED_RECORD])

    def test_float_element_malformed(self):
        r = P.validate_record(D("4.2"), 0, 1024)
        self.assertEqual(codes(r["findings"]), [P.MALFORMED_RECORD])

    def test_list_element_malformed(self):
        r = P.validate_record([1, 2, 3], 0, 1024)
        self.assertEqual(codes(r["findings"]), [P.MALFORMED_RECORD])

    def test_null_element_malformed(self):
        r = P.validate_record(None, 0, 1024)
        self.assertEqual(codes(r["findings"]), [P.MALFORMED_RECORD])

    def test_bool_element_malformed(self):
        r = P.validate_record(True, 0, 1024)
        self.assertEqual(codes(r["findings"]), [P.MALFORMED_RECORD])

    def test_empty_dict_is_not_malformed_record(self):
        # {} IS an object -- it's field-missing, not structurally malformed.
        r = P.validate_record({}, 0, 1024)
        self.assertNotIn(P.MALFORMED_RECORD, codes(r["findings"]))

    def test_malformed_record_has_no_payload_id(self):
        r = P.validate_record("bad", 0, 1024)
        self.assertIsNone(r["payload_id"])

    def test_malformed_record_skipped_by_duplicate_detection(self):
        data = ["bad", "bad"]
        report = P.build_report(data, 1024)
        for r in report["results"]:
            self.assertNotIn(P.DUPLICATE_PAYLOAD_ID, codes(r["findings"]))


# ============================================================================
# build_report / totals / finding ordering
# ============================================================================
class TestBuildReport(unittest.TestCase):
    def test_empty_array_is_clean(self):
        report = P.build_report([], 1024)
        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["totals"]["payloads"], 0)

    def test_all_valid_records_status_clean(self):
        data = [base(payload_id="a"), base(payload_id="b", memo_hex=hx("z"))]
        report = P.build_report(data, 1024)
        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["totals"]["ok"], 2)

    def test_any_finding_flips_status_to_issues(self):
        data = [base(payload_id="a"), base(payload_id="b", account="bad")]
        report = P.build_report(data, 1024)
        self.assertEqual(report["status"], "issues")

    def test_totals_counts_consistent(self):
        data = [base(payload_id="a"), base(payload_id="b", account="")]
        report = P.build_report(data, 1024)
        t = report["totals"]
        self.assertEqual(t["payloads"], 2)
        self.assertEqual(t["ok"] + t["with_findings"], t["payloads"])
        self.assertEqual(t["findings"], len(report["findings"]))

    def test_finding_counts_sum_matches_flat_findings(self):
        data = [base(payload_id="a", account="bad"),
                base(payload_id="b", memo_hex="zz")]
        report = P.build_report(data, 1024)
        self.assertEqual(sum(report["finding_counts"].values()), len(report["findings"]))

    def test_flat_findings_sorted_by_index_then_code(self):
        data = [base(payload_id="a", account="bad", amount_drops=-1),
                base(payload_id="b", memo_hex="zz")]
        report = P.build_report(data, 1024)
        keys = [(f["index"], f["code"]) for f in report["findings"]]
        self.assertEqual(keys, sorted(keys))

    def test_per_record_findings_sorted_by_code(self):
        rec = {}  # generates 5 MISSING_REQUIRED_FIELD across different fields
        report = P.build_report([rec], 1024)
        f = report["results"][0]["findings"]
        keys = [(x["code"], x["field"] or "") for x in f]
        self.assertEqual(keys, sorted(keys))

    def test_repeated_build_report_calls_are_identical(self):
        data = [base(payload_id="a", account="bad"), {}]
        r1 = P.canonical_json(P.build_report(data, 1024))
        r2 = P.canonical_json(P.build_report(data, 1024))
        self.assertEqual(r1, r2)

    def test_max_memo_bytes_recorded_in_report(self):
        report = P.build_report([], 777)
        self.assertEqual(report["max_memo_bytes"], 777)


# ============================================================================
# JSON parsing: Decimal, NaN/Infinity rejection
# ============================================================================
class TestJsonParsing(unittest.TestCase):
    def test_parse_float_yields_decimal(self):
        data = P.parse_json_text('[1.5]')
        self.assertIsInstance(data[0], decimal.Decimal)
        self.assertEqual(data[0], D("1.5"))

    def test_parse_int_stays_int(self):
        data = P.parse_json_text('[5]')
        self.assertIsInstance(data[0], int)

    def test_precision_not_lost_vs_naive_float_conversion(self):
        # 0.1 + 0.2 style precision trap: json parse_float=Decimal keeps the
        # literal text exactly; Decimal(str(float(...))) would not for all
        # inputs. Use a value with more digits than float53 can round-trip.
        text = '[0.123456789012345678]'
        data = P.parse_json_text(text)
        self.assertEqual(str(data[0]), "0.123456789012345678")

    def test_raw_nan_literal_rejected(self):
        with self.assertRaises(P.InputError):
            P.parse_json_text('[{"amount_drops": NaN}]')

    def test_raw_infinity_literal_rejected(self):
        with self.assertRaises(P.InputError):
            P.parse_json_text('[{"amount_drops": Infinity}]')

    def test_raw_negative_infinity_literal_rejected(self):
        with self.assertRaises(P.InputError):
            P.parse_json_text('[{"amount_drops": -Infinity}]')

    def test_quoted_nan_string_is_not_a_parse_error(self):
        data = P.parse_json_text('[{"amount_pft": "NaN"}]')
        self.assertEqual(data[0]["amount_pft"], "NaN")

    def test_invalid_json_syntax_raises_input_error(self):
        with self.assertRaises(P.InputError):
            P.parse_json_text('not json at all')

    def test_truncated_json_raises_input_error(self):
        with self.assertRaises(P.InputError):
            P.parse_json_text('[{"a":')


# ============================================================================
# CLI-level tests (subprocess, real captured output)
# ============================================================================
class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def _write(self, name, data):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w") as fh:
            json.dump(data, fh)
        return path

    def test_clean_input_exit_0(self):
        path = self._write("ok.json", [base()])
        res = run_cli([path])
        self.assertEqual(res.returncode, 0)
        report = json.loads(res.stdout)
        self.assertEqual(report["status"], "clean")

    def test_findings_input_exit_1(self):
        path = self._write("bad.json", [base(account="")])
        res = run_cli([path])
        self.assertEqual(res.returncode, 1)

    def test_nonexistent_file_exit_2(self):
        res = run_cli(["/definitely/not/a/real/path.json"])
        self.assertEqual(res.returncode, 2)
        self.assertIn("INVALID_INPUT", res.stderr)

    def test_invalid_json_exit_2(self):
        path = self._write("garbage.json", None)
        with open(path, "w") as fh:
            fh.write("{not valid json")
        res = run_cli([path])
        self.assertEqual(res.returncode, 2)

    def test_top_level_object_not_array_exit_2(self):
        path = self._write("obj.json", None)
        with open(path, "w") as fh:
            fh.write('{"payload_id": "x"}')
        res = run_cli([path])
        self.assertEqual(res.returncode, 2)

    def test_stdin_dash_reads_stdin(self):
        text = json.dumps([base()])
        res = run_cli(["-"], input_text=text)
        self.assertEqual(res.returncode, 0)

    def test_output_flag_writes_file(self):
        path = self._write("ok.json", [base()])
        outpath = os.path.join(self.tmpdir, "report.json")
        res = run_cli([path, "-o", outpath])
        self.assertEqual(res.returncode, 0)
        self.assertTrue(os.path.exists(outpath))
        with open(outpath) as fh:
            report = json.load(fh)
        self.assertEqual(report["status"], "clean")

    def test_output_flag_prints_summary_to_stdout(self):
        path = self._write("ok.json", [base()])
        outpath = os.path.join(self.tmpdir, "report.json")
        res = run_cli([path, "-o", outpath])
        self.assertIn("status=clean", res.stdout)

    def test_two_runs_with_output_are_byte_identical(self):
        path = self._write("bad.json", [base(account="bad"), {}])
        out1 = os.path.join(self.tmpdir, "r1.json")
        out2 = os.path.join(self.tmpdir, "r2.json")
        run_cli([path, "-o", out1])
        run_cli([path, "-o", out2])
        with open(out1, "rb") as f1, open(out2, "rb") as f2:
            self.assertEqual(f1.read(), f2.read())

    def test_max_memo_bytes_flag_changes_verdict(self):
        big_memo = "41" * 2000
        path = self._write("memo.json", [base(memo_hex=big_memo)])
        default_res = run_cli([path])
        loose_res = run_cli([path, "--max-memo-bytes", "5000"])
        self.assertEqual(default_res.returncode, 1)
        self.assertEqual(loose_res.returncode, 0)

    def test_max_memo_bytes_negative_rejected_by_argparse(self):
        path = self._write("ok.json", [base()])
        res = run_cli([path, "--max-memo-bytes", "-5"])
        self.assertEqual(res.returncode, 2)

    def test_max_memo_bytes_non_integer_rejected(self):
        path = self._write("ok.json", [base()])
        res = run_cli([path, "--max-memo-bytes", "abc"])
        self.assertEqual(res.returncode, 2)

    def test_missing_input_argument_exit_2(self):
        res = run_cli([])
        self.assertEqual(res.returncode, 2)

    def test_empty_array_input_exit_0(self):
        path = self._write("empty.json", [])
        res = run_cli([path])
        self.assertEqual(res.returncode, 0)

    def test_amounts_emitted_as_strings_in_output(self):
        path = self._write("ok.json", [base(amount_drops=100)])
        res = run_cli([path])
        report = json.loads(res.stdout)
        self.assertIsInstance(report["results"][0]["amount_drops"], str)

    def test_checksum_flip_demo_address_triggers_invalid_address(self):
        path = self._write("bad.json", [base(account=BADSUM)])
        res = run_cli([path])
        self.assertEqual(res.returncode, 1)
        report = json.loads(res.stdout)
        f = report["findings"][0]
        self.assertEqual(f["code"], "INVALID_ADDRESS")
        self.assertIn("BAD_CHECKSUM", f["detail"])

    def test_stdout_output_has_trailing_newline(self):
        path = self._write("ok.json", [base()])
        res = run_cli([path])
        self.assertTrue(res.stdout.endswith("\n"))

    def test_no_findings_report_has_empty_finding_counts(self):
        path = self._write("ok.json", [base()])
        res = run_cli([path])
        report = json.loads(res.stdout)
        self.assertEqual(report["finding_counts"], {})


# ============================================================================
# The specific edge cases called out in the task brief
# ============================================================================
class TestUtf8BomHandling(unittest.TestCase):
    """Regression tests for a real bug found during review: files/streams
    carrying a leading UTF-8 BOM (common from Windows editors) were
    rejected with a confusing JSONDecodeError instead of being read
    normally. Fixed by opening files with encoding="utf-8-sig" and by
    stripping a leading U+FEFF from stdin text. See README bug report.
    """
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_strip_bom_helper_removes_leading_bom(self):
        self.assertEqual(P._strip_bom("\ufeff[]"), "[]")

    def test_strip_bom_helper_noop_without_bom(self):
        self.assertEqual(P._strip_bom("[]"), "[]")

    def test_file_with_bom_parses_successfully(self):
        path = os.path.join(self.tmpdir, "bom.json")
        with open(path, "wb") as fh:
            fh.write(b"\xef\xbb\xbf" + json.dumps([base()]).encode("utf-8"))
        res = run_cli([path])
        self.assertEqual(res.returncode, 0)

    def test_stdin_with_bom_parses_successfully(self):
        raw = b"\xef\xbb\xbf" + json.dumps([base()]).encode("utf-8")
        cmd = [sys.executable, os.path.join(HERE, "payload_validate.py"), "-"]
        res = subprocess.run(cmd, input=raw, capture_output=True)
        self.assertEqual(res.returncode, 0)


class TestBriefEdgeCases(unittest.TestCase):
    def test_empty_memo_hex(self):
        r = P.validate_record(base(memo_hex=""), 0, 1024)
        self.assertEqual([f for f in r["findings"] if f["field"] == "memo_hex"], [])

    def test_uppercase_vs_lowercase_hex_equivalent(self):
        lower = P.validate_record(base(memo_hex="68656c6c6f"), 0, 1024)
        upper = P.validate_record(base(memo_hex="68656C6C6F"), 0, 1024)
        self.assertEqual(lower["findings"], upper["findings"])

    def test_memo_exactly_at_byte_limit_is_ok_not_flagged(self):
        r = P.validate_record(base(memo_hex="41" * 1024), 0, 1024)
        self.assertNotIn(P.MEMO_TOO_LARGE, codes(r["findings"]))

    def test_memo_one_over_byte_limit_is_flagged(self):
        r = P.validate_record(base(memo_hex="41" * 1025), 0, 1024)
        self.assertIn(P.MEMO_TOO_LARGE, codes(r["findings"]))

    def test_amount_drops_given_as_string(self):
        r = P.validate_record(base(amount_drops="777"), 0, 1024)
        self.assertEqual(r["amount_drops"], "777")
        self.assertEqual([f for f in r["findings"] if f["field"] == "amount_drops"], [])

    def test_both_amount_drops_and_amount_pft_present(self):
        r = P.validate_record(base(amount_drops=1, amount_pft="1"), 0, 1024)
        self.assertIn(P.INVALID_AMOUNT, codes(r["findings"]))

    def test_unicode_in_decoded_memo(self):
        r = P.validate_record(base(memo_hex=hx("日本語 memo ☃")), 0, 1024)
        self.assertEqual([f for f in r["findings"] if f["field"] == "memo_hex"], [])

    def test_record_that_is_not_an_object(self):
        r = P.validate_record(["a", "b"], 0, 1024)
        self.assertEqual(codes(r["findings"]), [P.MALFORMED_RECORD])


if __name__ == "__main__":
    unittest.main()
