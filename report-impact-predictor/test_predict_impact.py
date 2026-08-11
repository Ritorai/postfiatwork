"""Tests for predict_impact.py and the committed dependency map.

Run with:
    python3 -m unittest test_predict_impact -v

The tests that matter most are in TestGroundTruth: they pin the predictor's
answer against what the repository's own gates ACTUALLY do, measured by
running report-freshness and the enforcing unit tests before and after a real
change. Those measurements are recorded in sample_runs.txt and quoted in
README.md. A predictor whose tests only check its own arithmetic proves
nothing, so every scenario here is one that was run for real first.

WHY THE EVIDENCE FILE IS NOT NAMED captured_output.txt

Because that exact basename, one level under the repository root, is the input
to index-generator/pipe_scan.py and transcript-schema/validate_transcript.py.
Adding one here would move index-generator's committed
`total_command_records` (548) and `transcript_files_scanned` (51) and turn
`test_pipe_classify` red. The tool being delivered predicts precisely that,
and `test_the_predictor_predicts_its_own_evidence_filename_trap` asserts it.
"""
import json
import os
import subprocess
import sys
import unittest

import predict_impact as pi

BASE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(BASE, "predict_impact.py")
MAP = os.path.join(BASE, "dependency_map.json")


def run_cli(args):
    result = subprocess.run([sys.executable, CLI] + args,
                            capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def predict(*paths, **kw):
    """kw: change_kind (default "add", the common case for this repository's
    deliveries), new_dirs, gone_dirs."""
    doc = pi.load_map(MAP)
    return pi.predict(doc, sorted(set(pi.normalize_path(p) for p in paths)),
                      kw.get("new_dirs", ()), kw.get("change_kind", "add"),
                      kw.get("gone_dirs", ()))


def ids(report, confidence=None):
    return sorted(i["report_id"] for i in report["impacted"]
                  if confidence is None or i["confidence"] == confidence)


class TestPathNormalisation(unittest.TestCase):
    def test_leading_dot_slash_is_stripped(self):
        self.assertEqual(pi.normalize_path("./a/b.py"), "a/b.py")

    def test_double_separator_collapses(self):
        self.assertEqual(pi.normalize_path("a//b.py"), "a/b.py")

    def test_backslashes_become_slashes(self):
        self.assertEqual(pi.normalize_path("a\\b.py"), "a/b.py")

    def test_dot_dot_is_resolved(self):
        self.assertEqual(pi.normalize_path("a/c/../b.py"), "a/b.py")

    def test_absolute_path_is_a_usage_error(self):
        with self.assertRaises(pi.UsageError):
            pi.normalize_path("/etc/passwd")

    def test_escaping_the_root_is_a_usage_error(self):
        with self.assertRaises(pi.UsageError):
            pi.normalize_path("../outside.py")

    def test_drive_letter_is_a_usage_error(self):
        with self.assertRaises(pi.UsageError):
            pi.normalize_path("C:/repo/a.py")

    def test_the_root_itself_is_a_usage_error(self):
        with self.assertRaises(pi.UsageError):
            pi.normalize_path("./")


class TestTheThreeNamedPinningPatterns(unittest.TestCase):
    """The three relationships the brief calls out by name."""

    def test_argparse_line_numbers_editing_any_depth1_cli_reaches_doc_validator(self):
        got = predict("staleness-monitor/staleness.py")
        self.assertIn("doc-validator:option_report.json", ids(got))
        reason = [r for i in got["impacted"]
                  if i["report_id"] == "doc-validator:option_report.json"
                  for r in i["reasons"]]
        self.assertTrue(any("TOOL_CLI_PY" == r["rule"] for r in reason))

    def test_the_line_number_pin_is_named_in_the_map_not_just_implied(self):
        doc = pi.load_map(MAP)
        entry = [r for r in doc["reports"]
                 if r["id"] == "doc-validator:option_report.json"][0]
        blob = json.dumps(entry)
        self.assertIn("LINE NUMBER", blob)

    def test_a_depth_2_py_does_not_reach_doc_validator(self):
        """doc-validator lists *.py directly inside a tool directory only.
        A helper one level deeper is invisible to it -- and this is the kind
        of thing a reader guesses wrong, so it is pinned."""
        got = predict("regression-checker/fixtures/tool_ok/tool.py")
        self.assertNotIn("doc-validator:option_report.json", ids(got))
        self.assertIn("nondeterminism-scanner:self_scan_report.json", ids(got))

    def test_transcript_counts_a_captured_output_reaches_index_generator(self):
        got = predict("some-tool/captured_output.txt")
        self.assertIn("index-generator:pipe_classification_report.json", ids(got))
        self.assertIn("transcript-schema:validation_report.json", ids(got))

    def test_the_predictor_predicts_its_own_evidence_filename_trap(self):
        """Naming this directory's evidence file captured_output.txt would
        move index-generator's pinned counts; naming it sample_runs.txt does
        not. The tool has to get this right about itself."""
        trap = predict("report-impact-predictor/captured_output.txt")
        safe = predict("report-impact-predictor/sample_runs.txt")
        self.assertIn("index-generator:pipe_classification_report.json", ids(trap))
        self.assertNotIn("index-generator:pipe_classification_report.json", ids(safe))

    def test_a_transcript_deeper_than_depth_1_is_invisible(self):
        """commit-claim-auditor/fixture/captured_output.txt exists in this
        repository and is NOT scanned; depth is part of the rule."""
        got = predict("commit-claim-auditor/fixture/captured_output.txt")
        self.assertNotIn("index-generator:pipe_classification_report.json", ids(got))
        self.assertNotIn("transcript-schema:validation_report.json", ids(got))

    def test_weak_assertion_readme_is_reached_from_a_test_module(self):
        got = predict("link-integrity/test_probe_module.py")
        self.assertIn("weak-assertion-scanner:self_scan_report.json", ids(got))
        self.assertIn("weak-assertion-scanner:README.md", ids(got))

    def test_the_readme_edge_is_a_propagation_not_a_path_match(self):
        """Nothing about the path 'weak-assertion-scanner/README.md' says a
        test re-derives its numbers. That edge has to be declared."""
        got = predict("link-integrity/test_probe_module.py")
        entry = [i for i in got["impacted"]
                 if i["report_id"] == "weak-assertion-scanner:README.md"][0]
        self.assertTrue(any(r["kind"] == "propagation" for r in entry["reasons"]))

    def test_a_non_test_py_does_not_reach_weak_assertion(self):
        got = predict("link-integrity/link_integrity.py")
        self.assertNotIn("weak-assertion-scanner:self_scan_report.json", ids(got))
        self.assertIn("nondeterminism-scanner:self_scan_report.json", ids(got))


class TestGroundTruth(unittest.TestCase):
    """The `certain` set must equal what the repository's gates actually do.

    Each scenario below was measured before it was written down: the change
    was made in a copy of the tree, `report-freshness/freshness.py` was run,
    and the enforcing unit tests were run. sample_runs.txt has the output.
    """

    def test_new_test_module_certain_set_matches_measurement(self):
        """Measured: report-freshness moved nondeterminism-scanner and
        weak-assertion-scanner to 'stale'; test_weakassert_regen FAILED
        (failures=2); test_optioncheck OK; test_pipe_classify OK."""
        got = predict("link-integrity/test_probe_module.py", change_kind="add")
        self.assertEqual(ids(got, "certain"), [
            "nondeterminism-scanner:self_scan_report.json",
            "weak-assertion-scanner:README.md",
            "weak-assertion-scanner:self_scan_report.json",
        ])

    def test_new_test_module_false_positives_are_labelled_possible(self):
        """The two that did NOT move must not be claimed as certain."""
        got = predict("link-integrity/test_probe_module.py", change_kind="add")
        self.assertEqual(ids(got, "possible"), [
            "claim-crosscheck:sample_run.json",
            "doc-validator:option_report.json",
            "regression-checker:baseline_coverage_report.json",
        ])

    def test_new_non_tool_directory_certain_set_matches_measurement(self):
        """Measured: creating docs/notes.md moved exactly
        regression-checker and transcript-schema to 'stale'."""
        got = predict("docs/notes.md", change_kind="add")
        self.assertEqual(ids(got, "certain"), [
            "regression-checker:baseline_coverage_report.json",
            "transcript-schema:validation_report.json",
        ])

    def test_a_new_directory_without_a_transcript_spares_index_generator(self):
        """The measured asymmetry: transcript-schema records the directory
        NAME in directories_without_transcript, index-generator skips a
        directory with no transcript entirely."""
        got = predict("docs/notes.md")
        self.assertIn("transcript-schema:validation_report.json", ids(got))
        self.assertNotIn("index-generator:pipe_classification_report.json", ids(got))

    def test_every_certain_edge_declares_its_measurement(self):
        """An edge may only be called exact if the map says how that was
        established. This is the guard against a future editor upgrading a
        guess to 'exact' because it felt right."""
        doc = pi.load_map(MAP)
        for report in doc["reports"]:
            for trigger in report["triggers"]:
                if trigger["precision"] != "exact":
                    continue
                with self.subTest(report=report["id"], rule=trigger["rule"]):
                    self.assertIn("Measured", trigger["note"])


class TestNewDirOverride(unittest.TestCase):
    """The map describes the tree AS COMMITTED, so once a directory is in
    `known_tool_directories` the tool can no longer answer 'what happens when
    this directory is created'. --new-dir is how you ask that question, and
    this tool's own delivery is the case that forced it."""

    OWN = ["report-impact-predictor/README.md",
           "report-impact-predictor/dependency_map.json",
           "report-impact-predictor/predict_impact.py",
           "report-impact-predictor/sample_runs.txt",
           "report-impact-predictor/test_predict_impact.py"]

    def _predict(self, paths, new_dirs=()):
        doc = pi.load_map(MAP)
        return pi.predict(doc, sorted(set(pi.normalize_path(p) for p in paths)),
                          new_dirs, "add")

    def test_without_the_override_the_directory_looks_established(self):
        got = self._predict(self.OWN)
        self.assertNotIn("regression-checker:baseline_coverage_report.json",
                         ids(got))
        self.assertNotIn("transcript-schema:validation_report.json", ids(got))

    def test_with_the_override_the_creation_effects_appear(self):
        got = self._predict(self.OWN, ["report-impact-predictor"])
        self.assertIn("regression-checker:baseline_coverage_report.json", ids(got))
        self.assertIn("transcript-schema:validation_report.json", ids(got))

    def test_this_delivery_predicts_itself_exactly(self):
        """Ground truth, measured by copying this directory into a clean
        clone and running the gates: report-freshness moved claim-crosscheck,
        nondeterminism-scanner, regression-checker, transcript-schema and
        weak-assertion-scanner to 'stale'; test_weakassert_regen FAILED;
        test_optioncheck FAILED; test_pipe_classify OK."""
        got = self._predict(self.OWN, ["report-impact-predictor"])
        self.assertEqual(ids(got), [
            "claim-crosscheck:sample_run.json",
            "doc-validator:option_report.json",
            "nondeterminism-scanner:self_scan_report.json",
            "regression-checker:baseline_coverage_report.json",
            "transcript-schema:validation_report.json",
            "weak-assertion-scanner:README.md",
            "weak-assertion-scanner:self_scan_report.json",
        ])
        self.assertEqual(got["unaffected_reports"],
                         ["index-generator:pipe_classification_report.json"])

    def test_the_override_is_recorded_in_the_output(self):
        got = self._predict(self.OWN, ["report-impact-predictor"])
        self.assertEqual(got["treated_as_new_directories"],
                         ["report-impact-predictor"])

    def test_an_unused_override_changes_nothing_but_is_still_recorded(self):
        plain = self._predict(["link-integrity/link_integrity.py"])
        with_override = self._predict(["link-integrity/link_integrity.py"],
                                      ["some-other-dir"])
        self.assertEqual(ids(plain), ids(with_override))
        self.assertEqual(with_override["treated_as_new_directories"],
                         ["some-other-dir"])

    def test_a_path_rather_than_a_name_is_a_usage_error(self):
        code, out, err = run_cli(["a/b.py", "--new-dir", "a/b"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("top-level directory NAME", err)

    def test_the_override_is_repeatable(self):
        code, out, err = run_cli(["a/x.py", "b/y.py",
                                  "--new-dir", "a", "--new-dir", "b"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["treated_as_new_directories"],
                         ["a", "b"])


class TestTheEdgesHostileReviewFound(unittest.TestCase):
    """Every test here exists because a first pass got it wrong. Each was
    measured in a clean clone before the map was changed to cover it."""

    def test_editing_baselines_json_reaches_regression_checker(self):
        """Measured: deleting the budget-forecaster entry from
        regression-checker/baselines.json took that report to 'stale'. The
        freshness manifest declares this file as the entry's input, and the
        first version of the map ignored the manifest's `inputs` key and
        listed the report as UNAFFECTED."""
        got = predict("regression-checker/baselines.json", change_kind="edit")
        self.assertIn("regression-checker:baseline_coverage_report.json",
                      ids(got, "certain"))

    def test_editing_a_baselined_tools_fixture_reaches_regression_checker(self):
        """Measured: appending a record to dup-detector/records_dupes.json
        took that report to 'stale'. coverage_audit re-runs every baselined
        tool and hashes its output, so a fixture is an input too."""
        got = predict("dup-detector/records_dupes.json", change_kind="edit")
        self.assertIn("regression-checker:baseline_coverage_report.json",
                      ids(got))

    def test_a_non_baselined_tools_file_does_not(self):
        got = predict("shebang-mode/notes.txt", change_kind="edit")
        self.assertNotIn("regression-checker:baseline_coverage_report.json",
                         ids(got))

    def test_build_is_not_an_ignored_directory_for_the_counters(self):
        """Measured: creating build/notes.md took regression-checker AND
        transcript-schema to 'stale'. A single repository-wide ignore list
        made this tool answer exit 0 with an eight-way unaffected list."""
        got = predict("build/notes.md", change_kind="add")
        self.assertEqual(ids(got, "certain"), [
            "regression-checker:baseline_coverage_report.json",
            "transcript-schema:validation_report.json",
        ])

    def test_hg_is_not_ignored_by_the_python_scanner(self):
        """ndscan's IGNORED_DIR_NAMES has 11 entries and .hg is not one of
        them. Measured: creating .hg/x.py took nondeterminism-scanner to
        'stale'."""
        got = predict(".hg/x.py", change_kind="add")
        self.assertIn("nondeterminism-scanner:self_scan_report.json",
                      ids(got, "certain"))

    def test_pycache_is_ignored_by_every_rule(self):
        got = predict("link-integrity/__pycache__/x.py", change_kind="add")
        self.assertNotIn("nondeterminism-scanner:self_scan_report.json", ids(got))

    def test_the_rule_ignore_lists_are_not_all_the_same(self):
        """If a future editor collapses them back into one global list, this
        fails -- which is the whole point of it."""
        doc = pi.load_map(MAP)
        distinct = set(tuple(r.get("ignores", [])) for r in doc["rules"].values())
        self.assertGreater(len(distinct), 1)

    def test_an_edit_is_not_as_strong_as_an_add(self):
        """Measured: appending a comment line to
        commit-claim-auditor/fixture/test_example.py (a file with no
        findings) left report-freshness at exit 0 and every enforcing test
        green. The first version labelled three reports `certain` for it."""
        edited = predict("commit-claim-auditor/fixture/test_example.py",
                         change_kind="edit")
        self.assertEqual(ids(edited, "certain"), [])
        added = predict("commit-claim-auditor/fixture/test_example.py",
                        change_kind="add")
        self.assertNotEqual(ids(added, "certain"), [])

    def test_unknown_is_the_conservative_default(self):
        """Without --change-kind the caller has not said whether the path is
        added, removed or edited, so an edge that only holds for add/remove
        must not be reported as certain."""
        code, out, err = run_cli(["link-integrity/test_probe_module.py"])
        report = json.loads(out)
        self.assertEqual(report["change_kind"], "unknown")
        self.assertEqual(report["summary"]["certain"], 0)

    def test_a_chain_is_only_as_strong_as_its_weakest_link(self):
        """weak-assertion-scanner's README is reached by a propagation that
        is exact for every kind -- but if the report it propagates from is
        only `possible`, the regeneration might never happen, so the README
        cannot be certain either."""
        got = predict("commit-claim-auditor/fixture/test_example.py",
                      change_kind="edit")
        entry = [i for i in got["impacted"]
                 if i["report_id"] == "weak-assertion-scanner:README.md"][0]
        self.assertEqual(entry["confidence"], "possible")

    def test_an_unknown_change_kind_is_a_usage_error(self):
        code, out, err = run_cli(["a/b.py", "--change-kind", "refactor"])
        self.assertEqual(code, 2)

    def test_every_edge_declares_the_change_kinds_it_is_exact_for(self):
        doc = pi.load_map(MAP)
        for report in doc["reports"]:
            for trigger in report["triggers"]:
                with self.subTest(report=report["id"], rule=trigger["rule"]):
                    self.assertIn("exact_for", trigger)

    def test_the_baselined_tool_list_matches_the_repository(self):
        root = os.path.dirname(BASE)
        with open(os.path.join(root, "regression-checker", "baselines.json"),
                  encoding="utf-8") as fh:
            baselines = json.load(fh)
        doc = pi.load_map(MAP)
        self.assertEqual(doc["baselined_tools"], sorted(baselines["tools"]))


class TestTheEdgesSecondHostileReviewFound(unittest.TestCase):
    """Round two. Round one's repairs were real but did not generalise: the
    ignore lists were still transcribed rather than imported, only one of the
    two declared manifest inputs was covered, and the add/remove symmetry the
    map claimed did not exist for a deleted directory."""

    def test_the_ignore_lists_equal_their_consumers(self):
        """Imported, not transcribed. weakassert's set contains 'env' and NOT
        '.eggs'; ndscan's is the reverse. Getting that backwards made a test
        module under env/ `certain` for a report it does not move."""
        root = os.path.dirname(BASE)
        doc = pi.load_map(MAP)
        DOT = "<any name starting with a dot>"
        old = list(sys.path)
        try:
            for sub in ("weak-assertion-scanner", "nondeterminism-scanner",
                        "doc-validator"):
                sys.path.insert(0, os.path.join(root, sub))
            import weakassert
            import ndscan
            import docval
        finally:
            sys.path[:] = old
        self.assertEqual(doc["rules"]["ANY_PY"]["ignores"],
                         sorted(ndscan.IGNORED_DIR_NAMES))
        self.assertEqual(doc["rules"]["TEST_PY"]["ignores"],
                         sorted(set(weakassert.IGNORED_DIR_NAMES)) + [DOT])
        self.assertEqual(doc["rules"]["TOOL_CLI_PY"]["ignores"],
                         sorted(docval.SKIP_DIR_NAMES) + [DOT])

    def test_env_is_ignored_by_weakassert_and_not_by_ndscan(self):
        """Measured: creating env/test_probe.py took nondeterminism-scanner,
        regression-checker and transcript-schema to 'stale' and left
        test_weakassert_regen OK."""
        got = predict("env/test_probe.py", change_kind="add")
        self.assertEqual(ids(got, "certain"), [
            "nondeterminism-scanner:self_scan_report.json",
            "regression-checker:baseline_coverage_report.json",
            "transcript-schema:validation_report.json",
        ])
        self.assertNotIn("weak-assertion-scanner:self_scan_report.json", ids(got))
        self.assertNotIn("weak-assertion-scanner:README.md", ids(got))

    def test_every_declared_manifest_input_is_covered(self):
        """The generic version of round one's finding. The manifest declares
        `inputs` on four entries; the two live ones must each be reachable."""
        root = os.path.dirname(BASE)
        with open(os.path.join(root, "report-freshness", "manifest.json"),
                  encoding="utf-8") as fh:
            manifest = json.load(fh)
        doc = pi.load_map(MAP)
        pinned = set(r["id"] for r in doc["reports"] if r["pinned"])
        for entry in manifest["entries"]:
            for path in entry.get("inputs") or []:
                if entry["id"] in pinned:
                    continue
                with self.subTest(input=path):
                    got = predict(path, change_kind="edit")
                    self.assertIn(entry["id"], ids(got, "certain"))

    def test_transcript_schema_schema_json_is_not_a_false_negative(self):
        """Measured: bumping transcript-schema/schema.json's schema_version
        took validation_report.json to 'stale'. The first two versions of the
        map listed it under unaffected_reports."""
        got = predict("transcript-schema/schema.json", change_kind="edit")
        self.assertIn("transcript-schema:validation_report.json",
                      ids(got, "certain"))

    def test_a_declared_input_of_a_PINNED_entry_moves_nothing(self):
        """env-leak-scanner/review.json and transcript-drift/inventory.json
        are declared inputs of the two pinned entries. Pinned wins."""
        for path in ("env-leak-scanner/review.json",
                     "transcript-drift/inventory.json"):
            with self.subTest(path=path):
                got = predict(path, change_kind="edit")
                self.assertNotIn("env-leak-scanner:leak_report_2026-08-04.json",
                                 ids(got))
                self.assertNotIn(
                    "transcript-drift:drift_report_after_migration.json",
                    ids(got))

    def test_deleting_a_directory_reaches_the_directory_counters(self):
        """Measured: rm -rf sortkey-detector took claim-crosscheck,
        nondeterminism-scanner, regression-checker, transcript-schema and
        weak-assertion-scanner to 'stale'. NEW_TOOL_DIR could never fire for
        it -- a directory being deleted is still in known_tool_directories --
        so the first version reported regression-checker UNAFFECTED."""
        got = predict("sortkey-detector/sortdetect.py",
                      "sortkey-detector/README.md",
                      "sortkey-detector/test_sortdetect.py",
                      "sortkey-detector/captured_output.txt",
                      change_kind="remove", gone_dirs=["sortkey-detector"])
        for expected in ("regression-checker:baseline_coverage_report.json",
                         "transcript-schema:validation_report.json",
                         "nondeterminism-scanner:self_scan_report.json",
                         "weak-assertion-scanner:self_scan_report.json",
                         "claim-crosscheck:sample_run.json"):
            with self.subTest(report=expected):
                self.assertIn(expected, ids(got))
        self.assertEqual(got["unaffected_reports"], [])

    def test_without_gone_dir_the_deletion_is_not_visible(self):
        """Honest about the limit: a path list cannot tell one removed file
        from a removed directory, so it has to be declared."""
        got = predict("sortkey-detector/sortdetect.py", change_kind="remove")
        self.assertNotIn("regression-checker:baseline_coverage_report.json",
                         ids(got, "certain"))

    def test_gone_dir_is_recorded_in_the_output(self):
        got = predict("sortkey-detector/x.py", change_kind="remove",
                      gone_dirs=["sortkey-detector"])
        self.assertEqual(got["treated_as_deleted_directories"],
                         ["sortkey-detector"])

    def test_editing_a_generator_reaches_its_own_report(self):
        """Measured: changing a finding message in weakassert.py took
        self_scan_report.json to 'stale'. No rule connected a producer to its
        own artifact, so the report was listed UNAFFECTED."""
        got = predict("weak-assertion-scanner/weakassert.py",
                      change_kind="edit")
        self.assertIn("weak-assertion-scanner:self_scan_report.json", ids(got))

    def test_every_producer_reaches_its_own_report(self):
        doc = pi.load_map(MAP)
        for report in doc["reports"]:
            if report["pinned"] or not report.get("producer"):
                continue
            with self.subTest(report=report["id"]):
                got = predict(report["producer"], change_kind="edit")
                self.assertIn(report["id"], ids(got))

    def test_a_trailing_space_is_part_of_the_filename(self):
        """`link-integrity/probe.py ` is not a .py to any scanner here.
        Stripping it silently answered about a different file and returned
        `certain` for a report that does not move."""
        self.assertEqual(pi.normalize_path("link-integrity/probe.py "),
                         "link-integrity/probe.py ")
        got = predict("link-integrity/probe.py ", change_kind="add")
        self.assertEqual(ids(got, "certain"), [])

    def test_a_trailing_newline_from_a_line_oriented_caller_is_still_removed(self):
        self.assertEqual(pi.normalize_path("a/b.py\n"), "a/b.py")
        self.assertEqual(pi.normalize_path("a/b.py\r\n"), "a/b.py")

    def test_the_widest_input_fires_every_rule(self):
        """The old version of this test could not reach the report-specific
        rules, so two of them were never exercised together."""
        doc = pi.load_map(MAP)
        paths = ["t/a.py", "t/test_a.py", "t/captured_output.txt",
                 "t/README.md", "t/r.json", "brand-new/x.txt",
                 "dup-detector/records_dupes.json",
                 "transcript-schema/schema.json",
                 "regression-checker/baselines.json",
                 "nondeterminism-scanner/ndscan.py",
                 "sortkey-detector/gone.py"]
        got = pi.predict(doc, sorted(set(pi.normalize_path(p) for p in paths)),
                         (), "add", ["sortkey-detector"])
        fired = set(r["rule"] for i in got["impacted"] for r in i["reasons"]
                    if r["rule"])
        self.assertEqual(fired, set(doc["rules"]))
        self.assertEqual(got["summary"]["impacted_reports"], 8)


class TestUnrelatedAndEmptyInput(unittest.TestCase):
    def test_a_path_inside_a_known_tool_that_matches_nothing_is_clean(self):
        code, out, err = run_cli(["shebang-mode/notes.txt"])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["impacted"], [])
        self.assertEqual(report["summary"]["impacted_reports"], 0)

    def test_a_clean_run_still_lists_every_report_as_unaffected(self):
        got = predict("shebang-mode/notes.txt")
        self.assertEqual(len(got["unaffected_reports"]), 8)

    def test_the_same_path_in_a_BASELINED_tool_is_not_clean(self):
        """link-integrity has a baselines.json entry, shebang-mode does not.
        The difference is the whole point of BASELINED_TOOL_TREE."""
        got = predict("link-integrity/notes.txt")
        self.assertIn("regression-checker:baseline_coverage_report.json",
                      ids(got))
        self.assertEqual(ids(got, "certain"), [])

    def test_no_paths_is_a_usage_error(self):
        code, out, err = run_cli([])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("no paths given", err)

    def test_absolute_path_exits_2_and_writes_nothing_to_stdout(self):
        code, out, err = run_cli(["/etc/passwd"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")

    def test_impacted_exits_1(self):
        code, out, err = run_cli(["link-integrity/link_integrity.py"])
        self.assertEqual(code, 1)


class TestPinnedReportsAreNeverImpacted(unittest.TestCase):
    """Reporting point-in-time evidence as stale is the worst thing this
    tool could do, so it is pinned from several directions."""

    PINNED = ["env-leak-scanner:leak_report_2026-08-04.json",
              "transcript-drift:drift_report_after_migration.json"]

    def test_no_change_impacts_a_pinned_report(self):
        for path in ("env-leak-scanner/leakscan.py",
                     "transcript-drift/driftcheck.py",
                     "some-tool/captured_output.txt",
                     "docs/notes.md",
                     "a/b/c/anything.py"):
            with self.subTest(path=path):
                got = predict(path)
                for pinned in self.PINNED:
                    self.assertNotIn(pinned, ids(got))

    def test_pinned_reports_are_listed_separately_every_time(self):
        got = predict("docs/notes.md")
        self.assertEqual(got["pinned_reports_never_impacted"], self.PINNED)

    def test_a_pinned_report_may_not_declare_triggers(self):
        doc = pi.load_map(MAP)
        doc["reports"].append({"id": "x:y.json", "artifact": "x/y.json",
                               "pinned": True,
                               "triggers": [{"rule": "ANY_PY",
                                             "precision": "exact",
                                             "exact_for": ["add"],
                                             "note": "Measured"}]})
        with self.assertRaises(pi.UsageError):
            pi.validate_map(doc, "in-memory")


class TestDeterminism(unittest.TestCase):
    def test_two_runs_are_byte_identical(self):
        _, a, _ = run_cli(["link-integrity/test_x.py", "docs/notes.md"])
        _, b, _ = run_cli(["link-integrity/test_x.py", "docs/notes.md"])
        self.assertEqual(a, b)
        self.assertNotEqual(a, "")

    def test_reordering_the_input_changes_nothing(self):
        _, a, _ = run_cli(["b/x.py", "a/y.py"])
        _, b, _ = run_cli(["a/y.py", "b/x.py"])
        self.assertEqual(a, b)

    def test_duplicated_paths_change_nothing(self):
        _, a, _ = run_cli(["a/y.py"])
        _, b, _ = run_cli(["a/y.py", "./a/y.py", "a//y.py", "a\\y.py"])
        self.assertEqual(a, b)

    def test_the_report_carries_no_absolute_path(self):
        _, out, _ = run_cli(["a/y.py"])
        for probe in (BASE, os.getcwd(), os.sep + "tmp"):
            self.assertNotIn(probe, out)

    def test_the_environment_cannot_change_the_bytes(self):
        """A probe for '202' would false-positive on the pinned report's own
        filename (leak_report_2026-08-04.json), so instead of guessing what a
        timestamp looks like, run it twice under conditions that would move
        one: a different working directory and a different timezone."""
        import tempfile
        env = dict(os.environ)
        env["TZ"] = "Pacific/Kiritimati"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        here = subprocess.run([sys.executable, CLI, "a/y.py"],
                              capture_output=True, text=True)
        d = tempfile.mkdtemp(prefix="rip-cwd-")
        try:
            elsewhere = subprocess.run([sys.executable, CLI, "a/y.py"],
                                       capture_output=True, text=True,
                                       cwd=d, env=env)
        finally:
            os.rmdir(d)
        self.assertEqual(here.stdout, elsewhere.stdout)
        self.assertNotEqual(here.stdout, "")

    def test_stdout_and_the_written_file_are_the_same_bytes(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="rip-out-")
        try:
            target = os.path.join(d, "r.json")
            _, out, _ = run_cli(["a/y.py"])
            code, summary, _ = run_cli(["a/y.py", "--output", target])
            with open(target, encoding="utf-8") as fh:
                written = fh.read()
            self.assertEqual(out, written)
            self.assertEqual(code, 1)
        finally:
            # Only the directory this test created with mkdtemp.
            for name in sorted(os.listdir(d)):
                os.remove(os.path.join(d, name))
            os.rmdir(d)

    def test_stdin_and_arguments_agree(self):
        result = subprocess.run(
            [sys.executable, CLI, "--stdin"],
            input="a/y.py\nb/x.py\n", capture_output=True, text=True)
        _, direct, _ = run_cli(["a/y.py", "b/x.py"])
        self.assertEqual(result.stdout, direct)

    def test_stdin_and_positional_paths_together_are_refused(self):
        result = subprocess.run(
            [sys.executable, CLI, "--stdin", "a/y.py"],
            input="b/x.py\n", capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)


class TestTransitiveClosureTerminates(unittest.TestCase):
    def test_a_report_never_appears_in_its_own_chain(self):
        got = predict("link-integrity/test_x.py", "some-tool/captured_output.txt",
                      "docs/notes.md")
        for entry in got["impacted"]:
            for chain in entry["chains"]:
                with self.subTest(report=entry["report_id"], chain=chain):
                    self.assertEqual(len(chain), len(set(chain)))

    def test_every_chain_starts_at_a_changed_path_and_ends_at_its_report(self):
        got = predict("link-integrity/test_x.py")
        for entry in got["impacted"]:
            for chain in entry["chains"]:
                self.assertIn(chain[0], got["changed_paths"])
                self.assertEqual(chain[-1], entry["report_id"])

    def test_the_widest_input_still_terminates(self):
        """Every rule fired at once. If the fixed point did not terminate
        this would hang rather than fail, so it is worth having."""
        got = predict("t/a.py", "t/test_a.py", "t/captured_output.txt",
                      "t/README.md", "t/r.json", "brand-new/x.txt")
        self.assertEqual(got["summary"]["impacted_reports"], 8)


class TestTheCommittedMapIsCurrent(unittest.TestCase):
    def test_known_tool_directories_matches_the_repository(self):
        root = os.path.dirname(BASE)
        actual = sorted(
            name for name in sorted(os.listdir(root))
            if not name.startswith(".") and name != "__pycache__"
            and os.path.isdir(os.path.join(root, name)))
        doc = pi.load_map(MAP)
        self.assertEqual(doc["known_tool_directories"], actual)

    def test_every_artifact_named_in_the_map_exists(self):
        root = os.path.dirname(BASE)
        doc = pi.load_map(MAP)
        for report in doc["reports"]:
            with self.subTest(report=report["id"]):
                self.assertTrue(
                    os.path.isfile(os.path.join(root, report["artifact"])),
                    "%s does not exist" % report["artifact"])

    def test_every_producer_named_in_the_map_exists(self):
        root = os.path.dirname(BASE)
        doc = pi.load_map(MAP)
        for report in doc["reports"]:
            producer = report.get("producer")
            if not producer:
                continue
            with self.subTest(report=report["id"]):
                self.assertTrue(os.path.isfile(os.path.join(root, producer)))

    def test_the_two_pinned_reports_are_the_two_report_freshness_pins(self):
        """If report-freshness ever pins a third report, this map is stale
        and would start claiming that report goes stale when it does not."""
        root = os.path.dirname(BASE)
        with open(os.path.join(root, "report-freshness", "manifest.json"),
                  encoding="utf-8") as fh:
            manifest = json.load(fh)
        pinned = sorted(e["id"] for e in manifest["entries"]
                        if e["kind"] == "pinned")
        doc = pi.load_map(MAP)
        self.assertEqual(
            sorted(r["id"] for r in doc["reports"] if r["pinned"]), pinned)

    def test_every_freshness_manifest_entry_appears_in_the_map(self):
        root = os.path.dirname(BASE)
        with open(os.path.join(root, "report-freshness", "manifest.json"),
                  encoding="utf-8") as fh:
            manifest = json.load(fh)
        doc = pi.load_map(MAP)
        mapped = set(r["id"] for r in doc["reports"])
        for entry in manifest["entries"]:
            with self.subTest(entry=entry["id"]):
                self.assertIn(entry["id"], mapped)

    def test_the_regenerate_commands_match_the_freshness_manifest(self):
        """A regeneration command that has drifted from the manifest would
        send a reader to a command that produces different bytes."""
        root = os.path.dirname(BASE)
        with open(os.path.join(root, "report-freshness", "manifest.json"),
                  encoding="utf-8") as fh:
            manifest = {e["id"]: e for e in json.load(fh)["entries"]}
        doc = pi.load_map(MAP)
        for report in doc["reports"]:
            entry = manifest.get(report["id"])
            if entry is None or entry["kind"] != "regenerable":
                continue
            with self.subTest(report=report["id"]):
                self.assertEqual(report["regenerate"]["argv"],
                                 entry["generation"]["argv"])
                self.assertEqual(report["regenerate"]["cwd"],
                                 entry["generation"]["cwd"])
                self.assertEqual(report["regenerate"]["expected_exit_code"],
                                 entry["expected_exit_code"])


class TestAMalformedMapIsRefusedNotIgnored(unittest.TestCase):
    """The failure mode that matters: a predictor that answers 'nothing is
    impacted' because its map broke. The caller acts on that answer."""

    def _doc(self):
        return pi.load_map(MAP)

    def test_missing_rules_key(self):
        doc = self._doc(); del doc["rules"]
        with self.assertRaises(pi.UsageError):
            pi.validate_map(doc, "in-memory")

    def test_trigger_naming_an_unknown_rule(self):
        doc = self._doc()
        doc["reports"][0]["triggers"] = [{"rule": "NO_SUCH_RULE",
                                          "precision": "exact",
                                          "exact_for": ["add"],
                                          "note": "Measured"}]
        with self.assertRaises(pi.UsageError):
            pi.validate_map(doc, "in-memory")

    def test_trigger_without_a_precision(self):
        doc = self._doc()
        doc["reports"][0]["triggers"] = [{"rule": "ANY_PY",
                                          "exact_for": ["add"]}]
        with self.assertRaises(pi.UsageError):
            pi.validate_map(doc, "in-memory")

    def test_duplicate_report_id(self):
        doc = self._doc()
        doc["reports"].append(dict(doc["reports"][0]))
        with self.assertRaises(pi.UsageError):
            pi.validate_map(doc, "in-memory")

    def test_propagation_to_an_unknown_report(self):
        doc = self._doc()
        doc["propagations"].append({"from": doc["reports"][0]["id"],
                                    "to": "nope:nope.json", "why": "x"})
        with self.assertRaises(pi.UsageError):
            pi.validate_map(doc, "in-memory")

    def test_non_boolean_pinned(self):
        doc = self._doc()
        doc["reports"][0]["pinned"] = "yes"
        with self.assertRaises(pi.UsageError):
            pi.validate_map(doc, "in-memory")

    def test_unknown_match_kind_is_refused_when_it_is_reached(self):
        doc = self._doc()
        doc["rules"]["ANY_PY"]["match"] = "vibes"
        with self.assertRaises(pi.UsageError):
            pi.predict(doc, ["a/b.py"])

    def test_a_missing_map_file_exits_2(self):
        code, out, err = run_cli(["a/b.py", "--map", "no_such_map.json"])
        self.assertEqual(code, 2)
        self.assertIn("no such map", err)

    def test_a_map_that_is_not_json_exits_2(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="rip-map-")
        try:
            bad = os.path.join(d, "m.json")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            code, out, err = run_cli(["a/b.py", "--map", bad])
            self.assertEqual(code, 2)
            self.assertEqual(out, "")
        finally:
            for name in sorted(os.listdir(d)):
                os.remove(os.path.join(d, name))
            os.rmdir(d)


class TestNoExecutableBitsAndNoShebang(unittest.TestCase):
    def test_every_file_here_is_non_executable_and_has_no_shebang(self):
        for name in sorted(os.listdir(BASE)):
            path = os.path.join(BASE, name)
            if not os.path.isfile(path):
                continue
            with self.subTest(name=name):
                self.assertFalse(os.stat(path).st_mode & 0o111,
                                 "%s carries an executable bit" % name)
                with open(path, "rb") as fh:
                    self.assertNotEqual(fh.read(2), b"#!",
                                        "%s starts with a shebang" % name)

    def test_the_tool_imports_only_the_standard_library(self):
        import ast
        with open(CLI, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(sorted(imported), ["argparse", "json", "os", "sys"])


if __name__ == "__main__":
    unittest.main()
