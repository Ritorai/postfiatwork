"""Test suite for bundleverify.py.

Every test builds its own bundle inside a tempfile.TemporaryDirectory() (or,
where a specific *different* absolute path/name is required for a relocation
proof, an explicitly created directory that the test itself removes). No
test ever removes a directory it did not create.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

import bundleverify as bv

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TOOL_PATH = os.path.join(THIS_DIR, "bundleverify.py")


def sha256_of(data):
    return hashlib.sha256(data).hexdigest()


def write_file(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    with open(path, "wb") as f:
        f.write(data)


def write_manifest(bundle_dir, files, schema_version=1, manifest_name="manifest.json"):
    manifest = {"schema_version": schema_version, "files": files}
    with open(os.path.join(bundle_dir, manifest_name), "w", encoding="utf-8") as f:
        json.dump(manifest, f)


def entry_for(bundle_dir, rel_path, data=None, size_override=None, sha_override=None):
    if data is None:
        with open(os.path.join(bundle_dir, rel_path), "rb") as f:
            data = f.read()
    return {
        "path": rel_path,
        "sha256": sha_override if sha_override is not None else sha256_of(data),
        "size_bytes": size_override if size_override is not None else len(data),
    }


def run_cli(args, cwd=None):
    return subprocess.run(
        [sys.executable, TOOL_PATH] + args,
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def run_verify(bundle_dir, manifest_name="manifest.json"):
    """In-process helper: returns (report_dict, exit_code)."""
    findings, num_listed = bv.verify_bundle(bundle_dir, manifest_name)
    report, exit_code = bv.build_report(findings, num_listed, manifest_name)
    return report, exit_code


def codes_of(report):
    return [f["code"] for f in report["findings"]]


def find_one(report, code, path=None):
    matches = [f for f in report["findings"] if f["code"] == code and (path is None or f["path"] == path)]
    assert len(matches) >= 1, "expected a finding {0} at {1!r}, findings were: {2}".format(code, path, report["findings"])
    return matches[0]


# ==========================================================================
# Clean bundle
# ==========================================================================

class CleanBundleTests(unittest.TestCase):
    def test_clean_single_file_exit_0(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"hello")
            write_manifest(d, [entry_for(d, "a.txt")])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 0)
            self.assertEqual(report["findings"], [])
            self.assertEqual(report["status"], "clean")

    def test_clean_multiple_files(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"hello")
            write_file(os.path.join(d, "b.txt"), b"world")
            write_file(os.path.join(d, "c.bin"), b"\x00\x01\x02\x03")
            files = [entry_for(d, p) for p in ("a.txt", "b.txt", "c.bin")]
            write_manifest(d, files)
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 0)
            self.assertEqual(report["num_files_listed"], 3)

    def test_clean_nested_subdirectories(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "x", "y", "z.txt"), b"deep")
            write_file(os.path.join(d, "top.txt"), b"top")
            files = [entry_for(d, "x/y/z.txt"), entry_for(d, "top.txt")]
            write_manifest(d, files)
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 0)

    def test_clean_empty_file(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "empty.txt"), b"")
            write_manifest(d, [entry_for(d, "empty.txt")])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 0)

    def test_clean_unicode_filename(self):
        with tempfile.TemporaryDirectory() as d:
            name = "\u00e9\u00e0\u4e2d\u6587\U0001f600.txt"
            write_file(os.path.join(d, name), b"unicode contents")
            write_manifest(d, [entry_for(d, name)])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 0)

    def test_clean_binary_non_utf8_contents(self):
        with tempfile.TemporaryDirectory() as d:
            data = bytes(range(256)) * 4 + b"\xff\xfe\x00\x01invalid utf8 \x80\x81"
            write_file(os.path.join(d, "bin.dat"), data)
            write_manifest(d, [entry_for(d, "bin.dat", data=data)])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 0)

    def test_clean_large_ish_file_chunked_hash(self):
        with tempfile.TemporaryDirectory() as d:
            data = os.urandom(1024) * 5000  # ~5MB, exercises multi-chunk read
            write_file(os.path.join(d, "big.bin"), data)
            write_manifest(d, [entry_for(d, "big.bin", data=data)])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 0)

    def test_self_listed_manifest_is_verified_not_flagged_unlisted(self):
        # A manifest may optionally list itself. Because writing the entry
        # changes the manifest's own bytes, a self-referential digest can
        # never be satisfied (a documented, inherent limitation) -- but the
        # important behaviour under test is that this produces an ordinary
        # DIGEST_MISMATCH finding, never UNLISTED_FILE and never a crash.
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"hi")
            files = [entry_for(d, "a.txt"),
                     {"path": "manifest.json", "sha256": "0" * 64, "size_bytes": 0}]
            write_manifest(d, files)
            report, exit_code = run_verify(d)
            self.assertNotIn("UNLISTED_FILE", codes_of(report))
            self.assertIn("DIGEST_MISMATCH", codes_of(report))

    def test_report_schema_version_present(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"hi")
            write_manifest(d, [entry_for(d, "a.txt")])
            report, exit_code = run_verify(d)
            self.assertEqual(report["report_schema_version"], 1)


# ==========================================================================
# Individual finding codes
# ==========================================================================

class MissingFileTests(unittest.TestCase):
    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "ghost.txt", "sha256": "a" * 64, "size_bytes": 5}])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 1)
            self.assertEqual(codes_of(report), ["MISSING_FILE"])
            self.assertEqual(report["findings"][0]["path"], "ghost.txt")

    def test_missing_file_in_subdirectory(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "a/b/ghost.txt", "sha256": "a" * 64, "size_bytes": 5}])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 1)
            find_one(report, "MISSING_FILE", "a/b/ghost.txt")

    def test_manifest_path_points_at_directory_is_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "adir"))
            write_manifest(d, [{"path": "adir", "sha256": "a" * 64, "size_bytes": 0}])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 1)
            f = find_one(report, "MISSING_FILE", "adir")
            self.assertIn("not a regular file", f["detail"])


class DigestMismatchTests(unittest.TestCase):
    def test_digest_mismatch_size_matches_subtle_tamper(self):
        with tempfile.TemporaryDirectory() as d:
            data = b"original contents"
            write_file(os.path.join(d, "a.txt"), data)
            # Craft manifest with correct size but wrong digest -- the subtle tamper.
            write_manifest(d, [{"path": "a.txt", "sha256": "0" * 64, "size_bytes": len(data)}])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 1)
            self.assertEqual(codes_of(report), ["DIGEST_MISMATCH"])
            f = report["findings"][0]
            self.assertEqual(f["expected_sha256"], "0" * 64)
            self.assertEqual(f["actual_sha256"], sha256_of(data))

    def test_digest_mismatch_after_content_change_same_length(self):
        with tempfile.TemporaryDirectory() as d:
            original = b"AAAAAAAAAA"
            write_file(os.path.join(d, "a.txt"), original)
            write_manifest(d, [entry_for(d, "a.txt", data=original)])
            # Tamper: overwrite with same-length different content.
            write_file(os.path.join(d, "a.txt"), b"BBBBBBBBBB")
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 1)
            self.assertEqual(codes_of(report), ["DIGEST_MISMATCH"])


class SizeMismatchTests(unittest.TestCase):
    def test_size_mismatch_digest_matches_bad_size_field(self):
        with tempfile.TemporaryDirectory() as d:
            data = b"exact content"
            write_file(os.path.join(d, "a.txt"), data)
            write_manifest(d, [{"path": "a.txt", "sha256": sha256_of(data), "size_bytes": len(data) + 100}])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 1)
            self.assertEqual(codes_of(report), ["SIZE_MISMATCH"])
            f = report["findings"][0]
            self.assertEqual(f["expected_size_bytes"], len(data) + 100)
            self.assertEqual(f["actual_size_bytes"], len(data))

    def test_both_size_and_digest_mismatch_reported_independently(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"real data here")
            write_manifest(d, [{"path": "a.txt", "sha256": "f" * 64, "size_bytes": 999999}])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 1)
            self.assertEqual(sorted(codes_of(report)), ["DIGEST_MISMATCH", "SIZE_MISMATCH"])


class UnlistedFileTests(unittest.TestCase):
    def test_unlisted_file_detected(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "listed.txt"), b"ok")
            write_file(os.path.join(d, "sneaky.txt"), b"unlisted")
            write_manifest(d, [entry_for(d, "listed.txt")])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 1)
            self.assertEqual(codes_of(report), ["UNLISTED_FILE"])
            self.assertEqual(report["findings"][0]["path"], "sneaky.txt")

    def test_unlisted_file_in_nested_subdir(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "listed.txt"), b"ok")
            write_file(os.path.join(d, "a", "b", "sneaky.txt"), b"unlisted")
            write_manifest(d, [entry_for(d, "listed.txt")])
            report, exit_code = run_verify(d)
            find_one(report, "UNLISTED_FILE", "a/b/sneaky.txt")

    def test_manifest_json_itself_not_flagged_unlisted(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"ok")
            write_manifest(d, [entry_for(d, "a.txt")])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 0)

    def test_multiple_unlisted_files_all_reported(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "listed.txt"), b"ok")
            write_file(os.path.join(d, "u1.txt"), b"1")
            write_file(os.path.join(d, "u2.txt"), b"2")
            write_manifest(d, [entry_for(d, "listed.txt")])
            report, exit_code = run_verify(d)
            self.assertEqual(sorted(f["path"] for f in report["findings"]), ["u1.txt", "u2.txt"])


class DuplicatePathTests(unittest.TestCase):
    def test_duplicate_path_detected(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"hi")
            e = entry_for(d, "a.txt")
            write_manifest(d, [e, dict(e)])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 1)
            self.assertEqual(codes_of(report), ["DUPLICATE_PATH"])
            f = report["findings"][0]
            self.assertEqual(f["manifest_indices"], [0, 1])

    def test_duplicate_path_three_times(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"hi")
            e = entry_for(d, "a.txt")
            write_manifest(d, [e, dict(e), dict(e)])
            report, exit_code = run_verify(d)
            f = find_one(report, "DUPLICATE_PATH", "a.txt")
            self.assertEqual(f["manifest_indices"], [0, 1, 2])
            self.assertIn("3 times", f["detail"])

    def test_duplicate_path_with_conflicting_data_uses_first_occurrence(self):
        with tempfile.TemporaryDirectory() as d:
            data = b"hi"
            write_file(os.path.join(d, "a.txt"), data)
            good = entry_for(d, "a.txt", data=data)
            bad = {"path": "a.txt", "sha256": "0" * 64, "size_bytes": 1}
            write_manifest(d, [good, bad])
            report, exit_code = run_verify(d)
            codes = codes_of(report)
            self.assertIn("DUPLICATE_PATH", codes)
            self.assertNotIn("DIGEST_MISMATCH", codes)
            self.assertNotIn("SIZE_MISMATCH", codes)


class MalformedEntryTests(unittest.TestCase):
    def test_malformed_entry_missing_path(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"sha256": "a" * 64, "size_bytes": 1}])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 1)
            f = find_one(report, "MALFORMED_ENTRY")
            self.assertEqual(f["manifest_index"], 0)

    def test_malformed_entry_missing_sha256(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"hi")
            write_manifest(d, [{"path": "a.txt", "size_bytes": 2}])
            report, exit_code = run_verify(d)
            find_one(report, "MALFORMED_ENTRY")

    def test_malformed_entry_missing_size_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "a.txt", "sha256": "a" * 64}])
            report, exit_code = run_verify(d)
            find_one(report, "MALFORMED_ENTRY")

    def test_malformed_entry_bad_sha256_format_too_short(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "a.txt", "sha256": "abc", "size_bytes": 1}])
            report, exit_code = run_verify(d)
            find_one(report, "MALFORMED_ENTRY")

    def test_malformed_entry_bad_sha256_uppercase(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "a.txt", "sha256": "A" * 64, "size_bytes": 1}])
            report, exit_code = run_verify(d)
            find_one(report, "MALFORMED_ENTRY")

    def test_malformed_entry_size_bytes_negative(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "a.txt", "sha256": "a" * 64, "size_bytes": -1}])
            report, exit_code = run_verify(d)
            find_one(report, "MALFORMED_ENTRY")

    def test_malformed_entry_size_bytes_is_string(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "a.txt", "sha256": "a" * 64, "size_bytes": "5"}])
            report, exit_code = run_verify(d)
            find_one(report, "MALFORMED_ENTRY")

    def test_malformed_entry_size_bytes_is_bool(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "a.txt", "sha256": "a" * 64, "size_bytes": True}])
            report, exit_code = run_verify(d)
            find_one(report, "MALFORMED_ENTRY")

    def test_malformed_entry_size_bytes_is_float(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "a.txt", "sha256": "a" * 64, "size_bytes": 5.0}])
            report, exit_code = run_verify(d)
            find_one(report, "MALFORMED_ENTRY")

    def test_malformed_entry_path_is_not_string(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": 123, "sha256": "a" * 64, "size_bytes": 1}])
            report, exit_code = run_verify(d)
            find_one(report, "MALFORMED_ENTRY")

    def test_malformed_entry_path_is_empty_string(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "", "sha256": "a" * 64, "size_bytes": 1}])
            report, exit_code = run_verify(d)
            find_one(report, "MALFORMED_ENTRY")

    def test_malformed_entry_is_a_string_not_object(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, ["not-an-object"])
            report, exit_code = run_verify(d)
            find_one(report, "MALFORMED_ENTRY")

    def test_malformed_entry_is_a_list(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [["a.txt", "a" * 64, 1]])
            report, exit_code = run_verify(d)
            find_one(report, "MALFORMED_ENTRY")

    def test_malformed_entry_is_null(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [None])
            report, exit_code = run_verify(d)
            find_one(report, "MALFORMED_ENTRY")

    def test_malformed_entry_mid_manifest_does_not_hide_others(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "before.txt"), b"before")
            write_file(os.path.join(d, "after.txt"), b"after")
            files = [
                entry_for(d, "before.txt"),
                {"path": "broken.txt"},  # malformed: missing sha256, size_bytes
                entry_for(d, "after.txt"),
            ]
            write_manifest(d, files)
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 1)
            self.assertEqual(len(report["findings"]), 1)
            f = report["findings"][0]
            self.assertEqual(f["code"], "MALFORMED_ENTRY")
            self.assertEqual(f["manifest_index"], 1)

    def test_malformed_entry_run_continues_reporting_other_bad_files(self):
        with tempfile.TemporaryDirectory() as d:
            files = [
                {"path": "broken.txt"},
                {"path": "ghost.txt", "sha256": "a" * 64, "size_bytes": 1},
            ]
            write_manifest(d, files)
            report, exit_code = run_verify(d)
            codes = sorted(codes_of(report))
            self.assertEqual(codes, ["MALFORMED_ENTRY", "MISSING_FILE"])

    def test_malformed_entry_extra_unknown_keys_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            data = b"hi"
            write_file(os.path.join(d, "a.txt"), data)
            e = entry_for(d, "a.txt", data=data)
            e["note"] = "this is fine, extra keys are ignored"
            write_manifest(d, [e])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 0)

    def test_two_malformed_entries_no_path_tiebreak_by_manifest_index(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"sha256": "z"}, {"size_bytes": -5}])
            report, exit_code = run_verify(d)
            malformed = [f for f in report["findings"] if f["code"] == "MALFORMED_ENTRY"]
            self.assertEqual(len(malformed), 2)
            # Both share code="MALFORMED_ENTRY" and path="" -- verifies the
            # canonical-JSON tiebreak produces a stable, deterministic order
            # rather than crashing or being arbitrary.
            self.assertEqual(malformed[0]["manifest_index"], 0)
            self.assertEqual(malformed[1]["manifest_index"], 1)


class EmptyBundleTests(unittest.TestCase):
    def test_empty_bundle_finding(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 1)
            self.assertEqual(codes_of(report), ["EMPTY_BUNDLE"])
            self.assertEqual(report["num_files_listed"], 0)

    def test_empty_bundle_with_unlisted_files_on_disk(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "surprise.txt"), b"data")
            write_manifest(d, [])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 1)
            self.assertEqual(sorted(codes_of(report)), ["EMPTY_BUNDLE", "UNLISTED_FILE"])


# ==========================================================================
# PATH_ESCAPES_BUNDLE
# ==========================================================================

class PathEscapesBundleTests(unittest.TestCase):
    def test_dotdot_escape(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "../evil.txt", "sha256": "a" * 64, "size_bytes": 1}])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 1)
            self.assertEqual(codes_of(report), ["PATH_ESCAPES_BUNDLE"])

    def test_dotdot_nested_escape(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "a/../../evil.txt", "sha256": "a" * 64, "size_bytes": 1}])
            report, exit_code = run_verify(d)
            find_one(report, "PATH_ESCAPES_BUNDLE")

    def test_absolute_path_escape(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "/etc/passwd", "sha256": "a" * 64, "size_bytes": 1}])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 1)
            self.assertEqual(codes_of(report), ["PATH_ESCAPES_BUNDLE"])

    def test_windows_style_absolute_path_escape(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "C:\\Windows\\System32\\evil.txt", "sha256": "a" * 64, "size_bytes": 1}])
            report, exit_code = run_verify(d)
            find_one(report, "PATH_ESCAPES_BUNDLE")

    def test_unc_style_path_escape(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "\\\\server\\share\\evil.txt", "sha256": "a" * 64, "size_bytes": 1}])
            report, exit_code = run_verify(d)
            find_one(report, "PATH_ESCAPES_BUNDLE")

    def test_empty_component_escape(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "a//b.txt", "sha256": "a" * 64, "size_bytes": 1}])
            report, exit_code = run_verify(d)
            find_one(report, "PATH_ESCAPES_BUNDLE")

    def test_dot_component_escape(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "./a.txt", "sha256": "a" * 64, "size_bytes": 1}])
            report, exit_code = run_verify(d)
            find_one(report, "PATH_ESCAPES_BUNDLE")

    def test_symlink_file_pointing_outside_bundle_not_followed(self):
        with tempfile.TemporaryDirectory() as outside:
            secret = os.path.join(outside, "secret.txt")
            write_file(secret, b"top secret data")
            with tempfile.TemporaryDirectory() as d:
                link = os.path.join(d, "link.txt")
                os.symlink(secret, link)
                write_manifest(d, [{"path": "link.txt", "sha256": "a" * 64, "size_bytes": 1}])
                report, exit_code = run_verify(d)
                self.assertEqual(exit_code, 1)
                self.assertEqual(codes_of(report), ["PATH_ESCAPES_BUNDLE"])
                # Must not have been read/hashed: no DIGEST_MISMATCH/SIZE_MISMATCH.
                self.assertNotIn("DIGEST_MISMATCH", codes_of(report))
                self.assertNotIn("SIZE_MISMATCH", codes_of(report))

    def test_symlink_file_unlisted_pointing_outside_bundle(self):
        with tempfile.TemporaryDirectory() as outside:
            secret = os.path.join(outside, "secret.txt")
            write_file(secret, b"top secret data")
            with tempfile.TemporaryDirectory() as d:
                link = os.path.join(d, "link.txt")
                os.symlink(secret, link)
                write_manifest(d, [])
                report, exit_code = run_verify(d)
                codes = codes_of(report)
                self.assertIn("PATH_ESCAPES_BUNDLE", codes)
                self.assertNotIn("UNLISTED_FILE", codes)

    def test_symlink_dir_pointing_outside_bundle_not_traversed(self):
        with tempfile.TemporaryDirectory() as outside:
            write_file(os.path.join(outside, "hidden.txt"), b"hidden contents")
            with tempfile.TemporaryDirectory() as d:
                write_file(os.path.join(d, "normal.txt"), b"ok")
                os.symlink(outside, os.path.join(d, "linkdir"))
                write_manifest(d, [entry_for(d, "normal.txt")])
                report, exit_code = run_verify(d)
                self.assertEqual(exit_code, 1)
                self.assertEqual(codes_of(report), ["PATH_ESCAPES_BUNDLE"])
                paths = [f["path"] for f in report["findings"]]
                self.assertEqual(paths, ["linkdir"])
                # Confirm the escaping directory's contents were never surfaced.
                for f in report["findings"]:
                    self.assertNotIn("hidden.txt", f["path"])

    def test_symlink_via_ancestor_directory_manifest_reference_escapes(self):
        with tempfile.TemporaryDirectory() as outside:
            write_file(os.path.join(outside, "b.txt"), b"outside data")
            with tempfile.TemporaryDirectory() as d:
                os.symlink(outside, os.path.join(d, "a"))
                write_manifest(d, [{"path": "a/b.txt", "sha256": "a" * 64, "size_bytes": 1}])
                report, exit_code = run_verify(d)
                self.assertEqual(exit_code, 1)
                codes = codes_of(report)
                self.assertIn("PATH_ESCAPES_BUNDLE", codes)
                self.assertNotIn("DIGEST_MISMATCH", codes)
                self.assertNotIn("MISSING_FILE", codes)

    def test_symlink_pointing_inside_bundle_is_not_an_escape(self):
        with tempfile.TemporaryDirectory() as d:
            data = b"real content"
            write_file(os.path.join(d, "real.txt"), data)
            os.symlink(os.path.join(d, "real.txt"), os.path.join(d, "alias.txt"))
            write_manifest(d, [entry_for(d, "real.txt", data=data),
                                {"path": "alias.txt", "sha256": sha256_of(data), "size_bytes": len(data)}])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 0)

    def test_multiple_escaping_entries_all_reported(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [
                {"path": "../a.txt", "sha256": "a" * 64, "size_bytes": 1},
                {"path": "/etc/shadow", "sha256": "a" * 64, "size_bytes": 1},
            ])
            report, exit_code = run_verify(d)
            self.assertEqual(len(report["findings"]), 2)
            self.assertTrue(all(f["code"] == "PATH_ESCAPES_BUNDLE" for f in report["findings"]))


# ==========================================================================
# Sorting / total order
# ==========================================================================

class SortingTests(unittest.TestCase):
    def test_findings_sorted_by_code_then_path(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "z_unlisted.txt"), b"z")
            files = [
                {"path": "m1.txt", "sha256": "a" * 64, "size_bytes": 1},  # MISSING
                {"path": "m0.txt", "sha256": "a" * 64, "size_bytes": 1},  # MISSING
            ]
            write_manifest(d, files)
            report, exit_code = run_verify(d)
            codes_paths = [(f["code"], f["path"]) for f in report["findings"]]
            self.assertEqual(codes_paths, sorted(codes_paths))

    def test_permuted_manifest_order_same_report(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            for d in (d1, d2):
                write_file(os.path.join(d, "a.txt"), b"AAA")
                write_file(os.path.join(d, "b.txt"), b"BBB")
                write_file(os.path.join(d, "c.txt"), b"CCC")
            files1 = [entry_for(d1, "a.txt"), entry_for(d1, "b.txt"), entry_for(d1, "c.txt")]
            files2 = [entry_for(d2, "c.txt"), entry_for(d2, "a.txt"), entry_for(d2, "b.txt")]
            write_manifest(d1, files1)
            write_manifest(d2, files2)
            report1, _ = run_verify(d1)
            report2, _ = run_verify(d2)
            self.assertEqual(report1, report2)

    def test_permuted_manifest_order_with_findings_same_report(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            for d in (d1, d2):
                write_file(os.path.join(d, "a.txt"), b"AAA")
            files1 = [
                {"path": "a.txt", "sha256": "0" * 64, "size_bytes": 3},
                {"path": "missing1.txt", "sha256": "a" * 64, "size_bytes": 1},
                {"path": "missing2.txt", "sha256": "a" * 64, "size_bytes": 1},
            ]
            files2 = [
                {"path": "missing2.txt", "sha256": "a" * 64, "size_bytes": 1},
                {"path": "missing1.txt", "sha256": "a" * 64, "size_bytes": 1},
                {"path": "a.txt", "sha256": "0" * 64, "size_bytes": 3},
            ]
            write_manifest(d1, files1)
            write_manifest(d2, files2)
            report1, _ = run_verify(d1)
            report2, _ = run_verify(d2)
            self.assertEqual(report1["findings"], report2["findings"])

    def test_tiebreak_breaks_tie_between_identical_code_and_path(self):
        # Two DUPLICATE_PATH-adjacent malformed entries with the same code
        # and same (empty) path but different manifest_index -- the tiebreak
        # must be the deciding factor, not an arbitrary/unstable order.
        f1 = {"code": "MALFORMED_ENTRY", "path": "", "detail": "b problem", "manifest_index": 5}
        f2 = {"code": "MALFORMED_ENTRY", "path": "", "detail": "a problem", "manifest_index": 2}
        ordered = sorted([f1, f2], key=bv.sort_key)
        self.assertEqual(ordered[0]["manifest_index"], 2)
        self.assertEqual(ordered[1]["manifest_index"], 5)
        # Confirm canonical JSON dump actually differs and drives the order.
        self.assertNotEqual(bv._canonical_dump(f1), bv._canonical_dump(f2))

    def test_tiebreak_is_the_only_thing_that_differs(self):
        # Same code, same path, ONLY a tiebreak-relevant field differs.
        f1 = {"code": "UNLISTED_FILE", "path": "x.txt", "detail": "aaa"}
        f2 = {"code": "UNLISTED_FILE", "path": "x.txt", "detail": "zzz"}
        ordered = sorted([f2, f1], key=bv.sort_key)
        self.assertEqual([f["detail"] for f in ordered], ["aaa", "zzz"])


# ==========================================================================
# Determinism / report shape
# ==========================================================================

class DeterminismTests(unittest.TestCase):
    def test_byte_stable_across_two_runs(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"stable")
            write_manifest(d, [entry_for(d, "a.txt")])
            r1 = run_cli(["--bundle", d])
            r2 = run_cli(["--bundle", d])
            self.assertEqual(r1.stdout, r2.stdout)
            self.assertEqual(r1.returncode, r2.returncode)

    def test_report_is_single_line_plus_trailing_newline(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"stable")
            write_manifest(d, [entry_for(d, "a.txt")])
            out_path = os.path.join(d, "report.json")
            r = run_cli(["--bundle", d, "-o", out_path])
            self.assertEqual(r.returncode, 0)
            with open(out_path, "rb") as f:
                data = f.read()
            self.assertTrue(data.endswith(b"\n"))
            self.assertEqual(data.count(b"\n"), 1)

    def test_report_has_no_extra_whitespace(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"stable")
            write_manifest(d, [entry_for(d, "a.txt")])
            r = run_cli(["--bundle", d])
            body = r.stdout.rstrip("\n")
            self.assertNotIn(", ", body)
            self.assertNotIn(": ", body)

    def test_report_contains_no_absolute_paths(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"x")
            write_file(os.path.join(d, "unlisted.txt"), b"y")
            write_manifest(d, [{"path": "a.txt", "sha256": "0" * 64, "size_bytes": 1}])
            r = run_cli(["--bundle", d])
            self.assertNotIn(d, r.stdout)
            self.assertFalse(r.stdout.lstrip().startswith("/"))

    def test_report_keys_are_sorted(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"x")
            write_manifest(d, [entry_for(d, "a.txt")])
            r = run_cli(["--bundle", d])
            report = json.loads(r.stdout)
            reencoded = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
            self.assertEqual(r.stdout, reencoded)


# ==========================================================================
# Exit codes via subprocess
# ==========================================================================

class ExitCodeSubprocessTests(unittest.TestCase):
    def test_exit_0_clean(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"x")
            write_manifest(d, [entry_for(d, "a.txt")])
            r = run_cli(["--bundle", d])
            self.assertEqual(r.returncode, 0)

    def test_exit_1_findings(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "ghost.txt", "sha256": "a" * 64, "size_bytes": 1}])
            r = run_cli(["--bundle", d])
            self.assertEqual(r.returncode, 1)

    def test_exit_2_missing_bundle_dir(self):
        r = run_cli(["--bundle", "/definitely/does/not/exist/xyz"])
        self.assertEqual(r.returncode, 2)

    def test_exit_2_missing_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            r = run_cli(["--bundle", d])
            self.assertEqual(r.returncode, 2)

    def test_exit_2_bad_json(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "manifest.json"), "w") as f:
                f.write("{ this is not json")
            r = run_cli(["--bundle", d])
            self.assertEqual(r.returncode, 2)

    def test_exit_2_bad_args_missing_bundle_flag(self):
        r = run_cli([])
        self.assertEqual(r.returncode, 2)

    def test_exit_2_bad_args_unknown_flag(self):
        r = run_cli(["--bogus-flag", "x"])
        self.assertEqual(r.returncode, 2)

    def test_exit_2_unwritable_output(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"x")
            write_manifest(d, [entry_for(d, "a.txt")])
            bad_out = os.path.join(d, "no_such_subdir", "report.json")
            r = run_cli(["--bundle", d, "-o", bad_out])
            self.assertEqual(r.returncode, 2)

    def test_exit_2_manifest_not_a_json_object(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "manifest.json"), "w") as f:
                json.dump([1, 2, 3], f)
            r = run_cli(["--bundle", d])
            self.assertEqual(r.returncode, 2)

    def test_exit_2_manifest_missing_schema_version(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "manifest.json"), "w") as f:
                json.dump({"files": []}, f)
            r = run_cli(["--bundle", d])
            self.assertEqual(r.returncode, 2)

    def test_exit_2_manifest_unsupported_schema_version(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "manifest.json"), "w") as f:
                json.dump({"schema_version": 99, "files": []}, f)
            r = run_cli(["--bundle", d])
            self.assertEqual(r.returncode, 2)

    def test_exit_2_manifest_missing_files_key(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "manifest.json"), "w") as f:
                json.dump({"schema_version": 1}, f)
            r = run_cli(["--bundle", d])
            self.assertEqual(r.returncode, 2)

    def test_exit_2_manifest_files_not_a_list(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "manifest.json"), "w") as f:
                json.dump({"schema_version": 1, "files": "nope"}, f)
            r = run_cli(["--bundle", d])
            self.assertEqual(r.returncode, 2)

    def test_exit_2_never_conflated_with_exit_1(self):
        # A bundle with real findings must be exit 1, never 2.
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "ghost.txt", "sha256": "a" * 64, "size_bytes": 1}])
            r = run_cli(["--bundle", d])
            self.assertNotEqual(r.returncode, 2)
            self.assertEqual(r.returncode, 1)

    def test_stderr_used_for_harness_errors(self):
        r = run_cli(["--bundle", "/no/such/dir"])
        self.assertNotEqual(r.stderr.strip(), "")

    def test_custom_manifest_name(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"x")
            write_manifest(d, [entry_for(d, "a.txt")], manifest_name="custom.json")
            r = run_cli(["--bundle", d, "--manifest-name", "custom.json"])
            self.assertEqual(r.returncode, 0)

    def test_custom_manifest_name_rejects_slash(self):
        with tempfile.TemporaryDirectory() as d:
            r = run_cli(["--bundle", d, "--manifest-name", "sub/manifest.json"])
            self.assertEqual(r.returncode, 2)

    def test_custom_manifest_name_rejects_dotdot(self):
        with tempfile.TemporaryDirectory() as d:
            r = run_cli(["--bundle", d, "--manifest-name", ".."])
            self.assertEqual(r.returncode, 2)

    def test_output_written_to_file(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"x")
            write_manifest(d, [entry_for(d, "a.txt")])
            out_path = os.path.join(d, "out.json")
            r = run_cli(["--bundle", d, "-o", out_path])
            self.assertEqual(r.returncode, 0)
            self.assertTrue(os.path.isfile(out_path))
            with open(out_path, "r", encoding="utf-8") as f:
                report = json.load(f)
            self.assertEqual(report["status"], "clean")

    def test_output_omitted_prints_to_stdout(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"x")
            write_manifest(d, [entry_for(d, "a.txt")])
            r = run_cli(["--bundle", d])
            report = json.loads(r.stdout)
            self.assertEqual(report["status"], "clean")


# ==========================================================================
# Relocation / no-leak-of-absolute-path proof (in-process)
# ==========================================================================

class RelocationTests(unittest.TestCase):
    def test_relocating_bundle_produces_identical_report(self):
        with tempfile.TemporaryDirectory() as base:
            src = os.path.join(base, "orig_bundle_name")
            os.makedirs(src)
            write_file(os.path.join(src, "a.txt"), b"hello")
            write_file(os.path.join(src, "sub", "b.txt"), b"world")
            write_manifest(src, [entry_for(src, "a.txt"), entry_for(src, "sub/b.txt")])

            dst = os.path.join(base, "totally_renamed_dir_xyz")
            import shutil
            shutil.copytree(src, dst)
            try:
                report1, exit1 = run_verify(src)
                report2, exit2 = run_verify(dst)
                self.assertEqual(report1, report2)
                self.assertEqual(exit1, exit2)
            finally:
                shutil.rmtree(dst)


# ==========================================================================
# Source hygiene
# ==========================================================================

class SourceHygieneTests(unittest.TestCase):
    def test_no_time_functions_referenced_in_source(self):
        with open(os.path.join(THIS_DIR, "bundleverify.py"), "r", encoding="utf-8") as f:
            src = f.read()
        for forbidden in ("time.time", "utcnow", "datetime.now", "now()"):
            self.assertNotIn(forbidden, src, "found forbidden substring {0!r} in source".format(forbidden))

    def test_no_import_of_time_module_used_for_timestamps(self):
        with open(os.path.join(THIS_DIR, "bundleverify.py"), "r", encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("import time", src)
        self.assertNotIn("import datetime", src)


# ==========================================================================
# Miscellaneous edge cases
# ==========================================================================

class MiscTests(unittest.TestCase):
    def test_file_with_special_characters_in_name(self):
        with tempfile.TemporaryDirectory() as d:
            name = "weird name (1) [copy] #2.txt"
            write_file(os.path.join(d, name), b"data")
            write_manifest(d, [entry_for(d, name)])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 0)

    def test_compute_sha256_matches_hashlib(self):
        with tempfile.TemporaryDirectory() as d:
            data = os.urandom(4096) * 3
            p = os.path.join(d, "f.bin")
            write_file(p, data)
            self.assertEqual(bv.compute_sha256(p), hashlib.sha256(data).hexdigest())

    def test_string_path_problem_accepts_normal_relative_path(self):
        self.assertIsNone(bv._string_path_problem("a/b/c.txt"))

    def test_string_path_problem_rejects_null_byte(self):
        self.assertIsNotNone(bv._string_path_problem("a\x00b.txt"))

    def test_deeply_nested_clean_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            rel = "/".join(["level{0}".format(i) for i in range(10)]) + "/deep.txt"
            write_file(os.path.join(d, *rel.split("/")), b"deep content")
            write_manifest(d, [entry_for(d, rel)])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 0)

    def test_many_files_all_clean(self):
        with tempfile.TemporaryDirectory() as d:
            files = []
            for i in range(50):
                name = "file_{0:03d}.txt".format(i)
                data = "content {0}".format(i).encode("utf-8")
                write_file(os.path.join(d, name), data)
                files.append(entry_for(d, name, data=data))
            write_manifest(d, files)
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 0)
            self.assertEqual(report["num_files_listed"], 50)

    def test_num_findings_matches_findings_length(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [
                {"path": "g1.txt", "sha256": "a" * 64, "size_bytes": 1},
                {"path": "g2.txt", "sha256": "a" * 64, "size_bytes": 1},
            ])
            report, exit_code = run_verify(d)
            self.assertEqual(report["num_findings"], len(report["findings"]))

    def test_exit_code_matches_finding_count_zero(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"x")
            write_manifest(d, [entry_for(d, "a.txt")])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 0 if not report["findings"] else 1)

    def test_exit_code_matches_finding_count_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "g.txt", "sha256": "a" * 64, "size_bytes": 1}])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 1)
            self.assertTrue(len(report["findings"]) > 0)

    def test_report_findings_only_have_bundle_relative_paths(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"x")
            write_file(os.path.join(d, "unlisted.txt"), b"y")
            write_manifest(d, [{"path": "a.txt", "sha256": "0" * 64, "size_bytes": 1}])
            report, exit_code = run_verify(d)
            for f in report["findings"]:
                self.assertFalse(os.path.isabs(f["path"]))
                self.assertNotIn(d, f["path"])

    def test_manifest_path_field_in_report(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"x")
            write_manifest(d, [entry_for(d, "a.txt")], manifest_name="my_manifest.json")
            report, exit_code = run_verify(d, manifest_name="my_manifest.json")
            self.assertEqual(report["manifest_path"], "my_manifest.json")

    def test_sha256_uppercase_manifest_value_is_malformed_not_silently_lowered(self):
        with tempfile.TemporaryDirectory() as d:
            data = b"x"
            write_file(os.path.join(d, "a.txt"), data)
            write_manifest(d, [{"path": "a.txt", "sha256": sha256_of(data).upper(), "size_bytes": len(data)}])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 1)
            find_one(report, "MALFORMED_ENTRY")

    def test_size_bytes_zero_is_valid(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "empty.txt"), b"")
            write_manifest(d, [{"path": "empty.txt", "sha256": sha256_of(b""), "size_bytes": 0}])
            report, exit_code = run_verify(d)
            self.assertEqual(exit_code, 0)

    def test_validate_entry_structure_well_formed(self):
        path, problem, size_bytes, sha256 = bv.validate_entry_structure(
            {"path": "a.txt", "sha256": "a" * 64, "size_bytes": 5}
        )
        self.assertIsNone(problem)
        self.assertEqual(path, "a.txt")
        self.assertEqual(size_bytes, 5)
        self.assertEqual(sha256, "a" * 64)

    def test_build_report_exit_code_zero(self):
        report, exit_code = bv.build_report([], 0, "manifest.json")
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["status"], "clean")

    def test_build_report_exit_code_one(self):
        findings = [{"code": "MISSING_FILE", "path": "x.txt", "detail": "d"}]
        report, exit_code = bv.build_report(findings, 1, "manifest.json")
        self.assertEqual(exit_code, 1)
        self.assertEqual(report["status"], "findings_present")


# ==========================================================================
# Additional CLI / robustness coverage
# ==========================================================================

class MoreCliTests(unittest.TestCase):
    def test_bundle_path_with_trailing_slash(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"x")
            write_manifest(d, [entry_for(d, "a.txt")])
            r = run_cli(["--bundle", d + os.sep])
            self.assertEqual(r.returncode, 0)

    def test_relative_bundle_path_via_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"x")
            write_manifest(d, [entry_for(d, "a.txt")])
            r = run_cli(["--bundle", "."], cwd=d)
            self.assertEqual(r.returncode, 0)

    def test_bundle_argument_points_to_a_file_not_a_directory(self):
        with tempfile.TemporaryDirectory() as d:
            fpath = os.path.join(d, "notadir.txt")
            write_file(fpath, b"x")
            r = run_cli(["--bundle", fpath])
            self.assertEqual(r.returncode, 2)

    def test_output_and_stdout_identical_content(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outdir:
            write_file(os.path.join(d, "a.txt"), b"x")
            write_manifest(d, [entry_for(d, "a.txt")])
            out_path = os.path.join(outdir, "o.json")
            run_cli(["--bundle", d, "-o", out_path])
            with open(out_path, "r", encoding="utf-8") as f:
                file_content = f.read()
            r = run_cli(["--bundle", d])
            self.assertEqual(file_content, r.stdout)

    def test_help_flag_does_not_crash(self):
        r = run_cli(["--help"])
        self.assertEqual(r.returncode, 0)
        self.assertIn("--bundle", r.stdout)

    def test_manifest_name_argument_defaults_to_manifest_json(self):
        parser = bv.build_arg_parser()
        args = parser.parse_args(["--bundle", "somedir"])
        self.assertEqual(args.manifest_name, "manifest.json")

    def test_output_path_that_is_an_existing_directory_is_unwritable(self):
        # open(existing_directory, "w") always fails (IsADirectoryError),
        # even for root, so this exercises the unwritable-output harness
        # path without depending on POSIX permission bits.
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"x")
            write_manifest(d, [entry_for(d, "a.txt")])
            out_path = os.path.join(d, "iamadir")
            os.makedirs(out_path)
            r = run_cli(["--bundle", d, "-o", out_path])
            self.assertEqual(r.returncode, 2)

    def test_ensure_ascii_escapes_non_ascii_path_in_report(self):
        with tempfile.TemporaryDirectory() as d:
            name = "café.txt"
            write_file(os.path.join(d, name), b"data")
            write_manifest(d, [entry_for(d, name)])
            r = run_cli(["--bundle", d, "--manifest-name", "manifest.json"])
            # unlisted variant forces the name into the findings/report text
            write_manifest(d, [])
            r2 = run_cli(["--bundle", d])
            self.assertNotIn("é", r2.stdout)
            self.assertIn("\\u00e9", r2.stdout)

    def test_two_separate_runs_from_same_dir_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outdir:
            write_file(os.path.join(d, "a.txt"), b"one")
            write_file(os.path.join(d, "b.txt"), b"two")
            write_manifest(d, [entry_for(d, "a.txt"), entry_for(d, "b.txt")])
            out1 = os.path.join(outdir, "r1.json")
            out2 = os.path.join(outdir, "r2.json")
            run_cli(["--bundle", d, "-o", out1])
            run_cli(["--bundle", d, "-o", out2])
            with open(out1, "rb") as f:
                c1 = f.read()
            with open(out2, "rb") as f:
                c2 = f.read()
            self.assertEqual(c1, c2)

    def test_findings_present_status_string_exact(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "g.txt", "sha256": "a" * 64, "size_bytes": 1}])
            report, exit_code = run_verify(d)
            self.assertEqual(report["status"], "findings_present")

    def test_clean_status_string_exact(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"x")
            write_manifest(d, [entry_for(d, "a.txt")])
            report, exit_code = run_verify(d)
            self.assertEqual(report["status"], "clean")


class MoreFindingCombinationTests(unittest.TestCase):
    def test_missing_and_unlisted_together(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "surprise.txt"), b"s")
            write_manifest(d, [{"path": "ghost.txt", "sha256": "a" * 64, "size_bytes": 1}])
            report, exit_code = run_verify(d)
            self.assertEqual(sorted(codes_of(report)), ["MISSING_FILE", "UNLISTED_FILE"])

    def test_all_finding_codes_can_coexist_in_one_report(self):
        with tempfile.TemporaryDirectory() as outside:
            write_file(os.path.join(outside, "s.txt"), b"secret")
            with tempfile.TemporaryDirectory() as d:
                write_file(os.path.join(d, "ok.txt"), b"okdata")
                write_file(os.path.join(d, "tampered.txt"), b"original")
                write_file(os.path.join(d, "unlisted.txt"), b"u")
                os.symlink(os.path.join(outside, "s.txt"), os.path.join(d, "escape.txt"))
                dup_entry = entry_for(d, "ok.txt")
                files = [
                    dup_entry,
                    dict(dup_entry),  # DUPLICATE_PATH
                    {"path": "tampered.txt", "sha256": "0" * 64, "size_bytes": 8},  # DIGEST_MISMATCH
                    {"path": "vanished.txt", "sha256": "a" * 64, "size_bytes": 1},  # MISSING_FILE
                    {"path": "../nope.txt", "sha256": "a" * 64, "size_bytes": 1},  # PATH_ESCAPES_BUNDLE
                    {"path": "escape.txt", "sha256": "a" * 64, "size_bytes": 1},  # PATH_ESCAPES_BUNDLE (symlink)
                    {"bad": "entry"},  # MALFORMED_ENTRY
                ]
                write_manifest(d, files)
                report, exit_code = run_verify(d)
                self.assertEqual(exit_code, 1)
                codes = set(codes_of(report))
                self.assertEqual(codes, {
                    "DUPLICATE_PATH", "DIGEST_MISMATCH", "MISSING_FILE",
                    "PATH_ESCAPES_BUNDLE", "MALFORMED_ENTRY", "UNLISTED_FILE",
                })

    def test_finding_order_stable_across_many_permutations(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"A")
            files_base = [
                {"path": "a.txt", "sha256": "0" * 64, "size_bytes": 1},
                {"path": "m1.txt", "sha256": "a" * 64, "size_bytes": 1},
                {"path": "m2.txt", "sha256": "a" * 64, "size_bytes": 1},
                {"path": "m3.txt", "sha256": "a" * 64, "size_bytes": 1},
            ]
            import itertools
            reports = []
            for perm in itertools.permutations(files_base):
                write_manifest(d, list(perm))
                report, _ = run_verify(d)
                reports.append(report["findings"])
            first = reports[0]
            for r in reports[1:]:
                self.assertEqual(r, first)


class ExtraMalformedAndEscapeTests(unittest.TestCase):
    def test_malformed_entry_sha256_with_whitespace(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "a.txt", "sha256": " " + "a" * 63, "size_bytes": 1}])
            report, exit_code = run_verify(d)
            find_one(report, "MALFORMED_ENTRY")

    def test_malformed_entry_path_with_only_whitespace_is_allowed_as_path(self):
        # Whitespace-only path is a valid (if unusual) relative path string;
        # it should be treated as MISSING_FILE, not MALFORMED_ENTRY, since it
        # is structurally a fine non-empty string with no '..' or absolute markers.
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "   ", "sha256": "a" * 64, "size_bytes": 1}])
            report, exit_code = run_verify(d)
            self.assertEqual(codes_of(report), ["MISSING_FILE"])

    def test_path_component_dotdot_in_middle(self):
        with tempfile.TemporaryDirectory() as d:
            write_manifest(d, [{"path": "sub/../../../etc/passwd", "sha256": "a" * 64, "size_bytes": 1}])
            report, exit_code = run_verify(d)
            find_one(report, "PATH_ESCAPES_BUNDLE")

    def test_duplicate_path_and_escape_path_do_not_interact(self):
        with tempfile.TemporaryDirectory() as d:
            files = [
                {"path": "../a.txt", "sha256": "a" * 64, "size_bytes": 1},
                {"path": "../a.txt", "sha256": "b" * 64, "size_bytes": 2},
            ]
            write_manifest(d, files)
            report, exit_code = run_verify(d)
            # Both are string-unsafe, so both are folded into a single
            # deduplicated PATH_ESCAPES_BUNDLE finding, never DUPLICATE_PATH
            # (duplicate detection only applies to safe, verifiable paths).
            self.assertEqual(codes_of(report), ["PATH_ESCAPES_BUNDLE"])

    def test_bug_manifest_json_itself_a_symlink_escaping_bundle_is_refused(self):
        # Pinning test for a real bug found during the mandatory bug hunt:
        # bundleverify used to open manifest.json via a plain os.path.isfile
        # check, which FOLLOWS symlinks. A bundle author (or attacker) could
        # replace manifest.json with a symlink to an arbitrary file outside
        # the bundle (e.g. one claiming an empty file list), and the tool
        # would silently trust that external file as "the manifest",
        # completely hiding any real tampered/unlisted files in the actual
        # bundle. Triggering input: bundle_dir/manifest.json is a symlink
        # to <outside_dir>/evil_manifest.json declaring {"files": []} while
        # the bundle directory itself contains an untracked real file.
        # Fix: refuse to follow a manifest.json whose realpath escapes the
        # bundle root; treat it as a harness error (exit 2), never silently
        # substitute it.
        with tempfile.TemporaryDirectory() as outside:
            with open(os.path.join(outside, "evil_manifest.json"), "w", encoding="utf-8") as f:
                json.dump({"schema_version": 1, "files": []}, f)
            with tempfile.TemporaryDirectory() as d:
                write_file(os.path.join(d, "realfile.txt"), b"untracked real content")
                os.symlink(os.path.join(outside, "evil_manifest.json"), os.path.join(d, "manifest.json"))
                r = run_cli(["--bundle", d])
                self.assertEqual(r.returncode, 2)
                self.assertEqual(r.stdout, "")
                self.assertIn("manifest", r.stderr.lower())
                self.assertIn("symlink", r.stderr.lower())

    def test_manifest_json_symlink_pointing_inside_bundle_is_allowed(self):
        # A manifest.json symlink that resolves INSIDE the bundle is not a
        # security escape (unlike the bug above) and must not be refused as
        # a harness error; it should be read and used normally.
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "a.txt"), b"x")
            write_manifest(d, [entry_for(d, "a.txt")], manifest_name="real_manifest.json")
            os.symlink(os.path.join(d, "real_manifest.json"), os.path.join(d, "manifest.json"))
            r = run_cli(["--bundle", d])
            self.assertNotEqual(r.returncode, 2)
            report = json.loads(r.stdout)
            self.assertNotIn("PATH_ESCAPES_BUNDLE", [f["code"] for f in report["findings"]])

    def test_symlink_escape_detail_mentions_symlink(self):
        with tempfile.TemporaryDirectory() as outside:
            write_file(os.path.join(outside, "x.txt"), b"x")
            with tempfile.TemporaryDirectory() as d:
                os.symlink(os.path.join(outside, "x.txt"), os.path.join(d, "l.txt"))
                write_manifest(d, [{"path": "l.txt", "sha256": "a" * 64, "size_bytes": 1}])
                report, exit_code = run_verify(d)
                f = find_one(report, "PATH_ESCAPES_BUNDLE", "l.txt")
                self.assertIn("symlink", f["detail"].lower())


if __name__ == "__main__":
    unittest.main()
