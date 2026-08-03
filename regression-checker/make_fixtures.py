#!/usr/bin/env python3
"""make_fixtures.py -- regenerate regression-checker/fixtures/ from scratch.

Why this file exists
--------------------
`README.md` documents a committed `fixtures/` tree, `test_regress.py`
runs 14 CLI-level tests against it, and the README's own VERIFICATION
block invokes it directly. That tree was never committed. On a fresh
clone those 14 tests fail and the documented verification commands
cannot be reproduced. This script rebuilds the tree the tests and the
README describe, so `fixtures/` is derivable rather than hand-maintained.

Determinism
-----------
Every file written here is fixed text -- no timestamps, no absolute
paths, no randomness. `baselines_ok.json` is *not* hand-typed: the two
values that must be true (each tool's real exit code and the SHA-256 of
its real report bytes) are measured by running each fixture tool exactly
the way `regress.py` would. `baselines_drift.json` starts from those
measured values and then deliberately corrupts specific entries, so each
drift code has exactly one cause.

Usage
-----
    python3 make_fixtures.py [--out fixtures]
    python3 make_fixtures.py --check       # verify tree matches, write nothing

Exit codes: 0 success (or --check passed), 1 --check found a mismatch,
2 setup error.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable or "python3"

# --------------------------------------------------------------------------
# Fixture tool sources. Each is a whole file; keep them boring and fixed.
# --------------------------------------------------------------------------

# Writes a fixed report to -o/--output, or stdout when no -o is given.
TOOL_FILE_REPORT = '''#!/usr/bin/env python3
"""Fixture tool: writes a fixed report to --output. Deterministic."""
import argparse
import json
import sys

ap = argparse.ArgumentParser()
ap.add_argument("-o", "--output")
args = ap.parse_args()

report = {report}
text = json.dumps(report, sort_keys=True, separators=(",", ":")) + "\\n"
if args.output:
    with open(args.output, "w", encoding="utf-8", newline="\\n") as fh:
        fh.write(text)
else:
    sys.stdout.write(text)
sys.exit({exit_code})
'''

# Echoes its argv tail back into the report, so a shell-metacharacter token
# survives verbatim only if no shell ever touched it.
TOOL_ECHO_ARGV = '''#!/usr/bin/env python3
"""Fixture tool: echoes the --token value into the report verbatim.

If regress.py ever ran commands through a shell, the metacharacters in
the baselined token would be expanded or split and the report hash would
change. It never does, so the hash is stable.
"""
import argparse
import json
import sys

ap = argparse.ArgumentParser()
ap.add_argument("-o", "--output")
ap.add_argument("--token", default="")
args = ap.parse_args()

report = {"tool": "tool_shell_meta", "token": args.token}
text = json.dumps(report, sort_keys=True, separators=(",", ":")) + "\\n"
if args.output:
    with open(args.output, "w", encoding="utf-8", newline="\\n") as fh:
        fh.write(text)
else:
    sys.stdout.write(text)
sys.exit(0)
'''

README_TXT = """\
This directory is generated. Do not hand-edit.

Regenerate with:

    python3 ../make_fixtures.py

Verify it still matches what the generator produces:

    python3 ../make_fixtures.py --check

Seven synthetic tool directories plus two baseline files. They exist so
test_regress.py's CLI-level tests and the README's VERIFICATION block
never depend on the real sibling tool directories being present.
"""

# A token deliberately full of shell metacharacters. If a shell ever saw
# this it would glob, split, substitute or truncate it.
SHELL_META_TOKEN = "a b;c|d&e>f<g*h?i$j`k'l\"m(n)o[p]q{r}s~t#u!v"

# name -> (tool source or None, argv after the interpreter, report_mode)
FIXTURE_TOOLS = {
    "tool_ok": (
        TOOL_FILE_REPORT.format(report={"tool": "tool_ok", "findings": 0}, exit_code=0),
        ["tool.py", "-o", "{REPORT}"],
        "file",
    ),
    "tool_exit_drift": (
        TOOL_FILE_REPORT.format(report={"tool": "tool_exit_drift", "findings": 0}, exit_code=0),
        ["tool.py", "-o", "{REPORT}"],
        "file",
    ),
    "tool_hash_drift": (
        TOOL_FILE_REPORT.format(report={"tool": "tool_hash_drift", "findings": 3}, exit_code=0),
        ["tool.py", "-o", "{REPORT}"],
        "file",
    ),
    "tool_stdout": (
        TOOL_FILE_REPORT.format(report={"tool": "tool_stdout", "findings": 0}, exit_code=0),
        ["tool.py"],
        "stdout",
    ),
    "tool_shell_meta": (
        TOOL_ECHO_ARGV,
        ["tool.py", "-o", "{REPORT}", "--token", SHELL_META_TOKEN],
        "file",
    ),
    # No tool.py at all: unbaselineable in _ok, a real failing command in _drift.
    "tool_error": (None, ["tool.py", "-o", "{REPORT}"], "file"),
    "tool_null_hash_demo": (
        TOOL_FILE_REPORT.format(report={"tool": "tool_null_hash_demo", "findings": 1}, exit_code=0),
        ["tool.py", "-o", "{REPORT}"],
        "file",
    ),
}

# Entries that baselines_ok.json marks unbaselineable rather than measuring.
UNBASELINEABLE_IN_OK = {
    "tool_error": "no runnable tool.py: exists to exercise EXECUTION_ERROR in the drift baseline",
    "tool_null_hash_demo": "exists to exercise the null-hash always-drift marker in the drift baseline",
}


def measure(tool_dir, command, report_mode):
    """Run one fixture tool exactly the way regress.py would and return
    (exit_code, sha256_of_report_bytes). Raises RuntimeError if the run
    does not terminate normally or produces no report."""
    with tempfile.TemporaryDirectory(prefix="mkfix_") as td:
        report_path = os.path.join(td, "report.out")
        argv = [PY] + [tok.replace("{REPORT}", report_path) for tok in command]
        proc = subprocess.run(
            argv, cwd=tool_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60
        )
        if proc.returncode < 0:
            raise RuntimeError("%s terminated on signal %d" % (tool_dir, -proc.returncode))
        if report_mode == "stdout":
            data = proc.stdout
        else:
            if not os.path.isfile(report_path):
                raise RuntimeError("%s wrote no report file" % tool_dir)
            with open(report_path, "rb") as fh:
                data = fh.read()
        if not data:
            raise RuntimeError("%s produced a 0-byte report" % tool_dir)
        return proc.returncode, hashlib.sha256(data).hexdigest()


def flip_hex(digest):
    """Return a different but still well-formed 64-char sha256 hex string."""
    first = "1" if digest[0] == "0" else "0"
    return first + digest[1:]


def build(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "README.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(README_TXT)

    for name, (source, _command, _mode) in sorted(FIXTURE_TOOLS.items()):
        d = os.path.join(out_dir, name)
        os.makedirs(d, exist_ok=True)
        tool_py = os.path.join(d, "tool.py")
        if source is None:
            # tool_error must have NO tool.py. Remove a stale one so a rerun
            # after an edit cannot leave the tree in a half-old state.
            if os.path.exists(tool_py):
                os.remove(tool_py)
            with open(os.path.join(d, "NOTE.txt"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write("Intentionally has no tool.py. See ../README.txt.\n")
        else:
            with open(tool_py, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(source)

    measured = {}
    for name, (source, command, mode) in sorted(FIXTURE_TOOLS.items()):
        if source is None:
            continue
        measured[name] = measure(os.path.join(out_dir, name), command, mode)

    ok_tools = {}
    for name, (source, command, mode) in sorted(FIXTURE_TOOLS.items()):
        if name in UNBASELINEABLE_IN_OK:
            ok_tools[name] = {"status": "unbaselineable", "reason": UNBASELINEABLE_IN_OK[name]}
            continue
        exit_code, digest = measured[name]
        ok_tools[name] = {
            "status": "baselined",
            "command": [PY_TOKEN] + list(command),
            "report_mode": mode,
            "expected_exit_code": exit_code,
            "expected_report_sha256": digest,
        }

    drift_tools = {}
    for name, (source, command, mode) in sorted(FIXTURE_TOOLS.items()):
        if name == "tool_error":
            # A real command that cannot run -> EXECUTION_ERROR.
            drift_tools[name] = {
                "status": "baselined",
                "command": [PY_TOKEN] + list(command),
                "report_mode": mode,
                "expected_exit_code": 0,
                "expected_report_sha256": "0" * 64,
            }
            continue
        exit_code, digest = measured[name]
        entry = {
            "status": "baselined",
            "command": [PY_TOKEN] + list(command),
            "report_mode": mode,
            "expected_exit_code": exit_code,
            "expected_report_sha256": digest,
        }
        if name == "tool_exit_drift":
            entry["expected_exit_code"] = exit_code + 7  # -> EXIT_CODE_DRIFT only
        elif name == "tool_hash_drift":
            entry["expected_report_sha256"] = flip_hex(digest)  # -> REPORT_HASH_DRIFT only
        elif name == "tool_null_hash_demo":
            entry["expected_report_sha256"] = None  # -> REPORT_HASH_DRIFT, always
        drift_tools[name] = entry

    # Named in the baseline, no directory anywhere -> TOOL_MISSING.
    drift_tools["ghost_tool"] = {
        "status": "baselined",
        "command": [PY_TOKEN, "tool.py"],
        "report_mode": "stdout",
        "expected_exit_code": 0,
        "expected_report_sha256": "0" * 64,
    }

    write_json(os.path.join(out_dir, "baselines_ok.json"), {"tools": ok_tools})
    write_json(os.path.join(out_dir, "baselines_drift.json"), {"tools": drift_tools})


# "python3" rather than sys.executable: an absolute interpreter path in a
# committed baseline is exactly the unreproducible artefact this repo keeps
# finding elsewhere. regress.py resolves it via PATH.
PY_TOKEN = "python3"


def write_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
        fh.write("\n")


def snapshot(root):
    """Map relative path -> sha256 for every file under root, sorted."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            with open(full, "rb") as fh:
                out[rel] = hashlib.sha256(fh.read()).hexdigest()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="make_fixtures.py")
    ap.add_argument("--out", default=os.path.join(HERE, "fixtures"))
    ap.add_argument("--check", action="store_true",
                    help="regenerate into a temp dir and compare against --out; write nothing")
    args = ap.parse_args(argv)

    if not args.check:
        build(args.out)
        print("wrote %d files under %s" % (len(snapshot(args.out)), args.out))
        return 0

    if not os.path.isdir(args.out):
        sys.stderr.write("--check: %s does not exist\n" % args.out)
        return 1
    with tempfile.TemporaryDirectory(prefix="mkfix_check_") as td:
        ref = os.path.join(td, "fixtures")
        build(ref)
        want, got = snapshot(ref), snapshot(args.out)
    if want == got:
        print("fixtures match the generator (%d files)" % len(want))
        return 0
    for rel in sorted(set(want) | set(got)):
        if want.get(rel) != got.get(rel):
            print("MISMATCH %s: generated=%s on-disk=%s"
                  % (rel, want.get(rel, "<absent>")[:12], got.get(rel, "<absent>")[:12]))
    return 1


if __name__ == "__main__":
    sys.exit(main())
