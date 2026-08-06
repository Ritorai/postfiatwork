#!/usr/bin/env python3
"""migrate.py -- rewrite legacy captured_output.txt transcripts into the
structure defined by FORMAT.md, using only values already present in each
file.

WHY THIS EXISTS

driftcheck.py can only cross-check a captured_output.txt against its
README once the transcript is a sequence of

    === $ <command> ===
    ...
    exit=<int>

command records (see FORMAT.md). Some transcripts in this repository were
written before that header line existed and instead echo a bare

    $ <command>

line ahead of each command's output. driftcheck.py correctly reports those
as TRANSCRIPT_HAS_NO_COMMAND_RECORDS: nothing in the file can be matched
against the README because there is no record boundary to anchor a match
to.

The fix is NOT to reformat every "$ " line into a header. A header without
a real `exit=<int>` inside its body is exactly the other drift code,
TRANSCRIPT_RECORD_HAS_NO_EXIT -- and inventing an exit value (0 because the
command "looked like it passed", or because a human recalls it passed) is
the one thing this tool refuses to do. A drift checker whose own migration
tool fabricates the values being checked is worse than no migration tool.

THE RULE (stdlib-only, deterministic, byte-preserving)

For a file that contains **zero** normative `=== $ ... ===` headers (the
TRANSCRIPT_HAS_NO_COMMAND_RECORDS shape):

    A bare command line -- a line whose entire content matches ``^\\$ (.+)$``
    -- is promoted to a normative header IF AND ONLY IF the region from the
    line immediately after it up to (but not including) the next bare
    command line or end-of-file contains at least one line matching
    ``^\\s*exit=(-?\\d+)\\s*$`` (the exact grammar EXIT_RE in FORMAT.md/
    driftcheck.py uses).

    Promotion is a pure textual wrap: the line's content, which is already
    exactly ``$ <command>``, becomes ``=== $ <command> ===``. Nothing about
    the command text is parsed, normalised, or altered, and the exit value
    itself is never touched, moved, or duplicated -- it stays exactly where
    it was, now simply inside the record its own line created.

    A bare command line whose region has no such exit line is left
    completely unchanged. It remains free-form text: either preamble (if no
    header precedes it yet) or body text of whatever real record precedes
    it. FORMAT.md explicitly permits this ("body := line*"; "Anything
    outside a record ... is preamble and is ignored").

For a file that already contains at least one normative header (the
TRANSCRIPT_RECORD_HAS_NO_EXIT shape): no bare-line promotion is attempted
at all -- mixing the two heuristics in one file is exactly the kind of
"looks safe, isn't" case this tool exists to avoid. Instead every record's
body is checked for an exit= line. If ANY record in the file lacks one,
the ENTIRE FILE is refused and left byte-for-byte unchanged; the refusal
report names every offending record by its command and its 1-based line
number. There is no per-record fix here: appending a value is
fabrication, and this tool does not fabricate.

A file that has neither a normative header nor a promotable bare line
(no candidates, or candidates but none with a recoverable exit) is also
refused, unchanged, with a reason saying so.

A directory (in --all mode) whose captured_output.txt does not exist is
handled by the FILENAME NORMALIZATION rule below before falling back to a
refusal; an explicit path given on the command line that does not exist is
a setup error (exit 2) -- the two are different failure classes and get
different exit codes on purpose (see EXIT CODES below).

FILENAME NORMALIZATION (--all mode only)

FORMAT.md opens by describing itself as "the transcript file every tool
directory in this repository ships" -- the canonical filename is part of
the structure, not just its content. So: if a tool directory has no
captured_output.txt, and contains exactly ONE other *.txt file that is
already fully FORMAT.md-conformant (>=1 header, and every record already
carries its own exit=), that file is COPIED verbatim (binary, byte-for-byte,
no re-encoding) to captured_output.txt. The source file is left in place,
unchanged -- this is a copy, not a move, so nothing already on disk is ever
destroyed by a migration. If zero or more than one *.txt candidate in the
directory is fully conformant, the directory is refused with a reason
naming every candidate considered and why none was used. This rule never
inspects a candidate's *content* beyond the same structural check used
everywhere else in this file (headers + exit=); it never invents a byte.

VERIFY-NO-REGRESSION (default ON; --no-verify-no-regression to disable)

Every rewrite this tool would make -- a bare-line promotion or a filename
normalization -- is, by default, measured rather than assumed safe. Before
writing, the candidate rewrite is applied to a throwaway temporary copy
(created and destroyed by this run only) containing just that tool's
README.md and the two transcript candidates, and the repository's own
driftcheck.py is invoked on it, once against the ORIGINAL bytes and once
against the PROPOSED bytes. If the number of findings driftcheck.py reports
for that tool would INCREASE, the rewrite is reverted -- the file is left
exactly as it was found, reported as refused, and the new/increased finding
code(s) are named in the reason. If the count holds steady or drops, the
rewrite proceeds normally. This never encodes a tool name, a specific
finding code, or a target number anywhere in the logic -- it is a general
measurement against the repository's own checker, run fresh for every file.
`--no-verify-no-regression` disables this and restores the plain,
unconditional rule (every safely-promotable/copyable byte gets rewritten,
regardless of what driftcheck.py's other checks later make of it).

BYTE FIDELITY

The file is read and written in binary mode. Line boundaries are found
with a byte-level regex (``\\r\\n|\\r|\\n``) so CRLF, LF, and a missing
final newline are all reproduced exactly for every line this tool does not
touch, and the two new characters this tool ever adds (`=== ` prefix and
` ===` suffix) are plain ASCII appended to the existing line bytes with the
line's own terminator left alone.

EXIT CODES

    0  every requested file was migrated (partially or fully) or was
       already conformant; no refusals, no setup errors
    2  a setup error: an explicitly named path does not exist, is not a
       regular file, could not be decoded, or --root/--output could not be
       used; also used for CLI usage errors (e.g. no files and no --all)
    3  at least one file was REFUSED (left unchanged) -- distinct from 2
       so a caller can tell "nothing ran" apart from "something was
       correctly left alone"

Exit code priority when several outcomes occur in one run: 2 beats 3 beats
0 (a setup error is reported even if other files also had refusals).
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter

SCHEMA_VERSION = 1

HERE = os.path.dirname(os.path.abspath(__file__))
DRIFTCHECK_PATH = os.path.join(HERE, "driftcheck.py")

# -- byte-level grammar, identical in spirit to FORMAT.md / driftcheck.py --
LINE_SPLIT_RE = re.compile(rb"\r\n|\r|\n")
HEADER_RE_B = re.compile(rb"^=== \$ (.+?) ===\s*$")
BARE_RE_B = re.compile(rb"^\$ (.+)$")
EXIT_RE_B = re.compile(rb"^\s*exit=(-?\d+)\s*$")

STATUS_MIGRATED = "migrated"
STATUS_UNCHANGED_CONFORMANT = "unchanged_conformant"
STATUS_REFUSED = "refused"
STATUS_SETUP_ERROR = "setup_error"

EXIT_OK = 0
EXIT_SETUP_ERROR = 2
EXIT_REFUSED = 3


class SetupError(Exception):
    """A problem with the invocation itself, not with a transcript's content."""


# ---------------------------------------------------------------------------
# Binary-safe line splitting / joining
# ---------------------------------------------------------------------------

def split_lines(data):
    """Return a list of (content_bytes, eol_bytes) pairs covering `data`
    exactly: b''.join(c + e for c, e in split_lines(data)) == data.

    The final entry has eol_bytes == b'' when the file does not end with a
    newline; an empty file returns []."""
    lines = []
    pos = 0
    for m in LINE_SPLIT_RE.finditer(data):
        lines.append((data[pos:m.start()], m.group(0)))
        pos = m.end()
    if pos < len(data):
        lines.append((data[pos:], b""))
    return lines


def join_lines(lines):
    return b"".join(c + e for c, e in lines)


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

class FileAnalysis:
    """The result of deciding what, if anything, may safely change in one
    transcript. `new_lines` is always populated (identical to the input
    when nothing is safe to change); `changed` says whether it differs."""

    def __init__(self, status, reason, new_lines, old_lines,
                 promoted=None, left_bare=None, refused_records=None):
        self.status = status
        self.reason = reason
        self.new_lines = new_lines
        self.old_lines = old_lines
        self.promoted = promoted or []          # list of (line_no, command)
        self.left_bare = left_bare or []         # list of (line_no, command, why)
        self.refused_records = refused_records or []  # list of (line_no, command, why)

    @property
    def changed(self):
        return self.new_lines != self.old_lines


def _line_no(idx):
    return idx + 1


def analyze(data):
    """Pure function: bytes in, FileAnalysis out. No filesystem access."""
    lines = split_lines(data)
    n = len(lines)

    header_idxs = [i for i, (c, _) in enumerate(lines) if HEADER_RE_B.match(c)]

    if header_idxs:
        return _analyze_normative(lines, header_idxs)
    return _analyze_bare(lines)


def _analyze_normative(lines, header_idxs):
    """File already has >=1 real header. Verify every record has exit=;
    refuse the whole file (unchanged) if any does not."""
    n = len(lines)
    refused_records = []
    for pos, h in enumerate(header_idxs):
        end = header_idxs[pos + 1] if pos + 1 < len(header_idxs) else n
        body_has_exit = any(
            EXIT_RE_B.match(lines[i][0]) for i in range(h + 1, end)
        )
        if not body_has_exit:
            cmd = HEADER_RE_B.match(lines[h][0]).group(1)
            refused_records.append((
                _line_no(h),
                cmd.decode("utf-8", "backslashreplace"),
                "record body (lines %d-%d) contains no line matching "
                "exit=<int>; a value cannot be safely invented"
                % (_line_no(h), end),
            ))

    if refused_records:
        reason = "%d record(s) with no recoverable exit= value: %s" % (
            len(refused_records),
            "; ".join(
                "line %d (%s): %s" % (ln, cmd, why)
                for ln, cmd, why in refused_records
            ),
        )
        return FileAnalysis(STATUS_REFUSED, reason, lines, lines,
                             refused_records=refused_records)

    return FileAnalysis(STATUS_UNCHANGED_CONFORMANT,
                         "all %d record(s) already have a header and an "
                         "exit= value; nothing to migrate" % len(header_idxs),
                         lines, lines)


def _analyze_bare(lines):
    """File has zero real headers. Promote bare '$ cmd' lines whose region
    (up to the next bare line or EOF) already contains an exit= line."""
    n = len(lines)
    bare_idxs = [i for i, (c, _) in enumerate(lines) if BARE_RE_B.match(c)]

    if not bare_idxs:
        return FileAnalysis(
            STATUS_REFUSED,
            "no '=== $ ...' header and no bare '$ command' line found; "
            "nothing safe to migrate (file may be pure preamble or empty)",
            lines, lines,
        )

    promoted = []
    left_bare = []
    new_lines = list(lines)

    for pos, b in enumerate(bare_idxs):
        end = bare_idxs[pos + 1] if pos + 1 < len(bare_idxs) else n
        exit_line = next(
            (i for i in range(b + 1, end) if EXIT_RE_B.match(lines[i][0])),
            None,
        )
        cmd = BARE_RE_B.match(lines[b][0]).group(1).decode("utf-8", "backslashreplace")
        if exit_line is not None:
            content, eol = lines[b]
            new_lines[b] = (b"=== " + content + b" ===", eol)
            promoted.append((_line_no(b), cmd, _line_no(exit_line)))
        else:
            left_bare.append((
                _line_no(b), cmd,
                "no line matching exit=<int> between here and the next "
                "'$ ' line or end-of-file (region lines %d-%d); left as "
                "plain text, not promoted" % (_line_no(b + 1) if b + 1 <= end else _line_no(b), _line_no(end) if end <= n else _line_no(n))
            ))

    if not promoted:
        reason = ("%d candidate '$ command' line(s) found; none has a "
                   "recoverable exit= value: %s" % (
                       len(left_bare),
                       "; ".join("line %d (%s)" % (ln, cmd) for ln, cmd, _ in left_bare[:8]),
                   ))
        return FileAnalysis(STATUS_REFUSED, reason, lines, lines,
                             left_bare=left_bare)

    reason = "promoted %d/%d bare command line(s) to headers using their " \
              "own exit= value; %d left unchanged (no recoverable exit=)" % (
                  len(promoted), len(bare_idxs), len(left_bare))
    return FileAnalysis(STATUS_MIGRATED, reason, new_lines, lines,
                         promoted=promoted, left_bare=left_bare)


def is_fully_conformant_bytes(data):
    """True iff `data` already parses as >=1 normative header and every
    resulting record's body has a recoverable exit=<int>. Used only to
    decide whether an alternate *.txt file is safe to copy verbatim as a
    directory's captured_output.txt (filename normalization, below) --
    never to decide anything about the file's own content beyond that."""
    lines = split_lines(data)
    header_idxs = [i for i, (c, _) in enumerate(lines) if HEADER_RE_B.match(c)]
    if not header_idxs:
        return False
    n = len(lines)
    for pos, h in enumerate(header_idxs):
        end = header_idxs[pos + 1] if pos + 1 < len(header_idxs) else n
        if not any(EXIT_RE_B.match(lines[i][0]) for i in range(h + 1, end)):
            return False
    return True


# ---------------------------------------------------------------------------
# verify-no-regression: measure a candidate rewrite against the repo's own
# driftcheck.py before trusting it, rather than assuming it is safe.
# ---------------------------------------------------------------------------

def _driftcheck_findings_for(tool_name, readme_path, transcript_bytes):
    """Run driftcheck.py (the sibling copy next to this file) against a
    throwaway one-directory tree containing just `tool_name`'s README.md and
    (if not None) a captured_output.txt built from `transcript_bytes`.
    Passing transcript_bytes=None models a MISSING transcript, so this same
    helper measures both a rewrite of an existing file and the creation of
    one via filename normalization. Returns the list of finding dicts for
    that directory. The temp dir is created and destroyed here only."""
    name = tool_name or "tool"
    tmp = tempfile.mkdtemp(prefix="migrate_verify_")
    try:
        scratch_dir = os.path.join(tmp, name)
        os.makedirs(scratch_dir)
        shutil.copy2(readme_path, os.path.join(scratch_dir, "README.md"))
        if transcript_bytes is not None:
            with open(os.path.join(scratch_dir, "captured_output.txt"), "wb") as fh:
                fh.write(transcript_bytes)
        out_path = os.path.join(tmp, "report.json")
        subprocess.run(
            [sys.executable, DRIFTCHECK_PATH, "--root", tmp, "-o", out_path],
            capture_output=True, text=True)
        try:
            with open(out_path, "r", encoding="utf-8") as fh:
                report = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise SetupError("--verify-no-regression: driftcheck.py did not "
                             "produce a readable report: %s" % exc)
        return [f for f in report.get("findings", []) if f["tool"] == name]
    finally:
        assert os.path.basename(tmp.rstrip(os.sep)).startswith("migrate_verify_")
        shutil.rmtree(tmp, ignore_errors=True)


def verify_rewrite(tool_name, readme_path, before_bytes, after_bytes):
    """Measure, don't assume: compare driftcheck.py's findings for this one
    tool before and after a candidate rewrite. `before_bytes` may be None to
    model a currently-missing transcript. Returns a dict describing whether
    the rewrite should be blocked, and why."""
    if not os.path.isfile(DRIFTCHECK_PATH):
        raise SetupError("--verify-no-regression requires driftcheck.py next "
                         "to migrate.py; not found at %s" % DRIFTCHECK_PATH)
    if not os.path.isfile(readme_path):
        return {"attempted": False, "blocked": False,
                "skipped_reason": "no README.md found next to this transcript; "
                                  "verification skipped, rewrite applied unverified",
                "before_count": None, "after_count": None,
                "before_codes": [], "after_codes": [], "new_codes": []}

    before_findings = _driftcheck_findings_for(tool_name, readme_path, before_bytes)
    after_findings = _driftcheck_findings_for(tool_name, readme_path, after_bytes)
    before_counter = Counter(f["code"] for f in before_findings)
    after_counter = Counter(f["code"] for f in after_findings)
    new_codes = sorted(code for code in after_counter
                       if after_counter[code] > before_counter.get(code, 0))
    blocked = len(after_findings) > len(before_findings)
    return {
        "attempted": True, "blocked": blocked, "skipped_reason": None,
        "before_count": len(before_findings), "after_count": len(after_findings),
        "before_codes": sorted(before_counter.elements()),
        "after_codes": sorted(after_counter.elements()),
        "new_codes": new_codes,
    }


# ---------------------------------------------------------------------------
# Filesystem plumbing
# ---------------------------------------------------------------------------

def discover_all(root):
    """Sorted list of (tool_name, captured_output_path_or_None) for every
    tool subdirectory of root, deterministic order."""
    if not os.path.isdir(root):
        raise SetupError("--root is not a directory: %s" % root)
    out = []
    for name in sorted(os.listdir(root)):
        if name.startswith(".") or name == "__pycache__":
            continue
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        p = os.path.join(d, "captured_output.txt")
        out.append((name, p if os.path.isfile(p) else None))
    return out


def process_file(path, dry_run, tool_name=None, verify_no_regression=True):
    """Return a result dict for one existing captured_output.txt path."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise SetupError("could not read %s: %s" % (path, exc))

    analysis = analyze(data)
    result = {
        "tool": tool_name,
        "path": path,
        "status": analysis.status,
        "reason": analysis.reason,
        "changed": analysis.changed,
        "promoted": [{"line": ln, "command": cmd, "exit_line": el}
                     for ln, cmd, el in analysis.promoted],
        "left_bare": [{"line": ln, "command": cmd, "why": why}
                      for ln, cmd, why in analysis.left_bare],
        "refused_records": [{"line": ln, "command": cmd, "why": why}
                            for ln, cmd, why in analysis.refused_records],
        "source_file": None,
        "verification": None,
    }

    if analysis.changed:
        new_data = join_lines(analysis.new_lines)

        if verify_no_regression:
            dir_path = os.path.dirname(os.path.abspath(path))
            readme_path = os.path.join(dir_path, "README.md")
            verification = verify_rewrite(tool_name, readme_path, data, new_data)
            result["verification"] = verification
            if verification["blocked"]:
                result["status"] = STATUS_REFUSED
                result["changed"] = False
                result["reason"] = (
                    "verify-no-regression: this rewrite would increase "
                    "driftcheck.py's findings for this tool from %d to %d "
                    "(new/increased code(s): %s); reverted, left unchanged. "
                    "Underlying migration reason (not applied): %s"
                    % (verification["before_count"], verification["after_count"],
                       ", ".join(verification["new_codes"]) or "(count only)",
                       analysis.reason))
                return result

        if not dry_run:
            try:
                with open(path, "wb") as fh:
                    fh.write(new_data)
            except OSError as exc:
                raise SetupError("could not write %s: %s" % (path, exc))

    return result


def missing_result(tool_name, path, reason=None):
    return {
        "tool": tool_name,
        "path": path,
        "status": STATUS_REFUSED,
        "reason": reason or (
            "captured_output.txt does not exist in this directory; "
            "migrate.py does not create transcripts, only rewrites "
            "existing ones"),
        "changed": False,
        "promoted": [],
        "left_bare": [],
        "refused_records": [],
        "source_file": None,
        "verification": None,
    }


def try_filename_normalization(dir_path, tool_name, dry_run, verify_no_regression=True):
    """A tool directory has no captured_output.txt. If exactly one other
    *.txt file in it is already fully FORMAT.md-conformant, copy it
    verbatim (binary, byte-identical) to captured_output.txt -- a copy, not
    a move, so the original is never destroyed. Refuse, unchanged, if zero
    or more than one candidate qualifies."""
    target = os.path.join(dir_path, "captured_output.txt")
    readme_path = os.path.join(dir_path, "README.md")

    try:
        entries = sorted(
            fn for fn in os.listdir(dir_path)
            if fn.endswith(".txt") and fn != "captured_output.txt"
            and os.path.isfile(os.path.join(dir_path, fn))
        )
    except OSError as exc:
        raise SetupError("could not list %s: %s" % (dir_path, exc))

    conforming = []
    for fn in entries:
        try:
            with open(os.path.join(dir_path, fn), "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        if is_fully_conformant_bytes(data):
            conforming.append((fn, data))

    if len(conforming) == 0:
        reason = ("captured_output.txt does not exist in this directory, and "
                  "no *.txt file here is fully FORMAT.md-conformant to copy "
                  "from; migrate.py does not create transcripts, only "
                  "copies from an already-conformant one. "
                  + ("no *.txt files present at all."
                     if not entries else
                     "candidate(s) checked and rejected: %s." % ", ".join(entries)))
        return missing_result(tool_name, target, reason=reason)

    if len(conforming) > 1:
        names = sorted(fn for fn, _ in conforming)
        reason = ("captured_output.txt does not exist, and %d *.txt files "
                  "here are ALL fully conformant (%s); ambiguous which one "
                  "is authoritative, refusing rather than guessing"
                  % (len(names), ", ".join(names)))
        return missing_result(tool_name, target, reason=reason)

    fn, data = conforming[0]

    verification = None
    if verify_no_regression:
        verification = verify_rewrite(tool_name, readme_path, None, data)
        if verification["blocked"]:
            reason = (
                "verify-no-regression: creating captured_output.txt by "
                "copying '%s' would increase driftcheck.py's findings for "
                "this tool from %d to %d (new/increased code(s): %s); "
                "reverted, left unchanged"
                % (fn, verification["before_count"], verification["after_count"],
                   ", ".join(verification["new_codes"]) or "(count only)"))
            result = missing_result(tool_name, target, reason=reason)
            result["verification"] = verification
            return result

    if not dry_run:
        try:
            with open(target, "wb") as fh:
                fh.write(data)
        except OSError as exc:
            raise SetupError("could not write %s: %s" % (target, exc))

    reason = ("captured_output.txt did not exist; copied verbatim (binary, "
              "byte-identical) from the single fully-conformant candidate "
              "'%s' -- '%s' is left in place, unchanged (copy, not move: "
              "nothing already on disk is destroyed)" % (fn, fn))
    return {
        "tool": tool_name, "path": target, "status": STATUS_MIGRATED,
        "reason": reason, "changed": True,
        "promoted": [], "left_bare": [], "refused_records": [],
        "source_file": fn, "verification": verification,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(
        prog="migrate.py",
        description="Rewrite legacy captured_output.txt transcripts into "
                    "FORMAT.md's normative shape using only values already "
                    "present in each file. Never invents an exit code.")
    ap.add_argument("files", nargs="*", metavar="FILE",
                    help="explicit captured_output.txt path(s) to process")
    ap.add_argument("--all", action="store_true",
                    help="process every tool directory under --root")
    ap.add_argument("--root", default=".",
                    help="root containing tool subdirectories (with --all)")
    ap.add_argument("--dry-run", action="store_true",
                    help="analyze and report; write nothing")
    ap.add_argument("--report", default=None,
                    help="write the machine-readable JSON result here "
                        "(deterministic, sorted keys, no timestamps)")
    ap.add_argument("--no-verify-no-regression", dest="verify_no_regression",
                    action="store_false", default=True,
                    help="skip the driftcheck.py-measured no-regression check "
                        "(default: on) and apply the uniform rule unconditionally")
    return ap


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def print_human(report, out=sys.stdout):
    out.write("migrate.py %s\n" % ("(dry-run)" if report["dry_run"] else ""))
    for r in report["results"]:
        out.write("[%s] %s (%s)\n" % (r["status"], r["path"], r["tool"]))
        out.write("    %s\n" % r["reason"])
        for p in r["promoted"]:
            out.write("    promoted line %d: %s (exit= at line %d)\n" %
                      (p["line"], p["command"], p["exit_line"]))
        for b in r["left_bare"]:
            out.write("    left unchanged line %d: %s -- %s\n" %
                      (b["line"], b["command"], b["why"]))
        for x in r["refused_records"]:
            out.write("    refused line %d: %s -- %s\n" %
                      (x["line"], x["command"], x["why"]))
        if r.get("source_file"):
            out.write("    source: %s\n" % r["source_file"])
        v = r.get("verification")
        if v and v.get("attempted"):
            out.write("    verify-no-regression: %d -> %d finding(s) for this tool%s\n" %
                      (v["before_count"], v["after_count"],
                       " [BLOCKED]" if v["blocked"] else ""))
        elif v and v.get("skipped_reason"):
            out.write("    verify-no-regression: skipped (%s)\n" % v["skipped_reason"])
    out.write("\ncounts: %s\n" % json.dumps(report["counts"], sort_keys=True))


def main(argv=None):
    ap = build_parser()
    try:
        args = ap.parse_args(argv)
        if args.all and args.files:
            raise SetupError("--all and explicit FILE arguments are mutually exclusive")
        if not args.all and not args.files:
            raise SetupError("nothing to do: pass FILE path(s) or --all")

        results = []
        if args.all:
            for tool_name, path in discover_all(args.root):
                if path is None:
                    dir_path = os.path.join(args.root, tool_name)
                    results.append(try_filename_normalization(
                        dir_path, tool_name, args.dry_run,
                        verify_no_regression=args.verify_no_regression))
                    continue
                results.append(process_file(
                    path, args.dry_run, tool_name=tool_name,
                    verify_no_regression=args.verify_no_regression))
        else:
            for f in args.files:
                if not os.path.isfile(f):
                    raise SetupError("no such file: %s" % f)
                tool_name = os.path.basename(os.path.dirname(os.path.abspath(f))) or None
                results.append(process_file(
                    f, args.dry_run, tool_name=tool_name,
                    verify_no_regression=args.verify_no_regression))

        results.sort(key=lambda r: (r["tool"] or "", r["path"]))
        counts = {}
        for r in results:
            counts[r["status"]] = counts.get(r["status"], 0) + 1

        report = {
            "schema_version": SCHEMA_VERSION,
            "tool": "transcript-drift-migrate",
            "dry_run": args.dry_run,
            "counts": counts,
            "results": results,
        }
    except SetupError as exc:
        sys.stderr.write("migrate.py: setup error: %s\n" % exc)
        return EXIT_SETUP_ERROR

    print_human(report)

    if args.report:
        try:
            with open(args.report, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(canonical_json(report))
        except OSError as exc:
            sys.stderr.write("migrate.py: setup error: could not write --report %s: %s\n"
                             % (args.report, exc))
            return EXIT_SETUP_ERROR

    if report["counts"].get(STATUS_REFUSED, 0) > 0:
        return EXIT_REFUSED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
