#!/usr/bin/env python3
"""Unit and CLI tests for dupdetect."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dupdetect
from dupdetect import (
    InvalidInputError, analyze, canonical_json, format_score, jaccard,
    load_records, normalize_text, shingles, validate_records,
)

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "dupdetect.py")
CLEAN = os.path.join(HERE, "records_clean.json")
DUPES = os.path.join(HERE, "records_dupes.json")

LONG_A = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet "
          "kilo lima mike november oscar papa quebec romeo sierra tango")
LONG_B = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet "
          "kilo lima mike november oscar papa quebec romeo sierra uniform")
UNRELATED = ("zulu yankee xray whiskey victor uniform tango sierra romeo quebec "
             "papa oscar november mike lima kilo juliet india hotel golf")


def run_cli(*args):
    """Run the CLI as a subprocess; return (returncode, stdout, stderr)."""
    proc = subprocess.run(
        [sys.executable, SCRIPT] + list(args),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def write_temp(payload, suffix=".json", raw=False):
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(payload if raw else json.dumps(payload))
    return path


# ---------------------------------------------------------------------------
# Token normalization
# ---------------------------------------------------------------------------

class TestNormalization(unittest.TestCase):

    def test_lowercases_tokens(self):
        self.assertEqual(normalize_text("Alpha BRAVO Charlie"),
                         ["alpha", "bravo", "charlie"])

    def test_collapses_runs_of_whitespace(self):
        self.assertEqual(normalize_text("a   b\t\tc\n\nd"),
                         ["a", "b", "c", "d"])

    def test_leading_and_trailing_whitespace_ignored(self):
        self.assertEqual(normalize_text("   padded   "), ["padded"])

    def test_strips_trailing_punctuation(self):
        self.assertEqual(normalize_text("hello, world!"), ["hello", "world"])

    def test_strips_leading_punctuation(self):
        self.assertEqual(normalize_text('"quoted" (parenthetical)'),
                         ["quoted", "parenthetical"])

    def test_preserves_interior_apostrophe(self):
        self.assertEqual(normalize_text("don't"), ["don't"])

    def test_preserves_interior_hyphen(self):
        self.assertEqual(normalize_text("well-known"), ["well-known"])

    def test_pure_punctuation_tokens_dropped(self):
        self.assertEqual(normalize_text("a --- b"), ["a", "b"])

    def test_empty_text_yields_no_tokens(self):
        self.assertEqual(normalize_text(""), [])

    def test_whitespace_only_text_yields_no_tokens(self):
        self.assertEqual(normalize_text("   \t\n  "), [])

    def test_digits_are_kept(self):
        self.assertEqual(normalize_text("Section 12(b)."), ["section", "12(b"])

    def test_unicode_nfkc_normalization_applied(self):
        # Fullwidth 'AB' normalizes to ASCII 'ab'.
        self.assertEqual(normalize_text("ＡＢ"), ["ab"])

    def test_punctuation_differences_do_not_change_tokens(self):
        self.assertEqual(normalize_text("The end."), normalize_text("the END!"))

    def test_non_string_text_rejected(self):
        with self.assertRaises(InvalidInputError):
            normalize_text(42)


# ---------------------------------------------------------------------------
# Shingling
# ---------------------------------------------------------------------------

class TestShingles(unittest.TestCase):

    def test_basic_trigram_set(self):
        self.assertEqual(shingles(["a", "b", "c", "d"], 3),
                         {"a b c", "b c d"})

    def test_shingle_count_is_n_minus_k_plus_one(self):
        tokens = normalize_text(LONG_A)
        self.assertEqual(len(shingles(tokens, 5)), len(tokens) - 4)

    def test_unigram_shingles_are_the_token_set(self):
        self.assertEqual(shingles(["a", "b", "a"], 1), {"a", "b"})

    def test_repeated_ngrams_deduplicated(self):
        self.assertEqual(shingles(["a", "b", "a", "b"], 2), {"a b", "b a"})

    def test_text_shorter_than_shingle_size_is_empty(self):
        self.assertEqual(shingles(["a", "b"], 5), set())

    def test_token_count_equal_to_shingle_size_yields_one(self):
        self.assertEqual(len(shingles(["a", "b", "c", "d", "e"], 5)), 1)

    def test_empty_token_list_is_empty_set(self):
        self.assertEqual(shingles([], 5), set())

    def test_shingle_size_zero_rejected(self):
        with self.assertRaises(InvalidInputError):
            shingles(["a"], 0)

    def test_negative_shingle_size_rejected(self):
        with self.assertRaises(InvalidInputError):
            shingles(["a"], -3)

    def test_shingles_are_space_joined_strings(self):
        self.assertIn("a b c", shingles(["a", "b", "c"], 3))


# ---------------------------------------------------------------------------
# Jaccard
# ---------------------------------------------------------------------------

class TestJaccard(unittest.TestCase):

    def test_identical_sets_score_one(self):
        s = {"a", "b", "c"}
        self.assertEqual(jaccard(s, set(s)), 1.0)

    def test_disjoint_sets_score_zero(self):
        self.assertEqual(jaccard({"a"}, {"b"}), 0.0)

    def test_half_overlap(self):
        self.assertAlmostEqual(jaccard({"a", "b"}, {"b", "c"}), 1.0 / 3.0)

    def test_both_empty_is_zero_not_one(self):
        self.assertEqual(jaccard(set(), set()), 0.0)

    def test_one_empty_is_zero(self):
        self.assertEqual(jaccard({"a"}, set()), 0.0)

    def test_symmetric(self):
        a, b = {"a", "b", "c"}, {"c", "d"}
        self.assertEqual(jaccard(a, b), jaccard(b, a))

    def test_score_never_exceeds_one(self):
        self.assertLessEqual(jaccard({"a", "b"}, {"a", "b", "c"}), 1.0)


class TestScoreFormatting(unittest.TestCase):

    def test_rounds_to_six_decimals(self):
        self.assertEqual(format_score(1.0 / 3.0), 0.333333)

    def test_exact_one_stays_one(self):
        self.assertEqual(format_score(1.0), 1.0)

    def test_exact_zero_stays_zero(self):
        self.assertEqual(format_score(0.0), 0.0)

    def test_formatting_is_idempotent(self):
        v = format_score(7.0 / 9.0)
        self.assertEqual(format_score(v), v)

    def test_serialized_score_is_stable_across_calls(self):
        a = json.dumps(format_score(7.0 / 9.0))
        b = json.dumps(format_score(7.0 / 9.0))
        self.assertEqual(a, b)


# ---------------------------------------------------------------------------
# Record validation
# ---------------------------------------------------------------------------

class TestValidation(unittest.TestCase):

    def test_valid_records_accepted(self):
        recs = validate_records([{"submission_id": "a", "text": "x"}])
        self.assertEqual(recs, [("a", "x")])

    def test_extra_fields_ignored(self):
        recs = validate_records(
            [{"submission_id": "a", "text": "x", "author": "z"}])
        self.assertEqual(recs, [("a", "x")])

    def test_empty_array_is_valid(self):
        self.assertEqual(validate_records([]), [])

    def test_top_level_object_rejected(self):
        with self.assertRaises(InvalidInputError):
            validate_records({"submission_id": "a", "text": "x"})

    def test_top_level_string_rejected(self):
        with self.assertRaises(InvalidInputError):
            validate_records("nope")

    def test_non_object_element_rejected(self):
        with self.assertRaises(InvalidInputError):
            validate_records(["nope"])

    def test_missing_submission_id_rejected(self):
        with self.assertRaises(InvalidInputError):
            validate_records([{"text": "x"}])

    def test_missing_text_rejected(self):
        with self.assertRaises(InvalidInputError):
            validate_records([{"submission_id": "a"}])

    def test_non_string_submission_id_rejected(self):
        with self.assertRaises(InvalidInputError):
            validate_records([{"submission_id": 7, "text": "x"}])

    def test_empty_submission_id_rejected(self):
        with self.assertRaises(InvalidInputError):
            validate_records([{"submission_id": "", "text": "x"}])

    def test_whitespace_submission_id_rejected(self):
        with self.assertRaises(InvalidInputError):
            validate_records([{"submission_id": "   ", "text": "x"}])

    def test_null_text_rejected(self):
        with self.assertRaises(InvalidInputError):
            validate_records([{"submission_id": "a", "text": None}])

    def test_numeric_text_rejected(self):
        with self.assertRaises(InvalidInputError):
            validate_records([{"submission_id": "a", "text": 12}])

    def test_duplicate_submission_id_rejected(self):
        with self.assertRaises(InvalidInputError):
            validate_records([{"submission_id": "a", "text": "x"},
                              {"submission_id": "a", "text": "y"}])

    def test_error_message_names_the_index(self):
        with self.assertRaises(InvalidInputError) as ctx:
            validate_records([{"submission_id": "a", "text": "x"},
                              {"submission_id": "b"}])
        self.assertIn("index 1", str(ctx.exception))

    def test_load_records_missing_file(self):
        with self.assertRaises(InvalidInputError):
            load_records(os.path.join(HERE, "no_such_file_12345.json"))

    def test_load_records_malformed_json(self):
        path = write_temp("{not json", raw=True)
        try:
            with self.assertRaises(InvalidInputError):
                load_records(path)
        finally:
            os.unlink(path)

    def test_load_records_directory_path(self):
        with self.assertRaises(InvalidInputError):
            load_records(HERE)

    def test_load_records_round_trip(self):
        path = write_temp([{"submission_id": "a", "text": "hello"}])
        try:
            self.assertEqual(load_records(path), [("a", "hello")])
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Analysis behaviour
# ---------------------------------------------------------------------------

def recs(*pairs):
    return [(sid, text) for sid, text in pairs]


class TestAnalyze(unittest.TestCase):

    def test_zero_records_flags_nothing(self):
        report = analyze([])
        self.assertEqual(report["flagged_count"], 0)
        self.assertEqual(report["record_count"], 0)
        self.assertEqual(report["comparison_count"], 0)

    def test_single_record_flags_nothing(self):
        report = analyze(recs(("a", LONG_A)))
        self.assertEqual(report["flagged_count"], 0)
        self.assertEqual(report["comparison_count"], 0)

    def test_identical_texts_score_exactly_one(self):
        report = analyze(recs(("a", LONG_A), ("b", LONG_A)))
        self.assertEqual(report["flagged_count"], 1)
        self.assertEqual(report["flagged_pairs"][0]["score"], 1.0)

    def test_identical_up_to_case_and_punctuation_score_one(self):
        report = analyze(recs(("a", LONG_A), ("b", LONG_A.upper() + ".")))
        self.assertEqual(report["flagged_pairs"][0]["score"], 1.0)

    def test_lightly_reworded_pair_is_flagged(self):
        report = analyze(recs(("a", LONG_A), ("b", LONG_B)), threshold=0.6)
        self.assertEqual(report["flagged_count"], 1)
        self.assertGreater(report["flagged_pairs"][0]["score"], 0.6)
        self.assertLess(report["flagged_pairs"][0]["score"], 1.0)

    def test_unrelated_texts_not_flagged(self):
        report = analyze(recs(("a", LONG_A), ("b", UNRELATED)))
        self.assertEqual(report["flagged_count"], 0)

    def test_pair_ids_sorted_lexicographically(self):
        report = analyze(recs(("zeta", LONG_A), ("alpha", LONG_A)))
        pair = report["flagged_pairs"][0]
        self.assertEqual(pair["submission_id_a"], "alpha")
        self.assertEqual(pair["submission_id_b"], "zeta")

    def test_pairs_sorted_by_descending_score(self):
        report = analyze(
            recs(("a", LONG_A), ("b", LONG_A), ("c", LONG_B)), threshold=0.5)
        scores = [p["score"] for p in report["flagged_pairs"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_ties_broken_by_submission_id(self):
        report = analyze(
            recs(("b1", LONG_A), ("a1", LONG_A), ("c1", LONG_A)))
        keys = [(p["submission_id_a"], p["submission_id_b"])
                for p in report["flagged_pairs"]]
        self.assertEqual(keys, sorted(keys))

    def test_input_order_does_not_change_output(self):
        forward = analyze(recs(("a", LONG_A), ("b", LONG_B), ("c", UNRELATED)),
                          threshold=0.5)
        reverse = analyze(recs(("c", UNRELATED), ("b", LONG_B), ("a", LONG_A)),
                          threshold=0.5)
        self.assertEqual(canonical_json(forward), canonical_json(reverse))

    def test_overlapping_shingles_are_sorted(self):
        report = analyze(recs(("a", LONG_A), ("b", LONG_B)), threshold=0.5)
        shg = report["flagged_pairs"][0]["overlapping_shingles"]
        self.assertEqual(shg, sorted(shg))

    def test_overlap_count_matches_list_length(self):
        report = analyze(recs(("a", LONG_A), ("b", LONG_B)), threshold=0.5)
        pair = report["flagged_pairs"][0]
        self.assertEqual(pair["overlap_count"],
                         len(pair["overlapping_shingles"]))

    def test_comparison_count_is_n_choose_2(self):
        report = analyze(recs(("a", LONG_A), ("b", LONG_B), ("c", UNRELATED)))
        self.assertEqual(report["comparison_count"], 3)

    def test_threshold_zero_flags_any_pair_with_overlap(self):
        report = analyze(recs(("a", LONG_A), ("b", LONG_B)), threshold=0.0)
        self.assertEqual(report["flagged_count"], 1)

    def test_zero_overlap_pair_never_flagged_even_at_threshold_zero(self):
        report = analyze(recs(("a", LONG_A), ("b", UNRELATED)), threshold=0.0)
        self.assertEqual(report["flagged_count"], 0)

    def test_threshold_one_flags_only_identical(self):
        report = analyze(recs(("a", LONG_A), ("b", LONG_A), ("c", LONG_B)),
                         threshold=1.0)
        self.assertEqual(report["flagged_count"], 1)
        self.assertEqual(report["flagged_pairs"][0]["score"], 1.0)

    def test_threshold_is_inclusive_at_boundary(self):
        report = analyze(recs(("a", "w x y z"), ("b", "w x y q")),
                         shingle_size=2, threshold=0.5)
        # shingles: {w x, x y, y z} vs {w x, x y, y q} -> 2/4 = 0.5
        self.assertEqual(report["flagged_pairs"][0]["score"], 0.5)
        self.assertEqual(report["flagged_count"], 1)

    def test_just_above_threshold_excludes_pair(self):
        report = analyze(recs(("a", "w x y z"), ("b", "w x y q")),
                         shingle_size=2, threshold=0.500001)
        self.assertEqual(report["flagged_count"], 0)

    def test_larger_shingle_size_lowers_score(self):
        small = analyze(recs(("a", LONG_A), ("b", LONG_B)),
                        shingle_size=2, threshold=0.0)
        large = analyze(recs(("a", LONG_A), ("b", LONG_B)),
                        shingle_size=8, threshold=0.0)
        self.assertGreater(small["flagged_pairs"][0]["score"],
                           large["flagged_pairs"][0]["score"])

    def test_shingle_size_recorded_in_config(self):
        report = analyze(recs(("a", LONG_A)), shingle_size=7, threshold=0.42)
        self.assertEqual(report["config"]["shingle_size"], 7)
        self.assertEqual(report["config"]["threshold"], 0.42)

    def test_short_text_never_flagged_against_itself(self):
        report = analyze(recs(("a", "too short"), ("b", "too short")),
                         shingle_size=5, threshold=0.0)
        self.assertEqual(report["flagged_count"], 0)

    def test_short_text_flagged_when_shingle_size_fits(self):
        report = analyze(recs(("a", "too short"), ("b", "too short")),
                         shingle_size=2, threshold=0.6)
        self.assertEqual(report["flagged_count"], 1)

    def test_empty_texts_are_never_flagged(self):
        report = analyze(recs(("a", ""), ("b", "")), threshold=0.0)
        self.assertEqual(report["flagged_count"], 0)

    def test_empty_vs_long_text_not_flagged(self):
        report = analyze(recs(("a", ""), ("b", LONG_A)), threshold=0.0)
        self.assertEqual(report["flagged_count"], 0)

    def test_invalid_shingle_size_rejected(self):
        with self.assertRaises(InvalidInputError):
            analyze(recs(("a", LONG_A)), shingle_size=0)

    def test_threshold_above_one_rejected(self):
        with self.assertRaises(InvalidInputError):
            analyze(recs(("a", LONG_A)), threshold=1.5)

    def test_negative_threshold_rejected(self):
        with self.assertRaises(InvalidInputError):
            analyze(recs(("a", LONG_A)), threshold=-0.1)

    def test_three_way_duplicate_yields_three_pairs(self):
        report = analyze(recs(("a", LONG_A), ("b", LONG_A), ("c", LONG_A)))
        self.assertEqual(report["flagged_count"], 3)


# ---------------------------------------------------------------------------
# Canonical output
# ---------------------------------------------------------------------------

class TestCanonicalJson(unittest.TestCase):

    def setUp(self):
        self.report = analyze(recs(("b", LONG_A), ("a", LONG_B)),
                              threshold=0.5)

    def test_ends_with_single_trailing_newline(self):
        text = canonical_json(self.report)
        self.assertTrue(text.endswith("}\n"))
        self.assertFalse(text.endswith("\n\n"))

    def test_no_whitespace_after_separators(self):
        text = canonical_json(self.report)
        self.assertNotIn(", ", text)
        self.assertNotIn(": ", text)

    def test_keys_are_sorted(self):
        text = canonical_json(self.report)
        self.assertLess(text.index('"comparison_count"'), text.index('"config"'))
        self.assertLess(text.index('"flagged_count"'),
                        text.index('"flagged_pairs"'))

    def test_output_is_pure_ascii(self):
        report = analyze(recs(("a", "café " * 12), ("b", "café " * 12)))
        canonical_json(report).encode("ascii")

    def test_output_is_reparseable(self):
        self.assertEqual(json.loads(canonical_json(self.report)), self.report)

    def test_repeated_serialization_is_byte_identical(self):
        self.assertEqual(canonical_json(self.report),
                         canonical_json(self.report))

    def test_repeated_analysis_is_byte_identical(self):
        again = analyze(recs(("b", LONG_A), ("a", LONG_B)), threshold=0.5)
        self.assertEqual(canonical_json(self.report), canonical_json(again))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCli(unittest.TestCase):

    def test_clean_file_exits_zero(self):
        code, out, _ = run_cli(CLEAN)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["flagged_count"], 0)

    def test_dupes_file_exits_one(self):
        code, out, _ = run_cli(DUPES)
        self.assertEqual(code, 1)
        self.assertGreaterEqual(json.loads(out)["flagged_count"], 2)

    def test_missing_file_exits_two(self):
        code, _, err = run_cli(os.path.join(HERE, "definitely_missing.json"))
        self.assertEqual(code, 2)
        self.assertIn("error:", err)

    def test_malformed_json_exits_two(self):
        path = write_temp("[[[", raw=True)
        try:
            self.assertEqual(run_cli(path)[0], 2)
        finally:
            os.unlink(path)

    def test_bad_schema_exits_two(self):
        path = write_temp([{"text": "no id here"}])
        try:
            self.assertEqual(run_cli(path)[0], 2)
        finally:
            os.unlink(path)

    def test_duplicate_ids_exit_two(self):
        path = write_temp([{"submission_id": "x", "text": "a"},
                           {"submission_id": "x", "text": "b"}])
        try:
            self.assertEqual(run_cli(path)[0], 2)
        finally:
            os.unlink(path)

    def test_no_arguments_exits_two(self):
        self.assertEqual(run_cli()[0], 2)

    def test_bad_shingle_size_exits_two(self):
        self.assertEqual(run_cli(DUPES, "--shingle-size", "0")[0], 2)

    def test_bad_threshold_exits_two(self):
        self.assertEqual(run_cli(DUPES, "--threshold", "3.0")[0], 2)

    def test_non_numeric_threshold_exits_two(self):
        self.assertEqual(run_cli(DUPES, "--threshold", "high")[0], 2)

    def test_high_threshold_still_flags_exact_duplicate(self):
        code, out, _ = run_cli(DUPES, "--threshold", "0.99")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["flagged_count"], 1)

    def test_threshold_one_point_zero_on_clean_exits_zero(self):
        self.assertEqual(run_cli(CLEAN, "--threshold", "1.0")[0], 0)

    def test_out_flag_writes_file_and_keeps_stdout_empty(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            code, out, _ = run_cli(DUPES, "-o", path)
            self.assertEqual(code, 1)
            self.assertEqual(out, "")
            with open(path, encoding="utf-8") as fh:
                self.assertGreaterEqual(json.load(fh)["flagged_count"], 2)
        finally:
            os.unlink(path)

    def test_two_runs_produce_byte_identical_files(self):
        paths = []
        try:
            for _ in range(2):
                fd, path = tempfile.mkstemp(suffix=".json")
                os.close(fd)
                paths.append(path)
                self.assertEqual(run_cli(DUPES, "-o", path)[0], 1)
            with open(paths[0], "rb") as fa, open(paths[1], "rb") as fb:
                self.assertEqual(fa.read(), fb.read())
        finally:
            for p in paths:
                os.unlink(p)

    def test_stdout_matches_out_file_bytes(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            _, out, _ = run_cli(DUPES)
            run_cli(DUPES, "-o", path)
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), out)
        finally:
            os.unlink(path)

    def test_shingle_size_flag_changes_report(self):
        _, out5, _ = run_cli(DUPES)
        _, out9, _ = run_cli(DUPES, "--shingle-size", "9")
        self.assertEqual(json.loads(out5)["config"]["shingle_size"], 5)
        self.assertEqual(json.loads(out9)["config"]["shingle_size"], 9)
        self.assertNotEqual(out5, out9)

    def test_dupes_fixture_contains_an_exact_duplicate(self):
        _, out, _ = run_cli(DUPES)
        scores = [p["score"] for p in json.loads(out)["flagged_pairs"]]
        self.assertIn(1.0, scores)

    def test_dupes_fixture_contains_a_near_duplicate(self):
        _, out, _ = run_cli(DUPES)
        scores = [p["score"] for p in json.loads(out)["flagged_pairs"]]
        self.assertTrue(any(0.6 <= s < 1.0 for s in scores))

    def test_version_flag_exits_zero(self):
        proc = subprocess.run([sys.executable, SCRIPT, "--version"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True)
        self.assertEqual(proc.returncode, 0)

    def test_main_returns_codes_in_process(self):
        self.assertEqual(dupdetect.main([CLEAN, "-o", os.devnull]), 0)
        self.assertEqual(dupdetect.main([DUPES, "-o", os.devnull]), 1)
        self.assertEqual(dupdetect.main(["/nope/nope.json"]), 2)


if __name__ == "__main__":
    unittest.main()
