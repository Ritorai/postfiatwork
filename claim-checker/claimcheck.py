#!/usr/bin/env python3
"""claimcheck.py -- stdlib-only claim checker for verifier notes.

Extracts SHA-256, test-count and exit-code CLAIMS from a free-text
"verifier notes" file and checks each one against a submitted evidence
bundle directory: hashing named files, running the bundle's own unittest
suite, and (under tight safety rules) running a claimed command.

This tool does not index or validate a bundle on its own terms -- see
"Relationship to bundle_index / evidence-harness" in README.md. It only
answers one question per claim: is this specific, quoted assertion in the
notes MATCHED, MISMATCHED, or UNSUBSTANTIATED by what is actually in the
bundle -- and what, exactly, did we look at to decide that?

Standard library only. No third-party packages. No network access.

Exit codes:
  0  every extracted claim is MATCHED (vacuously true if there are none)
  1  at least one claim is MISMATCHED, UNSUBSTANTIATED or UNVERIFIABLE_COMMAND
  2  invalid input / usage error (nothing was checked, no verdict exists)

SAFETY (read this before touching COMMAND_* below):
  EXIT_CODE_CLAIM verification runs a command *taken from the notes file*,
  which is untrusted input -- the whole point of this tool is that notes
  may be wrong or adversarial. subprocess is invoked WITHOUT shell=True,
  the command text is parsed with shlex.split (never string-interpolated
  into a shell), a hard 60s timeout is enforced, and the command is refused
  unless it is exactly `python3 <file-inside-the-bundle> [args...]`. Every
  refusal is recorded as a claim with result UNVERIFIABLE_COMMAND and an
  evidence_source explaining why -- never silently dropped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

TOOL_NAME = "claimcheck"
TOOL_VERSION = "1.0.0"
SCHEMA_VERSION = 1

# Hard wall-clock cap on any command executed for EXIT_CODE_CLAIM
# verification. Overridable only for test purposes (see test_claimcheck.py);
# the CLI itself always uses this value.
COMMAND_TIMEOUT_SECONDS = 60

RESULT_MATCHED = "MATCHED"
RESULT_MISMATCHED = "MISMATCHED"
RESULT_UNSUBSTANTIATED = "UNSUBSTANTIATED"
RESULT_UNVERIFIABLE_COMMAND = "UNVERIFIABLE_COMMAND"

CLAIM_SHA256 = "SHA256_CLAIM"
CLAIM_TEST_COUNT = "TEST_COUNT_CLAIM"
CLAIM_EXIT_CODE = "EXIT_CODE_CLAIM"

_TYPE_RANK = {CLAIM_SHA256: 0, CLAIM_TEST_COUNT: 1, CLAIM_EXIT_CODE: 2}


class InputError(Exception):
    """Raised for conditions that map to exit code 2."""


# --------------------------------------------------------------------------
# Claim-extraction grammar
#
# Verifier notes are free text; claimcheck recognises three narrow,
# documented patterns per line (see README.md "Notes grammar"). A single
# line may contain more than one claim of the same or different types --
# every occurrence is extracted independently.
# --------------------------------------------------------------------------

HEX64_RE = re.compile(r"(?<![0-9A-Fa-f])([0-9A-Fa-f]{64})(?![0-9A-Fa-f])")

# A "filename token": something with a dot-extension, not whitespace/quote/
# paren/backtick, so it can sit next to punctuation without swallowing it.
# The extension must START WITH A LETTER (not a bare digit run) -- this is
# deliberate and fixes a real false-positive found while testing: without
# it, a version number like "2.5.1" in a sentence such as "Version 2.5.1
# sha256: <hash>" satisfies "something.something" and gets misread as the
# claimed filename, when no such file was ever named. Real extensions
# (.py, .txt, .json, .tar, ...) start with a letter; a trailing all-digit
# group after a dot almost always means "this is a number", not a file.
_FILE_TOKEN = r"[^\s`\"'()]+\.[A-Za-z][A-Za-z0-9]{0,9}"

# sha256(FILE) = HASH   or   sha256(FILE):HASH   -- FILE captured, looked up
# by scanning the text immediately BEFORE the hash match.
_SHA_PAREN_RE = re.compile(r"sha-?256\s*\(\s*(" + _FILE_TOKEN + r")\s*\)\s*[:=]?\s*$", re.IGNORECASE)

# FILE: HASH   or   FILE sha256: HASH   or   FILE sha256 = HASH -- FILE
# immediately before an optional "sha256" tag and a mandatory ':' or '='.
_SHA_COLON_RE = re.compile(r"(" + _FILE_TOKEN + r")\s*(?:sha-?256)?\s*[:=]\s*$", re.IGNORECASE)

# HASH  FILE   or   HASH *FILE  (sha256sum(1) output format) -- FILE
# immediately after the hash, separated by whitespace and an optional '*'.
_SHA_SUFFIX_RE = re.compile(r"^\s+\*?(" + _FILE_TOKEN + r")\b")

RAN_TESTS_RE = re.compile(r"\bran\s+(\d+)\s+tests?\b", re.IGNORECASE)
BARE_TESTS_RE = re.compile(r"\b(\d+)\s+tests?\b", re.IGNORECASE)

# A command claim MUST be backtick-delimited -- this is the one place the
# notes grammar requires explicit marking, precisely because what follows
# may be executed. Free text mentioning "exit code 0" with no backtick
# command is not enough to identify what to run.
COMMAND_RE = re.compile(r"`([^`]+)`")
EXIT_CLAIM_RE = re.compile(r"exit(?:[ _-]?code)?\s*[:=]?\s*(-?\d+)", re.IGNORECASE)

# Characters that are inert to subprocess (no shell=True is ever used) but
# which would matter under a shell, and which the task brief specifically
# calls out as untrustworthy. A command claim containing one of these is
# refused outright -- see README.md "Safety rules" point 1.
SHELL_METACHARACTERS = set(";|&$<>\n\r")


def sha256_hexdigest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json_bytes(obj: object) -> bytes:
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return (text + "\n").encode("ascii")


# --------------------------------------------------------------------------
# Bundle file discovery (mirrors bundle_index.py's convention: sorted,
# forward-slash, root-relative paths; no descent into symlinked dirs).
# --------------------------------------------------------------------------

def discover_files(root: Path) -> List[str]:
    relpaths: List[str] = []
    root_str = str(root)
    for dirpath, _dirnames, filenames in os.walk(root_str, onerror=lambda e: None):
        for name in filenames:
            abspath = Path(dirpath) / name
            rel = os.path.relpath(str(abspath), root_str)
            relpaths.append(Path(rel).as_posix())
    relpaths.sort()
    return relpaths


def hash_bundle_files(bundle_dir: Path, relpaths: Sequence[str]) -> Dict[str, Optional[str]]:
    """relpath -> sha256 hex digest, or None if the file could not be read."""
    out: Dict[str, Optional[str]] = {}
    for rel in relpaths:
        try:
            data = (bundle_dir / rel).read_bytes()
        except OSError:
            out[rel] = None
            continue
        out[rel] = sha256_hexdigest(data)
    return out


def resolve_filename(name: str, relpaths: Sequence[str]) -> Tuple[Optional[str], List[str]]:
    """Resolve a claimed filename against the bundle's known relative paths.

    Returns (resolved_relpath_or_None, candidates). `candidates` is only
    non-empty when the lookup was ambiguous (multiple basename matches);
    in that case resolved is None and the claim is UNSUBSTANTIATED with
    the candidate list recorded as evidence.
    """
    norm = name.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    if norm in relpaths:
        return norm, []
    base = os.path.basename(norm)
    matches = sorted(r for r in relpaths if os.path.basename(r) == base)
    if len(matches) == 1:
        return matches[0], []
    if len(matches) > 1:
        return None, matches
    return None, []


# --------------------------------------------------------------------------
# Claim occurrences: a lightweight intermediate form produced by scanning
# the notes text, before any bundle verification happens. `offset` is the
# character position of the match start within its line and exists purely
# to give every claim a deterministic total-order tiebreak (sort_keys=True
# only orders JSON *object* keys, never list items -- see README.md).
# --------------------------------------------------------------------------

class ClaimOccurrence:
    __slots__ = ("claim_type", "line_no", "offset", "line_text", "params")

    def __init__(self, claim_type: str, line_no: int, offset: int, line_text: str, params: dict):
        self.claim_type = claim_type
        self.line_no = line_no
        self.offset = offset
        self.line_text = line_text
        self.params = params

    def sort_key(self):
        return (self.line_no, self.offset, _TYPE_RANK[self.claim_type])


def _extract_sha256_occurrences(line_no: int, line: str) -> List[ClaimOccurrence]:
    occurrences = []
    for m in HEX64_RE.finditer(line):
        start, end = m.span(1)
        asserted_hash = m.group(1)
        before = line[:start]
        after = line[end:]
        filename = None
        pm = _SHA_PAREN_RE.search(before)
        if pm:
            filename = pm.group(1)
        else:
            cm = _SHA_COLON_RE.search(before)
            if cm:
                filename = cm.group(1)
            else:
                sm = _SHA_SUFFIX_RE.match(after)
                if sm:
                    filename = sm.group(1)
        occurrences.append(ClaimOccurrence(
            CLAIM_SHA256, line_no, start, line,
            {"asserted_hash": asserted_hash, "filename": filename},
        ))
    return occurrences


def _extract_test_count_occurrences(line_no: int, line: str) -> List[ClaimOccurrence]:
    occurrences = []
    ran_spans = []
    for m in RAN_TESTS_RE.finditer(line):
        ran_spans.append(m.span())
        occurrences.append(ClaimOccurrence(
            CLAIM_TEST_COUNT, line_no, m.start(), line, {"asserted_count": int(m.group(1))},
        ))
    for m in BARE_TESTS_RE.finditer(line):
        # Skip a "N tests" match that is really the tail of an already
        # captured "Ran N tests" occurrence (same digit run).
        if any(m.start() >= rs and m.end() <= re_ for rs, re_ in ran_spans):
            continue
        prefix = line[:m.start()]
        if re.search(r"\bran\s+$", prefix, re.IGNORECASE):
            continue
        occurrences.append(ClaimOccurrence(
            CLAIM_TEST_COUNT, line_no, m.start(), line, {"asserted_count": int(m.group(1))},
        ))
    return occurrences


def _extract_exit_code_occurrences(line_no: int, line: str) -> List[ClaimOccurrence]:
    occurrences = []
    commands = list(COMMAND_RE.finditer(line))
    for m in EXIT_CLAIM_RE.finditer(line):
        asserted_exit = int(m.group(1))
        # Associate with the nearest backtick command that appears before
        # this exit-code mention on the same line (closest one wins).
        command_text = None
        best_start = -1
        for cm in commands:
            if cm.start() <= m.start() and cm.start() > best_start:
                best_start = cm.start()
                command_text = cm.group(1)
        occurrences.append(ClaimOccurrence(
            CLAIM_EXIT_CODE, line_no, m.start(), line,
            {"asserted_exit": asserted_exit, "command_text": command_text},
        ))
    return occurrences


def extract_claim_occurrences(notes_text: str) -> List[ClaimOccurrence]:
    occurrences: List[ClaimOccurrence] = []
    lines = notes_text.split("\n")
    # A trailing empty element from a final newline is not a real line.
    if lines and lines[-1] == "":
        lines = lines[:-1]
    for idx, raw_line in enumerate(lines, start=1):
        occurrences.extend(_extract_sha256_occurrences(idx, raw_line))
        occurrences.extend(_extract_test_count_occurrences(idx, raw_line))
        occurrences.extend(_extract_exit_code_occurrences(idx, raw_line))
    occurrences.sort(key=lambda o: o.sort_key())
    return occurrences


# --------------------------------------------------------------------------
# Command safety gate (EXIT_CODE_CLAIM only)
# --------------------------------------------------------------------------

def _refuse(reason: str) -> Tuple[bool, str]:
    return False, reason


def vet_command(command_text: str, bundle_dir: Path, relpaths: Sequence[str]) -> Tuple[bool, str, Optional[List[str]]]:
    """Decide whether `command_text` may be executed.

    Returns (allowed, reason, argv). `argv` is only set when allowed is
    True. `reason` is always a human-readable explanation, used verbatim
    as evidence_source when the command is refused.
    """
    bad_chars = sorted(SHELL_METACHARACTERS.intersection(command_text))
    if bad_chars:
        return _refuse(
            "refused to execute: command text contains shell metacharacter(s) %s; "
            "this tool never runs commands through a shell, and text like this is "
            "exactly what an untrusted notes file could use to hide a second command"
            % ", ".join(repr(c) for c in bad_chars)
        ) + (None,)

    try:
        argv = shlex.split(command_text, posix=True)
    except ValueError as exc:
        return _refuse("refused to execute: command text could not be parsed (%s)" % exc) + (None,)

    if not argv:
        return _refuse("refused to execute: command text is empty after parsing") + (None,)

    if argv[0] != "python3":
        return _refuse(
            "refused to execute: only a bare 'python3 <file>' invocation is permitted; "
            "argv[0] was %r" % argv[0]
        ) + (None,)

    if len(argv) < 2:
        return _refuse("refused to execute: 'python3' with no target file is not permitted") + (None,)

    target = argv[1]
    if os.path.isabs(target):
        return _refuse("refused to execute: target %r is an absolute path, not a bundle-relative one" % target) + (None,)

    norm_target = os.path.normpath(target).replace("\\", "/")
    if norm_target == ".." or norm_target.startswith("../"):
        return _refuse("refused to execute: target %r resolves outside the bundle directory" % target) + (None,)

    if norm_target not in relpaths:
        return _refuse("refused to execute: target file %r was not found inside the bundle" % target) + (None,)

    return True, "python3 invocation of a file inside the bundle", argv


def run_command(argv: List[str], cwd: Path) -> Tuple[Optional[int], str]:
    """Execute argv (already vetted) with a hard timeout. Never uses shell=True.

    Returns (returncode_or_None, detail). returncode is None if the
    process could not be run at all or timed out.
    """
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            timeout=COMMAND_TIMEOUT_SECONDS,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except subprocess.TimeoutExpired:
        return None, "command exceeded the %ss timeout and was aborted" % COMMAND_TIMEOUT_SECONDS
    except OSError as exc:
        return None, "command could not be started (%s: %s)" % (type(exc).__name__, exc)
    return proc.returncode, "command ran to completion"


# --------------------------------------------------------------------------
# Per-claim-type verification
# --------------------------------------------------------------------------

def verify_sha256_claim(occ: ClaimOccurrence, bundle_dir: Path, relpaths: Sequence[str],
                         file_hashes: Dict[str, Optional[str]]) -> dict:
    asserted_hash = occ.params["asserted_hash"]
    filename = occ.params["filename"]
    asserted_value = {"sha256": asserted_hash.lower(), "filename": filename}

    if filename is not None:
        resolved, candidates = resolve_filename(filename, relpaths)
        if resolved is None and candidates:
            return _claim_dict(
                occ, asserted_value, None, RESULT_UNSUBSTANTIATED,
                "nothing in the bundle could substantiate this: filename %r is ambiguous "
                "and matches %d files in the bundle (%s)"
                % (filename, len(candidates), ", ".join(candidates)),
            )
        if resolved is None:
            return _claim_dict(
                occ, asserted_value, None, RESULT_UNSUBSTANTIATED,
                "nothing in the bundle could substantiate this: no file named %r exists in the bundle" % filename,
            )
        actual = file_hashes.get(resolved)
        if actual is None:
            return _claim_dict(
                occ, asserted_value, None, RESULT_UNSUBSTANTIATED,
                "nothing in the bundle could substantiate this: %r exists but could not be read" % resolved,
            )
        observed_value = {"sha256": actual, "filename": resolved}
        if actual.lower() == asserted_hash.lower():
            return _claim_dict(
                occ, asserted_value, observed_value, RESULT_MATCHED,
                "hashed bundle file %r: sha256 matches the claim" % resolved,
            )
        elsewhere = sorted(r for r, h in file_hashes.items() if h and h.lower() == asserted_hash.lower())
        observed_value["hash_claimed_found_at"] = elsewhere
        detail = "hashed bundle file %r: sha256 does not match the claim" % resolved
        if elsewhere:
            detail += " (the claimed hash actually belongs to %s)" % ", ".join(elsewhere)
        return _claim_dict(occ, asserted_value, observed_value, RESULT_MISMATCHED, detail)

    # Bare hash, no filename attached: hash every file in the bundle.
    matches = sorted(r for r, h in file_hashes.items() if h and h.lower() == asserted_hash.lower())
    observed_value = {"matched_files": matches}
    if matches:
        return _claim_dict(
            occ, asserted_value, observed_value, RESULT_MATCHED,
            "hashed every file in the bundle (%d file(s)); match found: %s" % (len(relpaths), ", ".join(matches)),
        )
    return _claim_dict(
        occ, asserted_value, observed_value, RESULT_MISMATCHED,
        "hashed every file in the bundle (%d file(s)); no file matches this hash" % len(relpaths),
    )


def verify_test_count_claim(occ: ClaimOccurrence, test_run: dict) -> dict:
    asserted_count = occ.params["asserted_count"]
    asserted_value = {"tests": asserted_count}
    if test_run["observed_count"] is None:
        return _claim_dict(
            occ, asserted_value, None, RESULT_UNSUBSTANTIATED,
            "attempted to run `python3 -m unittest discover -s . -v` inside the bundle; %s" % test_run["detail"],
        )
    observed_value = {"tests": test_run["observed_count"]}
    detail = "ran `python3 -m unittest discover -s . -v` inside the bundle; %s" % test_run["detail"]
    if observed_value["tests"] == asserted_count:
        return _claim_dict(occ, asserted_value, observed_value, RESULT_MATCHED, detail)
    return _claim_dict(occ, asserted_value, observed_value, RESULT_MISMATCHED, detail)


def verify_exit_code_claim(occ: ClaimOccurrence, bundle_dir: Path, relpaths: Sequence[str],
                            command_cache: Dict[str, Tuple[Optional[int], str]]) -> dict:
    asserted_exit = occ.params["asserted_exit"]
    command_text = occ.params["command_text"]
    asserted_value = {"command": command_text, "exit_code": asserted_exit}

    if command_text is None:
        return _claim_dict(
            occ, asserted_value, None, RESULT_UNVERIFIABLE_COMMAND,
            "refused to execute: no backtick-delimited command found on this line to associate "
            "with the claimed exit code",
        )

    allowed, reason, argv = vet_command(command_text, bundle_dir, relpaths)
    if not allowed:
        return _claim_dict(occ, asserted_value, None, RESULT_UNVERIFIABLE_COMMAND, reason)

    if command_text not in command_cache:
        rc, detail = run_command(argv, bundle_dir)
        command_cache[command_text] = (rc, detail)
    rc, detail = command_cache[command_text]

    evidence = "executed %r inside the bundle directory (%s)" % (command_text, reason)
    if rc is None:
        return _claim_dict(
            occ, asserted_value, None, RESULT_UNVERIFIABLE_COMMAND,
            "%s; %s" % (evidence, detail),
        )
    observed_value = {"exit_code": rc}
    evidence = "%s; observed real exit code %d" % (evidence, rc)
    if rc == asserted_exit:
        return _claim_dict(occ, asserted_value, observed_value, RESULT_MATCHED, evidence)
    return _claim_dict(occ, asserted_value, observed_value, RESULT_MISMATCHED, evidence)


def _claim_dict(occ: ClaimOccurrence, asserted_value, observed_value, result: str, evidence_source: str) -> dict:
    return {
        "claim_type": occ.claim_type,
        "claim_text": occ.line_text,
        "notes_line_number": occ.line_no,
        "asserted_value": asserted_value,
        "observed_value": observed_value,
        "result": result,
        "evidence_source": evidence_source,
    }


# --------------------------------------------------------------------------
# Bundle-wide test-suite run (executed at most once, shared by every
# TEST_COUNT_CLAIM). This is NOT a claimed command from the notes -- it is
# the tool's own fixed, hardcoded invocation of the bundle's test suite,
# so it is not subject to the notes-command safety gate above.
# --------------------------------------------------------------------------

def run_bundle_test_suite(bundle_dir: Path) -> dict:
    try:
        proc = subprocess.run(
            ["python3", "-m", "unittest", "discover", "-s", ".", "-v"],
            cwd=str(bundle_dir),
            timeout=COMMAND_TIMEOUT_SECONDS,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except subprocess.TimeoutExpired:
        return {"observed_count": None, "detail": "execution exceeded the %ss timeout" % COMMAND_TIMEOUT_SECONDS}
    except OSError as exc:
        return {"observed_count": None, "detail": "execution failed (%s: %s)" % (type(exc).__name__, exc)}

    combined = (proc.stdout or b"").decode("utf-8", "replace") + "\n" + (proc.stderr or b"").decode("utf-8", "replace")
    matches = RAN_TESTS_RE.findall(combined)
    if not matches:
        return {"observed_count": 0, "detail": "no 'Ran N tests' summary line was produced (0 tests discovered)"}
    observed = int(matches[-1])
    return {"observed_count": observed, "detail": "observed summary line 'Ran %d test%s'" % (observed, "" if observed == 1 else "s")}


# --------------------------------------------------------------------------
# Report construction
# --------------------------------------------------------------------------

def build_report(bundle_dir: Path, notes_path: Path, bundle_dir_arg: str, notes_path_arg: str) -> Tuple[dict, int]:
    if not bundle_dir.exists():
        raise InputError("bundle directory %r does not exist" % str(bundle_dir_arg))
    if not bundle_dir.is_dir():
        raise InputError("bundle path %r is not a directory" % str(bundle_dir_arg))
    if not notes_path.exists():
        raise InputError("notes file %r does not exist" % str(notes_path_arg))
    if not notes_path.is_file():
        raise InputError("notes path %r is not a file" % str(notes_path_arg))

    try:
        notes_bytes = notes_path.read_bytes()
    except OSError as exc:
        raise InputError("notes file %r could not be read (%s)" % (str(notes_path_arg), type(exc).__name__))
    try:
        notes_text = notes_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InputError("notes file %r is not valid UTF-8 (%s)" % (str(notes_path_arg), exc))

    relpaths = discover_files(bundle_dir)
    file_hashes = hash_bundle_files(bundle_dir, relpaths)

    occurrences = extract_claim_occurrences(notes_text)

    needs_test_run = any(o.claim_type == CLAIM_TEST_COUNT for o in occurrences)
    test_run = run_bundle_test_suite(bundle_dir) if needs_test_run else None

    command_cache: Dict[str, Tuple[Optional[int], str]] = {}

    claims: List[dict] = []
    for occ in occurrences:
        if occ.claim_type == CLAIM_SHA256:
            claims.append(verify_sha256_claim(occ, bundle_dir, relpaths, file_hashes))
        elif occ.claim_type == CLAIM_TEST_COUNT:
            claims.append(verify_test_count_claim(occ, test_run))
        elif occ.claim_type == CLAIM_EXIT_CODE:
            claims.append(verify_exit_code_claim(occ, bundle_dir, relpaths, command_cache))
        else:  # pragma: no cover - exhaustive by construction
            raise AssertionError("unreachable claim type %r" % occ.claim_type)

    counts = {RESULT_MATCHED: 0, RESULT_MISMATCHED: 0, RESULT_UNSUBSTANTIATED: 0, RESULT_UNVERIFIABLE_COMMAND: 0}
    for c in claims:
        counts[c["result"]] += 1

    issue_count = counts[RESULT_MISMATCHED] + counts[RESULT_UNSUBSTANTIATED] + counts[RESULT_UNVERIFIABLE_COMMAND]
    exit_code = 0 if issue_count == 0 else 1
    status = "all_matched" if issue_count == 0 else "issues_found"

    report = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "bundle_dir": bundle_dir_arg.replace(os.sep, "/"),
        "notes_file": notes_path_arg.replace(os.sep, "/"),
        "claim_count": len(claims),
        "claims": claims,
        "status": status,
        "exit_code": exit_code,
        "summary": {
            "matched": counts[RESULT_MATCHED],
            "mismatched": counts[RESULT_MISMATCHED],
            "unsubstantiated": counts[RESULT_UNSUBSTANTIATED],
            "unverifiable_command": counts[RESULT_UNVERIFIABLE_COMMAND],
        },
    }
    return report, exit_code


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claimcheck.py",
        description="Check SHA-256/test-count/exit-code claims in a verifier notes file against an evidence bundle.",
    )
    parser.add_argument("bundle_dir", help="path to the evidence bundle directory")
    parser.add_argument("notes_file", help="path to the verifier notes file to check claims from")
    parser.add_argument("-o", "--output", help="also write the canonical JSON report to this path")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)  # argparse itself exits 2 on usage errors

    bundle_dir = Path(args.bundle_dir)
    notes_path = Path(args.notes_file)

    try:
        report, exit_code = build_report(bundle_dir, notes_path, args.bundle_dir, args.notes_file)
    except InputError as exc:
        sys.stderr.write("claimcheck: input error: %s\n" % exc)
        return 2

    out_bytes = canonical_json_bytes(report)

    if args.output:
        out_path = Path(args.output)
        try:
            if out_path.parent and str(out_path.parent) not in ("", "."):
                out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "wb") as fh:
                fh.write(out_bytes)
        except OSError as exc:
            sys.stderr.write("claimcheck: input error: could not write output file (%s)\n" % type(exc).__name__)
            return 2

    sys.stdout.buffer.write(out_bytes)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
