#!/usr/bin/env python3
"""validate_transcript.py -- validate a captured_output.txt transcript
directly against transcript-drift/FORMAT.md, using schema.json as the
source of truth for every regex and every diagnostic's severity.

This is deliberately NOT a reimplementation of transcript-drift/driftcheck.py.
driftcheck.py compares a transcript against a README's claims (a *cross*-file
check) and can only fire its structural codes (TRANSCRIPT_HAS_NO_COMMAND_RECORDS,
TRANSCRIPT_RECORD_HAS_NO_EXIT, TRANSCRIPT_SHOWS_TEST_FAILURE) as a side effect
of that comparison. This tool checks a single transcript against the grammar
in FORMAT.md directly -- no README required -- and additionally catches
malformed near-misses (a header with the wrong spacing, "Exit=0", a verdict
line printed before its own "Ran N tests" line) that driftcheck.py has no
reason to look for. See README.md for the full comparison table.

Every regex and every diagnostic code's severity is read out of schema.json
at start-up; nothing here is a hardcoded duplicate of that data. See
README.md and test_validate_transcript.py ("schema really drives behaviour")
for a demonstration.

Exit codes:
    0  every transcript checked is valid
    1  a transcript was read but contains one or more error-severity findings
       (this includes an unreadable/undecodable individual file when scanning
       a --root of many directories -- the run itself succeeded, that one
       transcript failed)
    2  setup error: --root is not a directory, --schema is missing/invalid/
       fails self-validation, no target was given, or (single-file mode
       only) the given file does not exist or cannot be decoded at all --
       there being nothing else to report in that mode

See README.md for the full exit-code table, the diagnostic code table, and
the determinism/relocation proof.
"""

import argparse
import json
import os
import re
import sys

REPORT_SCHEMA_VERSION = 1
TOOL_NAME = "transcript-schema"
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SCHEMA_PATH = os.path.join(HERE, "schema.json")
MAX_TEXT_LEN = 300

REQUIRED_PATTERN_NAMES = (
    "header", "header_lookalike", "exit", "exit_lookalike",
    "ran", "ran_lookalike", "verdict", "test_command",
)


class SetupError(Exception):
    """A problem that prevents the tool from attempting validation at all."""


class ReadError(Exception):
    """A single transcript file could not be read or decoded."""


# --------------------------------------------------------------------------
# Schema loading
# --------------------------------------------------------------------------

def load_schema(path):
    if not os.path.isfile(path):
        raise SetupError("--schema not found: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            schema = json.load(fh)
    except (OSError, UnicodeDecodeError) as exc:
        raise SetupError("--schema could not be read: %s: %s" % (path, exc))
    except json.JSONDecodeError as exc:
        raise SetupError("--schema is not valid JSON: %s: %s" % (path, exc))

    if not isinstance(schema, dict):
        raise SetupError("--schema root must be a JSON object: %s" % path)
    if "schema_version" not in schema or not isinstance(schema["schema_version"], int):
        raise SetupError("--schema missing integer 'schema_version': %s" % path)

    patterns = schema.get("patterns")
    if not isinstance(patterns, dict):
        raise SetupError("--schema missing 'patterns' object: %s" % path)
    for name in REQUIRED_PATTERN_NAMES:
        entry = patterns.get(name)
        if not isinstance(entry, dict) or "regex" not in entry:
            raise SetupError("--schema patterns missing '%s'.regex: %s" % (name, path))

    diagnostics = schema.get("diagnostics")
    if not isinstance(diagnostics, dict) or not diagnostics:
        raise SetupError("--schema missing non-empty 'diagnostics' object: %s" % path)
    for code, entry in diagnostics.items():
        if not isinstance(entry, dict) or entry.get("severity") not in ("error", "info"):
            raise SetupError(
                "--schema diagnostics['%s'] must have severity 'error' or 'info': %s"
                % (code, path))

    return schema


def compile_patterns(schema):
    compiled = {}
    patterns = schema["patterns"]
    for name in REQUIRED_PATTERN_NAMES:
        raw = patterns[name]["regex"]
        try:
            compiled[name] = re.compile(raw)
        except re.error as exc:
            raise SetupError("--schema pattern '%s' does not compile: %s" % (name, exc))
    return compiled


def severity_of(schema, code, default="error"):
    return schema["diagnostics"].get(code, {}).get("severity", default)


def all_codes(schema):
    return sorted(schema["diagnostics"].keys())


# --------------------------------------------------------------------------
# I/O
# --------------------------------------------------------------------------

def read_transcript_text(path, schema):
    """Return decoded text, honouring schema['encoding']. Raises ReadError."""
    enc = schema.get("encoding", {})
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise ReadError("could not open %s: %s" % (path, exc))

    if enc.get("strip_utf8_bom", True) and raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]

    codec = enc.get("decode", "utf-8")
    errors = "strict" if enc.get("strict", True) else "replace"
    try:
        text = raw.decode(codec, errors=errors)
    except (UnicodeDecodeError, LookupError) as exc:
        raise ReadError("could not decode %s as %s: %s" % (path, codec, exc))
    return text


def truncate(text, limit=MAX_TEXT_LEN):
    if len(text) <= limit:
        return text
    return text[:limit] + "...<%d more chars>" % (len(text) - limit)


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------

def split_lines(text):
    """[(lineno, text), ...], 1-based. splitlines() gives universal newline
    handling for free: '\\n', '\\r\\n' and '\\r' are all treated as one break,
    which is how CRLF transcripts are handled without special-casing."""
    return list(enumerate(text.splitlines(), 1))


def find_matches(lines, regex):
    """[(lineno, text, match), ...] for every line regex.match()es."""
    out = []
    for lineno, text in lines:
        m = regex.match(text)
        if m:
            out.append((lineno, text, m))
    return out


def build_finding(schema, code, file_label, line, text, detail=None):
    return {
        "file": file_label,
        "line": line,
        "code": code,
        "severity": severity_of(schema, code),
        "text": truncate(text if text is not None else ""),
        "detail": detail or {},
    }


# --------------------------------------------------------------------------
# Core validation of already-decoded text
# --------------------------------------------------------------------------

def validate_transcript_text(text, schema, patterns, file_label):
    """Validate decoded transcript text against schema/patterns. Returns
    (findings, stats). Pure function -- no filesystem access -- so tests can
    feed it strings directly."""
    findings = []
    lines = split_lines(text)

    header_re = patterns["header"]
    header_lookalike_re = patterns["header_lookalike"]
    exit_re = patterns["exit"]
    exit_lookalike_re = patterns["exit_lookalike"]
    ran_re = patterns["ran"]
    ran_lookalike_re = patterns["ran_lookalike"]
    verdict_re = patterns["verdict"]
    test_command_re = patterns["test_command"]

    headers = find_matches(lines, header_re)  # strict headers only

    # ---- header-lookalike lines: any line that is NOT a strict header but
    # resembles one. Scanned over the whole file.
    header_lines_set = {ln for ln, _, _ in headers}
    for lineno, text_line in lines:
        if lineno in header_lines_set:
            continue
        if header_lookalike_re.match(text_line):
            findings.append(build_finding(
                schema, "TRANSCRIPT_HEADER_MALFORMED", file_label, lineno, text_line))

    # ---- whole-file FAILED-anywhere scan (mirrors driftcheck's verdict scan,
    # which runs over every line regardless of record boundaries).
    for lineno, text_line in lines:
        m = verdict_re.match(text_line)
        if m and m.group(1) == "FAILED":
            findings.append(build_finding(
                schema, "TRANSCRIPT_SHOWS_TEST_FAILURE", file_label, lineno, text_line,
                detail={"verdict": "FAILED"}))

    if not headers:
        findings.append(build_finding(
            schema, "TRANSCRIPT_HAS_NO_COMMAND_RECORDS", file_label,
            lines[0][0] if lines else 1,
            lines[0][1] if lines else "<empty file>"))
        # Whole file is preamble; still worth flagging exit-lookalikes in it.
        for lineno, text_line in lines:
            if exit_lookalike_re.match(text_line):
                findings.append(build_finding(
                    schema, "TRANSCRIPT_PREAMBLE_EXIT_LOOKALIKE", file_label,
                    lineno, text_line))
        findings.sort(key=lambda f: (f["line"] or 0, f["code"]))
        return findings, {"records": 0, "test_records": 0}

    # ---- preamble = lines strictly before the first header
    first_header_line = headers[0][0]
    preamble = [(ln, t) for ln, t in lines if ln < first_header_line]
    for lineno, text_line in preamble:
        if exit_lookalike_re.match(text_line):
            findings.append(build_finding(
                schema, "TRANSCRIPT_PREAMBLE_EXIT_LOOKALIKE", file_label,
                lineno, text_line))

    # ---- build record spans: [header_line+1, next_header_line-1] or EOF
    n = len(headers)
    last_lineno = lines[-1][0] if lines else first_header_line
    records = []
    for i, (hln, htext, hmatch) in enumerate(headers):
        end = headers[i + 1][0] - 1 if i + 1 < n else last_lineno
        body = [(ln, t) for ln, t in lines if hln < ln <= end]
        records.append({
            "header_line": hln, "header_text": htext,
            "command": hmatch.group(1), "body": body,
        })

    test_records = 0
    for rec in records:
        body = rec["body"]
        hln, htext = rec["header_line"], rec["header_text"]
        is_test = bool(test_command_re.search(rec["command"]))
        if is_test:
            test_records += 1

        # exit=, first wins; later strict matches are duplicates (info)
        exit_hits = find_matches(body, exit_re)
        if not exit_hits:
            findings.append(build_finding(
                schema, "TRANSCRIPT_RECORD_HAS_NO_EXIT", file_label, hln, htext,
                detail={"command": rec["command"]}))
        else:
            first_ln, first_txt, first_m = exit_hits[0]
            for dup_ln, dup_txt, dup_m in exit_hits[1:]:
                findings.append(build_finding(
                    schema, "TRANSCRIPT_RECORD_DUPLICATE_EXIT", file_label,
                    dup_ln, dup_txt,
                    detail={"first_exit_line": first_ln, "first_exit_value": first_m.group(1),
                            "duplicate_value": dup_m.group(1)}))

        exit_hit_lines = {ln for ln, _, _ in exit_hits}
        for ln, t in body:
            if ln in exit_hit_lines:
                continue
            if exit_lookalike_re.match(t):
                findings.append(build_finding(
                    schema, "TRANSCRIPT_RECORD_EXIT_MALFORMED", file_label, ln, t))

        # Ran N tests / verdict, required only for test-command records, but
        # near-miss detection runs on every record regardless of the heuristic.
        ran_hits = find_matches(body, ran_re)
        ran_hit_lines = {ln for ln, _, _ in ran_hits}
        for ln, t in body:
            if ln in ran_hit_lines:
                continue
            if ran_lookalike_re.match(t):
                findings.append(build_finding(
                    schema, "TRANSCRIPT_RECORD_RAN_LINE_MALFORMED", file_label, ln, t))

        verdict_hits = find_matches(body, verdict_re)

        if is_test:
            if not ran_hits:
                findings.append(build_finding(
                    schema, "TRANSCRIPT_RECORD_MISSING_RAN_LINE", file_label, hln, htext,
                    detail={"command": rec["command"]}))
            if not verdict_hits:
                findings.append(build_finding(
                    schema, "TRANSCRIPT_RECORD_MISSING_VERDICT", file_label, hln, htext,
                    detail={"command": rec["command"]}))
            if ran_hits and verdict_hits:
                ran_ln = ran_hits[0][0]
                verdict_ln, verdict_txt, _ = verdict_hits[0]
                if verdict_ln < ran_ln:
                    findings.append(build_finding(
                        schema, "TRANSCRIPT_RECORD_VERDICT_BEFORE_RAN", file_label,
                        verdict_ln, verdict_txt,
                        detail={"verdict_line": verdict_ln, "ran_line": ran_ln}))

    findings.sort(key=lambda f: (f["line"] or 0, f["code"]))
    return findings, {"records": len(records), "test_records": test_records}


def validate_file(path, schema, patterns, file_label):
    """Returns (findings, stats, readable:bool)."""
    try:
        text = read_transcript_text(path, schema)
    except ReadError as exc:
        finding = build_finding(schema, "TRANSCRIPT_FILE_UNREADABLE", file_label, 1, str(exc),
                                 detail={"reason": str(exc)})
        return [finding], {"records": 0, "test_records": 0}, False
    findings, stats = validate_transcript_text(text, schema, patterns, file_label)
    return findings, stats, True


# --------------------------------------------------------------------------
# Discovery (directory-scan / --root mode)
# --------------------------------------------------------------------------

def discover(root):
    if not os.path.isdir(root):
        raise SetupError("--root is not a directory: %s" % root)
    with_transcript, without_transcript = [], []
    for name in sorted(os.listdir(root)):
        if name.startswith(".") or name == "__pycache__":
            continue
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        candidate = os.path.join(d, "captured_output.txt")
        if os.path.isfile(candidate):
            with_transcript.append((name, candidate))
        else:
            without_transcript.append(name)
    return with_transcript, without_transcript


# --------------------------------------------------------------------------
# Report assembly
# --------------------------------------------------------------------------

def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def diagnostic_counts(schema, findings):
    counts = {c: 0 for c in all_codes(schema)}
    for f in findings:
        counts[f["code"]] = counts.get(f["code"], 0) + 1
    return counts


def any_error_severity(findings):
    return any(f["severity"] == "error" for f in findings)


def build_root_report(root, schema, patterns):
    with_transcript, without_transcript = discover(root)
    findings = []
    stats = {}
    for name, path in with_transcript:
        file_label = "%s/captured_output.txt" % name
        f, s, _readable = validate_file(path, schema, patterns, file_label)
        findings.extend(f)
        stats[name] = s
    findings.sort(key=lambda f: (f["file"], f["line"] or 0, f["code"]))

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "transcript_schema_version": schema["schema_version"],
        "status": "invalid" if any_error_severity(findings) else "valid",
        "diagnostic_counts": diagnostic_counts(schema, findings),
        "findings": findings,
        "coverage": {
            "directories_scanned": len(with_transcript),
            "directories_with_transcript": sorted(n for n, _ in with_transcript),
            "directories_without_transcript": sorted(without_transcript),
        },
        "stats": stats,
    }
    return report


def build_files_report(paths, schema, patterns):
    findings = []
    stats = {}
    for p in paths:
        f, s, _readable = validate_file(p, schema, patterns, p)
        findings.extend(f)
        stats[p] = s
    findings.sort(key=lambda f: (f["file"], f["line"] or 0, f["code"]))

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "transcript_schema_version": schema["schema_version"],
        "status": "invalid" if any_error_severity(findings) else "valid",
        "diagnostic_counts": diagnostic_counts(schema, findings),
        "findings": findings,
        "files_checked": list(paths),
        "stats": stats,
    }
    return report


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="validate_transcript.py")
    ap.add_argument("paths", nargs="*", help="one or more captured_output.txt files")
    ap.add_argument("--root", default=None,
                     help="directory of tool subdirectories to scan for captured_output.txt")
    ap.add_argument("--schema", default=DEFAULT_SCHEMA_PATH,
                     help="path to the schema JSON (default: schema.json next to this script)")
    ap.add_argument("-o", "--output", default=None, help="write the report JSON here")
    args = ap.parse_args(argv)

    try:
        if not args.root and not args.paths:
            raise SetupError("no target given: pass file paths or --root DIR")
        if args.root and args.paths:
            raise SetupError("--root and positional file paths are mutually exclusive")

        schema = load_schema(args.schema)
        patterns = compile_patterns(schema)

        if args.root:
            report = build_root_report(args.root, schema, patterns)
        else:
            for p in args.paths:
                if not os.path.isfile(p):
                    raise SetupError("file not found: %s" % p)
            report = build_files_report(args.paths, schema, patterns)
            # Single-file mode: an unreadable/undecodable file means there is
            # nothing else to report, so treat it as a setup error (exit 2)
            # rather than a validation finding (exit 1).
            unreadable = [f for f in report["findings"] if f["code"] == "TRANSCRIPT_FILE_UNREADABLE"]
            if unreadable:
                raise SetupError("could not read: %s" % "; ".join(
                    f["detail"].get("reason", f["text"]) for f in unreadable))

        text = canonical_json(report)
        if args.output:
            try:
                with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
            except OSError as exc:
                raise SetupError("could not write --output %s: %s" % (args.output, exc))
        else:
            sys.stdout.write(text)

        return 1 if report["status"] == "invalid" else 0

    except SetupError as exc:
        err_report = {"schema_version": REPORT_SCHEMA_VERSION, "tool": TOOL_NAME,
                       "status": "error", "error": str(exc)}
        text = canonical_json(err_report)
        if args.output:
            try:
                with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
            except OSError:
                sys.stderr.write(text)
        else:
            sys.stderr.write(text)
        return 2


if __name__ == "__main__":
    sys.exit(main())
