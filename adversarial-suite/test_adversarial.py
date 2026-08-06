#!/usr/bin/env python3
"""test_adversarial.py -- stdlib-only regression suite for evidence-manifest,
reward-reconciler and schema-checker: the three tools in this repository
whose READMEs disclosed no limitations before limitations-probe/ ran them
against adversarial input.

This suite does not repeat that documentation pass. It turns the failure
modes it already established (and a handful found independently while
building this suite -- see README.md "New findings") into pinned,
subprocess-level regression tests: real argv, real exit codes, real stdout
and stderr, asserted byte-for-byte or field-for-field, not by reading source.

Every test invokes the target tool as a SUBPROCESS with cwd set to that
tool's own directory, using a path to the fixture that is RELATIVE to that
directory. That is what makes the suite relocation-safe: nothing here reads
os.getcwd() at collection time or hardcodes /tmp/build_4/repo anywhere. The
only place an absolute path can leak into a test's own assertions is inside
a tool's stderr/traceback (which embeds the absolute path of the invoked
.py file) -- normalize() strips exactly that one prefix. See README.md
"What is normalized, and why" for the full rationale.

Run it:

    python3 -m unittest test_adversarial -v

Regenerate fixtures first if fixtures/ is missing or you changed
make_fixtures.py:

    python3 make_fixtures.py
"""
import hashlib
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
FIXTURES_DIR = os.path.join(HERE, "fixtures")
SCRATCH_DIR = os.path.join(HERE, "_scratch")

TOOLS = {
    "evidence-manifest": {
        "dir": os.path.join(REPO_ROOT, "evidence-manifest"),
        "script": "manifest.py",
    },
    "reward-reconciler": {
        "dir": os.path.join(REPO_ROOT, "reward-reconciler"),
        "script": "reconcile.py",
    },
    "schema-checker": {
        "dir": os.path.join(REPO_ROOT, "schema-checker"),
        "script": "schema_check.py",
    },
}

PY = sys.executable or "python3"

# A record kept per test id so we can dump expected_results.json / recompute
# hashes and cross-check the committed copy without re-running everything.
RESULTS = {}


def fixture_path(tool, name):
    """Absolute path to a fixture; callers turn it relative before use."""
    return os.path.join(FIXTURES_DIR, tool, name)


def rel_to_tool(tool, abspath):
    """Path relative to the tool's own directory -- what actually goes on
    argv, so neither the argv string nor any echo of it in tool output ties
    the test to this checkout's absolute location."""
    return os.path.relpath(abspath, TOOLS[tool]["dir"])


def normalize(text):
    """Strip the one thing that legitimately differs between an
    original-path run and a relocated-path run: the absolute path of the
    repository root, which CPython bakes into traceback 'File \"...\"'
    lines for the invoked script regardless of whether argv used a relative
    path. Everything else in this suite's fixture/tool argv is already
    relative, so nothing else needs stripping. See README.md.
    """
    return text.replace(REPO_ROOT, "<REPO_ROOT>")


def run_tool(tool, argv, timeout=30, cwd=None):
    """Invoke a tool as a subprocess, cwd'd into its own directory, script
    referenced by its bare relative filename. Returns (returncode_or_None,
    normalized_stdout, normalized_stderr, timed_out).
    returncode is None when the process was killed for exceeding `timeout`.
    """
    spec = TOOLS[tool]
    directory = cwd or spec["dir"]
    full_argv = [PY, spec["script"]] + list(argv)
    try:
        proc = subprocess.run(full_argv, cwd=directory, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "", "", True
    out = normalize(proc.stdout.decode("utf-8", "replace"))
    err = normalize(proc.stderr.decode("utf-8", "replace"))
    return proc.returncode, out, err, False


def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record(case_id, tool, argv_rel, exit_code, out, err, timed_out=False):
    """Store a normalized, hashable record of one case for
    expected_results.json cross-checking."""
    payload = json.dumps({
        "exit_code": exit_code,
        "timed_out": timed_out,
        "stdout": out,
        "stderr": err,
    }, sort_keys=True)
    RESULTS[case_id] = {
        "tool": tool,
        "argv": argv_rel,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "output_sha256": sha256_hex(payload),
    }


def ensure_fixtures():
    if not os.path.isdir(FIXTURES_DIR):
        raise RuntimeError(
            "fixtures/ missing -- run `python3 make_fixtures.py` in "
            "adversarial-suite/ before running the test suite")


def setUpModule():
    os.makedirs(SCRATCH_DIR, exist_ok=True)


def tearDownModule():
    import shutil
    if os.path.isdir(SCRATCH_DIR):
        shutil.rmtree(SCRATCH_DIR)


def jload(text):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


# ===========================================================================
# evidence-manifest
# ===========================================================================

class EvidenceManifestTests(unittest.TestCase):
    TOOL = "evidence-manifest"

    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def _build(self, fixture_name, extra=None, timeout=30):
        path = rel_to_tool(self.TOOL, fixture_path(self.TOOL, fixture_name))
        argv = ["build", path] + (extra or [])
        code, out, err, to = run_tool(self.TOOL, argv, timeout=timeout)
        record("EM-build-%s" % fixture_name, self.TOOL, argv, code, out, err, to)
        return code, out, err

    def _verify(self, fixture_name, timeout=30):
        path = rel_to_tool(self.TOOL, fixture_path(self.TOOL, fixture_name))
        argv = ["verify", path]
        code, out, err, to = run_tool(self.TOOL, argv, timeout=timeout)
        record("EM-verify-%s" % fixture_name, self.TOOL, argv, code, out, err, to)
        return code, out, err

    # --- baseline sanity ---------------------------------------------------

    def test_valid_basic_builds_clean(self):
        code, out, err = self._build("valid_basic.json")
        self.assertEqual(code, 0)
        doc = jload(out)
        self.assertIsNotNone(doc, "stdout was not valid JSON: %r" % out)
        self.assertEqual(doc["record_count"], 2)
        self.assertEqual(len(doc["entries"]), 2)

    def test_empty_array_builds_to_empty_root(self):
        code, out, err = self._build("empty_array.json")
        self.assertEqual(code, 0)
        doc = jload(out)
        self.assertEqual(doc["record_count"], 0)
        self.assertEqual(doc["batch_root"],
                         hashlib.sha256(b"empty:").hexdigest())

    # --- malformed / wrong-shape JSON --------------------------------------

    def test_empty_file_is_invalid_json_exit2(self):
        code, out, err = self._build("empty_file.json")
        self.assertEqual(code, 2)
        self.assertIn("INVALID_INPUT", err)

    def test_truncated_json_exit2(self):
        code, out, err = self._build("truncated.json")
        self.assertEqual(code, 2)
        self.assertIn("invalid JSON", err)

    def test_wrong_shape_object_rejected(self):
        code, out, err = self._build("wrong_shape_object.json")
        self.assertEqual(code, 2)
        self.assertIn("expected a JSON array", err)

    def test_wrong_shape_null_rejected(self):
        code, out, err = self._build("wrong_shape_null.json")
        self.assertEqual(code, 2)

    def test_wrong_shape_string_rejected(self):
        code, out, err = self._build("wrong_shape_string.json")
        self.assertEqual(code, 2)

    def test_array_of_non_objects_rejected(self):
        code, out, err = self._build("array_of_non_objects.json")
        self.assertEqual(code, 2)
        self.assertIn("record[0] must be an object", err)

    def test_array_mixed_types_rejected_at_offending_index(self):
        code, out, err = self._build("array_mixed_types.json")
        self.assertEqual(code, 2)
        self.assertIn("record[1] must be an object", err)

    # --- REPRODUCED FAILURE MODE 1: bare NaN / Infinity survive (EM-3) -----

    def test_bare_nan_survives_into_manifest_exit0(self):
        """EM-3, restated as a pinned regression: a record containing a bare
        NaN builds successfully and the literal token NaN appears in the
        emitted manifest -- which is not valid RFC 8259 JSON."""
        code, out, err = self._build("nan_value.json")
        self.assertEqual(code, 0)
        self.assertIn("NaN", out)
        with self.assertRaises(ValueError,
                               msg="a strict RFC 8259 reader should reject "
                                   "a bare NaN token"):
            def reject(name):
                raise ValueError("bare %s" % name)
            json.loads(out, parse_constant=reject)

    def test_bare_infinity_survives_into_manifest_exit0(self):
        code, out, err = self._build("infinity_value.json")
        self.assertEqual(code, 0)
        self.assertIn("Infinity", out)

    def test_bare_negative_infinity_survives_exit0(self):
        code, out, err = self._build("neg_infinity_value.json")
        self.assertEqual(code, 0)
        self.assertIn("-Infinity", out)

    def test_default_json_parser_accepts_the_manifest_anyway(self):
        """The narrower, corrected claim from limitations-probe/README.md:
        Python's default json.loads (non-strict mode) round-trips a NaN
        manifest without complaint -- it is only a *strict* reader that
        rejects it. Pinning both halves so neither claim goes stale alone.
        """
        code, out, err = self._build("nan_value.json")
        doc = json.loads(out)  # must not raise
        self.assertTrue(any(
            e["canonical"].get("score") != e["canonical"].get("score")
            for e in doc["entries"]))  # NaN != NaN

    # --- REPRODUCED FAILURE MODE 2 (new to this suite): uncaught OSError --
    # ------------------------------------------------------------------ ---

    def test_directory_as_records_path_crashes_uncaught_exit1(self):
        """Not in limitations-probe/: passing a directory where manifest.py
        expects a file is never tested there. load_records() only catches
        FileNotFoundError, so IsADirectoryError propagates uncaught, Python's
        default top-level handler prints a traceback, and the process exits
        1 -- the code this tool's own README defines as 'verification drift'
        for `verify`, and reuses here to mean 'unhandled crash'. A caller
        that treats exit 1 as 'ran fine, found a mismatch' will misread this
        as a content problem instead of an unreadable path."""
        code, out, err = self._build("dir_as_input")
        self.assertEqual(code, 1)
        self.assertIn("Traceback (most recent call last):", err)
        self.assertIn("IsADirectoryError", err)
        self.assertEqual(out, "", "a crash should not also print a manifest")

    def test_deep_nesting_crashes_uncaught_recursionerror_exit1(self):
        """Same failure class as the directory case, different trigger:
        json.load() itself raises RecursionError on sufficiently deep
        nesting, which load_records() also does not catch. 3000 levels of
        nested arrays inside one field value is enough on CPython 3.11's
        default recursion limit."""
        code, out, err = self._build("deep_nesting.json")
        self.assertEqual(code, 1)
        self.assertIn("RecursionError", err)

    # --- EM-1: whitespace-only differences collide on one root ------------

    def test_em1_whitespace_variants_collide_on_one_root(self):
        roots = {}
        for name in ("ws_two_spaces.json", "ws_one_space.json",
                     "ws_tab.json", "ws_newline.json"):
            code, out, err = self._build(name)
            self.assertEqual(code, 0)
            roots[name] = jload(out)["batch_root"]
        distinct = set(roots.values())
        self.assertEqual(len(distinct), 1,
                         "expected all four whitespace variants to collide "
                         "on one batch root (EM-1); got %r" % roots)

    def test_em1_whitespace_variants_are_actually_different_source_bytes(self):
        """Guards against the collision test passing for the wrong reason
        (e.g. all four fixtures accidentally being identical bytes)."""
        raw = set()
        for name in ("ws_two_spaces.json", "ws_one_space.json",
                     "ws_tab.json", "ws_newline.json"):
            with open(fixture_path(self.TOOL, name), "rb") as fh:
                raw.add(fh.read())
        self.assertEqual(len(raw), 4)

    # --- EM-2: identifier whitespace also collides -------------------------

    def test_em2_padded_and_bare_submission_id_collide(self):
        _, out_a, _ = self._build("id_padded.json")
        _, out_b, _ = self._build("id_bare.json")
        root_a = jload(out_a)["batch_root"]
        root_b = jload(out_b)["batch_root"]
        self.assertEqual(root_a, root_b)

    # --- EM-4: duplicate submission_id accepted silently -------------------

    def test_em4_duplicate_submission_id_accepted_silently(self):
        code, out, err = self._build("dup_submission_id.json")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        doc = jload(out)
        self.assertEqual(len(doc["entries"]), 2)

    # --- canonicalization contract, exercised rather than assumed ---------

    def test_duplicate_json_object_key_last_value_wins(self):
        """Not a manifest.py bug -- this is CPython's json module's own
        documented last-value-wins behaviour for duplicate object keys,
        pinned here because manifest.py never sees or reports the collision
        the way a stricter parser (e.g. one using object_pairs_hook) would."""
        code, out, err = self._build("dup_keys_in_record.json")
        self.assertEqual(code, 0)
        doc = jload(out)
        self.assertEqual(doc["entries"][0]["canonical"]["submission_id"], "S2")

    def test_bom_prefixed_file_rejected_exit2(self):
        code, out, err = self._build("bom.json")
        self.assertEqual(code, 2)
        self.assertIn("BOM", err)

    def test_no_trailing_newline_is_fine(self):
        """Negative result: a well-formed JSON array with no trailing
        newline is not a problem for json.load."""
        code, out, err = self._build("no_trailing_newline.json")
        self.assertEqual(code, 0)

    def test_crlf_inside_string_value_is_collapsed_like_any_whitespace(self):
        code, out, err = self._build("crlf_in_string.json")
        self.assertEqual(code, 0)
        doc = jload(out)
        self.assertEqual(doc["entries"][0]["canonical"]["evidence"], "line1 line2")

    def test_crlf_at_file_level_parses_fine(self):
        """Negative result: CRLF line endings between JSON tokens (as
        opposed to inside a string value) are just whitespace to the parser
        and do not affect the result."""
        code, out, err = self._build("crlf_file_level.json")
        code2, out2, err2 = self._build("id_bare.json")
        self.assertEqual(code, 0)
        self.assertEqual(jload(out)["batch_root"], jload(out2)["batch_root"])

    def test_embedded_nul_byte_survives_round_trip(self):
        code, out, err = self._build("embedded_null_in_id.json")
        self.assertEqual(code, 0)
        doc = jload(out)
        self.assertEqual(doc["entries"][0]["canonical"]["submission_id"], "S1\x00X")

    def test_astral_plane_and_rtl_override_survive_as_escaped_ascii(self):
        """Negative result: ensure_ascii=True means every non-ASCII
        codepoint, including an astral-plane emoji and a Unicode RTL
        override control character, comes out \\u-escaped rather than
        raw or mangled."""
        code, out, err = self._build("unicode_astral_rtl_nul.json")
        self.assertEqual(code, 0)
        self.assertNotIn('‮'.encode("utf-8").decode("latin-1"), out)
        self.assertIn("\\ud83d\\ude00", out)   # surrogate pair for U+1F600
        self.assertIn("\\u202e", out)          # RTL override

    def test_nfc_and_nfd_of_the_same_glyph_are_different_records(self):
        """Negative-but-worth-stating: canonicalization strips/collapses
        whitespace but does NOT Unicode-normalize, so an NFC 'é' and an NFD
        'e'+combining-acute produce different batch roots even though they
        render identically. Not a bug (no rule claims otherwise); pinned so
        a future canonicalization change doesn't silently start conflating
        them, or silently stop and go unnoticed."""
        _, out_c, _ = self._build("unicode_nfc.json")
        _, out_d, _ = self._build("unicode_nfd.json")
        self.assertNotEqual(jload(out_c)["batch_root"], jload(out_d)["batch_root"])

    def test_huge_integer_500_digits_does_not_crash(self):
        """Negative result: Python ints are arbitrary precision and json
        round-trips a 500-digit integer exactly; no overflow, no crash."""
        code, out, err = self._build("huge_int.json")
        self.assertEqual(code, 0)
        doc = jload(out)
        self.assertEqual(doc["entries"][0]["canonical"]["n"], int("9" * 500))

    def test_bool_and_int_values_preserve_type_not_just_value(self):
        code, out, err = self._build("bool_and_int_values.json")
        self.assertEqual(code, 0)
        c = jload(out)["entries"][0]["canonical"]
        self.assertIs(c["flag"], True)
        self.assertIs(c["z"], False)
        self.assertEqual(c["n"], 5)

    def test_nested_object_keys_sorted_recursively(self):
        code, out, err = self._build("nested_object_unsorted_keys.json")
        self.assertEqual(code, 0)
        # sort_keys=True at serialization time means the raw JSON text has
        # "a" before "b" and "x" before "y" wherever they appear.
        self.assertLess(out.index('"a":'), out.index('"b":'))
        self.assertLess(out.index('"x":'), out.index('"y":'))

    # --- determinism ---------------------------------------------------

    def test_build_is_byte_identical_across_two_runs(self):
        _, out1, _ = self._build("valid_basic.json")
        _, out2, _ = self._build("valid_basic.json")
        self.assertEqual(out1, out2)

    def test_nan_build_is_byte_identical_across_two_runs(self):
        _, out1, _ = self._build("nan_value.json")
        _, out2, _ = self._build("nan_value.json")
        self.assertEqual(out1, out2)

    # --- verify subcommand ---------------------------------------------

    def test_verify_clean_manifest_exit0(self):
        code, out, err = self._verify("manifest_clean.json")
        self.assertEqual(code, 0)
        self.assertIn("VERIFIED", out)

    def test_verify_tampered_manifest_exit1_with_two_drift_lines(self):
        code, out, err = self._verify("manifest_tampered.json")
        self.assertEqual(code, 1)
        self.assertIn("VERIFICATION FAILED", err)
        self.assertIn("leaf_digest drift", err)
        self.assertIn("batch_root drift", err)

    def test_verify_bad_shape_entries_not_array_exit2(self):
        code, out, err = self._verify("manifest_bad_shape.json")
        self.assertEqual(code, 2)
        self.assertIn("entries must be an array", err)

    def test_verify_missing_fields_in_entry_exit2(self):
        code, out, err = self._verify("manifest_missing_fields_entry.json")
        self.assertEqual(code, 2)
        self.assertIn("missing 'canonical' or 'leaf_digest'", err)

    def test_verify_manifest_not_an_object_exit2(self):
        code, out, err = self._verify("manifest_not_object.json")
        self.assertEqual(code, 2)
        self.assertIn("manifest must be a JSON object", err)

    def test_verify_nonexistent_file_exit2(self):
        argv = ["verify", "does_not_exist_at_all.json"]
        code, out, err, to = run_tool(self.TOOL, argv)
        record("EM-verify-nonexistent", self.TOOL, argv, code, out, err, to)
        self.assertEqual(code, 2)
        self.assertIn("file not found", err)

    # --- CLI shape ------------------------------------------------------

    def test_no_args_exit2_usage(self):
        code, out, err, to = run_tool(self.TOOL, [])
        record("EM-noargs", self.TOOL, [], code, out, err, to)
        self.assertEqual(code, 2)
        self.assertIn("usage:", err)

    def test_unknown_subcommand_exit2(self):
        argv = ["frobnicate"]
        code, out, err, to = run_tool(self.TOOL, argv)
        record("EM-badsubcmd", self.TOOL, argv, code, out, err, to)
        self.assertEqual(code, 2)

    def test_help_flag_exit0(self):
        argv = ["-h"]
        code, out, err, to = run_tool(self.TOOL, argv)
        record("EM-help", self.TOOL, argv, code, out, err, to)
        self.assertEqual(code, 0)
        self.assertIn("usage:", out)


# ===========================================================================
# reward-reconciler
# ===========================================================================

class RewardReconcilerTests(unittest.TestCase):
    TOOL = "reward-reconciler"

    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def _run(self, expected_name, payouts_name, timeout=30):
        e = rel_to_tool(self.TOOL, fixture_path(self.TOOL, expected_name))
        p = rel_to_tool(self.TOOL, fixture_path(self.TOOL, payouts_name))
        argv = [e, p]
        code, out, err, to = run_tool(self.TOOL, argv, timeout=timeout)
        record("RR-%s__%s" % (expected_name, payouts_name), self.TOOL, argv,
              code, out, err, to)
        return code, out, err

    # --- baseline ---------------------------------------------------------

    def test_balanced_exit0(self):
        code, out, err = self._run("balanced_expected.json", "balanced_payouts.json")
        self.assertEqual(code, 0)
        doc = jload(out)
        self.assertEqual(doc["status"], "balanced")
        self.assertEqual(doc["findings"], [])

    def test_both_empty_is_balanced(self):
        code, out, err = self._run("both_empty_expected.json", "both_empty_payouts.json")
        self.assertEqual(code, 0)
        self.assertEqual(jload(out)["status"], "balanced")

    # --- the five issue codes, each pinned individually --------------------

    def test_missing_payout_detected(self):
        code, out, err = self._run("missing_expected.json", "missing_payouts.json")
        self.assertEqual(code, 1)
        doc = jload(out)
        self.assertEqual([f["issue"] for f in doc["findings"]], ["MISSING_PAYOUT"])

    def test_duplicate_payout_detected(self):
        code, out, err = self._run("dup_expected.json", "dup_payouts.json")
        self.assertEqual(code, 1)
        doc = jload(out)
        self.assertEqual([f["issue"] for f in doc["findings"]], ["DUPLICATE_PAYOUT"])
        self.assertEqual(doc["findings"][0]["payout_amount"], "5.000000")

    def test_unexpected_payout_detected(self):
        code, out, err = self._run("unexpected_expected.json", "unexpected_payouts.json")
        self.assertEqual(code, 1)
        doc = jload(out)
        self.assertEqual([f["issue"] for f in doc["findings"]], ["UNEXPECTED_PAYOUT"])

    def test_amount_mismatch_detected(self):
        code, out, err = self._run("amount_mismatch_expected.json", "amount_mismatch_payouts.json")
        self.assertEqual(code, 1)
        doc = jload(out)
        self.assertEqual([f["issue"] for f in doc["findings"]], ["AMOUNT_MISMATCH"])
        self.assertEqual(doc["findings"][0]["delta"], "1.000000")

    def test_wallet_mismatch_detected_for_a_single_payout(self):
        """Negative result / contrast case for RR-4: when there is exactly
        ONE payout for the task, the wallet comparison DOES run and
        WALLET_MISMATCH DOES fire correctly, naming the wallet that was
        actually paid. The defect in RR-4 is specific to the split-payout
        (DUPLICATE_PAYOUT) branch, not to wallet checking in general."""
        code, out, err = self._run("wallet_mismatch_expected.json", "wallet_mismatch_payouts.json")
        self.assertEqual(code, 1)
        doc = jload(out)
        self.assertEqual([f["issue"] for f in doc["findings"]], ["WALLET_MISMATCH"])
        self.assertEqual(doc["findings"][0]["payout_wallet"], "rOTHER")
        self.assertIn("rOTHER", json.dumps(doc))

    # --- REPRODUCED FAILURE MODE (RR-4): split payout never names the ------
    # --- wallet that was actually paid -------------------------------------

    def test_rr4_split_payout_to_wrong_wallet_never_names_that_wallet(self):
        code, out, err = self._run("split_wrong_wallet_expected.json",
                                   "split_wrong_wallet_payouts.json")
        self.assertEqual(code, 1)
        doc = jload(out)
        issues = sorted({f["issue"] for f in doc["findings"]})
        wallets_named = sorted({f["wallet"] for f in doc["findings"]})
        self.assertEqual(issues, ["DUPLICATE_PAYOUT"])
        self.assertEqual(wallets_named, ["rHONEST"])
        self.assertNotIn("rATTACKER", json.dumps(doc),
                         "the wallet that actually received the money must "
                         "not silently disappear from the report")

    # --- REPRODUCED FAILURE MODE (RR-2): huge exponent crashes exit 1, -----
    # --- not the documented exit 2 ------------------------------------------

    def test_rr2_huge_exponent_crashes_uncaught_not_exit2(self):
        code, out, err = self._run("huge_exponent_expected.json", "huge_exponent_payouts.json")
        self.assertEqual(code, 1)
        self.assertIn("Traceback (most recent call last):", err)
        self.assertIn("decimal", err.lower())
        self.assertIsNone(jload(out), "no JSON report should be emitted on a crash")

    def test_directory_as_expected_path_crashes_uncaught_exit1(self):
        """Same uncaught-OSError class as evidence-manifest's directory
        case: _load_records only catches FileNotFoundError."""
        e = rel_to_tool(self.TOOL, fixture_path(self.TOOL, "dir_as_expected"))
        p = rel_to_tool(self.TOOL, fixture_path(self.TOOL, "balanced_payouts.json"))
        argv = [e, p]
        code, out, err, to = run_tool(self.TOOL, argv)
        record("RR-dir-as-expected", self.TOOL, argv, code, out, err, to)
        self.assertEqual(code, 1)
        self.assertIn("IsADirectoryError", err)

    def test_deep_nesting_crashes_uncaught_recursionerror_exit1(self):
        e = rel_to_tool(self.TOOL, fixture_path(self.TOOL, "deep_nesting_expected.json"))
        p = rel_to_tool(self.TOOL, fixture_path(self.TOOL, "both_empty_payouts.json"))
        argv = [e, p]
        code, out, err, to = run_tool(self.TOOL, argv)
        record("RR-deep-nesting", self.TOOL, argv, code, out, err, to)
        self.assertEqual(code, 1)
        self.assertIn("RecursionError", err)

    # --- RR-1: sub-precision discrepancy quantized away ---------------------

    def test_rr1_subprecision_discrepancy_reported_balanced(self):
        code, out, err = self._run("subprecision_expected.json", "subprecision_payouts.json")
        self.assertEqual(code, 0)
        doc = jload(out)
        self.assertEqual(doc["status"], "balanced")
        self.assertEqual(doc["findings"], [])

    # --- RR-3: no sign or range check ---------------------------------------

    def test_rr3_negative_amount_reconciles_cleanly(self):
        code, out, err = self._run("negative_expected.json", "negative_payouts.json")
        self.assertEqual(code, 0)
        doc = jload(out)
        self.assertEqual(doc["totals"]["expected_total"], "-5.000000")

    def test_scientific_notation_equals_plain_decimal(self):
        code, out, err = self._run("sci_notation_expected.json", "sci_notation_payouts.json")
        self.assertEqual(code, 0)
        self.assertEqual(jload(out)["status"], "balanced")

    def test_huge_valid_number_without_exponent_does_not_crash(self):
        """Contrast with RR-2: a very large amount is fine as long as it is
        not written with an out-of-range exponent. Narrows the RR-2 finding
        to the exponent notation specifically, not magnitude."""
        code, out, err = self._run("huge_valid_number_expected.json",
                                   "huge_valid_number_payouts.json")
        self.assertEqual(code, 0)
        self.assertEqual(jload(out)["status"], "balanced")

    def test_amount_as_json_integer_is_accepted(self):
        code, out, err = self._run("amount_as_int_expected.json", "amount_as_int_payouts.json")
        self.assertEqual(code, 0)

    # --- input validation: bool/float amounts, missing/empty fields --------

    def test_nan_amount_rejected_exit2(self):
        """A JSON NaN amount is a Python float, and _quantize()'s type gate
        (`not isinstance(raw, (str, int))`) rejects every float before its
        own NaN guard a few lines later ever runs -- so the message names
        the type, not the NaN-specific branch. Pinning the actual message
        rather than assuming which guard fires first."""
        code, out, err = self._run("nan_amount_expected.json", "balanced_payouts.json")
        self.assertEqual(code, 2)
        self.assertIn("got float", err)

    def test_bool_amount_rejected_exit2(self):
        """bool is a subclass of int in Python -- confirms reconcile.py
        explicitly guards against it (isinstance(raw, bool) check), unlike
        the documented regression-checker bug this axis is modeled on."""
        code, out, err = self._run("bool_amount_expected.json", "balanced_payouts.json")
        self.assertEqual(code, 2)
        self.assertIn("bool", err)

    def test_float_amount_rejected_exit2(self):
        code, out, err = self._run("float_amount_expected.json", "balanced_payouts.json")
        self.assertEqual(code, 2)
        self.assertIn("float", err)

    def test_duplicate_task_id_in_expected_set_rejected_exit2(self):
        code, out, err = self._run("dup_task_id_expected.json", "balanced_payouts.json")
        self.assertEqual(code, 2)
        self.assertIn("duplicate task_id", err)

    def test_missing_required_field_rejected_exit2(self):
        code, out, err = self._run("missing_field_expected.json", "balanced_payouts.json")
        self.assertEqual(code, 2)
        self.assertIn("missing required field", err)

    def test_empty_string_wallet_rejected_exit2(self):
        code, out, err = self._run("empty_string_wallet_expected.json", "balanced_payouts.json")
        self.assertEqual(code, 2)

    def test_whitespace_only_wallet_rejected_exit2(self):
        code, out, err = self._run("whitespace_wallet_expected.json", "balanced_payouts.json")
        self.assertEqual(code, 2)

    def test_wrong_shape_expected_object_rejected_exit2(self):
        code, out, err = self._run("wrong_shape_expected.json", "balanced_payouts.json")
        self.assertEqual(code, 2)

    def test_wrong_shape_payouts_null_rejected_exit2(self):
        code, out, err = self._run("balanced_expected.json", "wrong_shape_payouts.json")
        self.assertEqual(code, 2)

    def test_truncated_expected_rejected_exit2(self):
        code, out, err = self._run("truncated_expected.json", "balanced_payouts.json")
        self.assertEqual(code, 2)

    def test_empty_file_expected_rejected_exit2(self):
        code, out, err = self._run("empty_file_expected.json", "balanced_payouts.json")
        self.assertEqual(code, 2)

    # --- determinism ---------------------------------------------------

    def test_report_byte_identical_across_two_runs(self):
        _, out1, _ = self._run("amount_mismatch_expected.json", "amount_mismatch_payouts.json")
        _, out2, _ = self._run("amount_mismatch_expected.json", "amount_mismatch_payouts.json")
        self.assertEqual(out1, out2)

    # --- CLI shape ------------------------------------------------------

    def test_no_args_exit2_usage(self):
        code, out, err, to = run_tool(self.TOOL, [])
        record("RR-noargs", self.TOOL, [], code, out, err, to)
        self.assertEqual(code, 2)
        self.assertIn("usage:", err)

    def test_help_flag_exit0(self):
        argv = ["-h"]
        code, out, err, to = run_tool(self.TOOL, argv)
        record("RR-help", self.TOOL, argv, code, out, err, to)
        self.assertEqual(code, 0)
        self.assertIn("usage:", out)


# ===========================================================================
# schema-checker
# ===========================================================================

class SchemaCheckerTests(unittest.TestCase):
    TOOL = "schema-checker"

    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def _check(self, schema_name, payload_name, timeout=30):
        s = rel_to_tool(self.TOOL, fixture_path(self.TOOL, schema_name))
        p = rel_to_tool(self.TOOL, fixture_path(self.TOOL, payload_name))
        argv = [s, p]
        code, out, err, to = run_tool(self.TOOL, argv, timeout=timeout)
        record("SC-%s__%s" % (schema_name, payload_name), self.TOOL, argv,
              code, out, err, to)
        return code, out, err, to

    # --- baseline ---------------------------------------------------------

    def test_conforming_payload_exit0(self):
        code, out, err, to = self._check("schema_basic.json", "payload_conform.json")
        self.assertEqual(code, 0)
        doc = jload(out)
        self.assertEqual(doc["status"], "conform")
        self.assertEqual(doc["violation_count"], 0)

    def test_violations_reported_with_pointers(self):
        code, out, err, to = self._check("schema_basic.json", "payload_violations.json")
        self.assertEqual(code, 1)
        doc = jload(out)
        self.assertEqual(doc["status"], "violations")
        self.assertGreater(doc["violation_count"], 0)
        for v in doc["violations"]:
            self.assertTrue(v["pointer"].startswith("/") or v["pointer"] == "")

    def test_missing_required_field_detected(self):
        code, out, err, to = self._check("schema_basic.json", "payload_missing_required.json")
        self.assertEqual(code, 1)
        codes = {v["code"] for v in jload(out)["violations"]}
        self.assertIn("MISSING_REQUIRED", codes)

    def test_unexpected_key_detected_when_additional_properties_false(self):
        code, out, err, to = self._check("schema_strict.json", "payload_unexpected_key.json")
        self.assertEqual(code, 1)
        codes = {v["code"] for v in jload(out)["violations"]}
        self.assertIn("UNEXPECTED_KEY", codes)

    def test_malformed_schema_short_circuits_exit2(self):
        code, out, err, to = self._check("schema_malformed.json", "payload_conform.json")
        self.assertEqual(code, 2)
        doc = jload(out)
        self.assertEqual(doc["status"], "error")
        self.assertEqual(doc["violations"], [],
                         "violations must not be computed against a schema "
                         "that failed its own validation")

    # --- REPRODUCED FAILURE MODE (SC-1): pattern is a search, not a --------
    # --- full match ----------------------------------------------------------

    def test_sc1_unanchored_pattern_accepts_a_match_anywhere_in_string(self):
        code, out, err, to = self._check("schema_pattern_unanchored.json",
                                         "payload_pattern_search.json")
        self.assertEqual(code, 0, "'XX1234XX' should conform under an "
                                  "unanchored 4-digit pattern (SC-1)")
        self.assertEqual(jload(out)["status"], "conform")

    def test_sc1_contrast_anchored_pattern_correctly_rejects(self):
        """Negative result / contrast: the SAME value against the SAME
        4-digit pattern, anchored with ^...$, is correctly rejected. This
        isolates the defect to unanchored authoring, not to `pattern` as a
        keyword or to `re.search` as a mechanism in general."""
        code, out, err, to = self._check("schema_pattern_anchored.json",
                                         "payload_pattern_anchored_reject.json")
        self.assertEqual(code, 1)
        codes = {v["code"] for v in jload(out)["violations"]}
        self.assertIn("PATTERN_MISMATCH", codes)

    # --- SC-2: max_length counts code points, not UTF-8 bytes --------------

    def test_sc2_max_length_4_accepts_16_utf8_bytes_of_emoji(self):
        code, out, err, to = self._check("schema_max_length.json",
                                         "payload_max_length_multibyte.json")
        self.assertEqual(code, 0)
        with open(fixture_path(self.TOOL, "payload_max_length_multibyte.json"), "rb") as fh:
            raw = json.loads(fh.read())
        self.assertEqual(len(raw["s"].encode("utf-8")), 16)

    def test_sc2_contrast_max_length_4_rejects_5_ascii_chars(self):
        code, out, err, to = self._check("schema_max_length.json",
                                         "payload_max_length_too_many_codepoints.json")
        self.assertEqual(code, 1)
        codes = {v["code"] for v in jload(out)["violations"]}
        self.assertIn("MAX_LENGTH", codes)

    # --- SC-3: ReDoS -- schema-supplied pattern can hang the checker -------

    def test_sc3_redos_pattern_hangs_on_pathological_input(self):
        code, out, err, to = self._check("schema_redos.json", "payload_redos_long.json",
                                         timeout=5)
        self.assertTrue(to, "expected schema_check.py to still be running "
                            "after 5s on '^(a+)+$' against 33 non-matching "
                            "characters (SC-3); if this now finishes, the "
                            "regex engine or the tool changed and the "
                            "README claim needs re-checking")

    def test_sc3_contrast_same_pattern_short_non_match_is_fast(self):
        """Negative result: the pathological blowup needs the long
        backtracking chain; a short non-matching string under the same
        catastrophic-backtracking pattern returns well within budget."""
        code, out, err, to = self._check("schema_redos.json", "payload_redos_short.json",
                                         timeout=5)
        self.assertFalse(to)
        self.assertEqual(code, 1)

    # --- SC-4: negative result -- bool/float ARE correctly rejected --------

    def test_sc4_bool_rejected_for_integer_type(self):
        code, out, err, to = self._check("schema_int_strict.json", "payload_bool_as_int.json")
        self.assertEqual(code, 1)
        codes = {v["code"] for v in jload(out)["violations"]}
        self.assertIn("TYPE_MISMATCH", codes)

    def test_sc4_integral_float_rejected_for_integer_type(self):
        code, out, err, to = self._check("schema_int_strict.json",
                                         "payload_float_integral_as_int.json")
        self.assertEqual(code, 1)
        codes = {v["code"] for v in jload(out)["violations"]}
        self.assertIn("TYPE_MISMATCH", codes)

    def test_sc4_extreme_float_rejected_for_integer_type(self):
        code, out, err, to = self._check("schema_int_strict.json",
                                         "payload_float_extreme_as_int.json")
        self.assertEqual(code, 1)
        codes = {v["code"] for v in jload(out)["violations"]}
        self.assertIn("TYPE_MISMATCH", codes)

    # --- NEW FINDING (this suite): NaN/Infinity pass as JSON "number" ------

    def test_new_nan_silently_conforms_as_number_type(self):
        """Independent finding, not covered by limitations-probe/probe.py
        (which only exercises `integer` for SC-4): a schema field typed
        `number` with no minimum/maximum silently ACCEPTS a bare NaN value.
        json_type() maps Python float -> "number" without checking
        math.isnan/isinf, so the type check alone cannot catch it, and no
        min/max means no comparison ever runs into NaN's "everything is
        false" comparison semantics either. Contrast with SC-4: `integer`
        rejects non-finite values (they arrive as `float`, not `int`,
        so json_type already says "number" and TYPE_MISMATCH fires); the
        gap is specific to a field actually typed `number`."""
        code, out, err, to = self._check("schema_number_field.json",
                                         "payload_nan_as_number.json")
        self.assertEqual(code, 0)
        self.assertEqual(jload(out)["status"], "conform")

    def test_new_infinity_silently_conforms_as_number_type(self):
        code, out, err, to = self._check("schema_number_field.json",
                                         "payload_infinity_as_number.json")
        self.assertEqual(code, 0)

    def test_new_negative_infinity_silently_conforms_as_number_type(self):
        code, out, err, to = self._check("schema_number_field.json",
                                         "payload_neg_infinity_as_number.json")
        self.assertEqual(code, 0)

    # --- REPRODUCED FAILURE MODE (new to this suite): deep nesting crash ---

    def test_deep_nesting_payload_crashes_uncaught_recursionerror_exit1(self):
        """schema-checker is the most defensive of the three tools --
        load_json_file() explicitly catches OSError and UnicodeDecodeError
        around the read, and json.JSONDecodeError around the parse -- but
        RecursionError from a sufficiently deep payload is still none of
        those, so it is still uncaught and still exits via Python's default
        handler at 1, not the tool's own documented exit 2 for
        'could not be processed'."""
        code, out, err, to = self._check("schema_any.json", "payload_deep_nesting.json")
        self.assertEqual(code, 1)
        self.assertIn("RecursionError", err)

    # --- directory / empty / BOM / dup-keys handling (schema-checker's -----
    # --- own IO_ERROR path is the contrast: this tool DOES catch OSError) --

    def test_directory_as_payload_handled_gracefully_exit2(self):
        """Negative result / contrast with the other two tools: schema-
        checker's load_json_file() wraps the read in `except OSError`, so a
        directory passed as the payload path is a clean IO_ERROR at exit 2,
        not an uncaught traceback at exit 1."""
        code, out, err, to = self._check("schema_any.json", "dir_as_payload")
        self.assertEqual(code, 2)
        doc = jload(out)
        self.assertEqual(doc["status"], "error")
        self.assertEqual(doc["io_errors"][0]["code"], "IO_ERROR")

    def test_empty_payload_file_exit2(self):
        code, out, err, to = self._check("schema_any.json", "payload_empty_file.json")
        self.assertEqual(code, 2)
        self.assertEqual(jload(out)["io_errors"][0]["code"], "JSON_PARSE_ERROR")

    def test_payload_wrong_shape_array_where_object_expected(self):
        code, out, err, to = self._check("schema_object_only.json",
                                         "payload_wrong_shape_array.json")
        self.assertEqual(code, 1)
        v = jload(out)["violations"]
        self.assertEqual(v[0]["code"], "TYPE_MISMATCH")
        self.assertIn("array", v[0]["message"])

    def test_payload_wrong_shape_null_where_object_expected(self):
        code, out, err, to = self._check("schema_object_only.json",
                                         "payload_wrong_shape_null.json")
        self.assertEqual(code, 1)

    def test_duplicate_key_in_schema_document_last_value_wins(self):
        code, out, err, to = self._check("schema_dup_keys.json", "payload_conform.json")
        # last "root" wins: {"type": "any"} accepts anything.
        self.assertEqual(code, 0)

    def test_duplicate_key_in_payload_last_value_wins(self):
        """payload_dup_keys.json is {"n":1,"n":2} against a schema requiring
        "n" to be a bare number (no min/max): CPython's json module keeps
        the LAST value for a duplicate object key, so n=2 is what actually
        gets validated, and it conforms."""
        code, out, err, to = self._check("schema_number_field.json", "payload_dup_keys.json")
        self.assertEqual(code, 0, "n=2 (the last duplicate) should conform "
                                  "to a bare 'number' constraint")

    def test_bom_prefixed_schema_rejected_exit2(self):
        code, out, err, to = self._check("schema_bom.json", "payload_conform.json")
        self.assertEqual(code, 2)
        self.assertEqual(jload(out)["io_errors"][0]["code"], "JSON_PARSE_ERROR")

    def test_bom_prefixed_payload_rejected_exit2(self):
        code, out, err, to = self._check("schema_any.json", "payload_bom.json")
        self.assertEqual(code, 2)

    def test_no_trailing_newline_is_fine(self):
        code, out, err, to = self._check("schema_no_trailing_newline.json",
                                         "payload_no_trailing_newline.json")
        self.assertEqual(code, 0)

    def test_astral_plane_string_counts_as_one_codepoint_each(self):
        """Negative result: max_length: 10 against 3 astral-plane emoji (3
        Python codepoints, 12 UTF-8 bytes) conforms -- consistent with, not
        an exception to, SC-2's 'codepoints not bytes' rule."""
        code, out, err, to = self._check("schema_unicode.json", "payload_unicode_astral.json")
        self.assertEqual(code, 0)

    def test_bad_bounds_minimum_over_maximum_rejected_exit2(self):
        code, out, err, to = self._check("schema_bad_bounds.json", "payload_conform.json")
        self.assertEqual(code, 2)
        self.assertEqual(jload(out)["schema_errors"][0]["code"], "SCHEMA_BAD_BOUNDS")

    def test_bad_regex_pattern_rejected_exit2(self):
        code, out, err, to = self._check("schema_bad_regex.json", "payload_conform.json")
        self.assertEqual(code, 2)
        self.assertEqual(jload(out)["schema_errors"][0]["code"], "SCHEMA_BAD_REGEX")

    def test_unknown_schema_keyword_rejected_exit2(self):
        code, out, err, to = self._check("schema_unknown_keyword.json", "payload_conform.json")
        self.assertEqual(code, 2)
        self.assertEqual(jload(out)["schema_errors"][0]["code"], "SCHEMA_UNKNOWN_KEYWORD")

    def test_nonexistent_payload_file_exit2(self):
        s = rel_to_tool(self.TOOL, fixture_path(self.TOOL, "schema_any.json"))
        argv = [s, "does_not_exist.json"]
        code, out, err, to = run_tool(self.TOOL, argv)
        record("SC-nonexistent-payload", self.TOOL, argv, code, out, err, to)
        self.assertEqual(code, 2)
        self.assertEqual(jload(out)["io_errors"][0]["code"], "IO_ERROR")

    # --- determinism ---------------------------------------------------

    def test_report_byte_identical_across_two_runs(self):
        _, out1, _, _ = self._check("schema_basic.json", "payload_violations.json")
        _, out2, _, _ = self._check("schema_basic.json", "payload_violations.json")
        self.assertEqual(out1, out2)

    # --- CLI shape ------------------------------------------------------

    def test_no_args_exit2_usage(self):
        code, out, err, to = run_tool(self.TOOL, [])
        record("SC-noargs", self.TOOL, [], code, out, err, to)
        self.assertEqual(code, 2)
        self.assertIn("usage:", err)

    def test_version_flag_exit0(self):
        argv = ["--version"]
        code, out, err, to = run_tool(self.TOOL, argv)
        record("SC-version", self.TOOL, argv, code, out, err, to)
        self.assertEqual(code, 0)
        self.assertIn("schema_check.py", out)

    def test_help_flag_exit0(self):
        argv = ["-h"]
        code, out, err, to = run_tool(self.TOOL, argv)
        record("SC-help", self.TOOL, argv, code, out, err, to)
        self.assertEqual(code, 0)
        self.assertIn("usage:", out)


# ===========================================================================
# -o/--out file output: each tool's OTHER output path, and the byte-identical
# determinism claim every one of their READMEs makes about it
# ===========================================================================

class OutputFileTests(unittest.TestCase):
    """The READMEs' own 'exact rerun commands' sections all route through
    -o/--out rather than stdout for the determinism proof (build twice,
    sha256sum, cmp). These tests exercise that exact path -- a real file on
    disk, written twice, compared byte-for-byte -- for all three tools, not
    just stdout capture."""

    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def _scratch_rel(self, tool, filename):
        abspath = os.path.join(SCRATCH_DIR, tool, filename)
        os.makedirs(os.path.dirname(abspath), exist_ok=True)
        return rel_to_tool(tool, abspath), abspath

    def test_evidence_manifest_build_o_writes_byte_identical_files_twice(self):
        tool = "evidence-manifest"
        records = rel_to_tool(tool, fixture_path(tool, "valid_basic.json"))
        out1_rel, out1_abs = self._scratch_rel(tool, "run1.json")
        out2_rel, out2_abs = self._scratch_rel(tool, "run2.json")
        c1, o1, e1, _ = run_tool(tool, ["build", records, "-o", out1_rel])
        c2, o2, e2, _ = run_tool(tool, ["build", records, "-o", out2_rel])
        self.assertEqual((c1, c2), (0, 0))
        with open(out1_abs, "rb") as f1, open(out2_abs, "rb") as f2:
            b1, b2 = f1.read(), f2.read()
        self.assertEqual(b1, b2)
        self.assertIn("batch_root=", o1)
        self.assertTrue(b1.endswith(b"\n"))

    def test_reward_reconciler_o_writes_byte_identical_files_twice(self):
        tool = "reward-reconciler"
        e = rel_to_tool(tool, fixture_path(tool, "amount_mismatch_expected.json"))
        p = rel_to_tool(tool, fixture_path(tool, "amount_mismatch_payouts.json"))
        out1_rel, out1_abs = self._scratch_rel(tool, "report1.json")
        out2_rel, out2_abs = self._scratch_rel(tool, "report2.json")
        c1, o1, e1, _ = run_tool(tool, [e, p, "-o", out1_rel])
        c2, o2, e2, _ = run_tool(tool, [e, p, "-o", out2_rel])
        self.assertEqual((c1, c2), (1, 1))  # mismatched -> exit 1, even with -o
        with open(out1_abs, "rb") as f1, open(out2_abs, "rb") as f2:
            self.assertEqual(f1.read(), f2.read())

    def test_schema_checker_o_writes_byte_identical_files_twice_stdout_stays_empty(self):
        tool = "schema-checker"
        s = rel_to_tool(tool, fixture_path(tool, "schema_basic.json"))
        p = rel_to_tool(tool, fixture_path(tool, "payload_violations.json"))
        out1_rel, out1_abs = self._scratch_rel(tool, "report1.json")
        out2_rel, out2_abs = self._scratch_rel(tool, "report2.json")
        c1, o1, e1, _ = run_tool(tool, [s, p, "-o", out1_rel])
        c2, o2, e2, _ = run_tool(tool, [s, p, "-o", out2_rel])
        self.assertEqual((c1, c2), (1, 1))
        self.assertEqual(o1, "", "stdout must stay empty when -o is given")
        self.assertIn("status=violations", e1)
        with open(out1_abs, "rb") as f1, open(out2_abs, "rb") as f2:
            self.assertEqual(f1.read(), f2.read())

    def test_evidence_manifest_o_output_ends_with_ascii_content_and_newline(self):
        """The written file must be self-consistent with what verify later
        reads back -- build with -o, then verify the exact file just
        written, exit 0."""
        tool = "evidence-manifest"
        records = rel_to_tool(tool, fixture_path(tool, "valid_basic.json"))
        out_rel, out_abs = self._scratch_rel(tool, "roundtrip.json")
        c1, _, _, _ = run_tool(tool, ["build", records, "-o", out_rel])
        self.assertEqual(c1, 0)
        c2, o2, e2, _ = run_tool(tool, ["verify", out_rel])
        self.assertEqual(c2, 0)
        self.assertIn("VERIFIED", o2)


# ===========================================================================
# cross-tool: the shared uncaught-exception-exits-1 failure class
# ===========================================================================

class CrossToolFailureClassTests(unittest.TestCase):
    """All three tools were found (independently, while building this suite)
    to share one root cause across two different triggers: none of them
    wraps its per-record/per-file JSON loading in a catch-all exception
    handler, only in specific except clauses (FileNotFoundError /
    json.JSONDecodeError, or for schema-checker, OSError /
    UnicodeDecodeError / JSONDecodeError). Any OTHER exception --
    IsADirectoryError, RecursionError, decimal.InvalidOperation escaping
    quantize() -- reaches Python's default top-level handler, which prints
    a traceback to stderr and exits 1. Exit 1 is "verification drift" /
    "mismatched" / "violations found" in all three tools' own documented
    exit-code contracts, so a caller that branches only on exit code will
    misclassify a crash as a content finding. This class asserts the
    pattern holds across all three, not just within one tool's test class.
    """

    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def test_all_three_crash_exit1_not_exit2_on_a_directory_argument(self):
        cases = [
            ("evidence-manifest", ["build", rel_to_tool(
                "evidence-manifest", fixture_path("evidence-manifest", "dir_as_input"))]),
            ("reward-reconciler", [
                rel_to_tool("reward-reconciler", fixture_path("reward-reconciler", "dir_as_expected")),
                rel_to_tool("reward-reconciler", fixture_path("reward-reconciler", "balanced_payouts.json")),
            ]),
        ]
        for tool, argv in cases:
            with self.subTest(tool=tool):
                code, out, err, to = run_tool(tool, argv)
                self.assertEqual(code, 1, "%s: expected uncaught-crash exit 1" % tool)
                self.assertIn("IsADirectoryError", err)

    def test_schema_checker_is_the_exception_to_the_pattern_for_directories(self):
        """schema-checker catches OSError explicitly, so the SAME class of
        input (a directory) does NOT crash it -- exit 2, clean IO_ERROR.
        Stated here, next to the crash test above, so the contrast is not
        lost in a different test file."""
        code, out, err, to = run_tool("schema-checker", [
            rel_to_tool("schema-checker", fixture_path("schema-checker", "schema_any.json")),
            rel_to_tool("schema-checker", fixture_path("schema-checker", "dir_as_payload")),
        ])
        self.assertEqual(code, 2)

    def test_all_three_crash_exit1_on_pathological_json_nesting_depth(self):
        cases = [
            ("evidence-manifest", ["build", rel_to_tool(
                "evidence-manifest", fixture_path("evidence-manifest", "deep_nesting.json"))]),
            ("reward-reconciler", [
                rel_to_tool("reward-reconciler", fixture_path("reward-reconciler", "deep_nesting_expected.json")),
                rel_to_tool("reward-reconciler", fixture_path("reward-reconciler", "both_empty_payouts.json")),
            ]),
            ("schema-checker", [
                rel_to_tool("schema-checker", fixture_path("schema-checker", "schema_any.json")),
                rel_to_tool("schema-checker", fixture_path("schema-checker", "payload_deep_nesting.json")),
            ]),
        ]
        for tool, argv in cases:
            with self.subTest(tool=tool):
                code, out, err, to = run_tool(tool, argv)
                self.assertEqual(code, 1, "%s: expected RecursionError crash exit 1" % tool)
                self.assertIn("RecursionError", err)


# ===========================================================================
# meta: the suite's own artifacts are internally consistent
# ===========================================================================

class MetaSelfConsistencyTests(unittest.TestCase):
    """Checks on the suite itself: fixtures exist and round-trip, the tools
    this suite targets are exactly the three named in the brief (derived,
    not hardcoded as a repo-wide count), and captured_output.txt/README.md
    are present and shaped correctly. None of this asserts anything about
    mutable repo-wide state like a total tool/directory count.
    """

    def test_fixtures_directory_present(self):
        self.assertTrue(os.path.isdir(FIXTURES_DIR), "run make_fixtures.py first")

    def test_all_three_target_tool_scripts_exist(self):
        for tool, spec in TOOLS.items():
            path = os.path.join(spec["dir"], spec["script"])
            self.assertTrue(os.path.isfile(path), "%s missing at %s" % (tool, path))

    def test_generator_round_trips_cleanly(self):
        """Runs make_fixtures.py --verify as a subprocess (not by importing
        it, so this is the same code path a reviewer runs by hand)."""
        proc = subprocess.run([PY, os.path.join(HERE, "make_fixtures.py"), "--verify"],
                              cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=60)
        self.assertEqual(proc.returncode, 0,
                         "make_fixtures.py --verify failed:\n%s"
                         % proc.stdout.decode("utf-8", "replace"))

    def test_empty_fixture_directories_are_actually_empty_and_present(self):
        for relpath in ("evidence-manifest/dir_as_input",
                        "reward-reconciler/dir_as_expected",
                        "schema-checker/dir_as_payload"):
            full = os.path.join(FIXTURES_DIR, *relpath.split("/"))
            self.assertTrue(os.path.isdir(full), "%s should exist as a directory" % relpath)
            self.assertEqual(os.listdir(full), [], "%s should be empty" % relpath)

    def test_normalize_only_strips_the_repo_root_prefix(self):
        needle = os.path.join(REPO_ROOT, "evidence-manifest", "manifest.py")
        self.assertEqual(normalize(needle), "<REPO_ROOT>/evidence-manifest/manifest.py")
        untouched = "no repo path in here at all"
        self.assertEqual(normalize(untouched), untouched)

    def test_captured_output_transcript_present_and_well_formed(self):
        """Independently re-implements the header/exit grammar from
        transcript-drift/FORMAT.md (regexes copied verbatim from that file)
        rather than importing driftcheck.py, so this test has no coupling
        to that tool's internals -- only to the format both are built
        against."""
        path = os.path.join(HERE, "captured_output.txt")
        self.assertTrue(os.path.isfile(path))
        import re
        header_re = re.compile(r"^=== \$ (.+?) ===\s*$")
        exit_re = re.compile(r"^\s*exit=(-?\d+)\s*$")
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        lines = text.splitlines()
        headers = [i for i, ln in enumerate(lines) if header_re.match(ln)]
        self.assertGreater(len(headers), 0, "TRANSCRIPT_HAS_NO_COMMAND_RECORDS")
        for idx, start in enumerate(headers):
            end = headers[idx + 1] if idx + 1 < len(headers) else len(lines)
            body = lines[start + 1:end]
            self.assertTrue(any(exit_re.match(ln) for ln in body),
                            "record starting at line %d has no exit= line "
                            "(TRANSCRIPT_RECORD_HAS_NO_EXIT)" % (start + 1))
        self.assertNotIn("FAILED (", text,
                         "TRANSCRIPT_SHOWS_TEST_FAILURE: a committed "
                         "transcript must not show a failing run")

    def test_readme_present(self):
        self.assertTrue(os.path.isfile(os.path.join(HERE, "README.md")))

    def test_expected_results_json_present_and_parses(self):
        path = os.path.join(HERE, "expected_results.json")
        self.assertTrue(os.path.isfile(path))
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertIsInstance(doc, dict)
        self.assertIn("cases", doc)
        self.assertGreater(len(doc["cases"]), 0)


def _write_results_snapshot():
    """Called manually (see README) after a full run to (re)generate
    expected_results.json from RESULTS. Not invoked by the test run itself,
    so importing/running this module never mutates its own fixtures."""
    out = {
        "normalization": "absolute REPO_ROOT prefix replaced with "
                         "<REPO_ROOT>; all argv/paths were already relative "
                         "to the invoked tool's own directory.",
        "cases": RESULTS,
    }
    dest = os.path.join(HERE, "expected_results.json")
    with open(dest, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return dest


def _results_digest():
    """A single sha256 over every recorded case's normalized argv/exit
    code/output, sorted deterministically. This is the number the README's
    relocation proof compares across three runs (two original-path, one
    relocated) -- identical digests mean identical normalized behaviour
    regardless of where the checkout lives on disk."""
    canonical = json.dumps(RESULTS, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _run_all_and_report_digest():
    """Runs the full suite the same way `python3 -m unittest` does, but in
    -process so RESULTS is populated, then prints the digest. Used by the
    three-run relocation comparison in README.md / captured_output.txt."""
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=0)
    result = runner.run(suite)
    digest = _results_digest()
    sys.stderr.write("RESULTS_DIGEST sha256=%s cases=%d\n" % (digest, len(RESULTS)))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--digest"]:
        sys.exit(_run_all_and_report_digest())
    unittest.main()
