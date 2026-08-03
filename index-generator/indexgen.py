#!/usr/bin/env python3
"""indexgen.py -- generate and self-check a repository INDEX.md.

Scans a multi-tool repository one level deep under --root, builds a
deterministic markdown index of the discovered tools, and cross-checks the
result against the actual tree so that "documentation drift" (e.g. a root
README claiming 13 tools when 33 exist) is reported instead of silently
produced.

USAGE
    indexgen.py --root PATH [--write-index PATH] [--check-index PATH]
                [--root-readme PATH] [-o PATH]

EXIT CODES
    0  scan completed and produced zero findings
    1  scan completed and produced one or more findings
    2  invalid input / usage error (bad --root, unwritable output, bad args)

DETERMINISM CONTRACT
    * No wall-clock reads of any kind are performed by this module.
    * All output paths are repository-relative; the absolute value of
      --root is never written to any output.
    * Every list emitted in the JSON report or the markdown index is
      explicitly sorted. Findings are sorted on a tuple of semantic fields
      and then, as a final tiebreaker, on the canonical JSON encoding of the
      finding itself -- this guarantees a total order even when two
      findings are otherwise identical in every semantic field.
    * The JSON report is serialized with
      json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
      plus a trailing newline, written with newline="\\n" so the bytes are
      stable across platforms.

FINDING CODES
    MISSING_README            tool directory has no README.md
    MISSING_CAPTURED_OUTPUT   tool directory has no captured_output.txt
    MISSING_TEST_MODULE       tool directory has zero test_*.py modules
    NO_DESCRIPTION            README.md exists but no description line found
    NO_CLAIMED_TEST_COUNT     README.md exists but no test count is stated
    ENTRYPOINT_TEST_MISMATCH  foo.py present but no matching test_foo.py
    UNREADABLE_FILE           a file existed but could not be decoded/read
    INDEX_DRIFT               --check-index differs from the freshly computed index
    ROOT_README_COUNT_DRIFT   --root-readme claims counts that disagree with reality
"""

import argparse
import json
import os
import re
import sys

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

_BADGE_PREFIXES = ("[![", "![")
_BADGE_SUBSTRINGS = ("shields.io", "<img ")

_H1_RE = re.compile(r"^#\s+(.*)$")
_HEADING_RE = re.compile(r"^#{1,6}\s*")

_H1_SUBTITLE_SEPARATORS = (" — ", " - ", ": ")

# Patterns used to pull a claimed test count out of free-form README prose.
# Each has exactly one capturing group: the integer count.
_TEST_COUNT_PATTERNS = [
    re.compile(r"\bran\s+(\d+)\s+tests?\b", re.IGNORECASE),
    re.compile(r"\btests?\s*[:=]\s*(\d+)\b", re.IGNORECASE),
    re.compile(r"\b(\d+)\s*/\s*\d+\s+tests?\b", re.IGNORECASE),
    re.compile(r"\b(\d+)\s+tests?\b", re.IGNORECASE),
]

_TOOL_COUNT_PATTERNS = [
    re.compile(r"\btools?\s*[:=]\s*(\d+)\b", re.IGNORECASE),
    re.compile(r"\b(\d+)\s+tools?\b", re.IGNORECASE),
]


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def _is_badge_line(stripped):
    if stripped.startswith(_BADGE_PREFIXES):
        return True
    for sub in _BADGE_SUBSTRINGS:
        if sub in stripped:
            return True
    return False


def _h1_subtitle(content):
    """Given the text after the leading '# ', try to pull a subtitle."""
    for sep in _H1_SUBTITLE_SEPARATORS:
        if sep in content:
            candidate = content.split(sep, 1)[1].strip()
            if candidate:
                return candidate
    return None


def extract_description(text):
    """Return (description_or_None) from raw README text."""
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            m = _H1_RE.match(stripped)
            if m:
                sub = _h1_subtitle(m.group(1).strip())
                if sub:
                    return sub
            continue
        if _is_badge_line(stripped):
            continue
        return stripped
    return None


def _first_match(patterns, text):
    best = None
    for pat in patterns:
        m = pat.search(text)
        if m is None:
            continue
        if best is None or m.start() < best[0]:
            best = (m.start(), int(m.group(1)))
    return None if best is None else best[1]


def extract_claimed_test_count(text):
    """Return an int claimed test count from README prose, or None."""
    return _first_match(_TEST_COUNT_PATTERNS, text)


def extract_claimed_tool_count(text):
    """Return an int claimed tool count from README prose, or None."""
    return _first_match(_TOOL_COUNT_PATTERNS, text)


# --------------------------------------------------------------------------
# Canonical JSON
# --------------------------------------------------------------------------

def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def write_text_file(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------

def make_finding(code, tool, location, message):
    return {
        "code": code,
        "tool": tool,
        "location": location,
        "message": message,
    }


def finding_sort_key(finding):
    """Total-order sort key: semantic fields, then canonical-JSON tiebreak.

    Sorting purely on (code, tool, location, message) can still leave ties
    if two findings happen to share all four fields (e.g. two distinct
    causes producing identical text). The canonical JSON dump of the whole
    finding is appended as the final key so the ordering is always a total
    order, never merely a partial one.
    """
    tool = finding.get("tool")
    tool_key = "" if tool is None else tool
    return (
        finding["code"],
        tool_key,
        finding["location"],
        finding["message"],
        canonical_json(finding),
    )


def sort_findings(findings):
    return sorted(findings, key=finding_sort_key)


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

def _safe_read_text(path, findings, tool_dir):
    """Read a text file, reporting (not raising) on any failure.

    Returns the text, or None if the file could not be read/decoded.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        rel = os.path.basename(path)
        findings.append(
            make_finding(
                "UNREADABLE_FILE",
                tool_dir,
                rel if tool_dir is None else "%s/%s" % (tool_dir, rel),
                "could not read %s: %s" % (rel, exc.__class__.__name__),
            )
        )
        return None


def discover_tools(root, findings):
    """Discover tool directories one level deep under root.

    Returns a list of tool dicts sorted by directory name.
    """
    tools = []
    try:
        entries = sorted(os.listdir(root))
    except OSError as exc:
        raise ValueError("cannot list --root %r: %s" % (root, exc))

    for name in entries:
        full = os.path.join(root, name)
        if not os.path.isdir(full):
            continue
        try:
            child_names = sorted(os.listdir(full))
        except OSError:
            continue

        py_files = [
            n for n in child_names
            if n.endswith(".py") and not n.startswith("test_")
            and os.path.isfile(os.path.join(full, n))
        ]
        if not py_files:
            continue  # not a tool directory

        test_files = sorted(
            n for n in child_names
            if n.endswith(".py") and n.startswith("test_")
            and os.path.isfile(os.path.join(full, n))
        )

        readme_path = os.path.join(full, "README.md")
        has_readme = os.path.isfile(readme_path)
        captured_path = os.path.join(full, "captured_output.txt")
        has_captured = os.path.isfile(captured_path)

        description = None
        claimed_test_count = None
        if has_readme:
            text = _safe_read_text(readme_path, findings, name)
            if text is not None:
                description = extract_description(text)
                claimed_test_count = extract_claimed_test_count(text)

        tools.append({
            "dir": name,
            "entrypoints": sorted(py_files),
            "test_modules": test_files,
            "has_readme": has_readme,
            "has_captured_output": has_captured,
            "description": description,
            "claimed_test_count": claimed_test_count,
        })

    tools.sort(key=lambda t: t["dir"])
    return tools


def check_tool_findings(tool):
    findings = []
    name = tool["dir"]

    if not tool["has_readme"]:
        findings.append(make_finding(
            "MISSING_README", name, "%s/README.md" % name,
            "tool %r has no README.md" % name,
        ))
    if not tool["has_captured_output"]:
        findings.append(make_finding(
            "MISSING_CAPTURED_OUTPUT", name, "%s/captured_output.txt" % name,
            "tool %r has no captured_output.txt" % name,
        ))
    if not tool["test_modules"]:
        findings.append(make_finding(
            "MISSING_TEST_MODULE", name, name,
            "tool %r has no test_*.py modules" % name,
        ))
    if tool["has_readme"] and tool["description"] is None:
        findings.append(make_finding(
            "NO_DESCRIPTION", name, "%s/README.md" % name,
            "tool %r README.md has no extractable description" % name,
        ))
    if tool["has_readme"] and tool["claimed_test_count"] is None:
        findings.append(make_finding(
            "NO_CLAIMED_TEST_COUNT", name, "%s/README.md" % name,
            "tool %r README.md states no claimed test count" % name,
        ))

    test_set = set(tool["test_modules"])
    for entry in tool["entrypoints"]:
        base = entry[:-3] if entry.endswith(".py") else entry
        expected = "test_%s.py" % base
        if expected not in test_set:
            findings.append(make_finding(
                "ENTRYPOINT_TEST_MISMATCH", name, "%s/%s" % (name, entry),
                "%s present but no %s" % (entry, expected),
            ))

    return findings


# --------------------------------------------------------------------------
# Totals
# --------------------------------------------------------------------------

def compute_totals(tools):
    tool_count = len(tools)
    known = [t["claimed_test_count"] for t in tools if t["claimed_test_count"] is not None]
    unknown_count = tool_count - len(known)
    return {
        "tool_count": tool_count,
        "test_count_sum": sum(known) if known else 0,
        "test_count_known_tools": len(known),
        "test_count_unknown_tools": unknown_count,
    }


# --------------------------------------------------------------------------
# INDEX.md rendering + parsing
# --------------------------------------------------------------------------

_INDEX_HEADER = ["# Repository Index", ""]
_TABLE_HEADER = "| Tool | Description | Entrypoint(s) | Test Module(s) | Claimed Tests |"
_TABLE_SEP = "| --- | --- | --- | --- | --- |"


_NONE_MARKER = "_(none)_"

# _NONE_MARKER doubles as the "no value" placeholder for the Description,
# Entrypoint(s) and Test Module(s) cells. That is fine for the latter two
# (real filenames cannot equal that text), but a tool's *genuine* extracted
# description could -- in principle -- be the literal text "_(none)_" (e.g.
# a README whose first content line literally reads "_(none)_"). Without
# disambiguation that real value would parse back as None on the next
# --check-index run, producing a false-positive INDEX_DRIFT finding on an
# unchanged tree. See BUGS.md / README Limitations for details; the guard
# below is the fix, pinned by
# test_description_literally_equal_to_none_marker_round_trips in the tests.
_NONE_MARKER_ESCAPED = "\\" + _NONE_MARKER


def _escape_cell(text):
    return text.replace("|", "\\|")


def _unescape_cell(text):
    return text.replace("\\|", "|")


def _encode_description_cell(desc):
    if desc is None:
        return _NONE_MARKER
    escaped = _escape_cell(desc)
    if escaped == _NONE_MARKER:
        # Disambiguate real content that happens to equal the sentinel.
        return _NONE_MARKER_ESCAPED
    return escaped


def _decode_description_cell(cell):
    if cell == _NONE_MARKER:
        return None
    if cell == _NONE_MARKER_ESCAPED:
        return _NONE_MARKER
    return cell


def render_index(tools, totals):
    lines = list(_INDEX_HEADER)
    lines.append(_TABLE_HEADER)
    lines.append(_TABLE_SEP)
    for t in sorted(tools, key=lambda t: t["dir"]):
        desc_cell = _encode_description_cell(t["description"])
        entrypoints = sorted(t["entrypoints"])
        test_modules = sorted(t["test_modules"])
        entry_cell = _escape_cell(", ".join(entrypoints)) if entrypoints else "_(none)_"
        test_cell = _escape_cell(", ".join(test_modules)) if test_modules else "_(none)_"
        count = t["claimed_test_count"]
        count_cell = str(count) if count is not None else "?"
        lines.append("| %s | %s | %s | %s | %s |" % (
            _escape_cell(t["dir"]), desc_cell, entry_cell, test_cell, count_cell,
        ))
    lines.append("")
    lines.append(
        "**Totals:** %d tools; test count: %d (from %d tool%s; %d unknown)" % (
            totals["tool_count"],
            totals["test_count_sum"],
            totals["test_count_known_tools"],
            "" if totals["test_count_known_tools"] == 1 else "s",
            totals["test_count_unknown_tools"],
        )
    )
    return "\n".join(lines) + "\n"


def _split_row(line):
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    # split on unescaped pipes
    cells = []
    current = []
    i = 0
    while i < len(inner):
        ch = inner[i]
        if ch == "\\" and i + 1 < len(inner) and inner[i + 1] == "|":
            current.append("|")
            i += 2
            continue
        if ch == "|":
            cells.append("".join(current).strip())
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    cells.append("".join(current).strip())
    return cells


def parse_index(text):
    """Parse a markdown index file into {tool_dir: row_tuple}.

    row_tuple = (description_or_None, entrypoints_tuple, test_modules_tuple,
                 claimed_count_or_None)
    Returns an empty dict for text with no recognizable table rows.
    """
    rows = {}
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        if set(s.replace("|", "").replace("-", "").replace(" ", "").replace(":", "")) == set():
            continue  # separator row like | --- | --- |
        cells = _split_row(s)
        if len(cells) != 5:
            continue
        if cells[0] == "Tool":
            continue  # header row
        name = cells[0]
        desc = _decode_description_cell(cells[1])
        entrypoints = tuple() if cells[2] == "_(none)_" else tuple(
            sorted(x.strip() for x in cells[2].split(",") if x.strip())
        )
        test_modules = tuple() if cells[3] == "_(none)_" else tuple(
            sorted(x.strip() for x in cells[3].split(",") if x.strip())
        )
        count = None if cells[4] == "?" else int(cells[4])
        rows[name] = (desc, entrypoints, test_modules, count)
    return rows


def tool_to_row(tool):
    return (
        tool["description"],
        tuple(sorted(tool["entrypoints"])),
        tuple(sorted(tool["test_modules"])),
        tool["claimed_test_count"],
    )


def diff_index(old_rows, new_rows):
    findings = []
    old_names = set(old_rows)
    new_names = set(new_rows)

    for name in sorted(new_names - old_names):
        findings.append(make_finding(
            "INDEX_DRIFT", name, "row:%s" % name,
            "tool %r is present in the freshly computed index but missing "
            "from the checked index (added)" % name,
        ))
    for name in sorted(old_names - new_names):
        findings.append(make_finding(
            "INDEX_DRIFT", name, "row:%s" % name,
            "tool %r is present in the checked index but was not discovered "
            "in the freshly computed index (removed)" % name,
        ))
    for name in sorted(old_names & new_names):
        if old_rows[name] != new_rows[name]:
            findings.append(make_finding(
                "INDEX_DRIFT", name, "row:%s" % name,
                "tool %r row differs between the checked index and the "
                "freshly computed index (changed)" % name,
            ))
    return findings


# --------------------------------------------------------------------------
# Root README drift
# --------------------------------------------------------------------------

def check_root_readme_drift(text, totals):
    findings = []
    claimed_tools = extract_claimed_tool_count(text)
    claimed_tests = extract_claimed_test_count(text)

    if claimed_tools is not None and claimed_tools != totals["tool_count"]:
        findings.append(make_finding(
            "ROOT_README_COUNT_DRIFT", None, "root_readme:tool_count",
            "root README claims %d tool(s) but %d were discovered" % (
                claimed_tools, totals["tool_count"],
            ),
        ))
    if claimed_tests is not None and claimed_tests != totals["test_count_sum"]:
        findings.append(make_finding(
            "ROOT_README_COUNT_DRIFT", None, "root_readme:test_total",
            "root README claims %d test(s) but the sum of known "
            "per-tool claimed counts is %d (%d tool(s) have an unknown "
            "count and are excluded from this sum)" % (
                claimed_tests, totals["test_count_sum"],
                totals["test_count_unknown_tools"],
            ),
        ))
    return findings


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_arg_parser():
    p = argparse.ArgumentParser(prog="indexgen.py", description=__doc__.splitlines()[0])
    p.add_argument("--root", required=True, help="repository root to scan (one level deep)")
    p.add_argument("--write-index", metavar="PATH", help="write generated INDEX.md to PATH")
    p.add_argument("--check-index", metavar="PATH", help="compare an existing index file against the fresh scan")
    p.add_argument("--root-readme", metavar="PATH", help="root README.md to cross-check claimed counts against")
    p.add_argument("-o", "--output", metavar="PATH", help="write JSON report to PATH instead of stdout")
    return p


def run(argv):
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    root = args.root
    if not os.path.isdir(root):
        sys.stderr.write("indexgen.py: error: --root %r is not a directory\n" % root)
        return 2

    findings = []
    try:
        tools = discover_tools(root, findings)
    except ValueError as exc:
        sys.stderr.write("indexgen.py: error: %s\n" % exc)
        return 2

    for tool in tools:
        findings.extend(check_tool_findings(tool))

    totals = compute_totals(tools)

    if args.check_index:
        if not os.path.isfile(args.check_index):
            findings.append(make_finding(
                "UNREADABLE_FILE", None, "--check-index",
                "could not read --check-index path: not a file",
            ))
        else:
            old_text = _safe_read_text(args.check_index, findings, None)
            if old_text is not None:
                old_rows = parse_index(old_text)
                new_rows = {t["dir"]: tool_to_row(t) for t in tools}
                findings.extend(diff_index(old_rows, new_rows))

    if args.root_readme:
        if not os.path.isfile(args.root_readme):
            findings.append(make_finding(
                "UNREADABLE_FILE", None, "--root-readme",
                "could not read --root-readme path: not a file",
            ))
        else:
            readme_text = _safe_read_text(args.root_readme, findings, None)
            if readme_text is not None:
                findings.extend(check_root_readme_drift(readme_text, totals))

    findings = sort_findings(findings)

    report = {
        "findings": findings,
        "tools": tools,
        "totals": totals,
    }

    if args.write_index:
        index_text = render_index(tools, totals)
        try:
            write_text_file(args.write_index, index_text)
        except OSError as exc:
            sys.stderr.write("indexgen.py: error: cannot write --write-index: %s\n" % exc)
            return 2

    report_text = canonical_json(report)
    if args.output:
        try:
            write_text_file(args.output, report_text)
        except OSError as exc:
            sys.stderr.write("indexgen.py: error: cannot write --output: %s\n" % exc)
            return 2
    else:
        sys.stdout.write(report_text)

    return 1 if findings else 0


def main():
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
