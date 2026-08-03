"""Unit tests for bundle_index.py.

Run with: python3 -m unittest test_bundle_index -v
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import bundle_index as bi

HAS_SYMLINK = hasattr(os, "symlink")


def _make_symlink(target, link_path):
    try:
        os.symlink(target, link_path)
        return True
    except (OSError, NotImplementedError):
        return False


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="bundle_index_test_")
        self.root = Path(self._tmp)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def write(self, relpath, content, binary=False):
        p = self.root / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        mode = "wb" if binary else "w"
        with open(p, mode) as fh:
            if binary:
                fh.write(content)
            else:
                fh.write(content)
        return p


# ---------------------------------------------------------------------
# is_binary
# ---------------------------------------------------------------------

class TestIsBinary(unittest.TestCase):
    def test_empty_bytes_is_not_binary(self):
        self.assertFalse(bi.is_binary(b""))

    def test_plain_ascii_is_text(self):
        self.assertFalse(bi.is_binary(b"hello world\n"))

    def test_valid_utf8_multibyte_is_text(self):
        self.assertFalse(bi.is_binary("café résumé 日本語".encode("utf-8")))

    def test_null_byte_forces_binary(self):
        self.assertTrue(bi.is_binary(b"hello\x00world"))

    def test_null_byte_alone_forces_binary(self):
        self.assertTrue(bi.is_binary(b"\x00"))

    def test_invalid_utf8_is_binary(self):
        self.assertTrue(bi.is_binary(b"\xff\xfe\x00\x01garbage"))

    def test_truncated_multibyte_sequence_is_binary(self):
        self.assertTrue(bi.is_binary(b"\xe2\x82"))  # incomplete euro sign

    def test_png_header_is_binary(self):
        self.assertTrue(bi.is_binary(bytes([0x89, 0x50, 0x4E, 0x47, 0x00, 0x0D, 0x0A, 0x1A, 0x0A])))

    def test_crlf_text_is_not_binary(self):
        self.assertFalse(bi.is_binary(b"line1\r\nline2\r\n"))

    def test_high_byte_valid_utf8_continuation_is_text(self):
        # 0xC3 0xA9 is a valid two-byte UTF-8 sequence (e-acute)
        self.assertFalse(bi.is_binary(b"caf\xc3\xa9"))


# ---------------------------------------------------------------------
# count_lines
# ---------------------------------------------------------------------

class TestCountLines(unittest.TestCase):
    def test_empty_is_zero(self):
        self.assertEqual(bi.count_lines(b""), 0)

    def test_single_line_no_trailing_newline(self):
        self.assertEqual(bi.count_lines(b"hello"), 1)

    def test_single_line_with_trailing_newline(self):
        self.assertEqual(bi.count_lines(b"hello\n"), 1)

    def test_two_lines_trailing_newline(self):
        self.assertEqual(bi.count_lines(b"a\nb\n"), 2)

    def test_two_lines_no_trailing_newline(self):
        self.assertEqual(bi.count_lines(b"a\nb"), 2)

    def test_exactly_one_newline_byte(self):
        self.assertEqual(bi.count_lines(b"\n"), 1)

    def test_two_newlines_only(self):
        self.assertEqual(bi.count_lines(b"\n\n"), 2)

    def test_crlf_pair_counts_as_one_terminator(self):
        self.assertEqual(bi.count_lines(b"a\r\nb\r\n"), 2)

    def test_crlf_no_trailing_newline(self):
        self.assertEqual(bi.count_lines(b"a\r\nb"), 2)

    def test_bare_cr_counts_as_line_terminator(self):
        self.assertEqual(bi.count_lines(b"a\rb\r"), 2)

    def test_mixed_blank_lines(self):
        self.assertEqual(bi.count_lines(b"a\n\nb\n"), 3)

    def test_no_trailing_newline_off_by_one_regression(self):
        # This is the classic wc -l trap: wc -l would report 2 here
        # (it only counts newline bytes), undercounting the final partial
        # line. bundle_index must report 3.
        self.assertEqual(bi.count_lines(b"one\ntwo\nthree"), 3)

    def test_long_multiline_body(self):
        data = b"\n".join(b"line%d" % i for i in range(50))
        self.assertEqual(bi.count_lines(data), 50)


# ---------------------------------------------------------------------
# detected_type_for_text
# ---------------------------------------------------------------------

class TestDetectedTypeForText(unittest.TestCase):
    def test_python(self):
        self.assertEqual(bi.detected_type_for_text("foo.py"), "python")

    def test_json(self):
        self.assertEqual(bi.detected_type_for_text("data.json"), "json")

    def test_markdown_md(self):
        self.assertEqual(bi.detected_type_for_text("README.md"), "markdown")

    def test_markdown_markdown_ext(self):
        self.assertEqual(bi.detected_type_for_text("NOTES.markdown"), "markdown")

    def test_txt(self):
        self.assertEqual(bi.detected_type_for_text("notes.txt"), "text")

    def test_log_maps_to_text(self):
        self.assertEqual(bi.detected_type_for_text("run.log"), "text")

    def test_shell_sh(self):
        self.assertEqual(bi.detected_type_for_text("run.sh"), "shell")

    def test_shell_bash(self):
        self.assertEqual(bi.detected_type_for_text("run.bash"), "shell")

    def test_yaml_yml(self):
        self.assertEqual(bi.detected_type_for_text("config.yml"), "yaml")

    def test_yaml_yaml(self):
        self.assertEqual(bi.detected_type_for_text("config.yaml"), "yaml")

    def test_csv(self):
        self.assertEqual(bi.detected_type_for_text("table.csv"), "csv")

    def test_html(self):
        self.assertEqual(bi.detected_type_for_text("index.html"), "html")

    def test_css(self):
        self.assertEqual(bi.detected_type_for_text("style.css"), "css")

    def test_javascript(self):
        self.assertEqual(bi.detected_type_for_text("app.js"), "javascript")

    def test_toml(self):
        self.assertEqual(bi.detected_type_for_text("pyproject.toml"), "toml")

    def test_ini(self):
        self.assertEqual(bi.detected_type_for_text("setup.cfg"), "ini")

    def test_xml(self):
        self.assertEqual(bi.detected_type_for_text("pom.xml"), "xml")

    def test_c_source(self):
        self.assertEqual(bi.detected_type_for_text("main.c"), "c")

    def test_c_header(self):
        self.assertEqual(bi.detected_type_for_text("main.h"), "c-header")

    def test_rust(self):
        self.assertEqual(bi.detected_type_for_text("main.rs"), "rust")

    def test_unknown_extension_falls_back_to_text(self):
        self.assertEqual(bi.detected_type_for_text("weird.xyzabc"), "text")

    def test_no_extension_falls_back_to_text(self):
        self.assertEqual(bi.detected_type_for_text("Makefile"), "text")

    def test_case_insensitive_extension(self):
        self.assertEqual(bi.detected_type_for_text("SCRIPT.PY"), "python")

    def test_nested_path_uses_final_suffix_only(self):
        self.assertEqual(bi.detected_type_for_text("a/b/c/module.py"), "python")

    def test_dotfile_with_extension(self):
        self.assertEqual(bi.detected_type_for_text(".config.yaml"), "yaml")


# ---------------------------------------------------------------------
# suspicious_reasons
# ---------------------------------------------------------------------

class TestSuspiciousReasons(unittest.TestCase):
    def test_clean_path_has_no_reasons(self):
        self.assertEqual(bi.suspicious_reasons("src/main.py"), [])

    def test_pycache_dir_component(self):
        reasons = bi.suspicious_reasons("__pycache__/main.cpython-310.pyc")
        self.assertTrue(any("__pycache__" in r for r in reasons))

    def test_pyc_suffix_flagged_even_outside_pycache(self):
        reasons = bi.suspicious_reasons("build/artifact.pyc")
        self.assertTrue(any(".pyc" in r for r in reasons))

    def test_git_dir_component(self):
        reasons = bi.suspicious_reasons(".git/HEAD")
        self.assertTrue(any(".git" in r for r in reasons))

    def test_git_nested_deep(self):
        reasons = bi.suspicious_reasons(".git/objects/pack/pack-abc.idx")
        self.assertTrue(any(".git" in r for r in reasons))

    def test_ds_store_filename(self):
        reasons = bi.suspicious_reasons(".DS_Store")
        self.assertTrue(any("DS_Store" in r for r in reasons))

    def test_ds_store_nested(self):
        reasons = bi.suspicious_reasons("assets/.DS_Store")
        self.assertTrue(any("DS_Store" in r for r in reasons))

    def test_pycache_and_pyc_both_flagged_once_each(self):
        reasons = bi.suspicious_reasons("__pycache__/mod.pyc")
        self.assertEqual(len(reasons), 2)

    def test_filename_containing_git_substring_not_flagged(self):
        # "gitignore.txt" is not a ".git" path component
        self.assertEqual(bi.suspicious_reasons("gitignore.txt"), [])

    def test_pycache_like_name_not_exact_not_flagged(self):
        self.assertEqual(bi.suspicious_reasons("__pycache__extra/file.txt"), [])


# ---------------------------------------------------------------------
# extract_rerun_block
# ---------------------------------------------------------------------

class TestExtractRerunBlock(unittest.TestCase):
    def test_no_fences_returns_none(self):
        self.assertIsNone(bi.extract_rerun_block("just prose, no code blocks"))

    def test_bash_fence_found(self):
        text = "intro\n\n```bash\necho hi\n```\n\nmore text\n"
        result = bi.extract_rerun_block(text)
        self.assertEqual(result, ("bash", "echo hi"))

    def test_sh_fence_found(self):
        text = "```sh\necho one\necho two\n```\n"
        result = bi.extract_rerun_block(text)
        self.assertEqual(result, ("sh", "echo one\necho two"))

    def test_console_fence_found(self):
        text = "```console\n$ run.sh\n```\n"
        result = bi.extract_rerun_block(text)
        self.assertEqual(result[0], "console")

    def test_python_fence_alone_not_selected_by_language(self):
        text = "```python\nprint('hi')\n```\n"
        result = bi.extract_rerun_block(text)
        self.assertIsNone(result)

    def test_first_qualifying_fence_wins_over_later_one(self):
        text = "```python\nx = 1\n```\n\n```bash\nfirst command\n```\n\n```bash\nsecond command\n```\n"
        result = bi.extract_rerun_block(text)
        self.assertEqual(result, ("bash", "first command"))

    def test_language_tag_stripped_and_lowered(self):
        text = "```  BASH  \necho hi\n```\n"
        result = bi.extract_rerun_block(text)
        self.assertEqual(result[0], "bash")

    def test_heading_fallback_when_no_language_match(self):
        text = "## Rerun\n\n```text\nmake test\n```\n"
        result = bi.extract_rerun_block(text)
        self.assertEqual(result, ("text", "make test"))

    def test_heading_fallback_reproduce_variant(self):
        text = "## How to Reproduce\n\n```\nmake all\n```\n"
        result = bi.extract_rerun_block(text)
        self.assertEqual(result, (None, "make all"))

    def test_heading_fallback_commands_variant(self):
        text = "## Commands\n\n```\nls -la\n```\n"
        result = bi.extract_rerun_block(text)
        self.assertEqual(result, (None, "ls -la"))

    def test_heading_case_insensitive(self):
        text = "## RERUN\n\n```\ndo it\n```\n"
        result = bi.extract_rerun_block(text)
        self.assertIsNotNone(result)

    def test_heading_not_matching_topic_no_fallback(self):
        text = "## Overview\n\n```\nnot a rerun block\n```\n"
        result = bi.extract_rerun_block(text)
        self.assertIsNone(result)

    def test_heading_present_but_no_fence_after_it(self):
        text = "## Rerun commands\n\nprose only, no fence here.\n"
        result = bi.extract_rerun_block(text)
        self.assertIsNone(result)

    def test_fence_before_heading_ignored_by_fallback(self):
        # fence appears before the heading; fallback should ignore it and
        # look only at fences that start after the heading
        text = "```text\nunrelated block\n```\n\n## Rerun\n\nprose, no fence after.\n"
        result = bi.extract_rerun_block(text)
        self.assertIsNone(result)

    def test_language_priority_beats_heading_fallback(self):
        text = "## Overview\n\n```bash\nthe real command\n```\n"
        result = bi.extract_rerun_block(text)
        self.assertEqual(result, ("bash", "the real command"))

    def test_multiple_fenced_blocks_readme(self):
        text = (
            "# Bundle\n\n"
            "## Overview\n\n```json\n{\"a\": 1}\n```\n\n"
            "## Rerun commands\n\n```bash\npython3 -m unittest -v\n```\n\n"
            "## Appendix\n\n```text\nsome appendix content\n```\n"
        )
        result = bi.extract_rerun_block(text)
        self.assertEqual(result, ("bash", "python3 -m unittest -v"))

    def test_empty_readme_text(self):
        self.assertIsNone(bi.extract_rerun_block(""))

    def test_fence_with_extra_trailing_whitespace_on_close_line(self):
        text = "```bash   \necho hi\n```   \n"
        result = bi.extract_rerun_block(text)
        self.assertEqual(result, ("bash", "echo hi"))

    def test_multiline_bash_block_preserved_verbatim(self):
        text = "```bash\nstep one\nstep two\nstep three\n```\n"
        result = bi.extract_rerun_block(text)
        self.assertEqual(result[1], "step one\nstep two\nstep three")


# ---------------------------------------------------------------------
# make_finding / sort_findings
# ---------------------------------------------------------------------

class TestFindingHelpers(unittest.TestCase):
    def test_make_finding_shape(self):
        f = bi.make_finding("EMPTY_FILE", ["a.txt"], "zero bytes")
        self.assertEqual(f["code"], "EMPTY_FILE")
        self.assertEqual(f["paths"], ["a.txt"])
        self.assertEqual(f["detail"], "zero bytes")

    def test_make_finding_dedupes_and_sorts_paths(self):
        f = bi.make_finding("X", ["b.txt", "a.txt", "a.txt"], "d")
        self.assertEqual(f["paths"], ["a.txt", "b.txt"])

    def test_sort_findings_by_code_then_paths(self):
        findings = [
            bi.make_finding("Z_CODE", ["a"], "d"),
            bi.make_finding("A_CODE", ["b"], "d"),
            bi.make_finding("A_CODE", ["a"], "d"),
        ]
        ordered = bi.sort_findings(findings)
        codes_paths = [(f["code"], f["paths"]) for f in ordered]
        self.assertEqual(codes_paths, [
            ("A_CODE", ["a"]),
            ("A_CODE", ["b"]),
            ("Z_CODE", ["a"]),
        ])

    def test_sort_findings_is_stable_and_deterministic_across_runs(self):
        findings = [bi.make_finding("C", ["p%d" % i], "d") for i in range(20)]
        a = bi.sort_findings(list(reversed(findings)))
        b = bi.sort_findings(list(findings))
        self.assertEqual(a, b)


# ---------------------------------------------------------------------
# canonical_json_bytes
# ---------------------------------------------------------------------

class TestCanonicalJsonBytes(unittest.TestCase):
    def test_ends_with_single_newline(self):
        out = bi.canonical_json_bytes({"a": 1})
        self.assertTrue(out.endswith(b"\n"))
        self.assertFalse(out.endswith(b"\n\n"))

    def test_no_spaces_in_separators(self):
        out = bi.canonical_json_bytes({"a": 1, "b": 2})
        self.assertNotIn(b", ", out)
        self.assertNotIn(b": ", out)

    def test_keys_sorted(self):
        out = bi.canonical_json_bytes({"z": 1, "a": 2})
        self.assertLess(out.index(b'"a"'), out.index(b'"z"'))

    def test_ensure_ascii_escapes_non_ascii(self):
        out = bi.canonical_json_bytes({"name": "café"})
        self.assertNotIn("é".encode("utf-8"), out)
        self.assertIn(b"\\u00e9", out)

    def test_deterministic_for_same_input(self):
        obj = {"b": [3, 1, 2], "a": "x"}
        self.assertEqual(bi.canonical_json_bytes(obj), bi.canonical_json_bytes(dict(obj)))

    def test_output_is_valid_json(self):
        original = {"a": [1, 2, {"b": None}]}
        out = bi.canonical_json_bytes(original)
        parsed = json.loads(out.decode("utf-8"))
        self.assertEqual(parsed, original)


# ---------------------------------------------------------------------
# discover_files
# ---------------------------------------------------------------------

class TestDiscoverFiles(TempDirCase):
    def test_empty_directory_yields_no_files(self):
        self.assertEqual(bi.discover_files(self.root), [])

    def test_single_file(self):
        self.write("a.txt", "hi")
        self.assertEqual(bi.discover_files(self.root), ["a.txt"])

    def test_sorted_order(self):
        self.write("zeta.txt", "z")
        self.write("alpha.txt", "a")
        self.write("mid.txt", "m")
        self.assertEqual(bi.discover_files(self.root), ["alpha.txt", "mid.txt", "zeta.txt"])

    def test_dot_before_slash_ordering(self):
        # "a.txt" must sort before "a/b.txt" under plain string ordering
        self.write("a.txt", "x")
        self.write("a/b.txt", "y")
        self.assertEqual(bi.discover_files(self.root), ["a.txt", "a/b.txt"])

    def test_nested_subdirectories(self):
        self.write("x/y/z.txt", "deep")
        self.assertEqual(bi.discover_files(self.root), ["x/y/z.txt"])

    def test_deeply_nested_five_levels(self):
        self.write("l1/l2/l3/l4/l5/leaf.txt", "leaf")
        result = bi.discover_files(self.root)
        self.assertEqual(result, ["l1/l2/l3/l4/l5/leaf.txt"])

    def test_empty_subdirectory_produces_no_entries(self):
        (self.root / "emptydir").mkdir()
        self.write("present.txt", "x")
        self.assertEqual(bi.discover_files(self.root), ["present.txt"])

    def test_unicode_filename(self):
        self.write("日本語.txt", "content")
        self.assertEqual(bi.discover_files(self.root), ["日本語.txt"])

    def test_unicode_filename_with_accents(self):
        self.write("café.txt", "content")
        self.assertEqual(bi.discover_files(self.root), ["café.txt"])

    def test_paths_use_forward_slashes(self):
        self.write("a/b/c.txt", "x")
        result = bi.discover_files(self.root)
        self.assertEqual(result, ["a/b/c.txt"])
        self.assertNotIn("\\", result[0])

    def test_dotfile_discovered(self):
        self.write(".hidden", "secret")
        self.assertEqual(bi.discover_files(self.root), [".hidden"])

    @unittest.skipUnless(HAS_SYMLINK, "platform has no symlink support")
    def test_symlink_to_real_file_discovered(self):
        target = self.write("target.txt", "hi")
        link = self.root / "link.txt"
        if not _make_symlink("target.txt", str(link)):
            self.skipTest("symlink creation not permitted in this environment")
        result = bi.discover_files(self.root)
        self.assertIn("link.txt", result)

    @unittest.skipUnless(HAS_SYMLINK, "platform has no symlink support")
    def test_broken_symlink_still_discovered_as_file(self):
        link = self.root / "broken.txt"
        if not _make_symlink("nowhere.txt", str(link)):
            self.skipTest("symlink creation not permitted in this environment")
        result = bi.discover_files(self.root)
        self.assertIn("broken.txt", result)


# ---------------------------------------------------------------------
# build_report - basic integration behaviour
# ---------------------------------------------------------------------

class TestBuildReportBasics(TempDirCase):
    def test_missing_root_raises_input_error(self):
        with self.assertRaises(bi.InputError):
            bi.build_report(self.root / "does_not_exist")

    def test_root_is_a_file_raises_input_error(self):
        f = self.write("not_a_dir.txt", "x")
        with self.assertRaises(bi.InputError):
            bi.build_report(f)

    def test_clean_bundle_exit_code_zero(self):
        self.write("README.md", "# T\n\n## Rerun\n\n```bash\necho hi\n```\n")
        self.write("a.txt", "content\n")
        report, code = bi.build_report(self.root)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["finding_count"], 0)

    def test_report_has_expected_top_level_keys(self):
        self.write("README.md", "```bash\necho hi\n```\n")
        report, _ = bi.build_report(self.root)
        expected = {
            "tool", "tool_version", "schema_version", "file_count", "files",
            "finding_count", "findings", "rerun_command", "status", "exit_code",
        }
        self.assertEqual(set(report.keys()), expected)

    def test_tool_name_and_version_present(self):
        self.write("README.md", "```bash\nx\n```\n")
        report, _ = bi.build_report(self.root)
        self.assertEqual(report["tool"], "bundle_index")
        self.assertIsInstance(report["tool_version"], str)

    def test_file_count_matches_files_length(self):
        self.write("README.md", "```bash\nx\n```\n")
        self.write("a.txt", "1")
        self.write("b.txt", "2")
        report, _ = bi.build_report(self.root)
        self.assertEqual(report["file_count"], len(report["files"]))
        self.assertEqual(report["file_count"], 3)

    def test_files_list_sorted_by_relative_path(self):
        self.write("README.md", "```bash\nx\n```\n")
        self.write("z.txt", "1")
        self.write("a.txt", "2")
        report, _ = bi.build_report(self.root)
        paths = [f["relative_path"] for f in report["files"]]
        self.assertEqual(paths, sorted(paths))


# ---------------------------------------------------------------------
# build_report - per-field correctness
# ---------------------------------------------------------------------

class TestBuildReportFields(TempDirCase):
    def _file_entry(self, report, relpath):
        for f in report["files"]:
            if f["relative_path"] == relpath:
                return f
        self.fail("no entry for %s" % relpath)

    def test_sha256_matches_hashlib(self):
        self.write("README.md", "```bash\nx\n```\n")
        self.write("a.txt", "hello world")
        report, _ = bi.build_report(self.root)
        entry = self._file_entry(report, "a.txt")
        import hashlib
        expected = hashlib.sha256(b"hello world").hexdigest()
        self.assertEqual(entry["sha256"], expected)

    def test_size_bytes_correct(self):
        self.write("README.md", "```bash\nx\n```\n")
        self.write("a.txt", "12345")
        report, _ = bi.build_report(self.root)
        entry = self._file_entry(report, "a.txt")
        self.assertEqual(entry["size_bytes"], 5)

    def test_binary_file_line_count_is_null_not_zero(self):
        self.write("README.md", "```bash\nx\n```\n")
        self.write("b.bin", bytes([0, 1, 2, 255, 254]), binary=True)
        report, _ = bi.build_report(self.root)
        entry = self._file_entry(report, "b.bin")
        self.assertIsNone(entry["line_count"])
        self.assertEqual(entry["detected_type"], "binary")

    def test_empty_file_detected_type_and_line_count(self):
        self.write("README.md", "```bash\nx\n```\n")
        self.write("empty.txt", "")
        report, _ = bi.build_report(self.root)
        entry = self._file_entry(report, "empty.txt")
        self.assertEqual(entry["detected_type"], "empty")
        self.assertEqual(entry["line_count"], 0)
        self.assertEqual(entry["size_bytes"], 0)

    def test_no_trailing_newline_line_count(self):
        self.write("README.md", "```bash\nx\n```\n")
        self.write("a.txt", "line1\nline2\nline3")
        report, _ = bi.build_report(self.root)
        entry = self._file_entry(report, "a.txt")
        self.assertEqual(entry["line_count"], 3)

    def test_crlf_line_count(self):
        self.write("README.md", "```bash\nx\n```\n")
        self.write("a.txt", "line1\r\nline2\r\n", binary=False)
        report, _ = bi.build_report(self.root)
        entry = self._file_entry(report, "a.txt")
        self.assertEqual(entry["line_count"], 2)

    def test_unicode_content_hashed_and_counted(self):
        self.write("README.md", "```bash\nx\n```\n")
        self.write("u.txt", "café\nrésumé\n")
        report, _ = bi.build_report(self.root)
        entry = self._file_entry(report, "u.txt")
        self.assertEqual(entry["detected_type"], "text")
        self.assertEqual(entry["line_count"], 2)


# ---------------------------------------------------------------------
# build_report - findings
# ---------------------------------------------------------------------

class TestBuildReportFindings(TempDirCase):
    def _codes(self, report):
        return sorted(f["code"] for f in report["findings"])

    def test_missing_readme_finding(self):
        self.write("a.txt", "x")
        report, code = bi.build_report(self.root)
        self.assertIn("MISSING_README", self._codes(report))
        self.assertEqual(code, 1)

    def test_missing_readme_implies_no_rerun_block(self):
        self.write("a.txt", "x")
        report, _ = bi.build_report(self.root)
        self.assertIn("NO_RERUN_BLOCK", self._codes(report))
        self.assertEqual(report["rerun_command"], {"found": False, "language": None, "text": None})

    def test_readme_present_without_rerun_block(self):
        self.write("README.md", "just prose, no fenced block\n")
        report, _ = bi.build_report(self.root)
        codes = self._codes(report)
        self.assertIn("NO_RERUN_BLOCK", codes)
        self.assertNotIn("MISSING_README", codes)

    def test_readme_present_with_rerun_block_no_finding(self):
        self.write("README.md", "```bash\necho hi\n```\n")
        report, _ = bi.build_report(self.root)
        codes = self._codes(report)
        self.assertNotIn("NO_RERUN_BLOCK", codes)
        self.assertNotIn("MISSING_README", codes)

    def test_empty_file_finding(self):
        self.write("README.md", "```bash\nx\n```\n")
        self.write("empty.txt", "")
        report, _ = bi.build_report(self.root)
        self.assertIn("EMPTY_FILE", self._codes(report))

    def test_duplicate_content_finding_reports_both_paths(self):
        self.write("README.md", "```bash\nx\n```\n")
        self.write("a.txt", "same content")
        self.write("b.txt", "same content")
        report, _ = bi.build_report(self.root)
        dupes = [f for f in report["findings"] if f["code"] == "DUPLICATE_CONTENT"]
        self.assertEqual(len(dupes), 1)
        self.assertEqual(dupes[0]["paths"], ["a.txt", "b.txt"])

    def test_duplicate_content_three_way(self):
        self.write("README.md", "```bash\nx\n```\n")
        self.write("a.txt", "same")
        self.write("b.txt", "same")
        self.write("c.txt", "same")
        report, _ = bi.build_report(self.root)
        dupes = [f for f in report["findings"] if f["code"] == "DUPLICATE_CONTENT"]
        self.assertEqual(len(dupes), 1)
        self.assertEqual(dupes[0]["paths"], ["a.txt", "b.txt", "c.txt"])

    def test_no_false_positive_duplicate_for_distinct_content(self):
        self.write("README.md", "```bash\nx\n```\n")
        self.write("a.txt", "one")
        self.write("b.txt", "two")
        report, _ = bi.build_report(self.root)
        self.assertNotIn("DUPLICATE_CONTENT", self._codes(report))

    def test_suspicious_pycache_finding(self):
        self.write("README.md", "```bash\nx\n```\n")
        self.write("__pycache__/m.pyc", bytes([1, 2, 3]), binary=True)
        report, _ = bi.build_report(self.root)
        self.assertIn("SUSPICIOUS_ARTIFACT", self._codes(report))

    def test_suspicious_ds_store_finding(self):
        self.write("README.md", "```bash\nx\n```\n")
        self.write(".DS_Store", bytes([1, 2, 3]), binary=True)
        report, _ = bi.build_report(self.root)
        self.assertIn("SUSPICIOUS_ARTIFACT", self._codes(report))

    def test_suspicious_git_finding(self):
        self.write("README.md", "```bash\nx\n```\n")
        self.write(".git/HEAD", "ref: refs/heads/main\n")
        report, _ = bi.build_report(self.root)
        self.assertIn("SUSPICIOUS_ARTIFACT", self._codes(report))

    def test_clean_bundle_has_zero_findings(self):
        self.write("README.md", "```bash\necho hi\n```\n")
        self.write("a.txt", "content\n")
        report, code = bi.build_report(self.root)
        self.assertEqual(report["findings"], [])
        self.assertEqual(code, 0)

    @unittest.skipUnless(HAS_SYMLINK, "platform has no symlink support")
    def test_unreadable_broken_symlink_finding(self):
        self.write("README.md", "```bash\nx\n```\n")
        link = self.root / "broken.txt"
        if not _make_symlink("nowhere.txt", str(link)):
            self.skipTest("symlink creation not permitted in this environment")
        report, code = bi.build_report(self.root)
        self.assertIn("UNREADABLE_FILE", self._codes(report))
        self.assertEqual(code, 1)
        entry = [f for f in report["files"] if f["relative_path"] == "broken.txt"][0]
        self.assertIsNone(entry["sha256"])
        self.assertIsNone(entry["size_bytes"])
        self.assertIsNone(entry["line_count"])
        self.assertEqual(entry["detected_type"], "unreadable")

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                      "permission bits not meaningfully enforced (root or non-POSIX platform)")
    def test_unreadable_permission_denied_finding(self):
        self.write("README.md", "```bash\nx\n```\n")
        p = self.write("secret.txt", "shh")
        os.chmod(str(p), 0)
        try:
            report, code = bi.build_report(self.root)
        finally:
            os.chmod(str(p), stat.S_IRUSR | stat.S_IWUSR)
        codes = [f["code"] for f in report["findings"]]
        self.assertIn("UNREADABLE_FILE", codes)

    def test_unreadable_finding_detail_has_no_absolute_path(self):
        report_bytes = None
        self.write("README.md", "```bash\nx\n```\n")
        link = self.root / "broken.txt"
        if HAS_SYMLINK and _make_symlink("nowhere.txt", str(link)):
            report, _ = bi.build_report(self.root)
            out = bi.canonical_json_bytes(report)
            self.assertNotIn(str(self.root).encode("utf-8"), out)
        else:
            self.skipTest("symlink creation not permitted in this environment")


# ---------------------------------------------------------------------
# determinism / no-absolute-paths contract
# ---------------------------------------------------------------------

class TestDeterminismContract(TempDirCase):
    def _populate(self, root):
        (root / "src").mkdir(parents=True, exist_ok=True)
        with open(root / "README.md", "w") as fh:
            fh.write("# Bundle\n\n## Rerun\n\n```bash\npython3 -m unittest -v\n```\n")
        with open(root / "src" / "a.py", "w") as fh:
            fh.write("x = 1\n")
        with open(root / "notes.txt", "w") as fh:
            fh.write("hello\n")

    def test_two_runs_same_root_byte_identical(self):
        self._populate(self.root)
        r1, _ = bi.build_report(self.root)
        r2, _ = bi.build_report(self.root)
        self.assertEqual(bi.canonical_json_bytes(r1), bi.canonical_json_bytes(r2))

    def test_report_contains_no_absolute_path_of_root(self):
        self._populate(self.root)
        report, _ = bi.build_report(self.root)
        out = bi.canonical_json_bytes(report)
        self.assertNotIn(str(self.root).encode("utf-8"), out)
        self.assertNotIn(str(self.root.resolve()).encode("utf-8"), out)

    def test_report_has_no_common_absolute_prefixes(self):
        self._populate(self.root)
        report, _ = bi.build_report(self.root)
        out = bi.canonical_json_bytes(report)
        for prefix in (b"/tmp", b"/home", b"/sessions", b"/Users", b"C:\\\\"):
            self.assertNotIn(prefix, out)

    def test_relocation_produces_byte_identical_report(self):
        self._populate(self.root)
        report_a, _ = bi.build_report(self.root)
        bytes_a = bi.canonical_json_bytes(report_a)

        other_tmp = tempfile.mkdtemp(prefix="bundle_index_relocated_")
        try:
            relocated_root = Path(other_tmp) / "relocated_bundle"
            shutil.copytree(str(self.root), str(relocated_root))
            report_b, _ = bi.build_report(relocated_root)
            bytes_b = bi.canonical_json_bytes(report_b)
            self.assertEqual(bytes_a, bytes_b)
        finally:
            shutil.rmtree(other_tmp, ignore_errors=True)

    def test_relocation_at_very_different_depth(self):
        self._populate(self.root)
        report_a, _ = bi.build_report(self.root)
        bytes_a = bi.canonical_json_bytes(report_a)

        other_tmp = tempfile.mkdtemp(prefix="bi_deep_")
        try:
            deep_root = Path(other_tmp) / "a" / "b" / "c" / "d" / "bundle"
            shutil.copytree(str(self.root), str(deep_root))
            report_b, _ = bi.build_report(deep_root)
            self.assertEqual(bytes_a, bi.canonical_json_bytes(report_b))
        finally:
            shutil.rmtree(other_tmp, ignore_errors=True)

    def test_no_mtime_or_ctime_fields_present(self):
        self._populate(self.root)
        report, _ = bi.build_report(self.root)
        blob = json.dumps(report)
        for token in ("mtime", "ctime", "atime", "hostname", "uid", "gid", "owner"):
            self.assertNotIn(token, blob)


# ---------------------------------------------------------------------
# real fixture bundles
# ---------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).resolve().parent


@unittest.skipUnless((FIXTURES_DIR / "bundle_ok").is_dir(), "bundle_ok fixture not present")
class TestBundleOkFixture(unittest.TestCase):
    def test_exit_code_zero(self):
        report, code = bi.build_report(FIXTURES_DIR / "bundle_ok")
        self.assertEqual(code, 0)

    def test_zero_findings(self):
        report, _ = bi.build_report(FIXTURES_DIR / "bundle_ok")
        self.assertEqual(report["findings"], [])

    def test_rerun_block_found(self):
        report, _ = bi.build_report(FIXTURES_DIR / "bundle_ok")
        self.assertTrue(report["rerun_command"]["found"])

    def test_contains_expected_file_types(self):
        report, _ = bi.build_report(FIXTURES_DIR / "bundle_ok")
        types = {f["detected_type"] for f in report["files"]}
        self.assertIn("python", types)
        self.assertIn("json", types)
        self.assertIn("text", types)
        self.assertIn("markdown", types)


@unittest.skipUnless((FIXTURES_DIR / "bundle_bad").is_dir(), "bundle_bad fixture not present")
class TestBundleBadFixture(unittest.TestCase):
    def test_exit_code_one(self):
        report, code = bi.build_report(FIXTURES_DIR / "bundle_bad")
        self.assertEqual(code, 1)

    def test_has_findings(self):
        report, _ = bi.build_report(FIXTURES_DIR / "bundle_bad")
        self.assertGreater(report["finding_count"], 0)

    def test_missing_readme_present(self):
        report, _ = bi.build_report(FIXTURES_DIR / "bundle_bad")
        codes = {f["code"] for f in report["findings"]}
        self.assertIn("MISSING_README", codes)

    def test_all_six_finding_codes_present(self):
        report, _ = bi.build_report(FIXTURES_DIR / "bundle_bad")
        codes = {f["code"] for f in report["findings"]}
        expected = {
            "MISSING_README", "NO_RERUN_BLOCK", "EMPTY_FILE", "UNREADABLE_FILE",
            "DUPLICATE_CONTENT", "SUSPICIOUS_ARTIFACT",
        }
        self.assertEqual(codes, expected)

    def test_report_byte_identical_across_runs(self):
        r1, _ = bi.build_report(FIXTURES_DIR / "bundle_bad")
        r2, _ = bi.build_report(FIXTURES_DIR / "bundle_bad")
        self.assertEqual(bi.canonical_json_bytes(r1), bi.canonical_json_bytes(r2))


# ---------------------------------------------------------------------
# CLI-level (subprocess) tests
# ---------------------------------------------------------------------

TOOL_PATH = str(Path(__file__).resolve().parent / "bundle_index.py")


def run_cli(args, cwd=None):
    proc = subprocess.run(
        [sys.executable, TOOL_PATH] + args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc.returncode, proc.stdout, proc.stderr


class TestCliExitCodes(TempDirCase):
    def test_clean_bundle_exit_zero(self):
        (self.root / "README.md").write_text("```bash\necho hi\n```\n")
        (self.root / "a.txt").write_text("hi\n")
        code, out, err = run_cli([str(self.root)])
        self.assertEqual(code, 0)
        json.loads(out.decode("utf-8"))

    def test_findings_bundle_exit_one(self):
        (self.root / "a.txt").write_text("hi\n")
        code, out, err = run_cli([str(self.root)])
        self.assertEqual(code, 1)

    def test_nonexistent_dir_exit_two(self):
        code, out, err = run_cli([str(self.root / "nope")])
        self.assertEqual(code, 2)
        self.assertEqual(out, b"")
        self.assertIn(b"input error", err)

    def test_missing_argument_exit_two(self):
        code, out, err = run_cli([])
        self.assertEqual(code, 2)

    def test_target_is_a_file_not_directory_exit_two(self):
        f = self.root / "plain.txt"
        f.write_text("x")
        code, out, err = run_cli([str(f)])
        self.assertEqual(code, 2)

    def test_output_flag_writes_file(self):
        (self.root / "README.md").write_text("```bash\necho hi\n```\n")
        out_path = self.root / "report.json"
        code, out, err = run_cli([str(self.root), "-o", str(out_path)])
        self.assertEqual(code, 0)
        self.assertTrue(out_path.is_file())

    def test_output_flag_file_matches_stdout(self):
        (self.root / "README.md").write_text("```bash\necho hi\n```\n")
        out_path = self.root / "report.json"
        code, out, err = run_cli([str(self.root), "-o", str(out_path)])
        self.assertEqual(out, out_path.read_bytes())

    def test_output_flag_creates_parent_dirs(self):
        (self.root / "README.md").write_text("```bash\necho hi\n```\n")
        out_path = self.root / "nested" / "deep" / "report.json"
        code, out, err = run_cli([str(self.root), "-o", str(out_path)])
        self.assertEqual(code, 0)
        self.assertTrue(out_path.is_file())

    def test_two_runs_produce_byte_identical_output_files(self):
        # -o targets must live OUTSIDE the indexed directory: writing the
        # report inside the bundle would make the second run see a bundle
        # with one more file than the first run indexed.
        (self.root / "README.md").write_text("```bash\necho hi\n```\n")
        (self.root / "a.txt").write_text("data\n")
        scratch = tempfile.mkdtemp(prefix="bundle_index_scratch_")
        try:
            out1 = Path(scratch) / "r1.json"
            out2 = Path(scratch) / "r2.json"
            run_cli([str(self.root), "-o", str(out1)])
            run_cli([str(self.root), "-o", str(out2)])
            self.assertEqual(out1.read_bytes(), out2.read_bytes())
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def test_long_form_output_flag(self):
        (self.root / "README.md").write_text("```bash\necho hi\n```\n")
        out_path = self.root / "report.json"
        code, out, err = run_cli([str(self.root), "--output", str(out_path)])
        self.assertEqual(code, 0)
        self.assertTrue(out_path.is_file())

    def test_stdout_is_valid_canonical_json_with_trailing_newline(self):
        (self.root / "README.md").write_text("```bash\necho hi\n```\n")
        code, out, err = run_cli([str(self.root)])
        self.assertTrue(out.endswith(b"\n"))
        self.assertFalse(out.endswith(b"\n\n"))
        json.loads(out.decode("utf-8"))


class TestMainFunctionDirect(TempDirCase):
    def test_main_raises_systemexit_on_missing_argv(self):
        with self.assertRaises(SystemExit) as cm:
            bi.main([])
        self.assertEqual(cm.exception.code, 2)

    def test_main_returns_zero_for_clean_bundle(self):
        (self.root / "README.md").write_text("```bash\nx\n```\n")
        code = bi.main([str(self.root)])
        self.assertEqual(code, 0)

    def test_main_returns_one_for_findings_bundle(self):
        (self.root / "a.txt").write_text("x")
        code = bi.main([str(self.root)])
        self.assertEqual(code, 1)

    def test_main_returns_two_for_missing_dir(self):
        code = bi.main([str(self.root / "missing")])
        self.assertEqual(code, 2)


# ---------------------------------------------------------------------
# extra edge cases called out in the task spec
# ---------------------------------------------------------------------

class TestSpecialEdgeCases(TempDirCase):
    def test_file_that_is_exactly_one_newline_byte(self):
        self.write("README.md", "```bash\nx\n```\n")
        self.write("blank.txt", "\n")
        report, _ = bi.build_report(self.root)
        entry = [f for f in report["files"] if f["relative_path"] == "blank.txt"][0]
        self.assertEqual(entry["line_count"], 1)
        self.assertEqual(entry["size_bytes"], 1)

    def test_unicode_filename_roundtrips_through_json(self):
        self.write("README.md", "```bash\nx\n```\n")
        self.write("日本語.txt", "hi\n")
        report, _ = bi.build_report(self.root)
        out = bi.canonical_json_bytes(report)
        parsed = json.loads(out.decode("utf-8"))
        paths = [f["relative_path"] for f in parsed["files"]]
        self.assertIn("日本語.txt", paths)

    def test_deeply_nested_dirs_all_discovered(self):
        self.write("README.md", "```bash\nx\n```\n")
        self.write("a/b/c/d/e/f/g/h/deep.txt", "content\n")
        report, _ = bi.build_report(self.root)
        paths = [f["relative_path"] for f in report["files"]]
        self.assertIn("a/b/c/d/e/f/g/h/deep.txt", paths)

    def test_readme_with_multiple_fenced_blocks_picks_first_bash(self):
        text = (
            "# Title\n\n```json\n{}\n```\n\n```python\npass\n```\n\n"
            "```bash\nreal command here\n```\n\n```bash\nsecond bash block\n```\n"
        )
        self.write("README.md", text)
        self.write("a.txt", "x\n")
        report, _ = bi.build_report(self.root)
        self.assertEqual(report["rerun_command"]["text"], "real command here")

    def test_empty_directory_present_but_invisible(self):
        (self.root / "empty_subdir").mkdir()
        self.write("README.md", "```bash\nx\n```\n")
        report, _ = bi.build_report(self.root)
        paths = [f["relative_path"] for f in report["files"]]
        self.assertNotIn("empty_subdir", paths)
        self.assertEqual(report["finding_count"], 0)

    def test_binary_readme_does_not_crash(self):
        self.write("README.md", bytes([0, 1, 2, 255]), binary=True)
        self.write("a.txt", "x\n")
        report, code = bi.build_report(self.root)
        self.assertEqual(report["rerun_command"]["found"], False)
        self.assertEqual(code, 1)

    def test_readme_itself_is_indexed_as_a_file(self):
        self.write("README.md", "```bash\nx\n```\n")
        report, _ = bi.build_report(self.root)
        paths = [f["relative_path"] for f in report["files"]]
        self.assertIn("README.md", paths)

    def test_lowercase_readme_not_recognised(self):
        self.write("readme.md", "```bash\nx\n```\n")
        report, _ = bi.build_report(self.root)
        codes = {f["code"] for f in report["findings"]}
        self.assertIn("MISSING_README", codes)

    def test_nested_readme_not_recognised_as_bundle_readme(self):
        self.write("docs/README.md", "```bash\nx\n```\n")
        report, _ = bi.build_report(self.root)
        codes = {f["code"] for f in report["findings"]}
        self.assertIn("MISSING_README", codes)

    def test_many_small_files_all_indexed(self):
        self.write("README.md", "```bash\nx\n```\n")
        for i in range(30):
            self.write("file_%02d.txt" % i, "content %d\n" % i)
        report, _ = bi.build_report(self.root)
        self.assertEqual(report["file_count"], 31)

    def test_bytes_with_only_null_and_ascii_mixed(self):
        self.write("README.md", "```bash\nx\n```\n")
        self.write("mixed.dat", b"abc\x00def", binary=True)
        report, _ = bi.build_report(self.root)
        entry = [f for f in report["files"] if f["relative_path"] == "mixed.dat"][0]
        self.assertEqual(entry["detected_type"], "binary")
        self.assertIsNone(entry["line_count"])


if __name__ == "__main__":
    unittest.main()
