#!/usr/bin/env python3
"""regress.py -- stdlib-only repository regression checker.

Runs every tool's documented fixture command (as recorded in a baselines
file), compares the observed exit code and SHA-256 of the report bytes
against the committed baseline, and reports drift as canonical JSON.

Exit codes:
    0  no drift detected
    1  drift detected (includes per-tool execution errors -- see below)
    2  setup error (cannot read --baselines / --root, malformed baseline
       file, cannot write --output, etc.)

Why per-tool execution errors are exit 1, not exit 2
-----------------------------------------------------
A tool that used to run cleanly and now times out, or whose interpreter
can no longer be found, or whose report file silently disappeared, IS a
regression -- exactly the kind of thing this checker exists to catch.
Exit code 2 is reserved for problems with the *checker's own* setup
(the baselines file is missing/invalid, --root is missing, --output
cannot be written). Anything that happens while attempting to reproduce
a tool's documented command is reported as the drift code
EXECUTION_ERROR and rolls into the "drift detected" exit code 1.

No wall-clock data
-------------------
This report intentionally contains NO timestamps and NO durations.
Recording "how long did it take" is the obvious thing to add to a test
runner's report, and it is exactly the kind of field that silently
destroys byte-for-byte reproducibility (the whole point of hashing the
report). Two runs of the same inputs on the same code MUST produce
byte-identical JSON. If you need timing data, capture it out-of-band
(e.g. `time python3 regress.py ...`), never inside the report.

No absolute paths
------------------
Tool names and relative command tokens are recorded; the --root
directory itself, working directories, and any temp-file paths used to
capture file-based reports are never written into the report.
"""

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile

SCHEMA_VERSION = 1
TOOL_NAME = "regression-checker"
DEFAULT_TIMEOUT = 120
REPORT_PLACEHOLDER = "{REPORT}"

DRIFT_EXIT_CODE = "EXIT_CODE_DRIFT"
DRIFT_HASH = "REPORT_HASH_DRIFT"
DRIFT_TOOL_MISSING = "TOOL_MISSING"
DRIFT_UNBASELINED = "UNBASELINED_TOOL"
DRIFT_EXEC_ERROR = "EXECUTION_ERROR"

ALL_DRIFT_CODES = frozenset(
    {DRIFT_EXIT_CODE, DRIFT_HASH, DRIFT_TOOL_MISSING, DRIFT_UNBASELINED, DRIFT_EXEC_ERROR}
)


class SetupError(Exception):
    """Raised for problems with the checker's own setup (exit code 2)."""


# --------------------------------------------------------------------------
# Canonical JSON
# --------------------------------------------------------------------------

def canonical_json(obj):
    """Serialize obj as canonical JSON: sorted keys, tight separators,
    ASCII-only, single trailing newline. Byte-identical across runs for
    identical input."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------
# Baseline loading
# --------------------------------------------------------------------------

def load_baselines(path):
    """Load and validate the baselines file. Raises SetupError on any
    problem reading or parsing it. Returns dict: tool_name -> entry dict."""
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
        status = entry.get("status", "baselined")
        if status not in ("baselined", "unbaselineable"):
            raise SetupError(
                "baselines file %s: entry for %r has unknown status %r"
                % (path, name, status)
            )
        if status == "unbaselineable":
            if "reason" not in entry or not isinstance(entry["reason"], str):
                raise SetupError(
                    "baselines file %s: unbaselineable entry for %r needs a string 'reason'"
                    % (path, name)
                )
            tools[name] = {"status": "unbaselineable", "reason": entry["reason"]}
            continue

        for required in ("command", "report_mode", "expected_exit_code"):
            if required not in entry:
                raise SetupError(
                    "baselines file %s: entry for %r missing required field %r"
                    % (path, name, required)
                )
        if not isinstance(entry["command"], list) or not all(
            isinstance(tok, str) for tok in entry["command"]
        ):
            raise SetupError(
                "baselines file %s: entry for %r 'command' must be a list of strings"
                % (path, name)
            )
        if entry["report_mode"] not in ("file", "stdout"):
            raise SetupError(
                "baselines file %s: entry for %r has invalid report_mode %r (want 'file' or 'stdout')"
                % (path, name, entry.get("report_mode"))
            )
        if not isinstance(entry["expected_exit_code"], int):
            raise SetupError(
                "baselines file %s: entry for %r 'expected_exit_code' must be an integer"
                % (path, name)
            )
        expected_hash = entry.get("expected_report_sha256")
        if expected_hash is not None and (
            not isinstance(expected_hash, str) or len(expected_hash) != 64
        ):
            # A null hash is treated as an explicit, deliberate "always
            # drift on hash" marker (see README: null-hash baseline entry
            # edge case). Anything else non-null must be a real 64-hex sha256.
            raise SetupError(
                "baselines file %s: entry for %r has an invalid expected_report_sha256"
                " (must be a 64-character hex string or JSON null)" % (path, name)
            )
        tools[name] = {
            "status": "baselined",
            "command": list(entry["command"]),
            "report_mode": entry["report_mode"],
            "expected_exit_code": entry["expected_exit_code"],
            "expected_report_sha256": expected_hash,
        }
    return tools


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

def discover_tool_dirs(root):
    """Return a SORTED list of immediate subdirectory names under root
    that look like tool directories (skip dotfiles/dirs and __pycache__)."""
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
        full = os.path.join(root, name)
        if os.path.isdir(full):
            names.append(name)
    return sorted(names)


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------

def run_tool(tool_dir, entry, timeout):
    """Run one baselined tool's documented command. Returns a dict with
    keys: ok (bool), actual_exit_code, actual_report_sha256, error (str
    or None), report_bytes_length (int or None)."""
    command = list(entry["command"])
    report_mode = entry["report_mode"]

    tmp_report_path = None
    tmp_dir_obj = None
    if report_mode == "file":
        tmp_dir_obj = tempfile.TemporaryDirectory(prefix="regress_report_")
        tmp_report_path = os.path.join(tmp_dir_obj.name, "report.out")
        command = [
            (tok.replace(REPORT_PLACEHOLDER, tmp_report_path) if REPORT_PLACEHOLDER in tok else tok)
            for tok in command
        ]

    try:
        try:
            proc = subprocess.run(
                command,
                cwd=tool_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "actual_exit_code": None,
                "actual_report_sha256": None,
                "error": "timeout after %ss running: %s" % (timeout, " ".join(shlex.quote(c) for c in command)),
            }
        except FileNotFoundError as exc:
            return {
                "ok": False,
                "actual_exit_code": None,
                "actual_report_sha256": None,
                "error": "command not found: %s" % exc,
            }
        except OSError as exc:
            return {
                "ok": False,
                "actual_exit_code": None,
                "actual_report_sha256": None,
                "error": "could not execute command: %s" % exc,
            }

        if report_mode == "stdout":
            report_bytes = proc.stdout
        else:
            if not os.path.isfile(tmp_report_path):
                return {
                    "ok": False,
                    "actual_exit_code": proc.returncode,
                    "actual_report_sha256": None,
                    "error": "report file was not created by the command (report_mode=file)",
                }
            try:
                with open(tmp_report_path, "rb") as fh:
                    report_bytes = fh.read()
            except OSError as exc:
                return {
                    "ok": False,
                    "actual_exit_code": proc.returncode,
                    "actual_report_sha256": None,
                    "error": "could not read report file: %s" % exc,
                }

        return {
            "ok": True,
            "actual_exit_code": proc.returncode,
            "actual_report_sha256": sha256_hex(report_bytes),
            "error": None,
            "report_bytes_length": len(report_bytes),
        }
    finally:
        if tmp_dir_obj is not None:
            tmp_dir_obj.cleanup()


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------

def evaluate_tool(name, root, baseline_entry, present_dirs, timeout):
    """Compare one tool against its baseline. Returns a result dict
    (JSON-serialisable, no absolute paths, no timestamps)."""
    present = name in present_dirs

    if baseline_entry is not None and baseline_entry["status"] == "unbaselineable":
        result = {
            "tool": name,
            "status": "skipped_unbaselineable",
            "drift_codes": [],
            "reason": baseline_entry["reason"],
        }
        if not present:
            result["drift_codes"] = [DRIFT_TOOL_MISSING]
            result["status"] = "drift"
            result["note"] = "baseline marks this tool unbaselineable, but its directory is also missing"
        return result

    if baseline_entry is None:
        # UNBASELINED_TOOL: directory present, no baseline entry at all.
        return {
            "tool": name,
            "status": "drift",
            "drift_codes": [DRIFT_UNBASELINED],
            "detail": "tool directory exists under --root but has no entry in the baseline file",
        }

    if not present:
        return {
            "tool": name,
            "status": "drift",
            "drift_codes": [DRIFT_TOOL_MISSING],
            "detail": "baseline names this tool but its directory is not present under --root",
        }

    tool_dir = os.path.join(root, name)
    run_result = run_tool(tool_dir, baseline_entry, timeout)

    drift_codes = []
    detail = {}

    if not run_result["ok"]:
        drift_codes.append(DRIFT_EXEC_ERROR)
        detail["execution_error"] = run_result["error"]
        if run_result["actual_exit_code"] is not None:
            detail["actual_exit_code"] = run_result["actual_exit_code"]
            if run_result["actual_exit_code"] != baseline_entry["expected_exit_code"]:
                drift_codes.append(DRIFT_EXIT_CODE)
                detail["expected_exit_code"] = baseline_entry["expected_exit_code"]
    else:
        actual_exit = run_result["actual_exit_code"]
        actual_hash = run_result["actual_report_sha256"]
        expected_exit = baseline_entry["expected_exit_code"]
        expected_hash = baseline_entry["expected_report_sha256"]

        detail["actual_exit_code"] = actual_exit
        detail["expected_exit_code"] = expected_exit
        detail["actual_report_sha256"] = actual_hash
        detail["expected_report_sha256"] = expected_hash
        detail["report_bytes_length"] = run_result["report_bytes_length"]

        if actual_exit != expected_exit:
            drift_codes.append(DRIFT_EXIT_CODE)
        if expected_hash is None or actual_hash != expected_hash:
            drift_codes.append(DRIFT_HASH)

    status = "drift" if drift_codes else "clean"
    return {
        "tool": name,
        "status": status,
        "drift_codes": drift_codes,
        "detail": detail,
    }


# --------------------------------------------------------------------------
# Report assembly
# --------------------------------------------------------------------------

def build_report(root, baselines, timeout):
    present_dirs = set(discover_tool_dirs(root))
    all_names = sorted(set(baselines.keys()) | present_dirs)

    results = []
    for name in all_names:
        entry = baselines.get(name)
        results.append(evaluate_tool(name, root, entry, present_dirs, timeout))

    summary = {
        "clean": 0,
        "drift": 0,
        "skipped_unbaselineable": 0,
    }
    drift_counts = {code: 0 for code in sorted(ALL_DRIFT_CODES)}
    for r in results:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
        for code in r["drift_codes"]:
            drift_counts[code] += 1

    any_drift = summary["drift"] > 0
    report = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": "drift" if any_drift else "clean",
        "tools_checked": len(all_names),
        "summary": summary,
        "drift_counts": drift_counts,
        "results": results,
    }
    return report, any_drift


# --------------------------------------------------------------------------
# --update-baselines
# --------------------------------------------------------------------------

def update_baselines(root, baselines_path, raw_baselines, timeout, out_stream):
    print("=" * 78, file=out_stream)
    print("WARNING: --update-baselines REWRITES THE COMMITTED BASELINE.", file=out_stream)
    print("This is how a real regression gets whitewashed. Every entry's", file=out_stream)
    print("expected exit code and report hash will be overwritten with", file=out_stream)
    print("whatever the tools produce RIGHT NOW. Only run this deliberately,", file=out_stream)
    print("after confirming any exit-code/hash changes are intentional.", file=out_stream)
    print("This must never be the default and is never run implicitly.", file=out_stream)
    print("=" * 78, file=out_stream)

    present_dirs = set(discover_tool_dirs(root))
    tools = raw_baselines.get("tools", {})
    updated, failed, skipped = [], [], []

    for name in sorted(tools.keys()):
        entry = tools[name]
        if entry.get("status") == "unbaselineable":
            skipped.append(name)
            continue
        if name not in present_dirs:
            failed.append((name, "tool directory not present under --root"))
            continue
        norm_entry = {
            "status": "baselined",
            "command": list(entry["command"]),
            "report_mode": entry["report_mode"],
            "expected_exit_code": entry["expected_exit_code"],
            "expected_report_sha256": entry.get("expected_report_sha256"),
        }
        run_result = run_tool(os.path.join(root, name), norm_entry, timeout)
        if not run_result["ok"]:
            failed.append((name, run_result["error"]))
            continue
        old_exit = entry.get("expected_exit_code")
        old_hash = entry.get("expected_report_sha256")
        new_exit = run_result["actual_exit_code"]
        new_hash = run_result["actual_report_sha256"]
        entry["expected_exit_code"] = new_exit
        entry["expected_report_sha256"] = new_hash
        changed = (old_exit != new_exit) or (old_hash != new_hash)
        updated.append((name, changed))

    try:
        with open(baselines_path, "w", encoding="utf-8") as fh:
            json.dump(raw_baselines, fh, indent=2, sort_keys=True)
            fh.write("\n")
    except OSError as exc:
        raise SetupError("could not write updated baselines to %s: %s" % (baselines_path, exc))

    print("", file=out_stream)
    for name, changed in updated:
        marker = "CHANGED" if changed else "same"
        print("  updated  %-30s %s" % (name, marker), file=out_stream)
    for name in skipped:
        print("  skipped  %-30s (unbaselineable)" % name, file=out_stream)
    for name, why in failed:
        print("  FAILED   %-30s %s" % (name, why), file=out_stream)
    print("", file=out_stream)
    print(
        "%d updated, %d skipped (unbaselineable), %d failed"
        % (len(updated), len(skipped), len(failed)),
        file=out_stream,
    )
    return 2 if failed else 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="regress.py",
        description="Run every tool's documented fixture command and detect drift "
        "in exit codes or report bytes versus a committed baseline.",
    )
    p.add_argument("--root", default=".", help="directory containing tool subdirectories (default: .)")
    p.add_argument("--baselines", default="baselines.json", help="path to the baselines JSON file")
    p.add_argument("-o", "--output", default=None, help="write the report JSON here instead of stdout")
    p.add_argument(
        "--update-baselines",
        action="store_true",
        help="DANGEROUS: re-run every already-baselined tool and overwrite its expected "
        "exit code / report hash in the baselines file. Never implied by any other flag.",
    )
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
        if not os.path.isfile(args.baselines):
            raise SetupError("baselines file not found: %s" % args.baselines)
        try:
            with open(args.baselines, "r", encoding="utf-8") as fh:
                raw_baselines = json.load(fh)
        except (OSError, UnicodeDecodeError) as exc:
            raise SetupError("cannot read baselines file %s: %s" % (args.baselines, exc))
        except json.JSONDecodeError as exc:
            raise SetupError("baselines file %s is not valid JSON: %s" % (args.baselines, exc))

        if args.update_baselines:
            code = update_baselines(args.root, args.baselines, raw_baselines, args.timeout, sys.stderr)
            return code

        baselines = load_baselines(args.baselines)
        if not os.path.isdir(args.root):
            raise SetupError("--root is not a directory: %s" % args.root)

        report, any_drift = build_report(args.root, baselines, args.timeout)
        text = canonical_json(report)

        if args.output:
            try:
                with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
            except OSError as exc:
                raise SetupError("could not write --output %s: %s" % (args.output, exc))
        else:
            sys.stdout.write(text)

        return 1 if any_drift else 0

    except SetupError as exc:
        error_report = {
            "schema_version": SCHEMA_VERSION,
            "tool": TOOL_NAME,
            "status": "error",
            "error": str(exc),
        }
        text = canonical_json(error_report)
        if args.output:
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
