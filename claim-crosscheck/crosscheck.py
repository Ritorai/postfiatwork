#!/usr/bin/env python3
"""crosscheck.py -- cross-check file-level README claims against a tool's
own committed JSON report.

A README in this repository routinely narrates what a tool's *own* JSON
report contains: "file X carries a hostname leak", "121 confirmed leaks
across 23 files", "`temp_directory` | 101". Those sentences are claims about
a specific artifact sitting right next to the README, and nothing re-checks
them after the fact. A report gets re-run, a review entry moves a match from
"leak" to "benign", a count table is edited by hand -- and the prose keeps
asserting the old state forever, because nothing forces it to agree with the
JSON.

crosscheck.py extracts three kinds of CLAIM from a README:

  PRESENCE  "`file` carries a leak" / "`file` is flagged" / "`file` is
            present [...] at `file` line N"
            -- checked against: does `file` appear as a `file`/`path`/
            `filename` value inside a *positive* top-level list in the
            report (see "Bucket polarity" below)?

  CATEGORY  "`file` is a `category` leak"
            -- checked against: does a positive-bucket entry for `file`
            carry a `category`/`type`/`kind`/`class` field equal to
            `category`?

  COUNT     "| `category` | 101 |" (a two-cell table row), "121 confirmed
            leaks", "100 matches reviewed and dismissed as benign", "0 stale
            review entries", "across 23 files", "`dir/` alone accounts for
            12 of them"
            -- checked against: an integer found in the report under a
            matching key, or a bucket's length, or (for "across N files")
            the number of distinct files named across all positive buckets.

Every DISCREPANCY record -- a claim contradicted by the report, or a claim
the report cannot substantiate at all -- carries all of:

    readme_path, readme_line, readme_quote,
    report_path, json_pointer, report_excerpt

`readme_quote` is an exact substring of the README file as read (newlines
preserved verbatim, CRLF included) starting at `readme_line`. `json_pointer`
is RFC 6901. `report_excerpt` is canonical JSON (sorted keys, compact) of
the smallest report fragment that supports or contradicts the claim.

Bucket polarity
----------------
A report's top-level object may have several keys whose value is a JSON
array of objects ("buckets"). A bucket is NEGATIVE if its key, lower-cased
and with underscores turned to spaces, contains any of:

    benign, stale, ignored, exempt, excluded, suppressed, dismissed,
    skipped, allowlist, allowed, denylist

every other array-valued top-level key is POSITIVE. `confirmed_leaks` is
positive; `reviewed_benign` and `stale_review_entries` are negative. A
PRESENCE/CATEGORY claim must be satisfied by a POSITIVE-bucket entry --
appearing only in a negative bucket does not satisfy "carries a leak", and
is exactly the discrepancy this tool exists to catch.

Report discovery
-----------------
Given a tool directory, the report file is *not* guessed from a naming
convention -- it is read off the README itself, the same way a human
reviewer would: every backtick-quoted `*.json` filename that also exists on
disk is a candidate; candidates are narrowed (in order) by (a) whether the
JSON is "report-shaped" (a dict with at least one array of dicts, at least
one of which has a file/path/filename key), (b) whether the filename
appears within the phrase "committed as `<file>`", (c) whether the filename
appears near the word "committed" at all. If exactly one candidate survives
at any stage, that stage's answer is used. Otherwise discovery is
AMBIGUOUS or ABSENT and the tool directory is skipped (or, with
--strict-discovery, treated as an error). See README.md, "Report
discovery", for examples of the exact regexes and why each one exists.

Exit codes
-----------
    0   every extracted claim, in every report actually read, matched
    1   at least one discrepancy, and every report examined was readable
    2   a targeted report was missing / unreadable / not valid JSON /
        not a JSON object, OR a command-line usage error

Standard library only. No third-party packages. No network access.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

TOOL_NAME = "crosscheck"
SCHEMA_VERSION = 1

EXIT_OK = 0
EXIT_DISCREPANCIES = 1
EXIT_ERROR = 2

FILE_KEYS = ("file", "path", "filename")
CATEGORY_KEYS = ("category", "type", "kind", "class")

NEGATIVE_BUCKET_KEYWORDS = (
    "benign", "stale", "ignored", "exempt", "excluded", "suppressed",
    "dismissed", "skipped", "allowlist", "allowed", "denylist",
)


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------

class CrosscheckError(Exception):
    """Raised for setup errors: exit 2."""


class ReportError(CrosscheckError):
    """A targeted report file could not be loaded as a report."""


# --------------------------------------------------------------------------
# Canonical JSON / IO helpers
# --------------------------------------------------------------------------

def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True) + "\n"


def read_text_exact(path: str) -> str:
    """Read a text file preserving exact line endings (no CRLF -> LF
    translation), so extracted README quotes are byte-exact substrings."""
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def load_report(path: str) -> Dict[str, Any]:
    """Load and validate a report JSON file. Raises ReportError (-> exit 2)
    for anything that is not a readable, parseable, dict-shaped report."""
    if not os.path.isfile(path):
        raise ReportError("report not found: %s" % path)
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise ReportError("could not read report %s: %s" % (path, exc))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReportError("report %s is not valid UTF-8: %s" % (path, exc))
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReportError("report %s is not valid JSON: %s" % (path, exc))
    if not isinstance(data, dict):
        raise ReportError(
            "report %s: top-level JSON value must be an object, got %s"
            % (path, type(data).__name__))
    return data


# --------------------------------------------------------------------------
# RFC 6901 JSON Pointer helpers
# --------------------------------------------------------------------------

def pointer_escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def make_pointer(parts: Sequence[Any]) -> str:
    if not parts:
        return ""
    return "/" + "/".join(
        pointer_escape(str(p)) if not isinstance(p, int) else str(p)
        for p in parts
    )


def resolve_pointer(data: Any, pointer: str) -> Any:
    if pointer == "":
        return data
    if not pointer.startswith("/"):
        raise ValueError("invalid pointer: %r" % pointer)
    cur = data
    for raw_tok in pointer[1:].split("/"):
        tok = raw_tok.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, list):
            cur = cur[int(tok)]
        elif isinstance(cur, dict):
            cur = cur[tok]
        else:
            raise KeyError(pointer)
    return cur


def iter_tree(obj: Any, parts: Optional[List[Any]] = None):
    """Yield (parts, value) for every node (dict, list, and scalar leaf) in
    the tree, in a deterministic (sorted-key, list-index) order."""
    if parts is None:
        parts = []
    yield list(parts), obj
    if isinstance(obj, dict):
        for key in sorted(obj.keys()):
            yield from iter_tree(obj[key], parts + [key])
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            yield from iter_tree(item, parts + [idx])


# --------------------------------------------------------------------------
# Bucket classification and file lookup
# --------------------------------------------------------------------------

def classify_buckets(report: Dict[str, Any]) -> Dict[str, str]:
    """Map each top-level array-of-dicts key to 'positive' or 'negative'."""
    polarity: Dict[str, str] = {}
    for key, value in report.items():
        if not isinstance(value, list):
            continue
        name = key.lower().replace("_", " ")
        if any(kw in name for kw in NEGATIVE_BUCKET_KEYWORDS):
            polarity[key] = "negative"
        else:
            polarity[key] = "positive"
    return polarity


def entry_file_value(entry: Any) -> Optional[str]:
    if not isinstance(entry, dict):
        return None
    for key in FILE_KEYS:
        val = entry.get(key)
        if isinstance(val, str):
            return val
    return None


def entry_category_value(entry: Dict[str, Any]) -> Optional[str]:
    for key in CATEGORY_KEYS:
        val = entry.get(key)
        if isinstance(val, str):
            return val
    return None


def is_prefix_token(token: str) -> bool:
    """A token like 'doc-validator/' or bare 'doc-validator' (no '.' in its
    final path segment) is treated as a directory prefix rather than an
    exact file."""
    if token.endswith("/"):
        return True
    last_segment = token.rsplit("/", 1)[-1]
    return "." not in last_segment


def file_matches_token(file_value: str, token: str) -> bool:
    if is_prefix_token(token):
        prefix = token if token.endswith("/") else token + "/"
        bare = prefix[:-1]
        return file_value == token or file_value == bare or \
            file_value.startswith(prefix)
    return file_value == token


class FileMatch:
    __slots__ = ("pointer_parts", "bucket", "polarity", "entry")

    def __init__(self, pointer_parts, bucket, polarity, entry):
        self.pointer_parts = pointer_parts
        self.bucket = bucket
        self.polarity = polarity
        self.entry = entry


def find_file_matches(report: Dict[str, Any], polarity: Dict[str, str],
                       token: str) -> List[FileMatch]:
    """All bucket entries whose file/path/filename value matches token,
    across every classified bucket, in deterministic (bucket-name, index)
    order."""
    out: List[FileMatch] = []
    for bucket in sorted(polarity.keys()):
        items = report.get(bucket)
        if not isinstance(items, list):
            continue
        for idx, entry in enumerate(items):
            fv = entry_file_value(entry)
            if fv is not None and file_matches_token(fv, token):
                out.append(FileMatch([bucket, idx], bucket, polarity[bucket],
                                      entry))
    return out


def whole_report_bucket_summary(report: Dict[str, Any],
                                 polarity: Dict[str, str]) -> Dict[str, Any]:
    """A real, derived (not fabricated) excerpt used when a claim's subject
    cannot be found anywhere in the report: the size of every classified
    bucket, so a reader can see the report has no relevant entries."""
    return {bucket: len(report[bucket]) for bucket in sorted(polarity)}


def distinct_positive_files(report: Dict[str, Any],
                             polarity: Dict[str, str]) -> List[str]:
    files = set()
    for bucket, pol in polarity.items():
        if pol != "positive":
            continue
        for entry in report.get(bucket, []):
            fv = entry_file_value(entry)
            if fv is not None:
                files.add(fv)
    return sorted(files)


def excerpt_for_entry(entry: Any) -> str:
    return canonical_json(entry).rstrip("\n")


# --------------------------------------------------------------------------
# README claim extraction
# --------------------------------------------------------------------------

PATH_TOKEN_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-/]*$")
EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,8}$")


def looks_like_path_token(token: str) -> bool:
    if not PATH_TOKEN_RE.match(token):
        return False
    if token.endswith("/"):
        return "/" in token
    body = token[:-1] if token.endswith("/") else token
    last_segment = body.rsplit("/", 1)[-1]
    return "/" in token or bool(EXT_RE.search(last_segment))


BACKTICK_RE = re.compile(r"`([^`\n]+)`")

# Sentence/clause boundary: end-of-sentence punctuation followed by
# whitespace and a capital letter, a backtick, or a quote/paren -- i.e. the
# start of a new clause. Also treated as boundaries: blank lines, and the
# start of a markdown heading / table row / fence line.
SENT_BOUND_RE = re.compile(r"(?<=[.!?:])\s+(?=[A-Z`\"'(])")
BLOCK_BOUND_RE = re.compile(r"\n{2,}|\n(?=[#|>]|```)")


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def sentence_spans(text: str) -> List[Tuple[int, int]]:
    """Deterministic list of (start, end) spans covering the whole text,
    split first on block boundaries (blank lines / heading / table / fence
    starts), then on sentence boundaries within each block."""
    bounds = {0, len(text)}
    for m in BLOCK_BOUND_RE.finditer(text):
        bounds.add(m.start())
        bounds.add(m.end())
    block_bounds = sorted(bounds)
    spans: List[Tuple[int, int]] = []
    for a, b in zip(block_bounds, block_bounds[1:]):
        block = text[a:b]
        local = {0, len(block)}
        for m in SENT_BOUND_RE.finditer(block):
            local.add(m.end())
        local_sorted = sorted(local)
        for x, y in zip(local_sorted, local_sorted[1:]):
            if y > x:
                spans.append((a + x, a + y))
    return spans


def span_containing(spans: List[Tuple[int, int]], pos: int) -> Tuple[int, int]:
    for a, b in spans:
        if a <= pos < b:
            return a, b
    return spans[-1] if spans else (pos, pos)


class Claim:
    def __init__(self, kind: str, readme_path: str, readme_line: int,
                 readme_quote: str, **extra):
        self.kind = kind
        self.readme_path = readme_path
        self.readme_line = readme_line
        self.readme_quote = readme_quote
        self.extra = extra

    def sort_key(self):
        return (self.readme_line, self.kind, self.extra.get("token", ""),
                self.extra.get("category", ""),
                self.extra.get("expected", 0))


TABLE_ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*(-?\d+)\s*\|", re.MULTILINE)

# The progressive form ("is carrying") includes its auxiliary verb in the
# match itself, not just the "-ing" tail -- otherwise the gap between the
# subject token and the match start would contain "is ", which is not list
# glue, and the claim would be silently dropped.
PRESENCE_VERB_RES = [
    re.compile(r"\b(?:is|are)\s+carrying\b", re.IGNORECASE),
    re.compile(r"\bcarr(?:y|ies)\b", re.IGNORECASE),
    re.compile(r"\bis\s+flagged\b", re.IGNORECASE),
    re.compile(r"\bare\s+flagged\b", re.IGNORECASE),
    re.compile(r"\bis\s+present\b", re.IGNORECASE),
    re.compile(r"\bare\s+present\b", re.IGNORECASE),
]

LOCATION_RE = re.compile(r"\bat\s+`([^`\n]+)`\s+line\s+(\d+)\b")

CATEGORY_RE = re.compile(
    r"`([^`\n]+)`\s+is\s+an?\s+`([^`\n]+)`\s+leak\b", re.IGNORECASE)

DIR_COUNT_RE = re.compile(
    r"`([^`\n]+)`\s+(?:alone\s+)?accounts?\s+for\s+(\d+)\s+of\s+them\b",
    re.IGNORECASE)

SUMMARY_COUNT_PATTERNS = [
    (re.compile(r"(\d+)\s+confirmed\s+leaks?\b", re.IGNORECASE), "confirmed"),
    (re.compile(
        r"(\d+)\s+matches?\s+reviewed\s+and\s+dismissed\s+as\s+benign\b",
        re.IGNORECASE), "benign"),
    (re.compile(r"(\d+)\s+stale\s+review\s+entr(?:y|ies)\b", re.IGNORECASE),
     "stale"),
]

ACROSS_FILES_RE = re.compile(r"across\s+(\d+)\s+files?\b", re.IGNORECASE)


SEPARATOR_LINE_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def table_header_has_category(text: str, row_start_offset: int) -> bool:
    """True iff the markdown table containing the row at row_start_offset
    has a header row whose cells include the word 'category'. Walks
    upward from the row through contiguous '|'-prefixed lines to find the
    '|---|...' separator, then checks the line directly above it."""
    lines = text.split("\n")
    row_line_idx = text.count("\n", 0, row_start_offset)
    idx = row_line_idx
    while idx >= 0 and lines[idx].lstrip().startswith("|"):
        idx -= 1
    block_start = idx + 1
    if block_start + 1 >= len(lines):
        return False
    header_line = lines[block_start]
    sep_line = lines[block_start + 1] if block_start + 1 < len(lines) else ""
    if not SEPARATOR_LINE_RE.match(sep_line):
        return False
    return re.search(r"\bcategory\b", header_line, re.IGNORECASE) is not None


def extract_table_count_claims(text: str, readme_path: str) -> List[Claim]:
    claims = []
    for m in TABLE_ROW_RE.finditer(text):
        if not table_header_has_category(text, m.start()):
            continue
        category = m.group(1)
        count = int(m.group(2))
        line = line_of(text, m.start())
        quote = m.group(0)
        claims.append(Claim(
            "TABLE_COUNT", readme_path, line, quote,
            category=category, expected=count,
        ))
    return claims


def extract_summary_count_claims(text: str, readme_path: str) -> List[Claim]:
    claims = []
    for pattern, metric in SUMMARY_COUNT_PATTERNS:
        for m in pattern.finditer(text):
            line = line_of(text, m.start())
            claims.append(Claim(
                "SUMMARY_COUNT", readme_path, line, m.group(0),
                metric=metric, expected=int(m.group(1)),
            ))
    for m in ACROSS_FILES_RE.finditer(text):
        line = line_of(text, m.start())
        claims.append(Claim(
            "DISTINCT_FILE_COUNT", readme_path, line, m.group(0),
            expected=int(m.group(1)),
        ))
    return claims


# A token qualifies as part of the subject list feeding a PRESENCE verb only
# if the text between it and the next token (or the verb) is pure list glue
# -- a comma and/or "and" and whitespace, nothing else. This is what lets
# "`A`, `B`, `C` and `D` carry X" collect all four tokens while "`A`, keyed
# by `B`, and every entry carries X" collects none: "keyed by" is not glue,
# so the chain breaks and `A`/`B` are never attributed to "carries".
GLUE_RE = re.compile(r"^\s*(?:,\s*)?(?:and\s+)?\s*$")


def extract_presence_claims(text: str, readme_path: str) -> List[Claim]:
    spans = sentence_spans(text)
    claims = []
    seen = set()
    for verb_re in PRESENCE_VERB_RES:
        for vm in verb_re.finditer(text):
            a, b = span_containing(spans, vm.start())
            toks = [(m.start() + a, m.end() + a, m.group(1))
                    for m in BACKTICK_RE.finditer(text[a:vm.start()])]
            if not toks:
                continue
            if not GLUE_RE.match(text[toks[-1][1]:vm.start()]):
                continue
            chain = [toks[-1]]
            i = len(toks) - 2
            while i >= 0:
                gap = text[toks[i][1]:toks[i + 1][0]]
                if GLUE_RE.match(gap):
                    chain.append(toks[i])
                    i -= 1
                else:
                    break
            chain.reverse()

            q_start = chain[0][0]
            lstrip_n = len(text[a:b]) - len(text[a:b].lstrip())
            if q_start < a + lstrip_n:
                q_start = a + lstrip_n
            rstrip_n = len(text[a:b]) - len(text[a:b].rstrip())
            q_end = b - rstrip_n
            quote = text[q_start:q_end]
            line = line_of(text, q_start)

            for tok_start, _tok_end, token in chain:
                if not looks_like_path_token(token):
                    continue
                key = (q_start, token)
                if key in seen:
                    continue
                seen.add(key)
                claims.append(Claim(
                    "PRESENCE", readme_path, line, quote, token=token,
                ))
    return claims


def extract_location_claims(text: str, readme_path: str) -> List[Claim]:
    claims = []
    for m in LOCATION_RE.finditer(text):
        token = m.group(1)
        claimed_line = int(m.group(2))
        if not looks_like_path_token(token):
            continue
        line = line_of(text, m.start())
        claims.append(Claim(
            "LOCATION", readme_path, line, m.group(0), token=token,
            claimed_line=claimed_line,
        ))
    return claims


def extract_category_claims(text: str, readme_path: str) -> List[Claim]:
    claims = []
    for m in CATEGORY_RE.finditer(text):
        token, category = m.group(1), m.group(2)
        if not looks_like_path_token(token):
            continue
        line = line_of(text, m.start())
        claims.append(Claim(
            "CATEGORY", readme_path, line, m.group(0), token=token,
            category=category,
        ))
    return claims


def extract_dir_count_claims(text: str, readme_path: str) -> List[Claim]:
    claims = []
    for m in DIR_COUNT_RE.finditer(text):
        token = m.group(1)
        expected = int(m.group(2))
        if not looks_like_path_token(token):
            continue
        line = line_of(text, m.start())
        claims.append(Claim(
            "DIR_COUNT", readme_path, line, m.group(0), token=token,
            expected=expected,
        ))
    return claims


FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


def mask_fenced_blocks(text: str) -> str:
    """Blank out the interior of every well-formed ``` ... ``` fenced code
    block, replacing every non-newline character with a space. Length and
    line numbers are preserved exactly, so offsets computed against the
    masked text line up with the original file -- but nothing inside a
    fence can match a claim pattern any more (a count, a table row, or a
    file path that happens to appear in example command output is not a
    claim about the report). An UNTERMINATED fence (an odd, trailing ```
    with no closing ```) is not matched by this regex at all and is left
    unmasked -- see README.md, "Limitations", for why that is a known,
    named gap rather than a silent one."""
    def repl(m):
        return "".join(ch if ch == "\n" else " " for ch in m.group(0))
    return FENCE_RE.sub(repl, text)


def extract_claims(readme_path: str, raw_text: str) -> List[Claim]:
    text = mask_fenced_blocks(raw_text)
    claims: List[Claim] = []
    claims += extract_table_count_claims(text, readme_path)
    claims += extract_summary_count_claims(text, readme_path)
    claims += extract_presence_claims(text, readme_path)
    claims += extract_location_claims(text, readme_path)
    claims += extract_category_claims(text, readme_path)
    claims += extract_dir_count_claims(text, readme_path)
    claims.sort(key=lambda c: c.sort_key())
    return claims


# --------------------------------------------------------------------------
# Claim evaluation
# --------------------------------------------------------------------------

def discrepancy(claim: Claim, report_path: str, pointer_parts: Sequence[Any],
                 excerpt: str, reason: str) -> Dict[str, Any]:
    return {
        "kind": claim.kind,
        "reason": reason,
        "readme_path": claim.readme_path,
        "readme_line": claim.readme_line,
        "readme_quote": claim.readme_quote,
        "report_path": report_path,
        "json_pointer": make_pointer(pointer_parts),
        "report_excerpt": excerpt,
        "claim_detail": {k: v for k, v in claim.extra.items()},
    }


def evaluate_presence(claim: Claim, report: Dict[str, Any],
                       polarity: Dict[str, str],
                       report_path: str) -> Optional[Dict[str, Any]]:
    token = claim.extra["token"]
    matches = find_file_matches(report, polarity, token)
    positive = [m for m in matches if m.polarity == "positive"]
    if positive:
        return None
    if matches:
        m = matches[0]
        return discrepancy(
            claim, report_path, m.pointer_parts, excerpt_for_entry(m.entry),
            "README claims %r carries/is flagged/is present, but the only "
            "report entry (or entries) naming it are in the non-positive "
            "bucket %r." % (token, m.bucket))
    summary = whole_report_bucket_summary(report, polarity)
    return discrepancy(
        claim, report_path, [], canonical_json(summary).rstrip("\n"),
        "README claims %r carries/is flagged/is present, but no bucket in "
        "the report names it at all." % (token,))


def evaluate_location(claim: Claim, report: Dict[str, Any],
                       polarity: Dict[str, str],
                       report_path: str) -> Optional[Dict[str, Any]]:
    token = claim.extra["token"]
    claimed_line = claim.extra["claimed_line"]
    matches = find_file_matches(report, polarity, token)
    positive = [m for m in matches if m.polarity == "positive"]
    for m in positive:
        if isinstance(m.entry, dict) and m.entry.get("line") == claimed_line:
            return None
    if positive:
        m = positive[0]
        return discrepancy(
            claim, report_path, m.pointer_parts, excerpt_for_entry(m.entry),
            "README claims %r line %d is reported, but no positive-bucket "
            "entry for %r has that line number." % (token, claimed_line,
                                                      token))
    if matches:
        m = matches[0]
        return discrepancy(
            claim, report_path, m.pointer_parts, excerpt_for_entry(m.entry),
            "README claims %r line %d is reported, but %r only appears in "
            "the non-positive bucket %r." % (token, claimed_line, token,
                                              m.bucket))
    summary = whole_report_bucket_summary(report, polarity)
    return discrepancy(
        claim, report_path, [], canonical_json(summary).rstrip("\n"),
        "README claims %r line %d is reported, but %r appears in no "
        "bucket." % (token, claimed_line, token))


def evaluate_category(claim: Claim, report: Dict[str, Any],
                       polarity: Dict[str, str],
                       report_path: str) -> Optional[Dict[str, Any]]:
    token = claim.extra["token"]
    category = claim.extra["category"]
    matches = find_file_matches(report, polarity, token)
    positive = [m for m in matches if m.polarity == "positive"]
    for m in positive:
        cat = entry_category_value(m.entry) if isinstance(m.entry, dict) \
            else None
        if cat is not None and cat.lower() == category.lower():
            return None
    if positive:
        m = positive[0]
        cat = entry_category_value(m.entry) if isinstance(m.entry, dict) \
            else None
        return discrepancy(
            claim, report_path, m.pointer_parts, excerpt_for_entry(m.entry),
            "README claims %r is a %r leak, but the report's category for "
            "it is %r." % (token, category, cat))
    if matches:
        m = matches[0]
        return discrepancy(
            claim, report_path, m.pointer_parts, excerpt_for_entry(m.entry),
            "README claims %r is a %r leak, but %r only appears in the "
            "non-positive bucket %r." % (token, category, token, m.bucket))
    summary = whole_report_bucket_summary(report, polarity)
    return discrepancy(
        claim, report_path, [], canonical_json(summary).rstrip("\n"),
        "README claims %r is a %r leak, but %r appears in no bucket."
        % (token, category, token))


def evaluate_dir_count(claim: Claim, report: Dict[str, Any],
                        polarity: Dict[str, str],
                        report_path: str) -> Optional[Dict[str, Any]]:
    token = claim.extra["token"]
    expected = claim.extra["expected"]
    matches = find_file_matches(report, polarity, token)
    positive = [m for m in matches if m.polarity == "positive"]
    actual = len(positive)
    if actual == expected:
        return None
    excerpt = canonical_json(
        sorted(entry_file_value(m.entry) for m in positive)
    ).rstrip("\n") if positive else canonical_json(
        whole_report_bucket_summary(report, polarity)).rstrip("\n")
    pointer_parts = positive[0].pointer_parts[:1] if positive else []
    return discrepancy(
        claim, report_path, pointer_parts, excerpt,
        "README claims %r accounts for %d, but %d positive-bucket entries "
        "match that prefix." % (token, expected, actual))


def find_int_metric(report: Dict[str, Any], keyword: str
                     ) -> List[Tuple[List[Any], int]]:
    """All (pointer_parts, value) pairs where an int leaf's own key, or an
    enclosing top-level array key, contains `keyword` (case-insensitive,
    underscores as spaces)."""
    out: List[Tuple[List[Any], int]] = []
    counts_obj = report.get("counts")
    if isinstance(counts_obj, dict):
        for key in sorted(counts_obj.keys()):
            val = counts_obj[key]
            if isinstance(val, int) and not isinstance(val, bool):
                if keyword in key.lower():
                    out.append((["counts", key], val))
            elif isinstance(val, dict):
                for k2 in sorted(val.keys()):
                    v2 = val[k2]
                    if isinstance(v2, int) and not isinstance(v2, bool):
                        if keyword in k2.lower():
                            out.append((["counts", key, k2], v2))
    for key, value in sorted(report.items()):
        if isinstance(value, list):
            if keyword in key.lower():
                out.append(([key], len(value)))
    return out


def evaluate_table_count(claim: Claim, report: Dict[str, Any],
                          polarity: Dict[str, str],
                          report_path: str) -> Optional[Dict[str, Any]]:
    category = claim.extra["category"]
    expected = claim.extra["expected"]
    candidates = find_int_metric(report, category.lower())
    exact = [c for c in candidates if c[0] and c[0][-1] == category]
    use = exact if exact else candidates
    if not use:
        return discrepancy(
            claim, report_path, [], canonical_json(
                whole_report_bucket_summary(report, polarity)).rstrip("\n"),
            "README table claims %r has count %d, but the report has no "
            "integer field named %r." % (category, expected, category))
    values = {v for _, v in use}
    if len(values) == 1 and expected in values:
        return None
    pointer_parts, actual = sorted(use, key=lambda pv: pv[0])[0]
    excerpt = canonical_json(
        resolve_pointer_container(report, pointer_parts)).rstrip("\n")
    return discrepancy(
        claim, report_path, pointer_parts, excerpt,
        "README table claims %r has count %d, but the report records %d "
        "at %s." % (category, expected, actual, make_pointer(pointer_parts)))


def resolve_pointer_container(report: Dict[str, Any],
                               pointer_parts: Sequence[Any]) -> Any:
    """Resolve to the *parent* container of the final key, so the excerpt
    shows surrounding context instead of a bare scalar."""
    if not pointer_parts:
        return report
    if len(pointer_parts) == 1:
        return {pointer_parts[0]: resolve_pointer(report, make_pointer(
            pointer_parts))}
    parent_parts = pointer_parts[:-1]
    parent = resolve_pointer(report, make_pointer(parent_parts))
    return parent


def evaluate_summary_count(claim: Claim, report: Dict[str, Any],
                            polarity: Dict[str, str],
                            report_path: str) -> Optional[Dict[str, Any]]:
    metric = claim.extra["metric"]
    expected = claim.extra["expected"]
    candidates = find_int_metric(report, metric)
    if not candidates:
        return discrepancy(
            claim, report_path, [], canonical_json(
                whole_report_bucket_summary(report, polarity)).rstrip("\n"),
            "README claims %d for metric %r, but the report has no "
            "integer field matching that name." % (expected, metric))
    values = {v for _, v in candidates}
    if len(values) == 1 and expected in values:
        return None
    pointer_parts, actual = sorted(candidates, key=lambda pv: pv[0])[0]
    excerpt = canonical_json(
        resolve_pointer_container(report, pointer_parts)).rstrip("\n")
    return discrepancy(
        claim, report_path, pointer_parts, excerpt,
        "README claims %d for metric %r, but the report records %d at %s."
        % (expected, metric, actual, make_pointer(pointer_parts)))


def evaluate_distinct_file_count(claim: Claim, report: Dict[str, Any],
                                  polarity: Dict[str, str],
                                  report_path: str) -> Optional[Dict[str, Any]]:
    expected = claim.extra["expected"]
    files = distinct_positive_files(report, polarity)
    if len(files) == expected:
        return None
    return discrepancy(
        claim, report_path, [], canonical_json(files).rstrip("\n"),
        "README claims %d distinct files, but %d distinct files are named "
        "across positive buckets." % (expected, len(files)))


EVALUATORS = {
    "PRESENCE": evaluate_presence,
    "LOCATION": evaluate_location,
    "CATEGORY": evaluate_category,
    "DIR_COUNT": evaluate_dir_count,
    "TABLE_COUNT": evaluate_table_count,
    "SUMMARY_COUNT": evaluate_summary_count,
    "DISTINCT_FILE_COUNT": evaluate_distinct_file_count,
}


def evaluate_claims(claims: List[Claim], report: Dict[str, Any],
                     report_path: str) -> List[Dict[str, Any]]:
    polarity = classify_buckets(report)
    out = []
    for claim in claims:
        fn = EVALUATORS[claim.kind]
        result = fn(claim, report, polarity, report_path)
        if result is not None:
            out.append(result)
    return out


# --------------------------------------------------------------------------
# Report discovery
# --------------------------------------------------------------------------

JSON_MENTION_RE = re.compile(r"`([A-Za-z0-9_.\-/]+\.json)`")
COMMITTED_AS_RE = re.compile(r"committed\s+as\s+`([^`]+\.json)`",
                              re.IGNORECASE)


def is_report_shaped(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    for value in data.values():
        if isinstance(value, list):
            for entry in value:
                if entry_file_value(entry) is not None:
                    return True
    return False


class Discovery:
    def __init__(self, report_path: Optional[str], status: str,
                 candidates: List[str]):
        self.report_path = report_path
        self.status = status  # "unique" | "ambiguous" | "absent"
        self.candidates = candidates


def discover_report(tool_dir: str, readme_text: str) -> Discovery:
    try:
        on_disk = sorted(
            f for f in os.listdir(tool_dir) if f.endswith(".json")
        )
    except OSError:
        on_disk = []
    on_disk_set = set(on_disk)

    mentioned = sorted(set(JSON_MENTION_RE.findall(readme_text)) &
                        on_disk_set)

    def load_ok(name):
        try:
            return load_report(os.path.join(tool_dir, name))
        except ReportError:
            return None

    shaped = sorted(
        name for name in mentioned
        if (data := load_ok(name)) is not None and is_report_shaped(data)
    )
    if len(shaped) == 1:
        return Discovery(os.path.join(tool_dir, shaped[0]), "unique",
                          shaped)

    committed_as = sorted(set(COMMITTED_AS_RE.findall(readme_text)) &
                           on_disk_set)
    pool = committed_as if committed_as else []
    pool_in_mentioned = sorted(set(pool) & (set(shaped) or set(mentioned)))
    if len(pool_in_mentioned) == 1:
        return Discovery(os.path.join(tool_dir, pool_in_mentioned[0]),
                          "unique", pool_in_mentioned)

    # Shape detection cannot tell a genuinely-empty, all-clean report (every
    # bucket is []) apart from an unrelated JSON file with no file-keyed
    # entries -- there is nothing to key on. Fall back to prose proximity
    # over every existing, mentioned filename in that case.
    near_pool = shaped if shaped else mentioned
    near_committed = []
    for name in near_pool:
        pat = re.compile(
            r"committed[^.\n]{0,80}`%s`|`%s`[^.\n]{0,80}committed"
            % (re.escape(name), re.escape(name)))
        if pat.search(readme_text):
            near_committed.append(name)
    if len(near_committed) == 1:
        return Discovery(os.path.join(tool_dir, near_committed[0]),
                          "unique", near_committed)

    candidates = shaped if shaped else mentioned
    if not candidates:
        return Discovery(None, "absent", [])
    return Discovery(None, "ambiguous", candidates)


# --------------------------------------------------------------------------
# One (readme, report) target
# --------------------------------------------------------------------------

class TargetResult:
    def __init__(self, readme_path: str, report_path: Optional[str]):
        self.readme_path = readme_path
        self.report_path = report_path
        self.claims_checked = 0
        self.discrepancies: List[Dict[str, Any]] = []
        self.error: Optional[str] = None
        self.skipped_reason: Optional[str] = None


def run_target(readme_path: str, report_path: Optional[str]) -> TargetResult:
    result = TargetResult(readme_path, report_path)
    if not os.path.isfile(readme_path):
        result.error = "README not found: %s" % readme_path
        return result
    try:
        text = read_text_exact(readme_path)
    except OSError as exc:
        result.error = "could not read README %s: %s" % (readme_path, exc)
        return result
    except UnicodeDecodeError as exc:
        result.error = "README %s is not valid UTF-8: %s" % (readme_path,
                                                                exc)
        return result

    claims = extract_claims(readme_path, text)
    result.claims_checked = len(claims)
    if not claims:
        return result

    if report_path is None:
        result.error = "report path required: README has %d checkable " \
                        "claim(s) but no report was given" % len(claims)
        return result
    try:
        report = load_report(report_path)
    except ReportError as exc:
        result.error = str(exc)
        return result

    result.discrepancies = evaluate_claims(claims, report, report_path)
    return result


# --------------------------------------------------------------------------
# Human-readable rendering
# --------------------------------------------------------------------------

def render_human(results: List[TargetResult]) -> str:
    lines = []
    total_claims = sum(r.claims_checked for r in results)
    total_disc = sum(len(r.discrepancies) for r in results)
    errored = [r for r in results if r.error]
    skipped = [r for r in results if r.skipped_reason and not r.error]

    for r in results:
        if r.skipped_reason and not r.error and r.claims_checked == 0 \
                and r.report_path is None:
            continue
        lines.append("== %s ==" % r.readme_path)
        if r.error:
            lines.append("  ERROR: %s" % r.error)
            continue
        lines.append("  report: %s" % (r.report_path or "(none)"))
        lines.append("  claims checked: %d" % r.claims_checked)
        if not r.discrepancies:
            lines.append("  discrepancies: 0")
        else:
            lines.append("  discrepancies: %d" % len(r.discrepancies))
            for d in r.discrepancies:
                lines.append("  ---")
                lines.append("  kind: %s" % d["kind"])
                lines.append("  reason: %s" % d["reason"])
                lines.append("  readme: %s:%d" % (d["readme_path"],
                                                    d["readme_line"]))
                lines.append("  readme_quote: %r" % d["readme_quote"])
                lines.append("  report: %s#%s" % (d["report_path"],
                                                    d["json_pointer"]))
                lines.append("  report_excerpt: %s" % d["report_excerpt"])
        lines.append("")

    if skipped:
        lines.append("skipped (no discoverable report, no claims found):")
        for r in skipped:
            lines.append("  %s" % r.readme_path)
        lines.append("")

    lines.append("TOTAL: %d claim(s) checked, %d discrepancy(ies), "
                  "%d error(s)" % (total_claims, total_disc, len(errored)))
    return "\n".join(lines) + "\n"


def render_json(results: List[TargetResult]) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "results": [],
        "totals": {
            "claims_checked": sum(r.claims_checked for r in results),
            "discrepancies": sum(len(r.discrepancies) for r in results),
            "errors": sum(1 for r in results if r.error),
        },
    }
    for r in results:
        entry = {
            "readme_path": r.readme_path,
            "report_path": r.report_path,
            "claims_checked": r.claims_checked,
            "discrepancies": r.discrepancies,
        }
        if r.error:
            entry["error"] = r.error
        if r.skipped_reason:
            entry["skipped_reason"] = r.skipped_reason
        payload["results"].append(entry)
    return canonical_json(payload)


def overall_exit_code(results: List[TargetResult]) -> int:
    if any(r.error for r in results):
        return EXIT_ERROR
    if any(r.discrepancies for r in results):
        return EXIT_DISCREPANCIES
    return EXIT_OK


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def list_tool_dirs(root: str) -> List[str]:
    try:
        entries = sorted(os.listdir(root))
    except OSError as exc:
        raise CrosscheckError("could not list --root %s: %s" % (root, exc))
    out = []
    for name in entries:
        full = os.path.join(root, name)
        if os.path.isdir(full) and os.path.isfile(
                os.path.join(full, "README.md")):
            out.append(name)
    return out


def build_results(args) -> List[TargetResult]:
    if args.readme is not None:
        return [run_target(args.readme, args.report)]

    if args.tool is not None:
        tool_dir = os.path.join(args.root, args.tool)
        readme_path = os.path.join(tool_dir, "README.md")
        if not os.path.isfile(readme_path):
            raise CrosscheckError("no README.md in tool dir %s" % tool_dir)
        report_path = args.report
        disc = None
        if report_path is None:
            text = read_text_exact(readme_path)
            disc = discover_report(tool_dir, text)
            if disc.status == "unique":
                report_path = disc.report_path
        result = run_target(readme_path, report_path)
        if report_path is None and result.claims_checked and disc is not \
                None:
            result.error = (
                "report discovery %s for %s; candidates: %s"
                % (disc.status, tool_dir, ", ".join(disc.candidates)))
        return [result]

    # --all
    results = []
    for name in list_tool_dirs(args.root):
        tool_dir = os.path.join(args.root, name)
        readme_path = os.path.join(tool_dir, "README.md")
        text = read_text_exact(readme_path)
        disc = discover_report(tool_dir, text)
        report_path = disc.report_path if disc.status == "unique" else None
        result = run_target(readme_path, report_path)
        if report_path is None:
            if result.claims_checked == 0:
                result.skipped_reason = "no checkable claims (report " \
                                         "discovery: %s)" % disc.status
                result.error = None
            elif args.strict_discovery:
                result.error = (
                    "report discovery %s; candidates: %s"
                    % (disc.status, ", ".join(disc.candidates)))
            else:
                result.skipped_reason = (
                    "report discovery %s (%d claim(s) left unchecked); "
                    "candidates: %s"
                    % (disc.status, result.claims_checked,
                       ", ".join(disc.candidates)))
                result.error = None
                result.claims_checked = 0
        results.append(result)
    return results


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="crosscheck.py",
        description="Cross-check file-level README claims against a "
                     "tool's own committed JSON report.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--readme", default=None,
                       help="explicit README.md path (pairs with --report)")
    mode.add_argument("--tool", default=None,
                       help="tool directory name under --root")
    mode.add_argument("--all", action="store_true",
                       help="scan every tool dir under --root with a "
                            "README.md")
    ap.add_argument("--root", default=".",
                     help="repository root (default: .)")
    ap.add_argument("--report", default=None,
                     help="explicit report JSON path (overrides "
                          "discovery; required with --readme)")
    ap.add_argument("--strict-discovery", action="store_true",
                     help="in --all mode, treat ambiguous/absent report "
                          "discovery as an error (exit 2) instead of a "
                          "skip, for tools whose README has checkable "
                          "claims")
    out = ap.add_mutually_exclusive_group()
    out.add_argument("--json", action="store_true",
                      help="print canonical JSON to stdout instead of "
                           "human-readable text")
    out.add_argument("-o", "--output", default=None,
                      help="write canonical JSON to this file; stdout "
                           "still gets human-readable text unless --json "
                           "is also given")
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = build_arg_parser()
    args = ap.parse_args(argv)

    if args.readme is not None and args.report is None:
        sys.stderr.write(
            "crosscheck: input error: --readme requires --report\n")
        return EXIT_ERROR
    if args.readme is None and args.tool is None and not args.all:
        sys.stderr.write(
            "crosscheck: input error: one of --readme, --tool, --all is "
            "required\n")
        return EXIT_ERROR

    try:
        results = build_results(args)
    except CrosscheckError as exc:
        sys.stderr.write("crosscheck: input error: %s\n" % exc)
        return EXIT_ERROR

    json_text = render_json(results)
    if args.output:
        out_dir = os.path.dirname(args.output)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        try:
            with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(json_text)
        except OSError as exc:
            sys.stderr.write(
                "crosscheck: input error: could not write -o %s: %s\n"
                % (args.output, exc))
            return EXIT_ERROR

    if args.json:
        sys.stdout.write(json_text)
    else:
        sys.stdout.write(render_human(results))

    return overall_exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
