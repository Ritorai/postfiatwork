#!/usr/bin/env python3
"""exit-harness: run manifest-defined CLI cases and check exit code /
stdout / stderr against expected results.

See README.md for the full manifest schema, result-code meanings, and
exit-code meanings. This module is intentionally stdlib-only.

Determinism contract
---------------------
The JSON report this tool writes must be byte-identical for the same
manifest + fixture tree regardless of:
  - the absolute path the tree lives at ("relocation"),
  - the order cases appear in the manifest (case results are always
    sorted before being written -- see `_sort_key` / `sort_results`),
  - the machine's hostname, clock, or process timing.

To uphold that: the report never contains wall-clock timestamps,
durations, absolute paths, or hostnames. Every path that could vary with
relocation (case "cwd", "id", etc.) is recorded exactly as it appears in
the manifest (already relative to --root), never resolved to an absolute
path. Any incidental OS error text that *could* mention the absolute
--root path is scrubbed via `_scrub` before it is placed in the report.
"""
import argparse
import json
import os
import subprocess
import sys

# ---------------------------------------------------------------------------
# Result codes (per-case) and harness exit codes (whole run).
# ---------------------------------------------------------------------------

RESULT_MATCH = "MATCH"
RESULT_EXIT_MISMATCH = "EXIT_MISMATCH"
RESULT_STDOUT_NOT_JSON = "STDOUT_NOT_JSON"
RESULT_STDOUT_NOT_CANONICAL = "STDOUT_NOT_CANONICAL"
RESULT_STDOUT_MISSING_SUBSTRING = "STDOUT_MISSING_SUBSTRING"
RESULT_STDERR_MISSING_SUBSTRING = "STDERR_MISSING_SUBSTRING"
RESULT_TIMEOUT = "TIMEOUT"
RESULT_CASE_ERROR = "CASE_ERROR"
# Not one of the eight core comparison outcomes above: this covers a
# malformed manifest *entry* (missing/invalid keys), which must be
# reported and skipped rather than aborting the whole run.
RESULT_CASE_MALFORMED = "CASE_MALFORMED"

ALL_RESULT_CODES = (
    RESULT_MATCH,
    RESULT_EXIT_MISMATCH,
    RESULT_STDOUT_NOT_JSON,
    RESULT_STDOUT_NOT_CANONICAL,
    RESULT_STDOUT_MISSING_SUBSTRING,
    RESULT_STDERR_MISSING_SUBSTRING,
    RESULT_TIMEOUT,
    RESULT_CASE_ERROR,
    RESULT_CASE_MALFORMED,
)

EXIT_ALL_MATCHED = 0
EXIT_SOME_FAILED = 1
EXIT_HARNESS_ERROR = 2

DEFAULT_TIMEOUT_SECONDS = 30

REQUIRED_KEYS = ("id", "cwd", "argv", "expect_exit")
OPTIONAL_KEYS = (
    "expect_stdout_canonical_json",
    "expect_stdout_contains",
    "expect_stderr_contains",
    "timeout_seconds",
)
ALL_KNOWN_KEYS = REQUIRED_KEYS + OPTIONAL_KEYS


class HarnessError(Exception):
    """Raised for problems that make the whole run unable to proceed.

    Always corresponds to harness exit code 2. Never raised for a
    problem with a single case -- those are recorded as CASE_MALFORMED
    or CASE_ERROR results instead.
    """


def canonical_json_dumps(obj):
    """The one and only canonical JSON serialization used by this tool."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _scrub(text, root_abs):
    """Remove any occurrence of the absolute --root path from `text`.

    This is a defensive measure: incidental OS error strings (e.g. from
    a FileNotFoundError raised by subprocess when an executable is
    missing) could in principle include the absolute cwd. Since the
    report must contain only relative paths, any such substring is
    replaced with the literal token "<root>" before being stored.
    """
    if text is None:
        return None
    if root_abs and root_abs in text:
        text = text.replace(root_abs, "<root>")
    return text


def load_manifest(manifest_path):
    """Read and parse the manifest file. Raises HarnessError on any
    problem that means the harness cannot run at all (this is NOT for
    per-case problems).
    """
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError as exc:
        raise HarnessError(f"cannot read manifest {manifest_path!r}: {exc.strerror}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HarnessError(f"manifest {manifest_path!r} is not valid JSON: {exc}") from exc

    if isinstance(data, dict) and "cases" in data:
        cases = data["cases"]
    else:
        cases = data

    if not isinstance(cases, list):
        raise HarnessError(
            f"manifest {manifest_path!r} must contain a JSON list of cases "
            "(either as the top-level value, or under a top-level \"cases\" key)"
        )

    return cases


def validate_case(raw_case, index):
    """Validate a single raw manifest entry.

    Returns (normalized_case_dict, errors_list). `errors_list` is empty
    iff the case is well-formed; normalized_case_dict may be partially
    populated even when errors are present (best effort, used only for
    reporting -- a malformed case is never executed).
    """
    errors = []
    norm = {
        "id": None,
        "cwd": None,
        "argv": None,
        "expect_exit": None,
        "expect_stdout_canonical_json": False,
        "expect_stdout_contains": None,
        "expect_stderr_contains": None,
        "timeout_seconds": None,
    }

    if not isinstance(raw_case, dict):
        errors.append(f"case at manifest index {index} is not a JSON object")
        return norm, errors

    unknown = sorted(set(raw_case.keys()) - set(ALL_KNOWN_KEYS))
    for key in unknown:
        errors.append(f"unknown key {key!r}")

    for key in REQUIRED_KEYS:
        if key not in raw_case:
            errors.append(f"missing required key {key!r}")

    if "id" in raw_case:
        if isinstance(raw_case["id"], str) and raw_case["id"] != "":
            norm["id"] = raw_case["id"]
        else:
            errors.append("'id' must be a non-empty string")

    if "cwd" in raw_case:
        if isinstance(raw_case["cwd"], str):
            norm["cwd"] = raw_case["cwd"]
        else:
            errors.append("'cwd' must be a string")

    if "argv" in raw_case:
        argv = raw_case["argv"]
        if isinstance(argv, list) and len(argv) >= 1 and all(isinstance(a, str) for a in argv):
            norm["argv"] = argv
        else:
            errors.append("'argv' must be a non-empty JSON list of strings")

    if "expect_exit" in raw_case:
        ee = raw_case["expect_exit"]
        if isinstance(ee, int) and not isinstance(ee, bool):
            norm["expect_exit"] = ee
        else:
            errors.append("'expect_exit' must be an integer")

    if "expect_stdout_canonical_json" in raw_case:
        v = raw_case["expect_stdout_canonical_json"]
        if isinstance(v, bool):
            norm["expect_stdout_canonical_json"] = v
        else:
            errors.append("'expect_stdout_canonical_json' must be a boolean")

    if "expect_stdout_contains" in raw_case:
        v = raw_case["expect_stdout_contains"]
        if isinstance(v, str):
            norm["expect_stdout_contains"] = v
        else:
            errors.append("'expect_stdout_contains' must be a string")

    if "expect_stderr_contains" in raw_case:
        v = raw_case["expect_stderr_contains"]
        if isinstance(v, str):
            norm["expect_stderr_contains"] = v
        else:
            errors.append("'expect_stderr_contains' must be a string")

    if "timeout_seconds" in raw_case:
        v = raw_case["timeout_seconds"]
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            norm["timeout_seconds"] = v
        else:
            errors.append("'timeout_seconds' must be a positive number")

    return norm, errors


def _resolve_case_cwd(case_cwd, root_abs):
    """Safely resolve a case's ``cwd`` to an absolute path under ``root_abs``.

    Returns ``(cwd_abs, error)`` where exactly one of the two is
    non-``None``. This exists because ``os.path.join(root_abs, case_cwd)``
    is NOT safe on its own: if ``case_cwd`` happens to be an absolute
    path (e.g. ``"/etc"``), ``os.path.join`` silently *discards* the
    base and returns the absolute path verbatim -- which would let a
    manifest case escape ``--root`` entirely (see README "Bug found").
    A ``".."``-laden relative path (e.g. ``"../../etc"``) has the same
    effect. Both are rejected here so every case genuinely runs inside
    ``--root``, which is required for the relocation-safety contract.
    """
    if os.path.isabs(case_cwd):
        return None, f"'cwd' must be a relative path, got an absolute path: {case_cwd!r}"

    root_real = os.path.realpath(root_abs)
    cwd_abs = os.path.join(root_abs, case_cwd)
    cwd_real = os.path.realpath(cwd_abs)
    if cwd_real != root_real and not cwd_real.startswith(root_real + os.sep):
        return None, f"'cwd' escapes --root: {case_cwd!r}"
    return cwd_abs, None


def run_case(case, root_abs, default_timeout):
    """Execute one well-formed case and return a result dict.

    `root_abs` is the absolute --root path, used only to build the
    absolute cwd to hand to subprocess and to scrub error strings; it is
    never itself written into the result.
    """
    timeout = case["timeout_seconds"] if case["timeout_seconds"] is not None else default_timeout

    result = {
        "id": case["id"],
        "cwd": case["cwd"],
        "argv": case["argv"],
        "expect_exit": case["expect_exit"],
        "actual_exit": None,
        "result": None,
        "detail": None,
    }

    cwd_abs, cwd_error = _resolve_case_cwd(case["cwd"], root_abs)
    if cwd_error is not None:
        result["result"] = RESULT_CASE_ERROR
        result["detail"] = cwd_error
        return result

    if not os.path.isdir(cwd_abs):
        result["result"] = RESULT_CASE_ERROR
        result["detail"] = f"cwd does not exist or is not a directory: {case['cwd']!r}"
        return result

    try:
        proc = subprocess.run(
            case["argv"],
            cwd=cwd_abs,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        result["result"] = RESULT_TIMEOUT
        result["detail"] = f"case exceeded timeout of {timeout}s"
        return result
    except OSError as exc:
        result["result"] = RESULT_CASE_ERROR
        result["detail"] = _scrub(f"could not execute case: {exc}", root_abs)
        return result

    result["actual_exit"] = proc.returncode

    if proc.returncode != case["expect_exit"]:
        result["result"] = RESULT_EXIT_MISMATCH
        result["detail"] = f"expected exit {case['expect_exit']}, got {proc.returncode}"
        return result

    if case["expect_stdout_canonical_json"]:
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            result["result"] = RESULT_STDOUT_NOT_JSON
            result["detail"] = f"stdout did not parse as JSON: {exc}"
            return result

        canonical = canonical_json_dumps(parsed) + "\n"
        if proc.stdout != canonical:
            result["result"] = RESULT_STDOUT_NOT_CANONICAL
            result["detail"] = "stdout parsed as JSON but was not canonical form"
            return result

    if case["expect_stdout_contains"] is not None:
        if case["expect_stdout_contains"] not in proc.stdout:
            result["result"] = RESULT_STDOUT_MISSING_SUBSTRING
            result["detail"] = f"stdout did not contain expected substring {case['expect_stdout_contains']!r}"
            return result

    if case["expect_stderr_contains"] is not None:
        if case["expect_stderr_contains"] not in proc.stderr:
            result["result"] = RESULT_STDERR_MISSING_SUBSTRING
            result["detail"] = f"stderr did not contain expected substring {case['expect_stderr_contains']!r}"
            return result

    result["result"] = RESULT_MATCH
    result["detail"] = None
    return result


def _sort_key(item):
    """Total order for the results list.

    Primary keys: case id (empty string if None) then result code.
    Final tiebreak: the canonical JSON dump of the whole result item,
    which is guaranteed unique per distinct item and therefore turns
    this into a genuine total order even when two cases are identical
    on every field the primary keys look at (e.g. same id, same result,
    differing only in some other field, or truly identical entries that
    only differ by manifest position -- see README "Ordering" section).
    """
    return (
        item["id"] if item["id"] is not None else "",
        item["result"],
        canonical_json_dumps(item),
    )


def sort_results(results):
    return sorted(results, key=_sort_key)


def build_report(results):
    matched = sum(1 for r in results if r["result"] == RESULT_MATCH)
    malformed = sum(1 for r in results if r["result"] == RESULT_CASE_MALFORMED)
    failed = len(results) - matched - malformed
    summary = {
        "total": len(results),
        "matched": matched,
        "failed": failed,
        "malformed": malformed,
    }
    all_ok = (failed == 0 and malformed == 0)
    report = {
        "summary": summary,
        "harness_exit_code": EXIT_ALL_MATCHED if all_ok else EXIT_SOME_FAILED,
        "results": sort_results(results),
    }
    return report


def write_report(report, out_path):
    text = canonical_json_dumps(report) + "\n"
    try:
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    except OSError as exc:
        raise HarnessError(f"cannot write report to {out_path!r}: {exc.strerror}") from exc


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="exitharness.py",
        description="Run manifest-defined CLI cases and check exit code / stdout / stderr.",
    )
    parser.add_argument("--manifest", required=True, help="path to the JSON manifest file")
    parser.add_argument("--root", required=True, help="root directory that all manifest paths are relative to")
    parser.add_argument("-o", "--output", required=True, help="path to write the canonical JSON report to")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"default per-case timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    ns = parser.parse_args(argv)
    if ns.timeout <= 0:
        parser.error("--timeout must be a positive number")
    return ns


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    try:
        args = parse_args(argv)
    except SystemExit as exc:
        # argparse already printed usage/error to stderr and calls
        # sys.exit(2) itself; normalize to our documented exit code.
        code = exc.code if isinstance(exc.code, int) else EXIT_HARNESS_ERROR
        return EXIT_HARNESS_ERROR if code != 0 else EXIT_ALL_MATCHED

    try:
        root_abs = os.path.abspath(args.root)
        if not os.path.isdir(root_abs):
            raise HarnessError(f"--root {args.root!r} is not a directory")

        raw_cases = load_manifest(args.manifest)

        results = []
        for index, raw_case in enumerate(raw_cases):
            case, errors = validate_case(raw_case, index)
            if errors:
                results.append(
                    {
                        "id": case["id"],
                        "cwd": case["cwd"],
                        "argv": case["argv"],
                        "expect_exit": case["expect_exit"],
                        "actual_exit": None,
                        "result": RESULT_CASE_MALFORMED,
                        "detail": "; ".join(sorted(errors)),
                    }
                )
                continue
            results.append(run_case(case, root_abs, args.timeout))

        report = build_report(results)
        write_report(report, args.output)
    except HarnessError as exc:
        print(f"exitharness: error: {exc}", file=sys.stderr)
        return EXIT_HARNESS_ERROR

    return report["harness_exit_code"]


if __name__ == "__main__":
    sys.exit(main())
