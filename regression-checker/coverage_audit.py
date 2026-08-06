#!/usr/bin/env python3
"""coverage_audit.py -- stdlib-only baseline coverage auditor.

Checks EVERY tool directory in this repository against
`regression-checker/baselines.json` and reruns every tool that IS
baselined, from scratch, to see whether it still reproduces.

This is deliberately a different question from `regress.py`.
`regress.py` answers "did an already-baselined tool drift?" and treats a
directory with no baseline entry at all as one more kind of drift lumped
in with everything else. This tool answers a prior, cheaper question:
**does baseline coverage of the repository even hold together**, i.e.

  * is every tool directory represented in baselines.json at all
    (`not_baselined` if not)?
  * does every baseline entry point at a directory that still exists
    (`orphaned_baseline` if not)?
  * does the directory still contain the script the baseline's command
    names (`source_missing` if not) -- checked BEFORE attempting to
    execute anything, so a missing script is never confused with "ran
    and failed", and can never be silently reported as reproducing?
  * could the command be executed at all (`unrunnable` if the
    interpreter/binary itself could not be launched, or the run timed
    out)?
  * if it ran, did the actual exit code and report hash match the
    committed baseline (`stale` if not, `reproducing` if so)?

Every directory under --root, and every name in baselines.json, ends up
in exactly one of those six states. The report is the per-directory list
plus totals that are a pure count of that list -- a reviewer can verify
the totals by counting the array themselves.

Exit codes
----------
    0  every directory (baselined or not) is in state "reproducing"
    1  ran to completion but at least one directory is "not_baselined",
       "stale", "orphaned_baseline", "source_missing", or "unrunnable"
    2  setup error: baselines.json missing/unreadable/malformed, --root
       not a directory, or --output could not be written. Nothing was
       audited.

Booleans are not exit codes
----------------------------
In Python `bool` is a subclass of `int`, so a naive
`isinstance(x, int)` check silently accepts a baseline's
`"expected_exit_code": false` and then compares it equal to a real exit
code of 0. `expected_exit_code` is validated with an explicit bool
rejection (`is_exit_code`) at load time: a boolean there is a malformed
baselines file and load_baselines() raises SetupError (exit 2). It is
never coerced, and it is never allowed to make a tool look like it
reproduced.

No absolute paths, no timestamps
---------------------------------
Nothing derived from --root or --baselines (the paths themselves) is
written into the report. The only filesystem-derived strings that reach
the report are the (relative) tool directory name and the (relative,
single-component) script filename a baseline command refers to, e.g.
"forecast.py". Iteration is always over a sorted list of names. There is
no clock read anywhere in this file.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

SCHEMA_VERSION = 1
TOOL_NAME = "coverage-audit"
DEFAULT_TIMEOUT = 120
REPORT_PLACEHOLDER = "{REPORT}"

STATE_REPRODUCING = "reproducing"
STATE_STALE = "stale"
STATE_NOT_BASELINED = "not_baselined"
STATE_ORPHANED_BASELINE = "orphaned_baseline"
STATE_SOURCE_MISSING = "source_missing"
STATE_UNRUNNABLE = "unrunnable"

ALL_STATES = (
    STATE_REPRODUCING,
    STATE_STALE,
    STATE_NOT_BASELINED,
    STATE_ORPHANED_BASELINE,
    STATE_SOURCE_MISSING,
    STATE_UNRUNNABLE,
)

_INTERPRETER_RE = re.compile(r"^python3?(\.\d+)?$")


class SetupError(Exception):
    """Problem with the auditor's own setup. Maps to exit code 2. Nothing
    about any tool's reproducibility is implied by this exception -- it
    means the audit never got to run at all."""


# --------------------------------------------------------------------------
# Canonical JSON
# --------------------------------------------------------------------------

def canonical_json(obj):
    """sorted keys, tight separators, ASCII-only, single trailing newline.
    Byte-identical across runs and across machines for identical input --
    this is the property the determinism/relocation proof checks."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------
# expected_exit_code validation (the bool-coercion fix)
# --------------------------------------------------------------------------

def is_exit_code(value):
    """True only for a real (non-bool) integer exit code.

    `isinstance(True, int)` is True in Python, so a naive check would
    accept a baseline's ``"expected_exit_code": false`` and let later code
    compare it as ``0 == False`` -> True, silently matching a real exit
    code of 0 (or ``true`` matching 1). Reject bool explicitly.
    """
    return isinstance(value, int) and not isinstance(value, bool)


def _exit_code_error(path, name, value):
    if isinstance(value, bool):
        return (
            "baselines file %s: entry for %r has a JSON boolean (%s) as "
            "'expected_exit_code'. A boolean is not an exit code -- Python "
            "would compare it equal to %d and silently treat a real exit "
            "code as a match. Rejected, not coerced."
            % (path, name, "true" if value else "false", int(value))
        )
    return (
        "baselines file %s: entry for %r 'expected_exit_code' must be a "
        "JSON integer (got %r)" % (path, name, type(value).__name__)
    )


# --------------------------------------------------------------------------
# Baseline loading
# --------------------------------------------------------------------------

def load_baselines(path):
    """Load and strictly validate the baselines file. Raises SetupError on
    any structural problem. Returns dict: tool_name -> normalized entry.

    Only ``"status": "baselined"`` entries are supported (every entry in
    the real regression-checker/baselines.json is one). An entry with any
    other status -- e.g. the "unbaselineable" status regress.py supports
    -- is treated as malformed here and raises SetupError. This is a
    known, named scope limitation (see README "Limitations"), not a
    silent skip.
    """
    if not os.path.isfile(path):
        raise SetupError("baselines file not found: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, UnicodeDecodeError) as exc:
        raise SetupError("cannot read baselines file %s: %s" % (path, exc))
    except json.JSONDecodeError as exc:
        raise SetupError("baselines file %s is not valid JSON: %s" % (path, exc))

    if not isinstance(raw, dict) or "tools" not in raw or not isinstance(raw["tools"], dict):
        raise SetupError(
            "baselines file %s must be a JSON object with a top-level 'tools' object" % path
        )

    tools = {}
    for name, entry in raw["tools"].items():
        if not isinstance(entry, dict):
            raise SetupError("baselines file %s: entry for %r is not an object" % (path, name))

        status = entry.get("status")
        if status != "baselined":
            raise SetupError(
                "baselines file %s: entry for %r has status %r; coverage_audit.py "
                "only supports status == 'baselined' (see README Limitations)"
                % (path, name, status)
            )

        for required in ("command", "report_mode", "expected_exit_code", "expected_report_sha256"):
            if required not in entry:
                raise SetupError(
                    "baselines file %s: entry for %r missing required field %r"
                    % (path, name, required)
                )

        command = entry["command"]
        if not isinstance(command, list) or not command or not all(
            isinstance(tok, str) for tok in command
        ):
            raise SetupError(
                "baselines file %s: entry for %r 'command' must be a non-empty list of strings"
                % (path, name)
            )

        if entry["report_mode"] not in ("file", "stdout"):
            raise SetupError(
                "baselines file %s: entry for %r has invalid report_mode %r (want 'file' or 'stdout')"
                % (path, name, entry.get("report_mode"))
            )

        if not is_exit_code(entry["expected_exit_code"]):
            raise SetupError(_exit_code_error(path, name, entry["expected_exit_code"]))

        expected_hash = entry["expected_report_sha256"]
        if expected_hash is not None and (
            not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or any(c not in "0123456789abcdef" for c in expected_hash.lower())
        ):
            raise SetupError(
                "baselines file %s: entry for %r has an invalid expected_report_sha256"
                " (must be a 64-character hex string or JSON null)" % (path, name)
            )

        tools[name] = {
            "command": list(command),
            "report_mode": entry["report_mode"],
            "expected_exit_code": entry["expected_exit_code"],
            "expected_report_sha256": expected_hash,
        }
    return tools


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

def discover_tool_dirs(root):
    """Sorted list of immediate subdirectory names under root, skipping
    dotfiles/dirs and __pycache__. Deterministic: always sorted."""
    if not os.path.isdir(root):
        raise SetupError("--root is not a directory: %s" % root)
    try:
        entries = os.listdir(root)
    except OSError as exc:
        raise SetupError("cannot list --root %s: %s" % (root, exc))
    names = []
    for name in entries:
        if name.startswith("."):
            continue
        if name == "__pycache__":
            continue
        if os.path.isdir(os.path.join(root, name)):
            names.append(name)
    return sorted(names)


# --------------------------------------------------------------------------
# Script-existence check (source_missing), decided BEFORE any execution
# --------------------------------------------------------------------------

def find_script_token(command):
    """Best-effort extraction of the "source script" a baseline command
    runs, so its presence can be checked before attempting to execute
    anything.

    If command[0] looks like a Python interpreter ("python", "python3",
    "python3.11", ...), the script is command[1] (if present). Otherwise
    fall back to the first token that ends in ".py". Returns None if
    neither rule finds anything -- in that case source_missing is simply
    never reported for this entry, and any missing binary shows up later
    as `unrunnable` instead (see README Limitations: non-Python commands
    get weaker source-missing detection).
    """
    if not command:
        return None
    if _INTERPRETER_RE.match(command[0]) and len(command) > 1:
        return command[1]
    for tok in command:
        if tok.endswith(".py"):
            return tok
    return None


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------

def run_command(tool_dir, command, report_mode, timeout):
    """Execute one baselined command in tool_dir with a fresh temp
    {REPORT} path (file mode) that WE create and clean up. Returns a dict:

        ok                    bool -- False means the process could not
                               be launched/finished at all (unrunnable)
        actual_exit_code       int or None
        report_created         bool (file mode only; True for stdout mode)
        actual_report_sha256   str or None
        report_bytes_length    int or None
        error                  str or None -- set when ok is False
    """
    cmd = list(command)
    tmp_dir_obj = None
    tmp_report_path = None
    if report_mode == "file":
        tmp_dir_obj = tempfile.TemporaryDirectory(prefix="coverage_audit_report_")
        tmp_report_path = os.path.join(tmp_dir_obj.name, "report.out")
        cmd = [
            (tok.replace(REPORT_PLACEHOLDER, tmp_report_path) if REPORT_PLACEHOLDER in tok else tok)
            for tok in cmd
        ]

    try:
        try:
            proc = subprocess.run(
                cmd,
                cwd=tool_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "actual_exit_code": None,
                "report_created": False,
                "actual_report_sha256": None,
                "report_bytes_length": None,
                "error": "timeout after %ss" % timeout,
            }
        except (FileNotFoundError, PermissionError, OSError) as exc:
            return {
                "ok": False,
                "actual_exit_code": None,
                "report_created": False,
                "actual_report_sha256": None,
                "report_bytes_length": None,
                "error": "could not execute command: %s" % exc,
            }

        actual_exit_code = proc.returncode

        if report_mode == "stdout":
            report_bytes = proc.stdout
            report_created = True
        else:
            report_created = tmp_report_path is not None and os.path.isfile(tmp_report_path)
            report_bytes = None
            if report_created:
                try:
                    with open(tmp_report_path, "rb") as fh:
                        report_bytes = fh.read()
                except OSError:
                    report_created = False
                    report_bytes = None

        return {
            "ok": True,
            "actual_exit_code": actual_exit_code,
            "report_created": report_created,
            "actual_report_sha256": sha256_hex(report_bytes) if report_bytes is not None else None,
            "report_bytes_length": len(report_bytes) if report_bytes is not None else None,
            "error": None,
        }
    finally:
        if tmp_dir_obj is not None:
            tmp_dir_obj.cleanup()


# --------------------------------------------------------------------------
# Per-directory classification
# --------------------------------------------------------------------------

def classify(name, root, entry, present_dirs, timeout):
    """Classify one tool name into exactly one of ALL_STATES. Never
    reports source_missing or unrunnable as reproducing."""
    present = name in present_dirs

    if entry is None:
        # present is necessarily True here: `name` came from the union of
        # present_dirs and baseline keys, and entry is None means it is
        # not a baseline key, so it must be a present directory.
        return {
            "tool": name,
            "state": STATE_NOT_BASELINED,
            "detail": "directory exists under root but has no entry in baselines.json",
        }

    if not present:
        return {
            "tool": name,
            "state": STATE_ORPHANED_BASELINE,
            "detail": "baselines.json has an entry for this tool but no directory exists under root",
        }

    tool_dir = os.path.join(root, name)
    script = find_script_token(entry["command"])
    if script is not None:
        # Reject any script token that is not a single relative path
        # component under tool_dir (defence in depth; every real entry is
        # a bare filename like "forecast.py").
        script_path = os.path.join(tool_dir, script)
        if (
            os.path.isabs(script)
            or os.path.relpath(script_path, tool_dir).startswith("..")
            or not os.path.isfile(script_path)
        ):
            return {
                "tool": name,
                "state": STATE_SOURCE_MISSING,
                "script": script,
                "detail": "baseline command references %r but that file does not exist "
                "in the tool directory" % script,
            }

    run_result = run_command(tool_dir, entry["command"], entry["report_mode"], timeout)
    if not run_result["ok"]:
        return {
            "tool": name,
            "state": STATE_UNRUNNABLE,
            "detail": run_result["error"],
        }

    actual_exit = run_result["actual_exit_code"]
    expected_exit = entry["expected_exit_code"]
    expected_hash = entry["expected_report_sha256"]
    actual_hash = run_result["actual_report_sha256"]

    reasons = []
    if actual_exit != expected_exit:
        reasons.append("exit_code_mismatch")
    if entry["report_mode"] == "file" and not run_result["report_created"]:
        reasons.append("report_not_created")
    elif expected_hash is None:
        reasons.append("expected_hash_missing")
    elif actual_hash != expected_hash:
        reasons.append("hash_mismatch")

    common = {
        "actual_exit_code": actual_exit,
        "expected_exit_code": expected_exit,
        "actual_report_sha256": actual_hash,
        "expected_report_sha256": expected_hash,
        "report_bytes_length": run_result["report_bytes_length"],
    }

    if reasons:
        result = {"tool": name, "state": STATE_STALE, "reasons": reasons}
        result.update(common)
        return result

    result = {"tool": name, "state": STATE_REPRODUCING}
    result.update(common)
    return result


# --------------------------------------------------------------------------
# Report assembly
# --------------------------------------------------------------------------

def build_report(root, baselines_path, timeout=DEFAULT_TIMEOUT):
    """Returns (report_dict, exit_code). Raises SetupError for anything
    that stops the audit before it can classify a single directory."""
    baselines = load_baselines(baselines_path)
    present_dirs = set(discover_tool_dirs(root))
    all_names = sorted(set(baselines.keys()) | present_dirs)

    results = []
    for name in all_names:
        entry = baselines.get(name)
        results.append(classify(name, root, entry, present_dirs, timeout))

    totals = {state: 0 for state in ALL_STATES}
    for r in results:
        totals[r["state"]] += 1

    total_records = len(results)
    exit_code = 0 if totals[STATE_REPRODUCING] == total_records else 1

    counts = {
        "discovered_directories": len(present_dirs),
        "baseline_entries": len(baselines),
        "total_records": total_records,
    }
    counts.update(totals)

    report = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": STATE_REPRODUCING if exit_code == 0 else "issues_found",
        "counts": counts,
        "results": results,
    }
    return report, exit_code


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="coverage_audit.py",
        description="Check every tool directory against baselines.json (not-baselined / "
        "orphaned-baseline / source-missing / unrunnable / stale / reproducing) and "
        "rerun every baselined tool to verify it.",
    )
    p.add_argument("--root", default="..", help="directory containing tool subdirectories (default: ..)")
    p.add_argument(
        "--baselines", default="baselines.json", help="path to the baselines JSON file (default: baselines.json)"
    )
    p.add_argument("-o", "--output", default=None, help="write the report JSON here instead of stdout")
    p.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="per-tool subprocess timeout in seconds (default: %d)" % DEFAULT_TIMEOUT,
    )
    return p


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        report, exit_code = build_report(args.root, args.baselines, args.timeout)
        text = canonical_json(report)
        if args.output:
            try:
                with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
            except OSError as exc:
                raise SetupError("could not write --output %s: %s" % (args.output, exc))
        else:
            sys.stdout.write(text)
        return exit_code
    except SetupError as exc:
        error_report = {
            "schema_version": SCHEMA_VERSION,
            "tool": TOOL_NAME,
            "status": "error",
            "error": str(exc),
        }
        text = canonical_json(error_report)
        if args.output:
            # The machine-readable error report still goes to --output, but a
            # human running with -o would otherwise see an entirely silent
            # exit 2. Echo one line to stderr as well.
            sys.stderr.write("coverage_audit: setup error: %s\n" % exc)
            try:
                with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
            except OSError:
                sys.stderr.write(text)
        else:
            sys.stdout.write(text)
        return 2


if __name__ == "__main__":
    sys.exit(main())
