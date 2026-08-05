#!/usr/bin/env python3
"""Derive a repository tool index from tool READMEs and reconcile it with the root README.

EXIT CODES
  0  the root README index already matches the derived index
  1  differences were found (missing, extra, title, count, or aggregate)
  2  setup error (bad path, unreadable input, malformed corpus)

Standard library only: argparse, json, os, re, sys.
"""

import argparse
import json
import os
import re
import sys

EXIT_OK = 0
EXIT_DIFF = 1
EXIT_SETUP = 2

# A deliberately over-broad superset of every claim rule below. A line that no
# rule can match may still pass this filter; a line that any rule matches can
# never fail it. TestPrefilterIsSuperset pins that direction.
PREFILTER = re.compile(r"\d[^\n]{0,40}?tests?\b", re.I)

# A tool's own summary line: a bold or `Ran N tests` count on a line that also
# reports success. This outranks everything else because it is the line the
# author wrote to state the suite total.
STRONG_COUNT = re.compile(r"(?:\*\*\s*(\d+)\s+tests?\b|\bRan\s+(\d+)\s+tests?\b)", re.I)
SUCCESS_MARK = re.compile(r"\bOK\b|all passing|\bexit\s*0\b", re.I)

# A per-file self-report: "test_foo.py ... 170 unit tests". Distinct test files
# are additive, not contradictory.
SELF_REPORT = re.compile(
    r"(test_[A-Za-z0-9_]*\.py)[^\n]{0,80}?\b(\d+)(?:\s+[A-Za-z/]+){0,2}\s+tests?\b", re.I
)

# Weakest rule: a bare count anywhere. Allows up to two words between the number
# and "tests" so "170 unit tests" and "154 unit/integration tests" are seen.
BARE_COUNT = re.compile(r"\b(\d+)(?:\s+[A-Za-z/]+){0,2}\s+tests?\b", re.I)

HEADING = re.compile(r"^#\s+(.*\S)\s*$")
INDEX_ROW = re.compile(r"^\|\s*\[`([^`]+)`\]\(([^)]*)\)\s*\|([^|]*)\|(.*)\|\s*$")

STATUS_CLAIM = "claim"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_MISSING = "missing"


def canonical_json(obj):
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


class SetupError(Exception):
    pass


# ---------------------------------------------------------------- extraction


def extract_claim(candidate_lines):
    """Return (status, count, evidence) from a tool's candidate lines.

    evidence is a list of {"line": int, "rule": str, "value": int} recording
    exactly which lines decided the outcome, so a reviewer can check the call
    without rerunning anything.
    """
    strong, selfrep, bare = [], [], []
    for lineno, text in candidate_lines:
        for m in STRONG_COUNT.finditer(text):
            if SUCCESS_MARK.search(text):
                val = int(m.group(1) or m.group(2))
                strong.append({"line": lineno, "rule": "strong_summary", "value": val})
        for m in SELF_REPORT.finditer(text):
            selfrep.append(
                {
                    "line": lineno,
                    "rule": "self_report",
                    "value": int(m.group(2)),
                    "file": m.group(1).lower(),
                }
            )
        for m in BARE_COUNT.finditer(text):
            bare.append({"line": lineno, "rule": "bare", "value": int(m.group(1))})

    if strong:
        values = sorted({e["value"] for e in strong})
        if len(values) == 1:
            return STATUS_CLAIM, values[0], strong
        return STATUS_AMBIGUOUS, None, strong

    if selfrep:
        by_file = {}
        for e in selfrep:
            by_file.setdefault(e["file"], set()).add(e["value"])
        if any(len(v) > 1 for v in by_file.values()):
            return STATUS_AMBIGUOUS, None, selfrep
        if len(by_file) > 1:
            # Several distinct test_*.py files each report a count. Summing them
            # is wrong whenever one of them is a fixture rather than part of the
            # tool's own suite -- commit-claim-auditor names a bundled
            # test_example.py with 3 tests alongside its real 154-test suite, and
            # summing yields a confident 157 that appears nowhere in the README.
            # A tool that genuinely has several suites states the total itself,
            # and that total is caught by STRONG_COUNT before this point
            # (regression-checker: "**174 tests, OK** (131 + 8 + 35)"). So with
            # no stated total and several candidate files, the honest answer is
            # ambiguous.
            return STATUS_AMBIGUOUS, None, selfrep
        total = sum(next(iter(v)) for v in by_file.values())
        return STATUS_CLAIM, total, selfrep

    if bare:
        values = sorted({e["value"] for e in bare})
        if len(values) == 1:
            return STATUS_CLAIM, values[0], bare
        return STATUS_AMBIGUOUS, None, bare

    return STATUS_MISSING, None, []


def extract_title(text):
    for line in text.split("\n"):
        m = HEADING.match(line)
        if m:
            return m.group(1)
    return None


def candidates_from_text(text):
    out = []
    for i, line in enumerate(text.split("\n"), start=1):
        if PREFILTER.search(line):
            out.append((i, line))
    return out


# ---------------------------------------------------------------- discovery


def discover_from_root(root):
    if not os.path.isdir(root):
        raise SetupError("--root is not a directory: %s" % root)
    tools = {}
    for name in sorted(os.listdir(root)):
        if name.startswith("."):
            continue
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        readme = os.path.join(path, "README.md")
        if not os.path.isfile(readme):
            continue
        with open(readme, encoding="utf-8") as fh:
            text = fh.read()
        tools[name] = {
            "title": extract_title(text),
            "candidates": candidates_from_text(text),
            "readme_lines": len(text.split("\n")),
        }
    return tools


def discover_from_corpus(path):
    if not os.path.isfile(path):
        raise SetupError("--corpus is not a file: %s" % path)
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    tools = {}
    for n, line in enumerate(raw.split("\n"), start=1):
        if not line:
            continue
        parts = line.split("\t", 3)
        if len(parts) != 4:
            raise SetupError("corpus line %d is not 4 tab-separated fields" % n)
        name, lineno, kind, text = parts
        try:
            lineno = int(lineno)
        except ValueError:
            raise SetupError("corpus line %d has a non-integer line number" % n)
        t = tools.setdefault(name, {"title": None, "candidates": [], "readme_lines": None})
        if kind == "H":
            t["title"] = extract_title(text)
        elif kind == "T":
            t["candidates"].append((lineno, text))
        elif kind == "N":
            t["readme_lines"] = lineno
        else:
            raise SetupError("corpus line %d has unknown kind %r" % (n, kind))
    return tools


def build_tools(discovered):
    out = []
    for name in sorted(discovered):
        d = discovered[name]
        status, count, evidence = extract_claim(d["candidates"])
        out.append(
            {
                "tool": name,
                "title": d["title"],
                "claimed_tests": count,
                "status": status,
                "evidence": sorted(evidence, key=lambda e: (e["line"], e["rule"], e["value"])),
                "readme_lines": d["readme_lines"],
            }
        )
    return out


def compute_totals(tools):
    claimed = [t for t in tools if t["status"] == STATUS_CLAIM]
    return {
        "tools": len(tools),
        "tools_with_claim": len(claimed),
        "tools_ambiguous": sum(1 for t in tools if t["status"] == STATUS_AMBIGUOUS),
        "tools_missing": sum(1 for t in tools if t["status"] == STATUS_MISSING),
        "tests_from_claims": sum(t["claimed_tests"] for t in claimed),
    }


# ---------------------------------------------------------------- root README


def parse_index(text):
    rows = []
    for i, line in enumerate(text.split("\n"), start=1):
        m = INDEX_ROW.match(line)
        if m:
            cell = m.group(3).strip()
            rows.append(
                {
                    "line": i,
                    "tool": m.group(1),
                    "tests_cell": cell,
                    "tests": int(cell) if cell.isdigit() else None,
                }
            )
    return rows


def diff_index(existing_rows, tools):
    derived = {t["tool"]: t for t in tools}
    present = {r["tool"] for r in existing_rows}
    diffs = []
    for name in sorted(set(derived) - present):
        diffs.append({"kind": "missing_from_index", "tool": name})
    for name in sorted(present - set(derived)):
        diffs.append({"kind": "extra_in_index", "tool": name})
    for row in existing_rows:
        d = derived.get(row["tool"])
        if d is None:
            continue
        if d["status"] == STATUS_CLAIM and row["tests"] != d["claimed_tests"]:
            diffs.append(
                {
                    "kind": "count_differs",
                    "tool": row["tool"],
                    "index": row["tests"],
                    "derived": d["claimed_tests"],
                }
            )
        elif d["status"] != STATUS_CLAIM and row["tests"] is not None:
            diffs.append(
                {
                    "kind": "count_not_derivable",
                    "tool": row["tool"],
                    "index": row["tests"],
                    "derived_status": d["status"],
                }
            )
    old_total = sum(r["tests"] for r in existing_rows if r["tests"] is not None)
    new_total = compute_totals(tools)["tests_from_claims"]
    if old_total != new_total or len(existing_rows) != len(tools):
        diffs.append(
            {
                "kind": "aggregate_differs",
                "index_tools": len(existing_rows),
                "index_tests": old_total,
                "derived_tools": len(tools),
                "derived_tests": new_total,
            }
        )
    return diffs


def render_rows(tools):
    lines = ["| Tool | Tests | Title |", "|------|------:|-------|"]
    for t in tools:
        if t["status"] == STATUS_CLAIM:
            cell = str(t["claimed_tests"])
        elif t["status"] == STATUS_AMBIGUOUS:
            cell = "ambiguous"
        else:
            cell = "not stated"
        title = (t["title"] or "").replace("|", "\\|")
        lines.append("| [`%s`](%s) | %s | %s |" % (t["tool"], t["tool"], cell, title))
    return lines


def rewrite_index(readme_text, tools, heading, next_heading):
    lines = readme_text.split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == heading)
    except StopIteration:
        raise SetupError("heading not found in root README: %s" % heading)
    try:
        end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == next_heading)
    except StopIteration:
        raise SetupError("next heading not found in root README: %s" % next_heading)
    totals = compute_totals(tools)
    body = (
        [heading, ""]
        + render_rows(tools)
        + [
            "",
            "**Totals:** %d tools; %d tests from %d tools with a derivable claim "
            "(%d ambiguous, %d not stated)."
            % (
                totals["tools"],
                totals["tests_from_claims"],
                totals["tools_with_claim"],
                totals["tools_ambiguous"],
                totals["tools_missing"],
            ),
            "",
        ]
    )
    return "\n".join(lines[:start] + body + lines[end:])


# ---------------------------------------------------------------- cli


def main(argv=None):
    p = argparse.ArgumentParser(prog="readmeindex.py", description=__doc__.splitlines()[0])
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--root", help="repository root to scan one level deep")
    src.add_argument("--corpus", help="committed candidate-line corpus (no clone needed)")
    p.add_argument("--root-readme", help="root README.md to reconcile against")
    p.add_argument("--rewrite", metavar="PATH", help="write the regenerated README to PATH")
    p.add_argument("--heading", default="## The tools", help="index section heading")
    p.add_argument("--next-heading", default="## Judgement calls, collected",
                   help="heading that ends the index section")
    p.add_argument("-o", "--output", help="write the JSON report here instead of stdout")
    args = p.parse_args(argv)

    try:
        discovered = discover_from_root(args.root) if args.root else discover_from_corpus(args.corpus)
        if not discovered:
            raise SetupError("no tool directories with a README.md were found")
        tools = build_tools(discovered)
        report = {
            "schema_version": 1,
            "tool": "readme-index",
            "source": "root" if args.root else "corpus",
            "totals": compute_totals(tools),
            "tools": tools,
            "index_differences": [],
        }
        if args.root_readme:
            if not os.path.isfile(args.root_readme):
                raise SetupError("--root-readme is not a file: %s" % args.root_readme)
            with open(args.root_readme, encoding="utf-8") as fh:
                readme_text = fh.read()
            report["index_differences"] = diff_index(parse_index(readme_text), tools)
            if args.rewrite:
                new_text = rewrite_index(readme_text, tools, args.heading, args.next_heading)
                with open(args.rewrite, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(new_text)
        elif args.rewrite:
            raise SetupError("--rewrite requires --root-readme")
    except SetupError as exc:
        sys.stderr.write("setup error: %s\n" % exc)
        return EXIT_SETUP

    text = canonical_json(report)
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return EXIT_DIFF if report["index_differences"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
