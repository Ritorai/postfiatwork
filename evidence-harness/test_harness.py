#!/usr/bin/env python3
"""Unit tests for the Evidence Verification Harness.

Standard library only. Run with:

    python3 -m unittest test_harness -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import harness

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS_PY = os.path.join(HERE, "harness.py")
REQUIREMENTS = os.path.join(HERE, "requirements.json")
BUNDLE_GOOD = os.path.join(HERE, "bundle_good")
BUNDLE_BAD = os.path.join(HERE, "bundle_bad")

SHA256_A = "078fd1389f695e00c7a0fb03e8fac36c5a0a4953b93b9cfa393941e1f8ef1d5d"
SHA1_ONLY = "99354073feb34aeb69409ae8e63f07e94010fbb9"


class HarnessTestCase(unittest.TestCase):
    """Base class providing throwaway bundle/requirements builders."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="evh-test-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def make_bundle(self, files):
        """files: dict of relative path -> str (text) or bytes (raw)."""
        bundle = os.path.join(self.root, "bundle")
        os.makedirs(bundle, exist_ok=True)
        for rel, content in files.items():
            path = os.path.join(bundle, rel.replace("/", os.sep))
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            mode = "wb" if isinstance(content, bytes) else "w"
            with open(path, mode) as handle:
                handle.write(content)
        return bundle

    def make_requirements(self, obj, raw=None, name="requirements.json"):
        path = os.path.join(self.root, name)
        with open(path, "w") as handle:
            handle.write(raw if raw is not None else json.dumps(obj))
        return path

    def run_report(self, requirements, files, strict=False):
        req_path = self.make_requirements(requirements)
        bundle = self.make_bundle(files)
        return harness.build_report(req_path, bundle, strict=strict)

    def check_named(self, report, name):
        for entry in report["checks"]:
            if entry["check"] == name:
                return entry
        raise AssertionError("no check named %r in report" % name)


# --------------------------------------------------------------------------
# required_files
# --------------------------------------------------------------------------

class TestRequiredFiles(HarnessTestCase):
    def test_required_files_pass_exact_name(self):
        report, code = self.run_report({"required_files": ["README.md"]}, {"README.md": "hi\n"})
        self.assertEqual(self.check_named(report, "required_files")["status"], "pass")
        self.assertEqual(code, harness.EXIT_OK)

    def test_required_files_pass_star_glob(self):
        report, _ = self.run_report({"required_files": ["*.py"]}, {"tool.py": "x = 1\n"})
        entry = self.check_named(report, "required_files")
        self.assertEqual(entry["status"], "pass")
        self.assertIn("'*.py' matched: tool.py", entry["evidence"])

    def test_required_files_star_glob_matches_nested_by_basename(self):
        report, code = self.run_report({"required_files": ["*.py"]}, {"src/deep/tool.py": "x = 1\n"})
        self.assertEqual(self.check_named(report, "required_files")["status"], "pass")
        self.assertEqual(code, harness.EXIT_OK)

    def test_required_files_anchored_pattern_matches_relative_path(self):
        report, _ = self.run_report(
            {"required_files": ["docs/*.md"]}, {"docs/notes.md": "hi\n", "other.md": "hi\n"}
        )
        entry = self.check_named(report, "required_files")
        self.assertEqual(entry["status"], "pass")
        self.assertIn("'docs/*.md' matched: docs/notes.md", entry["evidence"])

    def test_required_files_fail_names_the_missing_pattern(self):
        report, code = self.run_report(
            {"required_files": ["*.py", "README.md"]}, {"tool.py": "x = 1\n"}
        )
        entry = self.check_named(report, "required_files")
        self.assertEqual(entry["status"], "fail")
        self.assertEqual(code, harness.EXIT_GAP)
        self.assertEqual(len(entry["gaps"]), 1)
        self.assertIn("'README.md'", entry["gaps"][0])
        self.assertIn("no file in the bundle matches", entry["gaps"][0])

    def test_required_files_skipped_when_not_declared(self):
        report, code = self.run_report({}, {"anything.txt": "hi\n"})
        self.assertEqual(self.check_named(report, "required_files")["status"], "skipped")
        self.assertEqual(code, harness.EXIT_OK)

    def test_required_files_ignores_pycache(self):
        report, _ = self.run_report(
            {"required_files": ["*.pyc"]}, {"__pycache__/tool.cpython-310.pyc": "junk\n"}
        )
        entry = self.check_named(report, "required_files")
        self.assertEqual(entry["status"], "fail")
        self.assertEqual(report["summary"]["files_in_bundle"], 0)


# --------------------------------------------------------------------------
# required_commands
# --------------------------------------------------------------------------

class TestRequiredCommands(HarnessTestCase):
    def test_required_commands_pass(self):
        report, code = self.run_report(
            {"required_commands": ["python3 -m unittest"]},
            {"log.txt": "$ python3 -m unittest -v\nOK\n"},
        )
        self.assertEqual(self.check_named(report, "required_commands")["status"], "pass")
        self.assertEqual(code, harness.EXIT_OK)

    def test_required_commands_normalises_whitespace(self):
        report, _ = self.run_report(
            {"required_commands": ["python3 -m unittest"]},
            {"log.md": "        python3   -m\n        unittest discover\n"},
        )
        self.assertEqual(self.check_named(report, "required_commands")["status"], "pass")

    def test_required_commands_fail_names_the_command(self):
        report, code = self.run_report(
            {"required_commands": ["python3 -m unittest", "sha256sum"]},
            {"log.txt": "$ sha256sum tool.py\n"},
        )
        entry = self.check_named(report, "required_commands")
        self.assertEqual(entry["status"], "fail")
        self.assertEqual(code, harness.EXIT_GAP)
        self.assertEqual(len(entry["gaps"]), 1)
        self.assertIn("'python3 -m unittest'", entry["gaps"][0])

    def test_required_commands_not_found_in_binary_file(self):
        report, _ = self.run_report(
            {"required_commands": ["python3 -m unittest"]},
            {"blob.bin": b"python3 -m unittest\x00\x01"},
        )
        self.assertEqual(self.check_named(report, "required_commands")["status"], "fail")
        self.assertEqual(report["summary"]["files_scanned_as_text"], 0)
        self.assertEqual(report["summary"]["files_skipped"], 1)

    def test_required_commands_skipped_when_not_declared(self):
        report, _ = self.run_report({"required_files": ["*.txt"]}, {"a.txt": "hi\n"})
        self.assertEqual(self.check_named(report, "required_commands")["status"], "skipped")


# --------------------------------------------------------------------------
# require_exit_codes
# --------------------------------------------------------------------------

class TestRequireExitCodes(HarnessTestCase):
    def test_exit_codes_equals_form(self):
        report, code = self.run_report({"require_exit_codes": True}, {"log.txt": "done\nexit=0\n"})
        self.assertEqual(self.check_named(report, "require_exit_codes")["status"], "pass")
        self.assertEqual(code, harness.EXIT_OK)

    def test_exit_codes_prose_form(self):
        report, _ = self.run_report(
            {"require_exit_codes": True}, {"log.txt": "the command returned exit code: 1\n"}
        )
        self.assertEqual(self.check_named(report, "require_exit_codes")["status"], "pass")

    def test_exit_codes_status_form(self):
        report, _ = self.run_report(
            {"require_exit_codes": True}, {"log.txt": "process left with exit status 2\n"}
        )
        self.assertEqual(self.check_named(report, "require_exit_codes")["status"], "pass")

    def test_exit_codes_fail_with_specific_guidance(self):
        report, code = self.run_report(
            {"require_exit_codes": True}, {"log.txt": "everything worked fine\n"}
        )
        entry = self.check_named(report, "require_exit_codes")
        self.assertEqual(entry["status"], "fail")
        self.assertEqual(code, harness.EXIT_GAP)
        self.assertIn("exit=$?", entry["gaps"][0])

    def test_exit_codes_skipped_when_false(self):
        report, code = self.run_report({"require_exit_codes": False}, {"log.txt": "nothing\n"})
        self.assertEqual(self.check_named(report, "require_exit_codes")["status"], "skipped")
        self.assertEqual(code, harness.EXIT_OK)


# --------------------------------------------------------------------------
# require_hashes
# --------------------------------------------------------------------------

class TestRequireHashes(HarnessTestCase):
    def test_hashes_pass(self):
        report, code = self.run_report(
            {"require_hashes": True}, {"log.txt": "%s  tool.py\n" % SHA256_A}
        )
        entry = self.check_named(report, "require_hashes")
        self.assertEqual(entry["status"], "pass")
        self.assertEqual(code, harness.EXIT_OK)
        self.assertEqual(entry["evidence"], ["log.txt: %s" % SHA256_A])

    def test_hashes_uppercase_normalised_to_lowercase(self):
        report, _ = self.run_report({"require_hashes": True}, {"log.txt": SHA256_A.upper() + "\n"})
        entry = self.check_named(report, "require_hashes")
        self.assertEqual(entry["status"], "pass")
        self.assertEqual(entry["evidence"], ["log.txt: %s" % SHA256_A])

    def test_hashes_fail_when_only_sha1_present(self):
        report, code = self.run_report(
            {"require_hashes": True}, {"log.txt": "%s  tool.py\n" % SHA1_ONLY}
        )
        entry = self.check_named(report, "require_hashes")
        self.assertEqual(entry["status"], "fail")
        self.assertEqual(code, harness.EXIT_GAP)
        self.assertIn("sha256sum", entry["gaps"][0])

    def test_hashes_reject_longer_hex_run(self):
        report, _ = self.run_report({"require_hashes": True}, {"log.txt": SHA256_A + "ab\n"})
        self.assertEqual(self.check_named(report, "require_hashes")["status"], "fail")

    def test_hashes_skipped_when_not_declared(self):
        report, _ = self.run_report({"required_files": ["*.txt"]}, {"a.txt": "hi\n"})
        self.assertEqual(self.check_named(report, "require_hashes")["status"], "skipped")


# --------------------------------------------------------------------------
# min_test_count
# --------------------------------------------------------------------------

class TestMinTestCount(HarnessTestCase):
    def test_min_test_count_pass(self):
        report, code = self.run_report(
            {"min_test_count": 10}, {"log.txt": "Ran 12 tests in 0.001s\nOK\n"}
        )
        self.assertEqual(self.check_named(report, "min_test_count")["status"], "pass")
        self.assertEqual(code, harness.EXIT_OK)

    def test_min_test_count_exact_boundary_passes(self):
        report, code = self.run_report({"min_test_count": 10}, {"log.txt": "Ran 10 tests in 0s\n"})
        self.assertEqual(self.check_named(report, "min_test_count")["status"], "pass")
        self.assertEqual(code, harness.EXIT_OK)

    def test_min_test_count_fail_reports_shortfall(self):
        report, code = self.run_report({"min_test_count": 10}, {"log.txt": "Ran 4 tests in 0s\n"})
        entry = self.check_named(report, "min_test_count")
        self.assertEqual(entry["status"], "fail")
        self.assertEqual(code, harness.EXIT_GAP)
        self.assertIn("is 4", entry["gaps"][0])
        self.assertIn("at least 10", entry["gaps"][0])
        self.assertIn("add 6 more", entry["gaps"][0])

    def test_min_test_count_fail_when_no_run_recorded(self):
        report, _ = self.run_report({"min_test_count": 10}, {"log.txt": "all good\n"})
        entry = self.check_named(report, "min_test_count")
        self.assertEqual(entry["status"], "fail")
        self.assertIn("no 'Ran N tests' summary line", entry["gaps"][0])

    def test_min_test_count_uses_largest_run_not_the_last(self):
        report, _ = self.run_report(
            {"min_test_count": 10}, {"a.txt": "Ran 12 tests in 0s\n", "b.txt": "Ran 3 tests in 0s\n"}
        )
        entry = self.check_named(report, "min_test_count")
        self.assertEqual(entry["status"], "pass")
        self.assertIn("was 12 test(s)", entry["detail"])

    def test_min_test_count_singular_form_parsed(self):
        report, _ = self.run_report({"min_test_count": 1}, {"log.txt": "Ran 1 test in 0.000s\n"})
        self.assertEqual(self.check_named(report, "min_test_count")["status"], "pass")

    def test_min_test_count_skipped_when_zero(self):
        report, code = self.run_report({"min_test_count": 0}, {"log.txt": "nothing\n"})
        self.assertEqual(self.check_named(report, "min_test_count")["status"], "skipped")
        self.assertEqual(code, harness.EXIT_OK)


# --------------------------------------------------------------------------
# empty bundles, strict mode, unknown keys
# --------------------------------------------------------------------------

class TestBundleShape(HarnessTestCase):
    def test_empty_bundle_fails_every_declared_check(self):
        report, code = self.run_report(
            {
                "required_files": ["*.py"],
                "required_commands": ["python3 -m unittest"],
                "require_exit_codes": True,
                "require_hashes": True,
                "min_test_count": 10,
            },
            {},
        )
        self.assertEqual(code, harness.EXIT_GAP)
        self.assertEqual(report["summary"]["checks_failed"], 5)
        self.assertEqual(report["summary"]["files_in_bundle"], 0)
        self.assertEqual(len(report["gaps"]), 5)

    def test_empty_requirements_and_empty_bundle_passes_without_strict(self):
        report, code = self.run_report({}, {})
        self.assertEqual(code, harness.EXIT_OK)
        self.assertEqual(report["status"], "pass")

    def test_strict_flags_empty_requirements_as_a_gap(self):
        report, code = self.run_report({}, {"a.txt": "hi\n"}, strict=True)
        self.assertEqual(code, harness.EXIT_GAP)
        entry = self.check_named(report, "strict_coverage")
        self.assertEqual(entry["status"], "fail")
        self.assertTrue(any("declares no verifiable requirement" in g for g in entry["gaps"]))

    def test_strict_flags_empty_bundle_as_a_gap(self):
        report, code = self.run_report({"require_hashes": True}, {}, strict=True)
        self.assertEqual(code, harness.EXIT_GAP)
        entry = self.check_named(report, "strict_coverage")
        self.assertTrue(any("contains no files at all" in g for g in entry["gaps"]))

    def test_unknown_keys_warn_without_strict(self):
        report, code = self.run_report(
            {"required_files": ["*.txt"], "require_screenshots": True}, {"a.txt": "hi\n"}
        )
        entry = self.check_named(report, "unknown_requirement_keys")
        self.assertEqual(entry["status"], "warn")
        self.assertEqual(code, harness.EXIT_OK)
        self.assertEqual(entry["gaps"], [])

    def test_unknown_keys_fail_with_strict(self):
        report, code = self.run_report(
            {"required_files": ["*.txt"], "require_screenshots": True}, {"a.txt": "hi\n"}, strict=True
        )
        entry = self.check_named(report, "unknown_requirement_keys")
        self.assertEqual(entry["status"], "fail")
        self.assertEqual(code, harness.EXIT_GAP)
        self.assertIn("'require_screenshots'", entry["gaps"][0])


# --------------------------------------------------------------------------
# invalid input handling (exit code 2 territory)
# --------------------------------------------------------------------------

class TestInvalidInput(HarnessTestCase):
    def test_missing_bundle_directory(self):
        req = self.make_requirements({"require_hashes": True})
        with self.assertRaises(harness.InputError) as ctx:
            harness.build_report(req, os.path.join(self.root, "nope"))
        self.assertIn("does not exist", str(ctx.exception))

    def test_bundle_path_is_a_file(self):
        req = self.make_requirements({"require_hashes": True})
        target = os.path.join(self.root, "afile")
        with open(target, "w") as handle:
            handle.write("x")
        with self.assertRaises(harness.InputError) as ctx:
            harness.build_report(req, target)
        self.assertIn("is not a directory", str(ctx.exception))

    def test_missing_requirements_file(self):
        bundle = self.make_bundle({"a.txt": "hi\n"})
        with self.assertRaises(harness.InputError) as ctx:
            harness.build_report(os.path.join(self.root, "absent.json"), bundle)
        self.assertIn("cannot read requirements file", str(ctx.exception))

    def test_malformed_requirements_json(self):
        bundle = self.make_bundle({"a.txt": "hi\n"})
        req = self.make_requirements(None, raw='{"required_files": ["*.py",}')
        with self.assertRaises(harness.InputError) as ctx:
            harness.build_report(req, bundle)
        self.assertIn("not valid JSON", str(ctx.exception))

    def test_requirements_must_be_an_object(self):
        bundle = self.make_bundle({"a.txt": "hi\n"})
        req = self.make_requirements(None, raw="[1, 2, 3]")
        with self.assertRaises(harness.InputError) as ctx:
            harness.build_report(req, bundle)
        self.assertIn("must contain a JSON object", str(ctx.exception))

    def test_requirements_wrong_value_type(self):
        bundle = self.make_bundle({"a.txt": "hi\n"})
        req = self.make_requirements({"required_files": "*.py"})
        with self.assertRaises(harness.InputError) as ctx:
            harness.build_report(req, bundle)
        self.assertIn("must be a list of strings", str(ctx.exception))

    def test_requirements_negative_min_test_count(self):
        bundle = self.make_bundle({"a.txt": "hi\n"})
        req = self.make_requirements({"min_test_count": -3})
        with self.assertRaises(harness.InputError) as ctx:
            harness.build_report(req, bundle)
        self.assertIn("must be >= 0", str(ctx.exception))

    def test_requirements_boolean_is_not_an_integer(self):
        bundle = self.make_bundle({"a.txt": "hi\n"})
        req = self.make_requirements({"min_test_count": True})
        with self.assertRaises(harness.InputError):
            harness.build_report(req, bundle)


# --------------------------------------------------------------------------
# canonical output and determinism
# --------------------------------------------------------------------------

class TestCanonicalOutput(HarnessTestCase):
    def test_render_is_canonical(self):
        payload = harness.render({"b": 1, "a": {"d": 2, "c": [3, 1]}})
        self.assertEqual(payload, b'{"a":{"c":[3,1],"d":2},"b":1}\n')

    def test_render_is_ascii_only(self):
        payload = harness.render({"note": "café"})
        self.assertEqual(payload, b'{"note":"caf\\u00e9"}\n')
        payload.decode("ascii")

    def test_report_bytes_are_stable_across_runs(self):
        req_path = self.make_requirements({"required_files": ["*.txt"], "require_hashes": True})
        bundle = self.make_bundle({"a.txt": "hi\n", "b.txt": SHA256_A + "\n"})
        first = harness.render(harness.build_report(req_path, bundle)[0])
        second = harness.render(harness.build_report(req_path, bundle)[0])
        self.assertEqual(first, second)

    def test_gaps_are_sorted_and_deduplicated(self):
        report, _ = self.run_report(
            {"required_files": ["a.py", "b.py"], "require_hashes": True}, {"note.txt": "hi\n"}
        )
        self.assertEqual(report["gaps"], sorted(report["gaps"]))
        self.assertEqual(len(report["gaps"]), len(set(report["gaps"])))

    def test_checks_are_sorted_by_name(self):
        report, _ = self.run_report({"require_hashes": True}, {"a.txt": "hi\n"})
        names = [c["check"] for c in report["checks"]]
        self.assertEqual(names, sorted(names))

    def test_summary_counts_match_the_check_list(self):
        report, _ = self.run_report(
            {"required_files": ["*.py"], "require_hashes": True}, {"a.txt": "hi\n"}
        )
        summary = report["summary"]
        self.assertEqual(summary["checks_total"], len(report["checks"]))
        self.assertEqual(
            summary["checks_total"],
            summary["checks_passed"]
            + summary["checks_failed"]
            + summary["checks_skipped"]
            + summary["checks_warned"],
        )


# --------------------------------------------------------------------------
# CLI behaviour, exercised through a real subprocess
# --------------------------------------------------------------------------

class TestCommandLine(HarnessTestCase):
    def run_cli(self, args):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run(
            [sys.executable, HARNESS_PY] + list(args),
            cwd=HERE,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_cli_exit_zero_on_good_bundle(self):
        proc = self.run_cli(["requirements.json", "bundle_good"])
        self.assertEqual(proc.returncode, harness.EXIT_OK, proc.stderr.decode())
        payload = json.loads(proc.stdout.decode("ascii"))
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["gaps"], [])

    def test_cli_exit_one_on_bad_bundle(self):
        proc = self.run_cli(["requirements.json", "bundle_bad"])
        self.assertEqual(proc.returncode, harness.EXIT_GAP)
        payload = json.loads(proc.stdout.decode("ascii"))
        self.assertEqual(payload["status"], "gap")
        self.assertEqual(payload["gap_count"], 4)

    def test_cli_exit_two_on_missing_requirements(self):
        proc = self.run_cli(["no_such_requirements.json", "bundle_good"])
        self.assertEqual(proc.returncode, harness.EXIT_INPUT)
        self.assertEqual(proc.stdout, b"")
        self.assertIn(b"input error", proc.stderr)

    def test_cli_exit_two_on_missing_bundle_dir(self):
        proc = self.run_cli(["requirements.json", "no_such_bundle"])
        self.assertEqual(proc.returncode, harness.EXIT_INPUT)
        self.assertIn(b"does not exist", proc.stderr)

    def test_cli_exit_two_on_malformed_requirements(self):
        req = self.make_requirements(None, raw="{not json")
        proc = self.run_cli([req, "bundle_good"])
        self.assertEqual(proc.returncode, harness.EXIT_INPUT)
        self.assertIn(b"not valid JSON", proc.stderr)

    def test_cli_out_file_matches_stdout_byte_for_byte(self):
        out = os.path.join(self.root, "report.json")
        proc = self.run_cli(["requirements.json", "bundle_good", "-o", out])
        self.assertEqual(proc.returncode, harness.EXIT_OK)
        with open(out, "rb") as handle:
            self.assertEqual(handle.read(), proc.stdout)

    def test_cli_repeated_runs_are_byte_identical(self):
        first = os.path.join(self.root, "one.json")
        second = os.path.join(self.root, "two.json")
        a = self.run_cli(["requirements.json", "bundle_bad", "-o", first])
        b = self.run_cli(["requirements.json", "bundle_bad", "-o", second])
        self.assertEqual(a.returncode, harness.EXIT_GAP)
        self.assertEqual(b.returncode, harness.EXIT_GAP)
        with open(first, "rb") as fh1, open(second, "rb") as fh2:
            self.assertEqual(fh1.read(), fh2.read())

    def test_cli_creates_missing_output_directory(self):
        out = os.path.join(self.root, "nested", "deep", "report.json")
        proc = self.run_cli(["requirements.json", "bundle_good", "-o", out])
        self.assertEqual(proc.returncode, harness.EXIT_OK)
        self.assertTrue(os.path.isfile(out))

    def test_cli_report_ends_with_single_trailing_newline(self):
        proc = self.run_cli(["requirements.json", "bundle_good"])
        self.assertTrue(proc.stdout.endswith(b"}\n"))
        self.assertFalse(proc.stdout.endswith(b"\n\n"))

    def test_cli_strict_on_good_bundle_still_exits_zero(self):
        proc = self.run_cli(["requirements.json", "bundle_good", "--strict"])
        self.assertEqual(proc.returncode, harness.EXIT_OK, proc.stderr.decode())

    def test_cli_missing_arguments_is_a_usage_error(self):
        proc = self.run_cli(["requirements.json"])
        self.assertEqual(proc.returncode, 2)
        self.assertIn(b"usage:", proc.stderr)


# --------------------------------------------------------------------------
# the shipped fixtures themselves
# --------------------------------------------------------------------------

class TestShippedFixtures(unittest.TestCase):
    def test_good_bundle_passes_every_declared_check(self):
        report, code = harness.build_report(REQUIREMENTS, BUNDLE_GOOD)
        self.assertEqual(code, harness.EXIT_OK)
        failed = [c["check"] for c in report["checks"] if c["status"] == "fail"]
        self.assertEqual(failed, [])

    def test_bad_bundle_fails_the_expected_four_checks(self):
        report, code = harness.build_report(REQUIREMENTS, BUNDLE_BAD)
        self.assertEqual(code, harness.EXIT_GAP)
        failed = sorted(c["check"] for c in report["checks"] if c["status"] == "fail")
        self.assertEqual(
            failed, ["min_test_count", "require_hashes", "required_commands", "required_files"]
        )

    def test_bad_bundle_still_passes_the_exit_code_check(self):
        report, _ = harness.build_report(REQUIREMENTS, BUNDLE_BAD)
        entry = [c for c in report["checks"] if c["check"] == "require_exit_codes"][0]
        self.assertEqual(entry["status"], "pass")


if __name__ == "__main__":
    unittest.main()
