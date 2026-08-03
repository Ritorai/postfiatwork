#!/usr/bin/env python3
"""dupdetect - detect lightly reworded duplicate submissions.

Standard library only. Reads a JSON array of evidence records, computes
Jaccard similarity over token k-gram shingles, and emits canonical,
byte-stable JSON describing every flagged pair.

Exit codes:
    0 - no pair scored at or above the threshold
    1 - at least one pair was flagged
    2 - invalid input (bad path, bad JSON, bad schema, bad options)
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata

__version__ = "1.0.0"

DEFAULT_SHINGLE_SIZE = 5
DEFAULT_THRESHOLD = 0.6
SCORE_DECIMALS = 6

EXIT_OK = 0
EXIT_FLAGGED = 1
EXIT_INVALID = 2


class InvalidInputError(Exception):
    """Raised for any condition that must produce exit code 2."""


# --------------------------------------------------------------------------
# Normalization and shingling
# --------------------------------------------------------------------------

def normalize_text(text):
    """Normalize raw text into a list of comparison tokens.

    Rules (documented in README.md):
      1. Unicode NFKC normalization.
      2. Lowercase via str.lower().
      3. Split on any run of whitespace (collapses whitespace).
      4. Strip non-alphanumeric characters from BOTH EDGES of each token.
         Interior characters (apostrophes, hyphens) are preserved.
      5. Drop tokens that are empty after stripping.
    """
    if not isinstance(text, str):
        raise InvalidInputError("text must be a string")
    text = unicodedata.normalize("NFKC", text).lower()
    tokens = []
    for raw in text.split():
        start = 0
        end = len(raw)
        while start < end and not raw[start].isalnum():
            start += 1
        while end > start and not raw[end - 1].isalnum():
            end -= 1
        token = raw[start:end]
        if token:
            tokens.append(token)
    return tokens


def shingles(tokens, size):
    """Return the SET of contiguous token k-grams of length `size`.

    If the token list is shorter than `size`, the result is the EMPTY SET.
    This is a deliberate, conservative choice: a record too short to fill one
    window is never comparable and therefore can never be flagged.
    """
    if size < 1:
        raise InvalidInputError("shingle size must be >= 1")
    if len(tokens) < size:
        return set()
    return {
        " ".join(tokens[i:i + size])
        for i in range(len(tokens) - size + 1)
    }


def jaccard(set_a, set_b):
    """Jaccard similarity |A n B| / |A u B|.

    Defined as 0.0 when the union is empty (i.e. at least one side had no
    shingles). Two uncomparable records are never called duplicates.
    """
    if not set_a or not set_b:
        return 0.0
    union = len(set_a | set_b)
    if union == 0:
        return 0.0
    return len(set_a & set_b) / union


def format_score(value):
    """Round to a fixed number of decimals so output bytes are stable."""
    return round(float(value), SCORE_DECIMALS)


# --------------------------------------------------------------------------
# Input loading and validation
# --------------------------------------------------------------------------

def validate_records(data):
    """Validate the decoded JSON payload and return [(submission_id, text)]."""
    if not isinstance(data, list):
        raise InvalidInputError(
            "top-level JSON value must be an array of record objects, "
            "got %s" % type(data).__name__
        )
    records = []
    seen = set()
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise InvalidInputError(
                "record at index %d must be an object, got %s"
                % (index, type(item).__name__)
            )
        if "submission_id" not in item:
            raise InvalidInputError(
                "record at index %d is missing 'submission_id'" % index)
        if "text" not in item:
            raise InvalidInputError(
                "record at index %d is missing 'text'" % index)
        sid = item["submission_id"]
        text = item["text"]
        if not isinstance(sid, str):
            raise InvalidInputError(
                "record at index %d: 'submission_id' must be a string, got %s"
                % (index, type(sid).__name__)
            )
        if not sid.strip():
            raise InvalidInputError(
                "record at index %d: 'submission_id' must be non-empty" % index)
        if not isinstance(text, str):
            raise InvalidInputError(
                "record at index %d: 'text' must be a string, got %s"
                % (index, type(text).__name__)
            )
        if sid in seen:
            raise InvalidInputError(
                "duplicate submission_id %r at index %d; submission ids must "
                "be unique" % (sid, index)
            )
        seen.add(sid)
        records.append((sid, text))
    return records


def load_records(path):
    """Read and validate a records file. Raises InvalidInputError."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except FileNotFoundError:
        raise InvalidInputError("input file not found: %s" % path)
    except IsADirectoryError:
        raise InvalidInputError("input path is a directory: %s" % path)
    except OSError as exc:
        raise InvalidInputError("could not read %s: %s" % (path, exc))
    except UnicodeDecodeError as exc:
        raise InvalidInputError("input file is not valid UTF-8: %s" % exc)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidInputError("input file is not valid JSON: %s" % exc)
    return validate_records(data)


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------

def analyze(records, shingle_size=DEFAULT_SHINGLE_SIZE,
            threshold=DEFAULT_THRESHOLD):
    """Compare every unordered pair and build the report dictionary."""
    if shingle_size < 1:
        raise InvalidInputError("--shingle-size must be >= 1")
    if not (0.0 <= threshold <= 1.0):
        raise InvalidInputError("--threshold must be between 0.0 and 1.0")

    prepared = []
    for sid, text in records:
        tokens = normalize_text(text)
        prepared.append((sid, shingles(tokens, shingle_size), len(tokens)))

    flagged = []
    comparisons = 0
    for i in range(len(prepared)):
        for j in range(i + 1, len(prepared)):
            comparisons += 1
            sid_i, sh_i, _ = prepared[i]
            sid_j, sh_j, _ = prepared[j]
            score = format_score(jaccard(sh_i, sh_j))
            overlap = sorted(sh_i & sh_j)
            # A pair with no shared shingle is never evidence of duplication,
            # even at --threshold 0.0. This guard is what keeps empty texts,
            # texts shorter than the shingle size, and wholly unrelated
            # records out of the report.
            if not overlap:
                continue
            if score < threshold:
                continue
            # Within a pair, ids are ordered lexicographically.
            a, b = sorted((sid_i, sid_j))
            flagged.append({
                "submission_id_a": a,
                "submission_id_b": b,
                "score": score,
                "overlapping_shingles": overlap,
                "overlap_count": len(overlap),
            })

    # Deterministic ordering: highest score first, then ids ascending.
    flagged.sort(key=lambda p: (-p["score"], p["submission_id_a"],
                                p["submission_id_b"]))

    return {
        "version": __version__,
        "config": {
            "shingle_size": shingle_size,
            "threshold": format_score(threshold),
        },
        "record_count": len(records),
        "comparison_count": comparisons,
        "flagged_count": len(flagged),
        "flagged_pairs": flagged,
    }


def canonical_json(report):
    """Canonical, byte-stable serialization with a trailing newline."""
    return json.dumps(
        report,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ) + "\n"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="dupdetect.py",
        description="Detect lightly reworded duplicate submissions using "
                    "Jaccard similarity over token k-gram shingles.",
    )
    parser.add_argument("records", metavar="records.json",
                        help="path to a JSON array of evidence records")
    parser.add_argument("-o", "--out", default=None,
                        help="write the report here instead of stdout")
    parser.add_argument("--shingle-size", type=int,
                        default=DEFAULT_SHINGLE_SIZE,
                        help="tokens per shingle (default: %d)"
                             % DEFAULT_SHINGLE_SIZE)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help="flag pairs scoring >= this value "
                             "(default: %s)" % DEFAULT_THRESHOLD)
    parser.add_argument("--version", action="version",
                        version="dupdetect %s" % __version__)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        records = load_records(args.records)
        report = analyze(records, args.shingle_size, args.threshold)
        payload = canonical_json(report)
        if args.out:
            try:
                with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(payload)
            except OSError as exc:
                raise InvalidInputError(
                    "could not write %s: %s" % (args.out, exc))
            sys.stderr.write(
                "wrote %s (%d record(s), %d flagged pair(s))\n"
                % (args.out, report["record_count"], report["flagged_count"]))
        else:
            sys.stdout.write(payload)
    except InvalidInputError as exc:
        sys.stderr.write("error: %s\n" % exc)
        return EXIT_INVALID
    return EXIT_FLAGGED if report["flagged_count"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
