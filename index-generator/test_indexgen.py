"""Test suite for indexgen.py.

Run with:  python3 -m unittest test_indexgen -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

import indexgen


THIS_DIR = os.path.dirname(os.path.abspath(__file__))
INDEXGEN_PATH = os.path.join(THIS_DIR, "indexgen.py")


# --------------------------------------------------------------------------
# Fixture helpers -- every TemporaryDirectory is the exact dir we clean up.
# --------------------------------------------------------------------------

def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def write(path, content, encoding="utf-8"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(content, bytes):
        with open(path, "wb") as fh:
            fh.write(content)
    else:
        with open(path, "w", encoding=encoding, newline="") as fh:
            fh.write(content)


def make_tool_dir(root, name, py_files=None, test_files=None, readme=None,
                   captured=True, entrypoint_body="pass\n"):
    tool_dir = os.path.join(root, name)
    os.makedirs(tool_dir, exist_ok=True)
    for f in (py_files or [name + ".py"]):
        write(os.path.join(tool_dir, f), "def main():\n    %s" % entrypoint_body)
    for f in (test_files if test_files is not None else ["test_%s.py" % name]):
        write(os.path.join(tool_dir, f), "def test_ok():\n    assert True\n")
    if readme is not None:
        write(os.path.join(tool_dir, "README.md"), readme)
    if captured:
        write(os.path.join(tool_dir, "captured_output.txt"), "ok\n")
    return tool_dir


class TempRepo(object):
    """Context manager wrapping tempfile.TemporaryDirectory for clarity."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="indexgen_test_")
        return self._tmp.name

    def __exit__(self, *exc):
        self._tmp.cleanup()
        return False


# ==========================================================================
# Description extraction
# ==========================================================================

class DescriptionExtractionTests(unittest.TestCase):

    def test_plain_first_line(self):
        self.assertEqual(indexgen.extract_description("Hello there.\nmore\n"), "Hello there.")

    def test_skips_leading_blank_lines(self):
        self.assertEqual(indexgen.extract_description("\n\n   \nActual line.\n"), "Actual line.")

    def test_skips_heading_no_subtitle(self):
        text = "# ToolName\n\nA real description.\n"
        self.assertEqual(indexgen.extract_description(text), "A real description.")

    def test_h1_subtitle_dash(self):
        text = "# ToolName - subtitle text\n\nother line\n"
        self.assertEqual(indexgen.extract_description(text), "subtitle text")

    def test_h1_subtitle_colon(self):
        text = "# ToolName: subtitle via colon\n"
        self.assertEqual(indexgen.extract_description(text), "subtitle via colon")

    def test_h1_subtitle_emdash(self):
        text = "# ToolName — subtitle via emdash\n"
        self.assertEqual(indexgen.extract_description(text), "subtitle via emdash")

    def test_h1_no_subtitle_falls_through_to_next_line(self):
        text = "# JustATitle\nNext plain line.\n"
        self.assertEqual(indexgen.extract_description(text), "Next plain line.")

    def test_h2_heading_skipped(self):
        text = "## Section\nReal desc.\n"
        self.assertEqual(indexgen.extract_description(text), "Real desc.")

    def test_badge_markdown_image_skipped(self):
        text = "![badge](http://example.com/badge.svg)\nReal desc.\n"
        self.assertEqual(indexgen.extract_description(text), "Real desc.")

    def test_badge_linked_image_skipped(self):
        text = "[![build](http://example.com/b.svg)](http://example.com)\nReal desc.\n"
        self.assertEqual(indexgen.extract_description(text), "Real desc.")

    def test_badge_shields_io_skipped(self):
        text = "See https://img.shields.io/badge/x-y-green for status.\nReal desc.\n"
        self.assertEqual(indexgen.extract_description(text), "Real desc.")

    def test_badge_html_img_skipped(self):
        text = '<img src="badge.png">\nReal desc.\n'
        self.assertEqual(indexgen.extract_description(text), "Real desc.")

    def test_empty_readme_returns_none(self):
        self.assertIsNone(indexgen.extract_description(""))

    def test_whitespace_only_readme_returns_none(self):
        self.assertIsNone(indexgen.extract_description("   \n\n\t\n"))

    def test_only_headings_returns_none(self):
        self.assertIsNone(indexgen.extract_description("# Title\n## Sub\n### Sub2\n"))

    def test_only_badges_returns_none(self):
        self.assertIsNone(indexgen.extract_description("![b](x.svg)\n[![b2](y.svg)](z)\n"))

    def test_multiple_badges_then_heading_then_text(self):
        text = "![b](x.svg)\n[![b2](y.svg)](z)\n# Title\nDescription here.\n"
        self.assertEqual(indexgen.extract_description(text), "Description here.")

    def test_h1_subtitle_prefers_dash_over_colon_when_both_absent_defaults(self):
        # Only colon present -> colon path used.
        text = "# Name: desc with colon\n"
        self.assertEqual(indexgen.extract_description(text), "desc with colon")

    def test_unicode_description(self):
        text = "# 名前\n\n日本語の説明です。\n"
        self.assertEqual(indexgen.extract_description(text), "日本語の説明です。")

    def test_crlf_line_endings(self):
        text = "Line one.\r\nLine two.\r\n"
        self.assertEqual(indexgen.extract_description(text), "Line one.")

    def test_h1_subtitle_empty_after_separator_falls_through(self):
        text = "# Title - \nReal one.\n"
        self.assertEqual(indexgen.extract_description(text), "Real one.")

    def test_leading_whitespace_on_line_is_stripped(self):
        text = "    Indented description.\n"
        self.assertEqual(indexgen.extract_description(text), "Indented description.")


# ==========================================================================
# Claimed test count extraction
# ==========================================================================

class ClaimedTestCountTests(unittest.TestCase):

    def test_simple_n_tests(self):
        self.assertEqual(indexgen.extract_claimed_test_count("This has 42 tests total."), 42)

    def test_tests_colon(self):
        self.assertEqual(indexgen.extract_claimed_test_count("Tests: 17"), 17)

    def test_tests_equals(self):
        self.assertEqual(indexgen.extract_claimed_test_count("tests=9"), 9)

    def test_ran_n_tests(self):
        self.assertEqual(indexgen.extract_claimed_test_count("Ran 55 tests OK"), 55)

    def test_fraction_form(self):
        self.assertEqual(indexgen.extract_claimed_test_count("42/42 tests passing"), 42)

    def test_singular_test_word(self):
        self.assertEqual(indexgen.extract_claimed_test_count("1 test passed"), 1)

    def test_no_count_present(self):
        self.assertIsNone(indexgen.extract_claimed_test_count("No numbers here at all."))

    def test_number_without_tests_word_not_matched(self):
        self.assertIsNone(indexgen.extract_claimed_test_count("Version 42 released."))

    def test_first_occurrence_wins(self):
        text = "Ran 5 tests, then later 99 tests were mentioned."
        self.assertEqual(indexgen.extract_claimed_test_count(text), 5)

    def test_case_insensitive(self):
        self.assertEqual(indexgen.extract_claimed_test_count("TESTS: 8"), 8)

    def test_multiline_search(self):
        text = "Intro line.\nMore text.\n120 tests all green.\n"
        self.assertEqual(indexgen.extract_claimed_test_count(text), 120)


class ClaimedToolCountTests(unittest.TestCase):

    def test_simple_n_tools(self):
        self.assertEqual(indexgen.extract_claimed_tool_count("13 tools in this repo"), 13)

    def test_tools_colon(self):
        self.assertEqual(indexgen.extract_claimed_tool_count("Tools: 33"), 33)

    def test_singular_tool(self):
        self.assertEqual(indexgen.extract_claimed_tool_count("1 tool included"), 1)

    def test_no_tool_count(self):
        self.assertIsNone(indexgen.extract_claimed_tool_count("nothing relevant"))

    def test_case_insensitive_tools(self):
        self.assertEqual(indexgen.extract_claimed_tool_count("TOOLS: 7"), 7)


# ==========================================================================
# Discovery
# ==========================================================================

class DiscoveryTests(unittest.TestCase):

    def test_empty_root_yields_no_tools(self):
        with TempRepo() as root:
            findings = []
            tools = indexgen.discover_tools(root, findings)
            self.assertEqual(tools, [])
            self.assertEqual(findings, [])

    def test_dir_with_only_test_file_is_not_a_tool(self):
        with TempRepo() as root:
            d = os.path.join(root, "onlytests")
            os.makedirs(d)
            write(os.path.join(d, "test_foo.py"), "def test_x(): pass\n")
            tools = indexgen.discover_tools(root, [])
            self.assertEqual(tools, [])

    def test_dir_with_only_non_py_files_is_not_a_tool(self):
        with TempRepo() as root:
            d = os.path.join(root, "notool")
            os.makedirs(d)
            write(os.path.join(d, "notes.txt"), "hi\n")
            tools = indexgen.discover_tools(root, [])
            self.assertEqual(tools, [])

    def test_basic_tool_discovered(self):
        with TempRepo() as root:
            make_tool_dir(root, "alpha", readme="Alpha desc.\n5 tests.\n")
            tools = indexgen.discover_tools(root, [])
            self.assertEqual(len(tools), 1)
            self.assertEqual(tools[0]["dir"], "alpha")
            self.assertEqual(tools[0]["entrypoints"], ["alpha.py"])
            self.assertEqual(tools[0]["test_modules"], ["test_alpha.py"])
            self.assertTrue(tools[0]["has_readme"])
            self.assertTrue(tools[0]["has_captured_output"])
            self.assertEqual(tools[0]["claimed_test_count"], 5)

    def test_nested_dirs_two_levels_deep_ignored(self):
        with TempRepo() as root:
            make_tool_dir(root, "alpha")
            nested = os.path.join(root, "alpha", "nested_tool")
            os.makedirs(nested)
            write(os.path.join(nested, "nested.py"), "pass\n")
            tools = indexgen.discover_tools(root, [])
            # nested_tool must not appear as a separate top-level tool.
            self.assertEqual([t["dir"] for t in tools], ["alpha"])

    def test_multiple_entrypoints_recorded(self):
        with TempRepo() as root:
            make_tool_dir(root, "multi", py_files=["multi.py", "helper.py"],
                          test_files=["test_multi.py", "test_helper.py"])
            tools = indexgen.discover_tools(root, [])
            self.assertEqual(tools[0]["entrypoints"], ["helper.py", "multi.py"])
            self.assertEqual(tools[0]["test_modules"], ["test_helper.py", "test_multi.py"])

    def test_files_at_root_level_ignored_only_dirs_scanned(self):
        with TempRepo() as root:
            write(os.path.join(root, "loose.py"), "pass\n")
            make_tool_dir(root, "alpha")
            tools = indexgen.discover_tools(root, [])
            self.assertEqual([t["dir"] for t in tools], ["alpha"])

    def test_tools_sorted_by_dir_name(self):
        with TempRepo() as root:
            make_tool_dir(root, "zeta")
            make_tool_dir(root, "alpha")
            make_tool_dir(root, "mu")
            tools = indexgen.discover_tools(root, [])
            self.assertEqual([t["dir"] for t in tools], ["alpha", "mu", "zeta"])

    def test_no_readme_description_and_count_are_none(self):
        with TempRepo() as root:
            make_tool_dir(root, "bare", readme=None)
            tools = indexgen.discover_tools(root, [])
            self.assertIsNone(tools[0]["description"])
            self.assertIsNone(tools[0]["claimed_test_count"])
            self.assertFalse(tools[0]["has_readme"])

    def test_no_captured_output_flag_false(self):
        with TempRepo() as root:
            make_tool_dir(root, "nocap", captured=False)
            tools = indexgen.discover_tools(root, [])
            self.assertFalse(tools[0]["has_captured_output"])

    def test_test_prefixed_file_not_counted_as_entrypoint(self):
        with TempRepo() as root:
            d = os.path.join(root, "onlytest2")
            os.makedirs(d)
            write(os.path.join(d, "real.py"), "pass\n")
            write(os.path.join(d, "test_real.py"), "pass\n")
            tools = indexgen.discover_tools(root, [])
            self.assertEqual(tools[0]["entrypoints"], ["real.py"])
            self.assertNotIn("test_real.py", tools[0]["entrypoints"])

    def test_symlink_like_subdirectory_of_tool_ignored_for_entrypoint_detection(self):
        with TempRepo() as root:
            d = os.path.join(root, "withsub")
            os.makedirs(d)
            write(os.path.join(d, "main.py"), "pass\n")
            write(os.path.join(d, "test_main.py"), "pass\n")
            sub = os.path.join(d, "fixtures")
            os.makedirs(sub)
            write(os.path.join(sub, "data.py"), "pass\n")
            tools = indexgen.discover_tools(root, [])
            self.assertEqual(tools[0]["entrypoints"], ["main.py"])

    def test_bad_root_raises_value_error(self):
        with self.assertRaises(ValueError):
            indexgen.discover_tools("/definitely/not/a/real/path/xyz", [])

    def test_readme_as_directory_is_not_treated_as_a_readme_file(self):
        # A directory literally named README.md is not a regular file, so
        # it must be treated the same as "no README.md" (has_readme=False,
        # -> MISSING_README finding), not silently accepted nor crashed on.
        with TempRepo() as root:
            d = os.path.join(root, "weird")
            os.makedirs(d)
            write(os.path.join(d, "weird.py"), "pass\n")
            write(os.path.join(d, "test_weird.py"), "pass\n")
            os.makedirs(os.path.join(d, "README.md"))
            findings = []
            tools = indexgen.discover_tools(root, findings)
            self.assertEqual(len(tools), 1)
            self.assertFalse(tools[0]["has_readme"])
            self.assertIsNone(tools[0]["description"])
            tool_findings = indexgen.check_tool_findings(tools[0])
            codes = [f["code"] for f in tool_findings]
            self.assertIn("MISSING_README", codes)

    def test_invalid_utf8_readme_reported_and_skipped(self):
        with TempRepo() as root:
            d = os.path.join(root, "badenc")
            os.makedirs(d)
            write(os.path.join(d, "badenc.py"), "pass\n")
            write(os.path.join(d, "test_badenc.py"), "pass\n")
            write(os.path.join(d, "README.md"), b"\xff\xfe not valid utf8 \x80\x81")
            findings = []
            tools = indexgen.discover_tools(root, findings)
            self.assertEqual(len(tools), 1)
            self.assertIsNone(tools[0]["description"])
            self.assertIsNone(tools[0]["claimed_test_count"])
            codes = [f["code"] for f in findings]
            self.assertIn("UNREADABLE_FILE", codes)

    def test_directory_named_readme_does_not_crash_discovery(self):
        with TempRepo() as root:
            d = os.path.join(root, "weird2")
            os.makedirs(d)
            write(os.path.join(d, "weird2.py"), "pass\n")
            write(os.path.join(d, "test_weird2.py"), "pass\n")
            os.makedirs(os.path.join(d, "README.md"))
            tools = indexgen.discover_tools(root, [])  # must not raise
            self.assertEqual(len(tools), 1)


# ==========================================================================
# Per-tool finding checks
# ==========================================================================

class ToolFindingTests(unittest.TestCase):

    def _tool(self, **overrides):
        base = {
            "dir": "sample",
            "entrypoints": ["sample.py"],
            "test_modules": ["test_sample.py"],
            "has_readme": True,
            "has_captured_output": True,
            "description": "desc",
            "claimed_test_count": 3,
        }
        base.update(overrides)
        return base

    def test_missing_readme_finding(self):
        findings = indexgen.check_tool_findings(self._tool(has_readme=False, description=None, claimed_test_count=None))
        codes = [f["code"] for f in findings]
        self.assertIn("MISSING_README", codes)
        # readme absent -> no separate NO_DESCRIPTION/NO_CLAIMED_TEST_COUNT
        self.assertNotIn("NO_DESCRIPTION", codes)
        self.assertNotIn("NO_CLAIMED_TEST_COUNT", codes)

    def test_missing_captured_output_finding(self):
        findings = indexgen.check_tool_findings(self._tool(has_captured_output=False))
        codes = [f["code"] for f in findings]
        self.assertIn("MISSING_CAPTURED_OUTPUT", codes)

    def test_missing_test_module_finding(self):
        findings = indexgen.check_tool_findings(self._tool(test_modules=[]))
        codes = [f["code"] for f in findings]
        self.assertIn("MISSING_TEST_MODULE", codes)

    def test_no_description_finding(self):
        findings = indexgen.check_tool_findings(self._tool(description=None))
        codes = [f["code"] for f in findings]
        self.assertIn("NO_DESCRIPTION", codes)

    def test_no_claimed_test_count_finding(self):
        findings = indexgen.check_tool_findings(self._tool(claimed_test_count=None))
        codes = [f["code"] for f in findings]
        self.assertIn("NO_CLAIMED_TEST_COUNT", codes)

    def test_entrypoint_test_mismatch_finding(self):
        findings = indexgen.check_tool_findings(self._tool(
            entrypoints=["sample.py", "extra.py"], test_modules=["test_sample.py"]))
        mismatch = [f for f in findings if f["code"] == "ENTRYPOINT_TEST_MISMATCH"]
        self.assertEqual(len(mismatch), 1)
        self.assertIn("extra.py", mismatch[0]["message"])

    def test_no_findings_for_complete_tool(self):
        findings = indexgen.check_tool_findings(self._tool())
        self.assertEqual(findings, [])

    def test_multiple_entrypoints_all_matched_no_mismatch(self):
        findings = indexgen.check_tool_findings(self._tool(
            entrypoints=["sample.py", "extra.py"],
            test_modules=["test_sample.py", "test_extra.py"]))
        self.assertEqual([f for f in findings if f["code"] == "ENTRYPOINT_TEST_MISMATCH"], [])

    def test_all_finding_codes_reachable_simultaneously(self):
        findings = indexgen.check_tool_findings(self._tool(
            has_readme=False, has_captured_output=False, test_modules=[],
            description=None, claimed_test_count=None,
            entrypoints=["sample.py", "extra.py"]))
        codes = set(f["code"] for f in findings)
        self.assertIn("MISSING_README", codes)
        self.assertIn("MISSING_CAPTURED_OUTPUT", codes)
        self.assertIn("MISSING_TEST_MODULE", codes)
        self.assertIn("ENTRYPOINT_TEST_MISMATCH", codes)
        self.assertNotIn("NO_DESCRIPTION", codes)  # no readme => not applicable


# ==========================================================================
# Totals
# ==========================================================================

class TotalsTests(unittest.TestCase):

    def test_totals_empty(self):
        totals = indexgen.compute_totals([])
        self.assertEqual(totals["tool_count"], 0)
        self.assertEqual(totals["test_count_sum"], 0)
        self.assertEqual(totals["test_count_known_tools"], 0)
        self.assertEqual(totals["test_count_unknown_tools"], 0)

    def test_totals_mixed_known_unknown(self):
        tools = [
            {"claimed_test_count": 5},
            {"claimed_test_count": None},
            {"claimed_test_count": 10},
        ]
        totals = indexgen.compute_totals(tools)
        self.assertEqual(totals["tool_count"], 3)
        self.assertEqual(totals["test_count_sum"], 15)
        self.assertEqual(totals["test_count_known_tools"], 2)
        self.assertEqual(totals["test_count_unknown_tools"], 1)

    def test_totals_all_unknown(self):
        tools = [{"claimed_test_count": None}, {"claimed_test_count": None}]
        totals = indexgen.compute_totals(tools)
        self.assertEqual(totals["test_count_sum"], 0)
        self.assertEqual(totals["test_count_unknown_tools"], 2)


# ==========================================================================
# Root README drift
# ==========================================================================

class RootReadmeDriftTests(unittest.TestCase):

    def test_drift_fires_on_mismatched_tool_count(self):
        totals = {"tool_count": 33, "test_count_sum": 100, "test_count_unknown_tools": 0}
        findings = indexgen.check_root_readme_drift("13 tools / 476 tests", totals)
        codes = [f["code"] for f in findings]
        self.assertIn("ROOT_README_COUNT_DRIFT", codes)
        locations = [f["location"] for f in findings]
        self.assertIn("root_readme:tool_count", locations)

    def test_drift_does_not_fire_when_counts_agree(self):
        totals = {"tool_count": 33, "test_count_sum": 476, "test_count_unknown_tools": 0}
        findings = indexgen.check_root_readme_drift("33 tools / 476 tests", totals)
        self.assertEqual(findings, [])

    def test_drift_fires_on_test_total_mismatch_only(self):
        totals = {"tool_count": 5, "test_count_sum": 40, "test_count_unknown_tools": 0}
        findings = indexgen.check_root_readme_drift("5 tools / 99 tests", totals)
        locations = [f["location"] for f in findings]
        self.assertEqual(locations, ["root_readme:test_total"])

    def test_drift_no_claims_no_findings(self):
        totals = {"tool_count": 5, "test_count_sum": 40, "test_count_unknown_tools": 0}
        findings = indexgen.check_root_readme_drift("Just a readme, no numbers.", totals)
        self.assertEqual(findings, [])

    def test_drift_tool_count_only_claim(self):
        totals = {"tool_count": 33, "test_count_sum": 0, "test_count_unknown_tools": 33}
        findings = indexgen.check_root_readme_drift("13 tools total", totals)
        locations = [f["location"] for f in findings]
        self.assertEqual(locations, ["root_readme:tool_count"])

    def test_drift_the_13_vs_33_example(self):
        totals = {"tool_count": 33, "test_count_sum": 476, "test_count_unknown_tools": 0}
        findings = indexgen.check_root_readme_drift(
            "This repository ships 13 tools and 476 tests.", totals)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["location"], "root_readme:tool_count")
        self.assertIn("13", findings[0]["message"])
        self.assertIn("33", findings[0]["message"])


# ==========================================================================
# INDEX.md rendering + parsing
# ==========================================================================

class IndexRenderParseTests(unittest.TestCase):

    def _tool(self, **overrides):
        base = {
            "dir": "sample",
            "entrypoints": ["sample.py"],
            "test_modules": ["test_sample.py"],
            "description": "A sample tool.",
            "claimed_test_count": 3,
        }
        base.update(overrides)
        return base

    def test_render_contains_header(self):
        text = indexgen.render_index([], indexgen.compute_totals([]))
        self.assertIn("# Repository Index", text)
        self.assertIn("| Tool | Description |", text)

    def test_render_row_basic(self):
        tools = [self._tool()]
        text = indexgen.render_index(tools, indexgen.compute_totals(tools))
        self.assertIn("| sample | A sample tool. | sample.py | test_sample.py | 3 |", text)

    def test_render_none_description_placeholder(self):
        tools = [self._tool(description=None)]
        text = indexgen.render_index(tools, indexgen.compute_totals(tools))
        self.assertIn("_(none)_", text)

    def test_render_none_count_placeholder(self):
        tools = [self._tool(claimed_test_count=None)]
        text = indexgen.render_index(tools, indexgen.compute_totals(tools))
        self.assertIn("| ? |", text)

    def test_render_escapes_pipe_in_description(self):
        tools = [self._tool(description="Has a | pipe in it")]
        text = indexgen.render_index(tools, indexgen.compute_totals(tools))
        self.assertIn("Has a \\| pipe in it", text)

    def test_render_totals_line(self):
        tools = [self._tool(), self._tool(dir="other", claimed_test_count=None)]
        totals = indexgen.compute_totals(tools)
        text = indexgen.render_index(tools, totals)
        self.assertIn("**Totals:** 2 tools; test count: 3 (from 1 tool; 1 unknown)", text)

    def test_render_ends_with_single_trailing_newline(self):
        tools = [self._tool()]
        text = indexgen.render_index(tools, indexgen.compute_totals(tools))
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))

    def test_roundtrip_render_then_parse(self):
        tools = [self._tool(), self._tool(dir="zzz", description=None, claimed_test_count=None,
                                            entrypoints=[], test_modules=[])]
        text = indexgen.render_index(tools, indexgen.compute_totals(tools))
        parsed = indexgen.parse_index(text)
        self.assertEqual(set(parsed), {"sample", "zzz"})
        self.assertEqual(parsed["sample"], indexgen.tool_to_row(tools[0]))
        self.assertEqual(parsed["zzz"], indexgen.tool_to_row(tools[1]))

    def test_roundtrip_preserves_escaped_pipe(self):
        tools = [self._tool(description="a | b")]
        text = indexgen.render_index(tools, indexgen.compute_totals(tools))
        parsed = indexgen.parse_index(text)
        self.assertEqual(parsed["sample"][0], "a | b")

    def test_parse_ignores_separator_row(self):
        text = "| --- | --- | --- | --- | --- |\n"
        parsed = indexgen.parse_index(text)
        self.assertEqual(parsed, {})

    def test_parse_ignores_header_row(self):
        text = "| Tool | Description | Entrypoint(s) | Test Module(s) | Claimed Tests |\n"
        parsed = indexgen.parse_index(text)
        self.assertEqual(parsed, {})

    def test_parse_empty_text(self):
        self.assertEqual(indexgen.parse_index(""), {})

    def test_parse_ignores_non_table_lines(self):
        text = "# Title\nSome prose.\n**Totals:** 1 tools\n"
        self.assertEqual(indexgen.parse_index(text), {})

    def test_description_literally_equal_to_none_marker_round_trips(self):
        # BUG (fixed): a tool whose genuinely-extracted description happens
        # to be the exact literal text "_(none)_" (the sentinel used to mean
        # "no description") used to be silently reparsed as None by
        # parse_index, which made --check-index raise a false-positive
        # INDEX_DRIFT on a completely unchanged tree. Pinning test for the
        # fix in _encode_description_cell / _decode_description_cell.
        tools = [self._tool(description="_(none)_")]
        text = indexgen.render_index(tools, indexgen.compute_totals(tools))
        parsed = indexgen.parse_index(text)
        self.assertEqual(parsed["sample"][0], "_(none)_")

    def test_genuine_none_description_still_parses_as_none(self):
        tools = [self._tool(description=None)]
        text = indexgen.render_index(tools, indexgen.compute_totals(tools))
        parsed = indexgen.parse_index(text)
        self.assertIsNone(parsed["sample"][0])

    def test_none_marker_collision_causes_no_false_index_drift_end_to_end(self):
        with TempRepo() as root:
            make_tool_dir(root, "sample", readme="_(none)_\n3 tests.\n")
            idx = os.path.join(root, "INDEX.md")
            indexgen.run(["--root", root, "--write-index", idx, "-o", os.path.join(root, "r1.json")])
            out2 = os.path.join(root, "r2.json")
            rc = indexgen.run(["--root", root, "--check-index", idx, "-o", out2])
            with open(out2, encoding="utf-8") as _fh:
                data = json.load(_fh)
            drift = [f for f in data["findings"] if f["code"] == "INDEX_DRIFT"]
            self.assertEqual(drift, [], "spurious INDEX_DRIFT on an unchanged tree")

    def test_multiple_entrypoints_sorted_in_row(self):
        tools = [self._tool(entrypoints=["b.py", "a.py"], test_modules=["test_b.py", "test_a.py"])]
        text = indexgen.render_index(tools, indexgen.compute_totals(tools))
        self.assertIn("a.py, b.py", text)
        self.assertIn("test_a.py, test_b.py", text)


# ==========================================================================
# Index diff (INDEX_DRIFT)
# ==========================================================================

class IndexDiffTests(unittest.TestCase):

    def test_added_row(self):
        old = {}
        new = {"alpha": (None, (), (), None)}
        findings = indexgen.diff_index(old, new)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["code"], "INDEX_DRIFT")
        self.assertIn("added", findings[0]["message"])

    def test_removed_row(self):
        old = {"alpha": (None, (), (), None)}
        new = {}
        findings = indexgen.diff_index(old, new)
        self.assertEqual(len(findings), 1)
        self.assertIn("removed", findings[0]["message"])

    def test_changed_row(self):
        old = {"alpha": ("desc1", ("a.py",), ("test_a.py",), 1)}
        new = {"alpha": ("desc2", ("a.py",), ("test_a.py",), 1)}
        findings = indexgen.diff_index(old, new)
        self.assertEqual(len(findings), 1)
        self.assertIn("changed", findings[0]["message"])

    def test_identical_no_findings(self):
        row = ("desc", ("a.py",), ("test_a.py",), 1)
        findings = indexgen.diff_index({"alpha": row}, {"alpha": row})
        self.assertEqual(findings, [])

    def test_multiple_changes_all_reported_sorted(self):
        old = {"b": (None, (), (), None), "a": (None, (), (), None)}
        new = {"b": ("x", (), (), None), "a": ("y", (), (), None)}
        findings = indexgen.diff_index(old, new)
        self.assertEqual(len(findings), 2)
        self.assertEqual([f["tool"] for f in findings], ["a", "b"])


# ==========================================================================
# Canonical JSON + finding ordering
# ==========================================================================

class CanonicalJsonTests(unittest.TestCase):

    def test_format_exact(self):
        obj = {"b": 1, "a": 2}
        text = indexgen.canonical_json(obj)
        self.assertEqual(text, '{"a":2,"b":1}\n')

    def test_ensure_ascii(self):
        text = indexgen.canonical_json({"x": "日本語"})
        self.assertNotIn("日本語", text)
        self.assertIn("\\u", text)

    def test_ends_with_single_newline(self):
        text = indexgen.canonical_json({"a": 1})
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))

    def test_no_spaces_in_separators(self):
        text = indexgen.canonical_json({"a": 1, "b": [1, 2]})
        self.assertNotIn(", ", text)
        self.assertNotIn(": ", text)


class FindingOrderTests(unittest.TestCase):

    def test_sort_by_code_first(self):
        f1 = indexgen.make_finding("ZZZ", "t", "loc", "m")
        f2 = indexgen.make_finding("AAA", "t", "loc", "m")
        result = indexgen.sort_findings([f1, f2])
        self.assertEqual([f["code"] for f in result], ["AAA", "ZZZ"])

    def test_sort_by_tool_second(self):
        f1 = indexgen.make_finding("SAME", "zzz", "loc", "m")
        f2 = indexgen.make_finding("SAME", "aaa", "loc", "m")
        result = indexgen.sort_findings([f1, f2])
        self.assertEqual([f["tool"] for f in result], ["aaa", "zzz"])

    def test_none_tool_sorts_before_named_tool(self):
        f1 = indexgen.make_finding("SAME", "aaa", "loc", "m")
        f2 = indexgen.make_finding("SAME", None, "loc", "m")
        result = indexgen.sort_findings([f1, f2])
        self.assertEqual([f["tool"] for f in result], [None, "aaa"])

    def test_tiebreak_by_canonical_json_breaks_true_tie(self):
        # Two findings identical in code/tool/location/message but the dict
        # itself differs via an extra example -- here we simulate a genuine
        # tie broken deterministically by constructing two dicts with the
        # same four semantic fields but verify the sort is still stable and
        # total by comparing the canonical json directly.
        f1 = indexgen.make_finding("SAME", "t", "loc", "m")
        f2 = indexgen.make_finding("SAME", "t", "loc", "m")
        key1 = indexgen.finding_sort_key(f1)
        key2 = indexgen.finding_sort_key(f2)
        self.assertEqual(key1, key2)  # identical findings -> identical keys (order-stable)

    def test_tiebreak_actually_distinguishes_extra_field(self):
        # Construct two finding-like dicts that share code/tool/location/message
        # but differ in an extra key, proving the canonical-json tiebreak
        # notices a difference invisible to the four semantic fields alone.
        f1 = {"code": "SAME", "tool": "t", "location": "loc", "message": "m", "extra": 1}
        f2 = {"code": "SAME", "tool": "t", "location": "loc", "message": "m", "extra": 2}
        key1 = indexgen.finding_sort_key(f1)
        key2 = indexgen.finding_sort_key(f2)
        self.assertNotEqual(key1, key2)
        ordered = sorted([f2, f1], key=indexgen.finding_sort_key)
        self.assertEqual([f["extra"] for f in ordered], [1, 2])

    def test_sort_is_total_order_for_many_items(self):
        import random
        findings = []
        codes = ["A", "B", "C"]
        tools = ["x", "y", None]
        for i in range(50):
            findings.append(indexgen.make_finding(
                codes[i % 3], tools[i % 3], "loc%d" % (i % 5), "msg%d" % (i % 4)))
        rnd = list(findings)
        random.Random(42).shuffle(rnd)
        sorted_once = indexgen.sort_findings(rnd)
        sorted_twice = indexgen.sort_findings(list(reversed(sorted_once)))
        self.assertEqual(
            [indexgen.canonical_json(f) for f in sorted_once],
            [indexgen.canonical_json(f) for f in sorted_twice],
        )


# ==========================================================================
# End-to-end via run()
# ==========================================================================

class RunEndToEndTests(unittest.TestCase):

    def test_clean_repo_zero_findings_exit_0(self):
        with TempRepo() as root:
            make_tool_dir(root, "alpha", readme="Alpha desc.\n5 tests.\n")
            out = os.path.join(root, "report.json")
            rc = indexgen.run(["--root", root, "-o", out])
            self.assertEqual(rc, 0)
            with open(out, encoding="utf-8") as _fh:
                data = json.load(_fh)
            self.assertEqual(data["findings"], [])

    def test_broken_repo_nonzero_findings_exit_1(self):
        with TempRepo() as root:
            make_tool_dir(root, "broken", readme=None, captured=False, test_files=[])
            out = os.path.join(root, "report.json")
            rc = indexgen.run(["--root", root, "-o", out])
            self.assertEqual(rc, 1)
            with open(out, encoding="utf-8") as _fh:
                data = json.load(_fh)
            self.assertGreater(len(data["findings"]), 0)

    def test_bad_root_exit_2(self):
        rc = indexgen.run(["--root", "/no/such/dir/at/all"])
        self.assertEqual(rc, 2)

    def test_write_index_creates_file(self):
        with TempRepo() as root:
            make_tool_dir(root, "alpha", readme="Alpha desc.\n5 tests.\n")
            idx = os.path.join(root, "INDEX.md")
            rc = indexgen.run(["--root", root, "--write-index", idx, "-o", os.path.join(root, "r.json")])
            self.assertIn(rc, (0, 1))
            self.assertTrue(os.path.isfile(idx))
            with open(idx, encoding="utf-8") as _fh:
                content = _fh.read()
            self.assertIn("alpha", content)

    def test_check_index_drift_detected(self):
        with TempRepo() as root:
            make_tool_dir(root, "alpha", readme="Alpha desc.\n5 tests.\n")
            idx = os.path.join(root, "INDEX.md")
            indexgen.run(["--root", root, "--write-index", idx, "-o", os.path.join(root, "r1.json")])
            # Now add a new tool and check drift against the stale index.
            make_tool_dir(root, "beta", readme="Beta desc.\n2 tests.\n")
            out2 = os.path.join(root, "r2.json")
            rc = indexgen.run(["--root", root, "--check-index", idx, "-o", out2])
            self.assertEqual(rc, 1)
            with open(out2, encoding="utf-8") as _fh:
                data = json.load(_fh)
            drift_findings = [f for f in data["findings"] if f["code"] == "INDEX_DRIFT"]
            self.assertTrue(any("beta" == f["tool"] for f in drift_findings))

    def test_check_index_no_drift_when_unchanged(self):
        with TempRepo() as root:
            make_tool_dir(root, "alpha", readme="Alpha desc.\n5 tests.\n")
            idx = os.path.join(root, "INDEX.md")
            indexgen.run(["--root", root, "--write-index", idx, "-o", os.path.join(root, "r1.json")])
            out2 = os.path.join(root, "r2.json")
            rc = indexgen.run(["--root", root, "--check-index", idx, "-o", out2])
            with open(out2, encoding="utf-8") as _fh:
                data = json.load(_fh)
            drift_findings = [f for f in data["findings"] if f["code"] == "INDEX_DRIFT"]
            self.assertEqual(drift_findings, [])

    def test_root_readme_drift_end_to_end(self):
        with TempRepo() as root:
            make_tool_dir(root, "a1", readme="d1\n1 tests\n")
            make_tool_dir(root, "a2", readme="d2\n1 tests\n")
            rr = os.path.join(root, "ROOT_README.md")
            write(rr, "This repo has 1 tools and 2 tests.\n")
            out = os.path.join(root, "r.json")
            rc = indexgen.run(["--root", root, "--root-readme", rr, "-o", out])
            self.assertEqual(rc, 1)
            with open(out, encoding="utf-8") as _fh:
                data = json.load(_fh)
            codes = [f["code"] for f in data["findings"]]
            self.assertIn("ROOT_README_COUNT_DRIFT", codes)

    def test_root_readme_no_drift_when_correct(self):
        with TempRepo() as root:
            make_tool_dir(root, "a1", readme="d1\n1 tests\n")
            make_tool_dir(root, "a2", readme="d2\n1 tests\n")
            rr = os.path.join(root, "ROOT_README.md")
            write(rr, "This repo has 2 tools and 2 tests.\n")
            out = os.path.join(root, "r.json")
            rc = indexgen.run(["--root", root, "--root-readme", rr, "-o", out])
            with open(out, encoding="utf-8") as _fh:
                data = json.load(_fh)
            codes = [f["code"] for f in data["findings"]]
            self.assertNotIn("ROOT_README_COUNT_DRIFT", codes)
            self.assertEqual(rc, 0)

    def test_stale_catalogued_test_count_alone_exits_1_end_to_end(self):
        """A catalogued TEST COUNT going stale must exit 1, tool count held correct.

        This isolates the test-count half of ROOT_README_COUNT_DRIFT end to end.
        test_root_readme_drift_end_to_end also exits 1, but its drift is on the
        TOOL count; test_drift_fires_on_test_total_mismatch_only isolates the
        test total but calls check_root_readme_drift() directly and never
        exercises an exit code. Neither covers "a catalogued test count was
        deliberately made stale, therefore exit 1", so this test does.
        """
        with TempRepo() as root:
            # Two tools, 3 + 4 = 7 tests actually catalogued.
            make_tool_dir(root, "a1", readme="d1\n3 tests\n")
            make_tool_dir(root, "a2", readme="d2\n4 tests\n")
            rr = os.path.join(root, "ROOT_README.md")

            # Baseline: tool count AND test total both correct -> exit 0.
            write(rr, "This repo has 2 tools and 7 tests.\n")
            out_ok = os.path.join(root, "ok.json")
            rc_ok = indexgen.run(["--root", root, "--root-readme", rr, "-o", out_ok])
            with open(out_ok, encoding="utf-8") as _fh:
                codes_ok = [f["code"] for f in json.load(_fh)["findings"]]
            self.assertNotIn("ROOT_README_COUNT_DRIFT", codes_ok)
            self.assertEqual(rc_ok, 0)

            # Now make ONLY the catalogued test count stale (7 -> 6). The tool
            # count stays correct, so any drift found must be the test total.
            write(rr, "This repo has 2 tools and 6 tests.\n")
            out = os.path.join(root, "stale.json")
            rc = indexgen.run(["--root", root, "--root-readme", rr, "-o", out])

            self.assertEqual(rc, 1, "a stale catalogued test count must exit 1")

            with open(out, encoding="utf-8") as _fh:
                data = json.load(_fh)
            drift = [f for f in data["findings"] if f["code"] == "ROOT_README_COUNT_DRIFT"]
            self.assertTrue(drift, "expected ROOT_README_COUNT_DRIFT")
            locations = sorted(f["location"] for f in drift)
            self.assertEqual(
                locations,
                ["root_readme:test_total"],
                "only the test-total half should fire; the tool count is correct",
            )

    def test_stale_catalogued_test_count_exits_1_via_subprocess(self):
        """Same case, but through the real CLI so the exit code is the process's
        own status rather than run()'s return value."""
        with TempRepo() as root:
            make_tool_dir(root, "a1", readme="d1\n3 tests\n")
            make_tool_dir(root, "a2", readme="d2\n4 tests\n")
            rr = os.path.join(root, "ROOT_README.md")
            write(rr, "This repo has 2 tools and 6 tests.\n")
            out = os.path.join(root, "r.json")
            proc = subprocess.run(
                [sys.executable, INDEXGEN_PATH,
                 "--root", root, "--root-readme", rr, "-o", out],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 1)
            with open(out, encoding="utf-8") as _fh:
                data = json.load(_fh)
            self.assertIn(
                "root_readme:test_total",
                [
                    f["location"]
                    for f in data["findings"]
                    if f["code"] == "ROOT_README_COUNT_DRIFT"
                ],
            )

    def test_missing_check_index_file_reported_not_fatal(self):
        with TempRepo() as root:
            make_tool_dir(root, "a1", readme="d1\n1 tests\n")
            out = os.path.join(root, "r.json")
            rc = indexgen.run(["--root", root, "--check-index", os.path.join(root, "nope.md"), "-o", out])
            with open(out, encoding="utf-8") as _fh:
                data = json.load(_fh)
            codes = [f["code"] for f in data["findings"]]
            self.assertIn("UNREADABLE_FILE", codes)
            self.assertIn(rc, (0, 1))

    def test_missing_root_readme_file_reported_not_fatal(self):
        with TempRepo() as root:
            make_tool_dir(root, "a1", readme="d1\n1 tests\n")
            out = os.path.join(root, "r.json")
            rc = indexgen.run(["--root", root, "--root-readme", os.path.join(root, "nope.md"), "-o", out])
            with open(out, encoding="utf-8") as _fh:
                data = json.load(_fh)
            codes = [f["code"] for f in data["findings"]]
            self.assertIn("UNREADABLE_FILE", codes)

    def test_unwritable_write_index_exit_2(self):
        with TempRepo() as root:
            make_tool_dir(root, "a1", readme="d1\n1 tests\n")
            bad_path = os.path.join(root, "no_such_subdir", "INDEX.md")
            rc = indexgen.run(["--root", root, "--write-index", bad_path])
            self.assertEqual(rc, 2)

    def test_unwritable_output_exit_2(self):
        with TempRepo() as root:
            make_tool_dir(root, "a1", readme="d1\n1 tests\n")
            bad_path = os.path.join(root, "no_such_subdir", "report.json")
            rc = indexgen.run(["--root", root, "-o", bad_path])
            self.assertEqual(rc, 2)

    def test_report_has_tools_and_totals_keys(self):
        with TempRepo() as root:
            make_tool_dir(root, "a1", readme="d1\n1 tests\n")
            out = os.path.join(root, "r.json")
            indexgen.run(["--root", root, "-o", out])
            with open(out, encoding="utf-8") as _fh:
                data = json.load(_fh)
            self.assertIn("tools", data)
            self.assertIn("totals", data)
            self.assertIn("findings", data)

    def test_stdout_output_when_no_o_flag(self):
        with TempRepo() as root:
            make_tool_dir(root, "a1", readme="d1\n1 tests\n")
            proc = subprocess.run(
                [sys.executable, INDEXGEN_PATH, "--root", root],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0)
            data = json.loads(proc.stdout)
            self.assertIn("tools", data)


# ==========================================================================
# Byte-stability / determinism (in-process)
# ==========================================================================

class DeterminismTests(unittest.TestCase):

    def test_index_byte_stable_across_two_runs(self):
        with TempRepo() as root:
            make_tool_dir(root, "alpha", readme="Alpha - subtitle.\n5 tests.\n")
            make_tool_dir(root, "beta", readme=None, captured=False, test_files=[])
            idx1 = os.path.join(root, "INDEX1.md")
            idx2 = os.path.join(root, "INDEX2.md")
            indexgen.run(["--root", root, "--write-index", idx1, "-o", os.path.join(root, "r1.json")])
            indexgen.run(["--root", root, "--write-index", idx2, "-o", os.path.join(root, "r2.json")])
            b1 = read_bytes(idx1)
            b2 = read_bytes(idx2)
            self.assertEqual(b1, b2)

    def test_json_byte_stable_across_two_runs(self):
        with TempRepo() as root:
            make_tool_dir(root, "alpha", readme="Alpha - subtitle.\n5 tests.\n")
            r1 = os.path.join(root, "r1.json")
            r2 = os.path.join(root, "r2.json")
            indexgen.run(["--root", root, "-o", r1])
            indexgen.run(["--root", root, "-o", r2])
            self.assertEqual(read_bytes(r1), read_bytes(r2))

    def test_no_absolute_root_path_leaks_into_report(self):
        with TempRepo() as root:
            make_tool_dir(root, "alpha", readme="Alpha - subtitle.\n5 tests.\n")
            out = os.path.join(root, "r.json")
            indexgen.run(["--root", root, "-o", out])
            with open(out, encoding="utf-8") as _fh:
                text = _fh.read()
            self.assertNotIn(root, text)

    def test_no_absolute_root_path_leaks_into_index(self):
        with TempRepo() as root:
            make_tool_dir(root, "alpha", readme="Alpha - subtitle.\n5 tests.\n")
            idx = os.path.join(root, "INDEX.md")
            indexgen.run(["--root", root, "--write-index", idx, "-o", os.path.join(root, "r.json")])
            with open(idx, encoding="utf-8") as _fh:
                text = _fh.read()
            self.assertNotIn(root, text)


# ==========================================================================
# CLI / subprocess exit-code tests (real process invocations)
# ==========================================================================

class CliSubprocessTests(unittest.TestCase):

    def _run(self, args):
        return subprocess.run(
            [sys.executable, INDEXGEN_PATH] + args,
            capture_output=True, text=True,
        )

    def test_exit_0_clean(self):
        with TempRepo() as root:
            make_tool_dir(root, "alpha", readme="Alpha desc.\n1 tests.\n")
            proc = self._run(["--root", root])
            self.assertEqual(proc.returncode, 0)

    def test_exit_1_findings(self):
        with TempRepo() as root:
            make_tool_dir(root, "alpha", readme=None, captured=False, test_files=[])
            proc = self._run(["--root", root])
            self.assertEqual(proc.returncode, 1)

    def test_exit_2_bad_root(self):
        proc = self._run(["--root", "/no/such/dir/xyzxyz"])
        self.assertEqual(proc.returncode, 2)

    def test_exit_2_missing_required_arg(self):
        proc = self._run([])
        self.assertEqual(proc.returncode, 2)

    def test_exit_2_unknown_arg(self):
        with TempRepo() as root:
            proc = self._run(["--root", root, "--not-a-real-flag"])
            self.assertEqual(proc.returncode, 2)

    def test_stderr_nonempty_on_bad_root(self):
        proc = self._run(["--root", "/no/such/dir/xyzxyz"])
        self.assertTrue(len(proc.stderr) > 0)


# ==========================================================================
# Unicode + CRLF end-to-end
# ==========================================================================

class UnicodeCrlfTests(unittest.TestCase):

    def test_unicode_description_end_to_end(self):
        with TempRepo() as root:
            make_tool_dir(root, "uni", readme="# 名前\n\n説明文です。5 tests.\n")
            out = os.path.join(root, "r.json")
            indexgen.run(["--root", root, "-o", out])
            with open(out, encoding="utf-8") as _fh:
                data = json.load(_fh)
            tool = data["tools"][0]
            self.assertEqual(tool["description"], "説明文です。5 tests.")

    def test_crlf_readme_end_to_end(self):
        with TempRepo() as root:
            d = make_tool_dir(root, "crlf", readme=None)
            with open(os.path.join(d, "README.md"), "wb") as fh:
                fh.write(b"Description line.\r\n7 tests.\r\n")
            out = os.path.join(root, "r.json")
            indexgen.run(["--root", root, "-o", out])
            with open(out, encoding="utf-8") as _fh:
                data = json.load(_fh)
            tool = data["tools"][0]
            self.assertEqual(tool["description"], "Description line.")
            self.assertEqual(tool["claimed_test_count"], 7)

    def test_index_with_unicode_roundtrips(self):
        with TempRepo() as root:
            make_tool_dir(root, "uni", readme="日本語の説明。3 tests.\n")
            idx = os.path.join(root, "INDEX.md")
            indexgen.run(["--root", root, "--write-index", idx, "-o", os.path.join(root, "r.json")])
            with open(idx, encoding="utf-8") as _fh:
                text = _fh.read()
            self.assertIn("日本語の説明。", text)
            parsed = indexgen.parse_index(text)
            self.assertEqual(parsed["uni"][0], "日本語の説明。3 tests.")


# ==========================================================================
# Relocation: same tree, different absolute path -> identical bytes
# ==========================================================================

class RelocationTests(unittest.TestCase):

    def _build_fixture(self, base):
        make_tool_dir(base, "alpha", readme="Alpha - does alpha.\n5 tests.\n")
        make_tool_dir(base, "beta", readme=None, captured=False, test_files=[])
        make_tool_dir(base, "gamma", readme="Gamma tool.\nTests: 12\n",
                      py_files=["gamma.py", "extra.py"], test_files=["test_gamma.py"])

    def test_relocated_tree_produces_identical_bytes(self):
        with tempfile.TemporaryDirectory(prefix="reloc_a_") as parent_a, \
             tempfile.TemporaryDirectory(prefix="reloc_zzzzzzzzzz_") as parent_b:
            root_a = os.path.join(parent_a, "fx")
            root_b = os.path.join(parent_b, "fx_renamed")
            os.makedirs(root_a)
            os.makedirs(root_b)
            self._build_fixture(root_a)
            self._build_fixture(root_b)

            idx_a = os.path.join(parent_a, "INDEX.md")
            idx_b = os.path.join(parent_b, "INDEX.md")
            rep_a = os.path.join(parent_a, "report.json")
            rep_b = os.path.join(parent_b, "report.json")

            indexgen.run(["--root", root_a, "--write-index", idx_a, "-o", rep_a])
            indexgen.run(["--root", root_b, "--write-index", idx_b, "-o", rep_b])

            self.assertEqual(read_bytes(idx_a), read_bytes(idx_b))
            self.assertEqual(read_bytes(rep_a), read_bytes(rep_b))


# ==========================================================================
# make_finding / helpers
# ==========================================================================

class MakeFindingTests(unittest.TestCase):

    def test_make_finding_shape(self):
        f = indexgen.make_finding("CODE", "tool", "loc", "msg")
        self.assertEqual(f, {"code": "CODE", "tool": "tool", "location": "loc", "message": "msg"})

    def test_make_finding_none_tool(self):
        f = indexgen.make_finding("CODE", None, "loc", "msg")
        self.assertIsNone(f["tool"])


class SplitRowTests(unittest.TestCase):

    def test_split_row_basic(self):
        cells = indexgen._split_row("| a | b | c | d | e |")
        self.assertEqual(cells, ["a", "b", "c", "d", "e"])

    def test_split_row_escaped_pipe(self):
        cells = indexgen._split_row(r"| a\|b | c | d | e | f |")
        self.assertEqual(cells[0], "a|b")

    def test_split_row_empty_cell(self):
        cells = indexgen._split_row("|  | b | c | d | e |")
        self.assertEqual(cells[0], "")


class ToolToRowTests(unittest.TestCase):

    def test_tool_to_row_sorts_lists(self):
        tool = {
            "description": "d",
            "entrypoints": ["b.py", "a.py"],
            "test_modules": ["test_b.py", "test_a.py"],
            "claimed_test_count": 1,
        }
        row = indexgen.tool_to_row(tool)
        self.assertEqual(row, ("d", ("a.py", "b.py"), ("test_a.py", "test_b.py"), 1))


if __name__ == "__main__":
    unittest.main()
