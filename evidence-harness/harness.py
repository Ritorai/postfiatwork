#!/usr/bin/env python3
"""Evidence Verification Harness.

A pre-submission self-check tool. A contributor points it at a machine
readable statement of what a task brief demands (requirements.json) and at
their own evidence bundle directory. The harness reports, per requirement,
whether the bundle satisfies it and -- when it does not -- the *specific*
gap that has to be closed before submitting.

Standard library only. No network access. Deterministic output.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys

SCHEMA_VERSION = 1

# Files larger than this are not scanned for textual evidence. Evidence logs
# in a review bundle are small; anything larger is almost certainly a build
# artifact and scanning it would slow the harness down for no benefit.
MAX_SCAN_BYTES = 2 * 1024 * 1024

# Directories never scanned or matched against required_files patterns.
SKIP_DIRS = frozenset(
    {"__pycache__", ".git", ".hg", ".svn", ".mypy_cache", ".pytest_cache", ".tox"}
)

# Cap on how many items a single check puts in its evidence list, so that a
# huge bundle cannot produce a megabyte-sized report. Truncation is recorded
# explicitly and the retained items are the sorted-first ones, so the output
# stays deterministic.
MAX_EVIDENCE = 25

EXIT_OK = 0
EXIT_GAP = 1
EXIT_INPUT = 2

KNOWN_KEYS = (
    "min_test_count",
    "require_exit_codes",
    "require_hashes",
    "required_commands",
    "required_files",
)

CHECK_NAMES = (
    "min_test_count",
    "require_exit_codes",
    "require_hashes",
    "required_commands",
    "required_files",
)

# "exit=0", "exit code: 1", "exit status 2", "exit_code=137"
EXIT_CODE_PATTERNS = (
    re.compile(r"exit\s*=\s*(-?\d+)", re.IGNORECASE),
    re.compile(r"exit[ _-]?code\s*[:=]?\s*(-?\d+)", re.IGNORECASE),
    re.compile(r"exit[ _-]?status\s*[:=]?\s*(-?\d+)", re.IGNORECASE),
)

# Exactly 64 hex characters, not embedded in a longer hex run.
SHA256_PATTERN = re.compile(r"(?<![0-9A-Fa-f])([0-9A-Fa-f]{64})(?![0-9A-Fa-f])")

# unittest prints "Ran 1 test in ..." / "Ran 12 tests in ..."
RAN_TESTS_PATTERN = re.compile(r"\bRan\s+(\d+)\s+tests?\b")

_WS = re.compile(r"\s+")


class InputError(Exception):
    """Raised for unreadable or structurally invalid input (exit code 2)."""


# --------------------------------------------------------------------------
# input loading
# --------------------------------------------------------------------------

def load_requirements(path):
    """Read and validate requirements.json. Raises InputError on any problem."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise InputError("cannot read requirements file %r: %s" % (path, exc.strerror or exc))

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InputError("requirements file %r is not valid UTF-8: %s" % (path, exc))

    try:
        data = json.loads(text)
    except ValueError as exc:
        raise InputError("requirements file %r is not valid JSON: %s" % (path, exc))

    if not isinstance(data, dict):
        raise InputError(
            "requirements file %r must contain a JSON object at the top level, got %s"
            % (path, type(data).__name__)
        )

    _validate_types(path, data)
    return data


def _validate_types(path, data):
    def bad(key, expected, value):
        raise InputError(
            "requirements file %r: key %r must be %s, got %s"
            % (path, key, expected, type(value).__name__)
        )

    for key in ("required_files", "required_commands"):
        if key in data and data[key] is not None:
            value = data[key]
            if not isinstance(value, list):
                bad(key, "a list of strings", value)
            for item in value:
                if not isinstance(item, str):
                    bad(key, "a list of strings", item)

    for key in ("require_exit_codes", "require_hashes"):
        if key in data and data[key] is not None:
            if not isinstance(data[key], bool):
                bad(key, "a boolean", data[key])

    if "min_test_count" in data and data["min_test_count"] is not None:
        value = data["min_test_count"]
        if isinstance(value, bool) or not isinstance(value, int):
            bad("min_test_count", "an integer", value)
        if value < 0:
            raise InputError(
                "requirements file %r: key 'min_test_count' must be >= 0, got %d" % (path, value)
            )


def collect_bundle(bundle_dir):
    """Walk the bundle. Return (all_rel_paths, scanned, skipped).

    all_rel_paths -- sorted posix-style relative paths of every regular file.
    scanned       -- dict of relative path -> decoded text, for text files.
    skipped       -- sorted list of "path (reason)" strings.
    """
    if not os.path.exists(bundle_dir):
        raise InputError("bundle directory %r does not exist" % bundle_dir)
    if not os.path.isdir(bundle_dir):
        raise InputError("bundle path %r is not a directory" % bundle_dir)

    all_paths = []
    scanned = {}
    skipped = []

    for root, dirs, files in os.walk(bundle_dir):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            abs_path = os.path.join(root, name)
            rel = os.path.relpath(abs_path, bundle_dir).replace(os.sep, "/")
            if not os.path.isfile(abs_path):
                continue
            all_paths.append(rel)
            try:
                size = os.path.getsize(abs_path)
            except OSError as exc:
                skipped.append("%s (unreadable: %s)" % (rel, exc.strerror or exc))
                continue
            if size > MAX_SCAN_BYTES:
                skipped.append("%s (larger than %d bytes)" % (rel, MAX_SCAN_BYTES))
                continue
            try:
                with open(abs_path, "rb") as handle:
                    blob = handle.read()
            except OSError as exc:
                skipped.append("%s (unreadable: %s)" % (rel, exc.strerror or exc))
                continue
            if b"\x00" in blob:
                skipped.append("%s (binary)" % rel)
                continue
            try:
                scanned[rel] = blob.decode("utf-8")
            except UnicodeDecodeError:
                skipped.append("%s (not valid UTF-8 text)" % rel)

    all_paths.sort()
    skipped.sort()
    return all_paths, scanned, skipped


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _clip(items):
    """Deterministically bound an evidence list."""
    items = sorted(items)
    if len(items) <= MAX_EVIDENCE:
        return items
    kept = items[:MAX_EVIDENCE]
    kept.append("... %d more omitted" % (len(items) - MAX_EVIDENCE))
    return kept


def _normalise(text):
    """Collapse every whitespace run to a single space."""
    return _WS.sub(" ", text)


def _skipped(name, reason):
    return {"check": name, "status": "skipped", "detail": reason, "gaps": [], "evidence": []}


def _quote_list(items):
    return ", ".join(repr(str(i)) for i in items)


def match_pattern(pattern, rel_paths):
    """Return the sorted paths matching a required_files pattern.

    A pattern matches if it matches the whole posix-style relative path or
    the bare file name. That makes '*.py' find 'src/tool.py' while
    'docs/*.md' still anchors to the documented sub-directory.
    """
    hits = set()
    for rel in rel_paths:
        if fnmatch.fnmatchcase(rel, pattern) or fnmatch.fnmatchcase(os.path.basename(rel), pattern):
            hits.add(rel)
    return sorted(hits)


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def check_required_files(req, all_paths):
    patterns = req.get("required_files") or []
    if not patterns:
        return _skipped("required_files", "not required by the brief (no 'required_files' declared)")

    gaps = []
    evidence = []
    missing = []
    for pattern in sorted(set(patterns)):
        hits = match_pattern(pattern, all_paths)
        if hits:
            evidence.append("%r matched: %s" % (pattern, ", ".join(hits[:MAX_EVIDENCE])))
        else:
            missing.append(pattern)
            gaps.append(
                "required_files: no file in the bundle matches the required pattern %r "
                "(bundle contains %d file(s))" % (pattern, len(all_paths))
            )

    if gaps:
        detail = "%d of %d required file pattern(s) unmatched: %s" % (
            len(missing),
            len(set(patterns)),
            _quote_list(sorted(missing)),
        )
        status = "fail"
    else:
        detail = "all %d required file pattern(s) matched at least one file" % len(set(patterns))
        status = "pass"

    return {
        "check": "required_files",
        "status": status,
        "detail": detail,
        "gaps": sorted(gaps),
        "evidence": _clip(evidence),
    }


def check_required_commands(req, scanned):
    commands = req.get("required_commands") or []
    if not commands:
        return _skipped(
            "required_commands", "not required by the brief (no 'required_commands' declared)"
        )

    normalised = {rel: _normalise(text) for rel, text in scanned.items()}
    gaps = []
    evidence = []
    missing = []

    for command in sorted(set(commands)):
        needle = _normalise(command).strip()
        found_in = sorted(rel for rel, text in normalised.items() if needle and needle in text)
        if found_in:
            evidence.append("%r found in: %s" % (command, ", ".join(found_in[:MAX_EVIDENCE])))
        else:
            missing.append(command)
            gaps.append(
                "required_commands: the command %r does not appear in any of the %d scanned "
                "text file(s); document the command and paste its captured output" % (command, len(scanned))
            )

    if gaps:
        status = "fail"
        detail = "%d of %d required command(s) never appear in the bundle: %s" % (
            len(missing),
            len(set(commands)),
            _quote_list(sorted(missing)),
        )
    else:
        status = "pass"
        detail = "all %d required command(s) appear in the bundle" % len(set(commands))

    return {
        "check": "required_commands",
        "status": status,
        "detail": detail,
        "gaps": sorted(gaps),
        "evidence": _clip(evidence),
    }


def check_require_exit_codes(req, scanned):
    if not req.get("require_exit_codes"):
        return _skipped(
            "require_exit_codes", "not required by the brief ('require_exit_codes' is absent or false)"
        )

    evidence = []
    for rel, text in scanned.items():
        found = set()
        for pattern in EXIT_CODE_PATTERNS:
            for match in pattern.finditer(text):
                found.add(match.group(0).strip())
        for token in found:
            evidence.append("%s: %s" % (rel, _normalise(token)))

    if evidence:
        return {
            "check": "require_exit_codes",
            "status": "pass",
            "detail": "found %d exit-code marker(s) in the bundle" % len(evidence),
            "gaps": [],
            "evidence": _clip(evidence),
        }

    return {
        "check": "require_exit_codes",
        "status": "fail",
        "detail": "no exit-code marker found in any of the %d scanned text file(s)" % len(scanned),
        "gaps": [
            "require_exit_codes: no exit-code marker (for example 'exit=0', 'exit code: 1' or "
            "'exit status 2') appears in any of the %d scanned text file(s); re-capture the "
            "commands with a visible 'exit=$?' line after each one" % len(scanned)
        ],
        "evidence": [],
    }


def check_require_hashes(req, scanned):
    if not req.get("require_hashes"):
        return _skipped(
            "require_hashes", "not required by the brief ('require_hashes' is absent or false)"
        )

    evidence = []
    total = 0
    for rel, text in scanned.items():
        digests = sorted({m.group(1).lower() for m in SHA256_PATTERN.finditer(text)})
        total += len(digests)
        for digest in digests:
            evidence.append("%s: %s" % (rel, digest))

    if evidence:
        return {
            "check": "require_hashes",
            "status": "pass",
            "detail": "found %d distinct sha256 digest(s) across the bundle" % total,
            "gaps": [],
            "evidence": _clip(evidence),
        }

    return {
        "check": "require_hashes",
        "status": "fail",
        "detail": "no 64-character sha256 digest found in any of the %d scanned text file(s)"
        % len(scanned),
        "gaps": [
            "require_hashes: no 64-hex-character sha256 digest appears in any of the %d scanned "
            "text file(s); run sha256sum over the artefacts and paste the output into the bundle"
            % len(scanned)
        ],
        "evidence": [],
    }


def check_min_test_count(req, scanned):
    minimum = req.get("min_test_count")
    if minimum is None or minimum <= 0:
        return _skipped(
            "min_test_count", "not required by the brief ('min_test_count' is absent, null or 0)"
        )

    observations = []
    best = None
    for rel, text in scanned.items():
        for match in RAN_TESTS_PATTERN.finditer(text):
            count = int(match.group(1))
            observations.append("%s: Ran %d tests" % (rel, count))
            if best is None or count > best:
                best = count

    if best is None:
        return {
            "check": "min_test_count",
            "status": "fail",
            "detail": "no 'Ran N tests' line found; the brief requires at least %d test(s)" % minimum,
            "gaps": [
                "min_test_count: no 'Ran N tests' summary line appears in any of the %d scanned "
                "text file(s), so the required minimum of %d test(s) cannot be confirmed; include "
                "the verbatim test-runner output in the bundle" % (len(scanned), minimum)
            ],
            "evidence": [],
        }

    if best < minimum:
        return {
            "check": "min_test_count",
            "status": "fail",
            "detail": "largest observed test run was %d test(s), the brief requires at least %d"
            % (best, minimum),
            "gaps": [
                "min_test_count: the largest 'Ran N tests' value in the bundle is %d but the brief "
                "requires at least %d; add %d more test(s) and re-capture the run"
                % (best, minimum, minimum - best)
            ],
            "evidence": _clip(observations),
        }

    return {
        "check": "min_test_count",
        "status": "pass",
        "detail": "largest observed test run was %d test(s), meeting the required minimum of %d"
        % (best, minimum),
        "gaps": [],
        "evidence": _clip(observations),
    }


def check_unknown_keys(req, strict):
    unknown = sorted(k for k in req if k not in KNOWN_KEYS)
    if not unknown:
        return _skipped("unknown_requirement_keys", "every declared requirement key is recognised")
    detail = "%d unrecognised requirement key(s): %s" % (len(unknown), _quote_list(unknown))
    if strict:
        return {
            "check": "unknown_requirement_keys",
            "status": "fail",
            "detail": detail,
            "gaps": [
                "unknown_requirement_keys: --strict was requested and requirements.json declares "
                "the unrecognised key(s) %s, which this harness cannot verify; remove them or "
                "drop --strict" % _quote_list(unknown)
            ],
            "evidence": _clip(unknown),
        }
    return {
        "check": "unknown_requirement_keys",
        "status": "warn",
        "detail": detail + " (ignored; re-run with --strict to treat this as a gap)",
        "gaps": [],
        "evidence": _clip(unknown),
    }


def check_strict_coverage(req, all_paths, scanned, strict):
    if not strict:
        return _skipped("strict_coverage", "--strict not requested")

    gaps = []
    if not all_paths:
        gaps.append(
            "strict_coverage: --strict was requested but the bundle directory contains no files at all"
        )
    elif not scanned:
        gaps.append(
            "strict_coverage: --strict was requested but none of the %d file(s) in the bundle are "
            "readable UTF-8 text, so no command, exit code, hash or test count can be verified"
            % len(all_paths)
        )
    declared = [k for k in KNOWN_KEYS if k in req and req[k] not in (None, False, 0, [])]
    if not declared:
        gaps.append(
            "strict_coverage: --strict was requested but requirements.json declares no verifiable "
            "requirement; a brief that demands evidence must declare at least one of %s"
            % _quote_list(KNOWN_KEYS)
        )

    if gaps:
        return {
            "check": "strict_coverage",
            "status": "fail",
            "detail": "strict-mode preconditions not met (%d issue(s))" % len(gaps),
            "gaps": sorted(gaps),
            "evidence": [],
        }
    return {
        "check": "strict_coverage",
        "status": "pass",
        "detail": "strict-mode preconditions met: %d verifiable requirement(s) declared, %d text "
        "file(s) scanned" % (len(declared), len(scanned)),
        "gaps": [],
        "evidence": _clip(declared),
    }


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------

def build_report(requirements_path, bundle_dir, strict=False):
    """Run every check. Returns (report_dict, exit_code)."""
    req = load_requirements(requirements_path)
    all_paths, scanned, skipped = collect_bundle(bundle_dir)

    checks = [
        check_required_files(req, all_paths),
        check_required_commands(req, scanned),
        check_require_exit_codes(req, scanned),
        check_require_hashes(req, scanned),
        check_min_test_count(req, scanned),
        check_unknown_keys(req, strict),
        check_strict_coverage(req, all_paths, scanned, strict),
    ]
    checks.sort(key=lambda c: c["check"])

    gaps = sorted({g for check in checks for g in check["gaps"]})

    counts = {"pass": 0, "fail": 0, "skipped": 0, "warn": 0}
    for check in checks:
        counts[check["status"]] += 1

    exit_code = EXIT_GAP if gaps else EXIT_OK

    report = {
        "schema_version": SCHEMA_VERSION,
        "tool": "evidence-verification-harness",
        "bundle_dir": bundle_dir.replace(os.sep, "/"),
        "requirements_file": requirements_path.replace(os.sep, "/"),
        "strict": bool(strict),
        "status": "pass" if not gaps else "gap",
        "exit_code": exit_code,
        "gap_count": len(gaps),
        "gaps": gaps,
        "checks": checks,
        "summary": {
            "checks_failed": counts["fail"],
            "checks_passed": counts["pass"],
            "checks_skipped": counts["skipped"],
            "checks_total": len(checks),
            "checks_warned": counts["warn"],
            "files_in_bundle": len(all_paths),
            "files_scanned_as_text": len(scanned),
            "files_skipped": len(skipped),
        },
        "bundle_files": _clip(all_paths),
        "unscanned_files": _clip(skipped),
    }
    return report, exit_code


def render(report):
    """Canonical JSON bytes: sorted keys, tight separators, ASCII, trailing newline."""
    return (
        json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode("ascii")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="harness.py",
        description=(
            "Evidence Verification Harness: check that an evidence bundle satisfies the "
            "verification requirements a task brief states, before submitting it."
        ),
    )
    parser.add_argument("requirements", metavar="requirements.json", help="JSON file describing what the brief demands")
    parser.add_argument("bundle_dir", metavar="bundle_dir", help="directory containing the evidence bundle")
    parser.add_argument("-o", "--out", metavar="PATH", default=None, help="also write the canonical JSON report to PATH")
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "treat unrecognised requirement keys, an unreadable/empty bundle, and a "
            "requirements file with nothing verifiable in it as gaps"
        ),
    )
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        report, exit_code = build_report(args.requirements, args.bundle_dir, strict=args.strict)
    except InputError as exc:
        sys.stderr.write("harness: input error: %s\n" % exc)
        return EXIT_INPUT

    payload = render(report)

    if args.out:
        try:
            parent = os.path.dirname(os.path.abspath(args.out))
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)
            with open(args.out, "wb") as handle:
                handle.write(payload)
        except OSError as exc:
            sys.stderr.write("harness: cannot write report to %r: %s\n" % (args.out, exc.strerror or exc))
            return EXIT_INPUT

    sys.stdout.write(payload.decode("ascii"))
    sys.stdout.flush()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
