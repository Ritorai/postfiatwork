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
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

TOOL_NAME = "claimcheck"
TOOL_VERSION = "1.1.0"
SCHEMA_VERSION = 2

# Hard wall-clock cap on any command executed for EXIT_CODE_CLAIM
# verification. Overridable only for test purposes (see test_claimcheck.py);
# the CLI itself always uses this value.
COMMAND_TIMEOUT_SECONDS = 60

RESULT_MATCHED = "MATCHED"
RESULT_MISMATCHED = "MISMATCHED"
RESULT_UNSUBSTANTIATED = "UNSUBSTANTIATED"
RESULT_UNVERIFIABLE_COMMAND = "UNVERIFIABLE_COMMAND"

# Reproduction-only result: --run-repro was never requested, so nothing was
# actually re-executed and nothing is guessed. Never confused with the four
# values above, which only ever describe the CLAIM's own (non-repro) check.
RESULT_NOT_RUN = "NOT_RUN"

CLAIM_SHA256 = "SHA256_CLAIM"
CLAIM_TEST_COUNT = "TEST_COUNT_CLAIM"
CLAIM_EXIT_CODE = "EXIT_CODE_CLAIM"

_TYPE_RANK = {CLAIM_SHA256: 0, CLAIM_TEST_COUNT: 1, CLAIM_EXIT_CODE: 2}

# --------------------------------------------------------------------------
# Checklist item kinds. These are NOT verdicts on any single claim's truth
# the way RESULT_* is -- they are prompts telling a human reviewer where to
# look next. See HUMAN_REVIEW_NOTICE below and README.md "IMPORTANT".
# --------------------------------------------------------------------------

CHECKLIST_UNLINKED_CLAIM = "UNLINKED_CLAIM"
CHECKLIST_NO_DISCLOSED_LIMITATIONS = "NO_DISCLOSED_LIMITATIONS"
CHECKLIST_UNSUPPORTED_ASSERTION = "UNSUPPORTED_ASSERTION"

_CHECKLIST_KIND_RANK = {
    CHECKLIST_UNLINKED_CLAIM: 0,
    CHECKLIST_NO_DISCLOSED_LIMITATIONS: 1,
    CHECKLIST_UNSUPPORTED_ASSERTION: 2,
}

HUMAN_REVIEW_NOTICE = (
    "This report is GUIDANCE FOR A HUMAN REVIEWER, not a judgement of the "
    "contributor. Every entry in 'checklist' is a prompt telling a reviewer "
    "where to look next -- an incomplete submission, an honest oversight, or "
    "a documentation gap is far more likely than misconduct. A checklist "
    "entry means 'a person should check this', never 'this person did "
    "something wrong'."
)

# A submission's notes disclosing SOME limitation, caveat, or known issue,
# in any of the common phrasings. Deliberately a phrase list, not language
# understanding -- see README.md Limitations #4.
LIMITATION_RE = re.compile(
    r"\b(limitations?|caveats?|known\s+issues?|not\s+supported|"
    r"does(?:n'?t|\s+not)\s+(?:handle|support))\b",
    re.IGNORECASE,
)

# Confident-sounding language that, unaccompanied by anything checkable on
# the same line, is exactly what CHECKLIST_UNSUPPORTED_ASSERTION exists to
# flag. Deliberately a phrase list, not language understanding -- see
# README.md Limitations #4.
#
# NOTE: "100%" is matched OUTSIDE the \b(...)\b group on purpose. A
# trailing \b after "%" can never match: \b only fires at a transition
# between a word character and a non-word character (or string start/end),
# and "%" is itself a non-word character, so "100% " (percent-sign then
# space) is a non-word-to-non-word transition -- no boundary exists there,
# so `\b(...100%...)\b` silently never matches "100%" at all, no matter
# what follows it. This was caught by
# test_confidence_phrase_with_percent_and_no_claim_still_flagged (see
# captured_output.txt "The bug found in Step 5").
CONFIDENCE_RE = re.compile(
    r"\b(?:fully|completely|guaranteed|always\s+works|no\s+bugs|fully\s+tested|proven)\b|100%",
    re.IGNORECASE,
)


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
# immediately after the hash, immediately after the hash, separated by whitespace and an optional '*'.
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


def resolve_within_workspace(workspace_root: Path, arg: str) -> Optional[str]:
    """Return the realpath of `arg` (joined onto workspace_root) iff that
    realpath is contained within workspace_root's own realpath, else None.

    Guards two ways a naive join can be tricked into escaping the sandbox:

    * `os.path.join(base, arg)` silently DISCARDS `base` entirely when
      `arg` is itself absolute (this is documented os.path behaviour, not
      a bug) -- so an absolute `arg` must be rejected before it ever
      reaches os.path.join, not caught by inspecting the join's result.
    * a relative `arg` containing `..` components can still walk out of
      workspace_root once actually resolved -- containment is therefore
      checked on the REALPATH, after `..` has been collapsed, using
      os.path.commonpath against the workspace's own realpath.
    """
    if os.path.isabs(arg):
        return None
    base = os.path.realpath(str(workspace_root))
    candidate = os.path.realpath(os.path.join(base, arg))
    try:
        common = os.path.commonpath([base, candidate])
    except ValueError:
        # Different drives on Windows, or other incomparable paths --
        # cannot possibly be "contained", so refuse.
        return None
    if common != base:
        return None
    return candidate


def vet_repro_arguments(argv: Sequence[str], workspace_root: Path) -> Tuple[bool, str]:
    """Vet argv[1:] (the target file and any further arguments) against a
    reproduction workspace. `argv[0]` ("python3") needs no path check.

    This is IN ADDITION TO vet_command()'s own checks -- vet_command()
    already refuses an absolute or `..`-escaping argv[1] against the
    ORIGINAL bundle_dir, but a reproduction run executes inside a
    DIFFERENT directory (a disposable copy), and any argument after the
    target file (argv[2:]) was never checked by vet_command() at all.
    """
    for arg in argv[1:]:
        if os.path.isabs(arg):
            return False, "refused to reproduce: argument %r is an absolute path, not a workspace-relative one" % arg
        if resolve_within_workspace(workspace_root, arg) is None:
            return False, "refused to reproduce: argument %r resolves outside the reproduction workspace" % arg
    return True, "every argument resolves inside the reproduction workspace"


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
                            command_cache: Dict[str, Tuple[Optional[int], str]],
                            run_repro: bool = False,
                            repro_workspace: Optional[Path] = None,
                            repro_cache: Optional[Dict[str, Tuple[Optional[int], str]]] = None) -> dict:
    asserted_exit = occ.params["asserted_exit"]
    command_text = occ.params["command_text"]
    asserted_value = {"command": command_text, "exit_code": asserted_exit}

    repro_result, repro_evidence = _verify_repro(
        occ, command_text, asserted_exit, bundle_dir, relpaths,
        run_repro, repro_workspace, repro_cache,
    )

    if command_text is None:
        return _claim_dict(
            occ, asserted_value, None, RESULT_UNVERIFIABLE_COMMAND,
            "refused to execute: no backtick-delimited command found on this line to associate "
            "with the claimed exit code",
            repro_result, repro_evidence,
        )

    allowed, reason, argv = vet_command(command_text, bundle_dir, relpaths)
    if not allowed:
        return _claim_dict(occ, asserted_value, None, RESULT_UNVERIFIABLE_COMMAND, reason,
                            repro_result, repro_evidence)

    if command_text not in command_cache:
        rc, detail = run_command(argv, bundle_dir)
        command_cache[command_text] = (rc, detail)
    rc, detail = command_cache[command_text]

    evidence = "executed %r inside the bundle directory (%s)" % (command_text, reason)
    if rc is None:
        return _claim_dict(
            occ, asserted_value, None, RESULT_UNVERIFIABLE_COMMAND,
            "%s; %s" % (evidence, detail),
            repro_result, repro_evidence,
        )
    observed_value = {"exit_code": rc}
    evidence = "%s; observed real exit code %d" % (evidence, rc)
    if rc == asserted_exit:
        return _claim_dict(occ, asserted_value, observed_value, RESULT_MATCHED, evidence,
                            repro_result, repro_evidence)
    return _claim_dict(occ, asserted_value, observed_value, RESULT_MISMATCHED, evidence,
                        repro_result, repro_evidence)


def _verify_repro(occ: ClaimOccurrence, command_text: Optional[str], asserted_exit: int,
                   bundle_dir: Path, relpaths: Sequence[str], run_repro: bool,
                   repro_workspace: Optional[Path],
                   repro_cache: Optional[Dict[str, Tuple[Optional[int], str]]]) -> Tuple[str, str]:
    """Compute (repro_result, repro_evidence_source) for one EXIT_CODE_CLAIM.

    Never guesses: if `run_repro` is False (the default -- --run-repro was
    not passed on the command line), this ALWAYS returns RESULT_NOT_RUN,
    regardless of anything else about the claim.
    """
    if not run_repro:
        return RESULT_NOT_RUN, "reproduction mode was not requested (pass --run-repro to actually run this command)"

    if command_text is None:
        return RESULT_UNVERIFIABLE_COMMAND, (
            "refused to reproduce: no backtick-delimited command found on this line to associate "
            "with the claimed exit code"
        )

    allowed, reason, argv = vet_command(command_text, bundle_dir, relpaths)
    if not allowed:
        return RESULT_UNVERIFIABLE_COMMAND, "reproduction " + reason

    assert repro_workspace is not None  # build_report always supplies one when run_repro is True
    args_ok, args_reason = vet_repro_arguments(argv, repro_workspace)
    if not args_ok:
        return RESULT_UNVERIFIABLE_COMMAND, args_reason

    cache = repro_cache if repro_cache is not None else {}
    if command_text not in cache:
        rc, detail = run_command(argv, repro_workspace)
        cache[command_text] = (rc, detail)
    rc, detail = cache[command_text]

    evidence = "reproduced %r inside a disposable copy of the bundle (%s)" % (command_text, reason)
    if rc is None:
        return RESULT_UNVERIFIABLE_COMMAND, "%s; %s" % (evidence, detail)
    if rc == asserted_exit:
        return RESULT_MATCHED, "%s; observed real exit code %d" % (evidence, rc)
    return RESULT_MISMATCHED, "%s; observed real exit code %d" % (evidence, rc)


def _claim_dict(occ: ClaimOccurrence, asserted_value, observed_value, result: str, evidence_source: str,
                 repro_result: Optional[str] = None, repro_evidence_source: Optional[str] = None) -> dict:
    claim = {
        "claim_type": occ.claim_type,
        "claim_text": occ.line_text,
        "notes_line_number": occ.line_no,
        "asserted_value": asserted_value,
        "observed_value": observed_value,
        "result": result,
        "evidence_source": evidence_source,
    }
    # repro_result/repro_evidence_source only ever apply to EXIT_CODE_CLAIM
    # (the only claim type with a command to reproduce); every other claim
    # type keeps exactly the original seven fields.
    if repro_result is not None:
        claim["repro_result"] = repro_result
        claim["repro_evidence_source"] = repro_evidence_source
    return claim


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
# Checklist: claim-to-artifact linking, missing disclosed limitations,
# unsupported assertions. These are prompts for a human reviewer, never a
# verdict -- see HUMAN_REVIEW_NOTICE and README.md "IMPORTANT".
# --------------------------------------------------------------------------

def _checklist_item(kind: str, notes_line_number: Optional[int], claim_text: Optional[str], detail: str) -> dict:
    return {
        "kind": kind,
        "notes_line_number": notes_line_number,
        "claim_text": claim_text,
        "detail": detail,
    }


def _claim_has_backing_artifact(claim: dict) -> bool:
    """Whether `claim` resolves to something real actually present in the
    bundle -- a specific file (SHA256_CLAIM) or a specific in-bundle
    command target (EXIT_CODE_CLAIM). TEST_COUNT_CLAIM has no single
    linkable artifact (it is about the whole bundle's test suite) and is
    always considered linked -- see README.md Limitations #5.
    """
    claim_type = claim["claim_type"]
    if claim_type == CLAIM_SHA256:
        observed_value = claim["observed_value"]
        if observed_value is None:
            return False  # UNSUBSTANTIATED: no file existed to compare against
        if "filename" in observed_value:
            return True  # resolved to one real bundle file, MATCHED or MISMATCHED
        return bool(observed_value.get("matched_files"))  # bare hash: linked iff some file matched
    if claim_type == CLAIM_EXIT_CODE:
        if claim["asserted_value"].get("command") is None:
            return False  # nothing was even quoted to associate with the claim
        if claim["result"] == RESULT_UNVERIFIABLE_COMMAND and "not found inside the bundle" in claim["evidence_source"]:
            return False  # the claimed target file itself does not exist in the bundle
        return True
    return True  # CLAIM_TEST_COUNT


def _build_unlinked_claim_items(claims: Sequence[dict]) -> List[dict]:
    items = []
    for claim in claims:
        if not _claim_has_backing_artifact(claim):
            items.append(_checklist_item(
                CHECKLIST_UNLINKED_CLAIM, claim["notes_line_number"], claim["claim_text"],
                "this %s does not resolve to any file or in-bundle command target actually "
                "present in the bundle; check by hand whether it is backed by anything real"
                % claim["claim_type"],
            ))
    return items


def _notes_lines(notes_text: str) -> List[str]:
    lines = notes_text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    return lines


def _build_limitations_item(notes_text: str) -> List[dict]:
    if LIMITATION_RE.search(notes_text):
        return []
    return [_checklist_item(
        CHECKLIST_NO_DISCLOSED_LIMITATIONS, None, None,
        "the submission notes do not appear to disclose any limitations, caveats, or known "
        "issues; check whether that is because there genuinely are none",
    )]


def _build_unsupported_assertion_items(notes_text: str) -> List[dict]:
    items = []
    for line_no, line in enumerate(_notes_lines(notes_text), start=1):
        if not CONFIDENCE_RE.search(line):
            continue
        has_claim = bool(
            _extract_sha256_occurrences(line_no, line)
            or _extract_test_count_occurrences(line_no, line)
            or _extract_exit_code_occurrences(line_no, line)
        )
        if has_claim:
            continue
        items.append(_checklist_item(
            CHECKLIST_UNSUPPORTED_ASSERTION, line_no, line,
            "this line uses confident language but carries no hash, test-count, or command "
            "claim on the same line for claimcheck to verify; check what backs this assertion",
        ))
    return items


def build_checklist(notes_text: str, claims: Sequence[dict]) -> List[dict]:
    items = []
    items.extend(_build_unlinked_claim_items(claims))
    items.extend(_build_limitations_item(notes_text))
    items.extend(_build_unsupported_assertion_items(notes_text))
    items.sort(key=lambda it: (
        _CHECKLIST_KIND_RANK[it["kind"]],
        -1 if it["notes_line_number"] is None else it["notes_line_number"],
        canonical_json_bytes(it),
    ))
    return items


# --------------------------------------------------------------------------
# Reproduction workspace (--run-repro): a disposable COPY of the bundle,
# never the submitted directory itself. Created at most once per report.
# --------------------------------------------------------------------------

def _make_repro_workspace(bundle_dir: Path):
    """Return (tempdir_handle, workspace_path). Caller must call
    tempdir_handle.cleanup() when done. Raises InputError (-> exit 2) if
    the copy itself could not be made -- that is a tool failure, not a
    claim result.
    """
    tempdir_handle = tempfile.TemporaryDirectory(prefix="claimcheck_repro_")
    workspace = Path(tempdir_handle.name) / "workspace"
    try:
        # symlinks=True: copy symlinks AS symlinks rather than dereferencing
        # them. Dereferencing (the default) crashes copytree outright on a
        # broken symlink -- which discover_files()/hash_bundle_files()
        # elsewhere in this tool already tolerate gracefully (a symlink
        # that cannot be read just hashes to None) -- and would otherwise
        # turn an unrelated, pre-existing broken symlink in someone's
        # bundle into an unconditional exit-2 tool failure the moment
        # --run-repro is used.
        shutil.copytree(str(bundle_dir), str(workspace), symlinks=True)
    except OSError as exc:
        tempdir_handle.cleanup()
        raise InputError("could not create reproduction workspace (%s: %s)" % (type(exc).__name__, exc))
    return tempdir_handle, workspace


# --------------------------------------------------------------------------
# Report construction
# --------------------------------------------------------------------------

def build_report(bundle_dir: Path, notes_path: Path, bundle_dir_arg: str, notes_path_arg: str,
                  run_repro: bool = False) -> Tuple[dict, int]:
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

    needs_repro_workspace = run_repro and any(o.claim_type == CLAIM_EXIT_CODE for o in occurrences)
    repro_tempdir = None
    repro_workspace = None
    if needs_repro_workspace:
        repro_tempdir, repro_workspace = _make_repro_workspace(bundle_dir)

    try:
        command_cache: Dict[str, Tuple[Optional[int], str]] = {}
        repro_cache: Dict[str, Tuple[Optional[int], str]] = {}

        # (occurrence, claim) pairs, built and later sorted together so the
        # claim's own offset (not itself a JSON field) can still anchor the
        # final, explicit total-order sort below.
        pairs: List[Tuple[ClaimOccurrence, dict]] = []
        for occ in occurrences:
            if occ.claim_type == CLAIM_SHA256:
                claim = verify_sha256_claim(occ, bundle_dir, relpaths, file_hashes)
            elif occ.claim_type == CLAIM_TEST_COUNT:
                claim = verify_test_count_claim(occ, test_run)
            elif occ.claim_type == CLAIM_EXIT_CODE:
                claim = verify_exit_code_claim(
                    occ, bundle_dir, relpaths, command_cache,
                    run_repro=run_repro, repro_workspace=repro_workspace, repro_cache=repro_cache,
                )
            else:  # pragma: no cover - exhaustive by construction
                raise AssertionError("unreachable claim type %r" % occ.claim_type)
            pairs.append((occ, claim))
    finally:
        if repro_tempdir is not None:
            repro_tempdir.cleanup()

    # Every emitted list is explicitly sorted with the canonical JSON dump
    # of the item appended as the FINAL tiebreak key, guaranteeing a total
    # order even between two entries identical on every documented field.
    pairs.sort(key=lambda pair: (
        pair[0].line_no, pair[0].offset, _TYPE_RANK[pair[0].claim_type], canonical_json_bytes(pair[1]),
    ))
    claims = [claim for _occ, claim in pairs]

    checklist = build_checklist(notes_text, claims)

    counts = {RESULT_MATCHED: 0, RESULT_MISMATCHED: 0, RESULT_UNSUBSTANTIATED: 0, RESULT_UNVERIFIABLE_COMMAND: 0}
    for c in claims:
        counts[c["result"]] += 1

    # Reproduction mismatches count as issues too, but ONLY when --run-repro
    # was actually used (RESULT_NOT_RUN never contributes) -- this is what
    # keeps --run-repro's absence from ever silently downgrading a real
    # repro failure into a clean-looking exit 0, while never affecting a
    # report where reproduction was never requested at all.
    repro_issue_count = sum(
        1 for c in claims
        if c.get("repro_result") in (RESULT_MISMATCHED, RESULT_UNVERIFIABLE_COMMAND)
    )

    # Checklist entries are prompts for a human reviewer (see
    # HUMAN_REVIEW_NOTICE), not verdicts, and are deliberately NOT counted
    # toward exit_code/status: doing so would flip a great many bundles
    # whose every CLAIM is genuinely MATCHED into exit 1 the moment their
    # notes simply omit a limitations section, which would not be a
    # meaningful signal of anything being wrong with the bundle itself.
    # `checklist` is always present in the report regardless of exit code,
    # and a human reviewer should look at it independently of exit_code.
    issue_count = counts[RESULT_MISMATCHED] + counts[RESULT_UNSUBSTANTIATED] \
        + counts[RESULT_UNVERIFIABLE_COMMAND] + repro_issue_count
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
        "checklist": checklist,
        "human_review_notice": HUMAN_REVIEW_NOTICE,
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
    parser.add_argument(
        "--run-repro", action="store_true",
        help="opt-in: actually execute documented EXIT_CODE_CLAIM reproduction commands in a "
             "disposable copy of the bundle (never the bundle itself); without this flag every "
             "such claim's repro_result is NOT_RUN",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)  # argparse itself exits 2 on usage errors

    bundle_dir = Path(args.bundle_dir)
    notes_path = Path(args.notes_file)

    try:
        report, exit_code = build_report(bundle_dir, notes_path, args.bundle_dir, args.notes_file,
                                          run_repro=args.run_repro)
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
