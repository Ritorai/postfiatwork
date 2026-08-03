#!/usr/bin/env python3
"""crosspath.py -- stdlib-only cross-path reproducibility runner.

Runs every documented tool command **twice, from two copies of the tree at
two different absolute paths**, canonicalises each JSON report, and
compares the hashes. A tool whose output depends on where the checkout
lives diverges here.

Why this is not the regression checker
--------------------------------------
`regression-checker` compares one run against a hash committed earlier.
That is the right tool for "did this tool's output change?", and it is
structurally blind to location dependence:

* Its baseline was recorded from **one** path. A tool that writes its own
  absolute path into its report produces a hash that is wrong on every
  other machine -- but the checker cannot tell "leaks its path" from
  "genuinely changed", because it only ever sees one hash at a time.
* Worse, if the baseline was recorded from the same path the check is run
  from -- the normal case, and exactly what `--update-baselines` does --
  the leak is identical in both, the hash matches, and the tool is
  reported **clean forever**.

This runner needs no baseline. It runs both halves of the comparison in
the same invocation, from paths chosen to differ in spelling *and* in
length, so a length-dependent artefact (a column-aligned table, a
truncated path) cannot cancel out.

Exit codes:
    0  every tool produced identical canonical output from both paths
    1  at least one tool diverged
    2  setup error, or a tool could not be executed in one or both copies

Why an execution error is exit 2 here and exit 1 in `regression-checker`:
there, a tool that stopped running *is* the regression being looked for.
Here, a tool that did not run in both copies produced no comparison at
all. Reporting "no divergence found" for a tool that never ran would be
the same false assurance this repository keeps finding elsewhere.

No absolute paths in the report
--------------------------------
The two working paths are the *inputs* to this tool, so quoting them back
would defeat the point. Every string that reaches the report is passed
through a redactor that replaces them with `<ROOT_A>` and `<ROOT_B>`.
That is also how a leak is reported: if a redaction fired inside a tool's
own report, that tool leaked its path, and the finding says so without
reproducing the path.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

SCHEMA_VERSION = 1
TOOL_NAME = "crosspath-runner"
DEFAULT_TIMEOUT = 120
REPORT_PLACEHOLDER = "{REPORT}"

# Deliberately different spelling AND different length. Equal-length names
# would not catch an artefact that depends on how long the path is.
#
# The length DIFFERENCE matters too, and this is not a detail. Any artefact
# of the form `f(len(path)) % N` cancels out whenever N divides the length
# difference. My first pair differed by 30, and two of this tool's own tests
# -- a formatting choice keyed on `len(cwd) % 3` and an exit code keyed on
# `len(cwd) % 2` -- silently passed as "identical", because 2 and 3 both
# divide 30. The difference is now 41, a prime, so every period from 2 to 40
# is exposed; only a period of exactly 41 or a multiple of it can hide.
# Asserted by a test rather than left to a comment.
DIR_A = "xp_a"
DIR_B = "xp_b_this_copy_has_a_deliberately_longer_name"

C_HASH = "REPORT_HASH_DIVERGENCE"
C_EXIT = "EXIT_CODE_DIVERGENCE"
C_LEAK = "PATH_LEAK"
C_ERROR = "EXECUTION_ERROR"
C_NONJSON = "NON_CANONICAL_JSON"

ALL_CODES = frozenset({C_HASH, C_EXIT, C_LEAK, C_ERROR, C_NONJSON})

MAX_REPORTED_DIFFS = 5
MAX_VALUE_CHARS = 200


class SetupError(Exception):
    """Problems with the runner's own setup (exit code 2)."""


# --------------------------------------------------------------------------
# Canonical JSON
# --------------------------------------------------------------------------

def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def sha256_hex(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------

class Redactor:
    """Replaces the two working roots with stable placeholders.

    Longest-first so that a nested path is not half-replaced. Reports
    whether anything was replaced, which is how PATH_LEAK is detected.
    """

    def __init__(self, path_a, path_b):
        self.pairs = sorted(
            [(path_a, "<ROOT_A>"), (path_b, "<ROOT_B>")],
            key=lambda p: len(p[0]),
            reverse=True,
        )
        self.hit = False

    def text(self, value):
        if not isinstance(value, str):
            return value
        out = value
        for needle, token in self.pairs:
            if needle and needle in out:
                self.hit = True
                out = out.replace(needle, token)
        return out

    def scan(self, blob):
        """True if either root appears anywhere in blob."""
        if isinstance(blob, bytes):
            blob = blob.decode("utf-8", "replace")
        return any(needle and needle in blob for needle, _ in self.pairs)


def clip(value):
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    if len(text) > MAX_VALUE_CHARS:
        return text[:MAX_VALUE_CHARS] + "...[clipped]"
    return text


# --------------------------------------------------------------------------
# Structural diff
# --------------------------------------------------------------------------

def json_pointer_diffs(a, b, pointer="", out=None):
    """Sorted list of (json_pointer, value_a, value_b) where a and b differ.

    Deterministic: dict keys are visited in sorted order and the walk stops
    at the first differing node rather than descending into it, so the
    output is a set of minimal differing subtrees, not a flood of leaves.
    """
    if out is None:
        out = []
    if type(a) is not type(b):
        out.append((pointer or "/", a, b))
        return out
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            child = pointer + "/" + str(key).replace("~", "~0").replace("/", "~1")
            if key not in a:
                out.append((child, None, b[key]))
            elif key not in b:
                out.append((child, a[key], None))
            else:
                json_pointer_diffs(a[key], b[key], child, out)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append((pointer or "/", "list of %d" % len(a), "list of %d" % len(b)))
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                json_pointer_diffs(x, y, pointer + "/" + str(i), out)
    elif a != b:
        out.append((pointer or "/", a, b))
    return out


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------

def load_manifest(path):
    """Load a tools manifest. Deliberately accepts `regression-checker`'s
    own `baselines.json` unchanged -- the command and report_mode fields
    mean the same thing, and duplicating that inventory in a second format
    is how the two drift apart."""
    if not os.path.isfile(path):
        raise SetupError("manifest not found: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, UnicodeDecodeError) as exc:
        raise SetupError("cannot read manifest %s: %s" % (path, exc))
    except json.JSONDecodeError as exc:
        raise SetupError("manifest %s is not valid JSON: %s" % (path, exc))
    if not isinstance(raw, dict) or not isinstance(raw.get("tools"), dict):
        raise SetupError("manifest %s must be an object with a 'tools' object" % path)

    tools = {}
    for name, entry in raw["tools"].items():
        if not isinstance(entry, dict):
            raise SetupError("manifest %s: entry for %r is not an object" % (path, name))
        if entry.get("status") == "unbaselineable":
            # Not runnable by contract. Skipped, and said so in the report.
            tools[name] = {"runnable": False,
                           "reason": entry.get("reason", "marked unbaselineable")}
            continue
        for required in ("command", "report_mode"):
            if required not in entry:
                raise SetupError(
                    "manifest %s: entry for %r missing %r" % (path, name, required))
        if not isinstance(entry["command"], list) or not all(
                isinstance(t, str) for t in entry["command"]):
            raise SetupError(
                "manifest %s: entry for %r 'command' must be a list of strings"
                % (path, name))
        if entry["report_mode"] not in ("file", "stdout"):
            raise SetupError(
                "manifest %s: entry for %r has invalid report_mode %r"
                % (path, name, entry["report_mode"]))
        tools[name] = {"runnable": True,
                       "command": list(entry["command"]),
                       "report_mode": entry["report_mode"]}
    return tools


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------

def run_once(tool_dir, command, report_mode, timeout):
    """Run one tool command in one copy. Returns a dict; never raises for
    the tool's own failure."""
    tmp_dir_obj = None
    argv = list(command)
    if report_mode == "file":
        tmp_dir_obj = tempfile.TemporaryDirectory(prefix="crosspath_report_")
        report_path = os.path.join(tmp_dir_obj.name, "report.out")
        argv = [t.replace(REPORT_PLACEHOLDER, report_path) if REPORT_PLACEHOLDER in t else t
                for t in argv]
    try:
        if not os.path.isdir(tool_dir):
            return {"ok": False, "error": "tool directory not present in this copy"}
        try:
            proc = subprocess.run(argv, cwd=tool_dir, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "timeout after %ss" % timeout}
        except FileNotFoundError as exc:
            return {"ok": False, "error": "command not found: %s" % exc}
        except OSError as exc:
            return {"ok": False, "error": "could not execute command: %s" % exc}

        if report_mode == "stdout":
            data = proc.stdout
        else:
            if not os.path.isfile(report_path):
                return {"ok": False, "exit_code": proc.returncode,
                        "error": "report file was not created (report_mode=file)"}
            with open(report_path, "rb") as fh:
                data = fh.read()
        return {"ok": True, "exit_code": proc.returncode, "bytes": data}
    finally:
        if tmp_dir_obj is not None:
            tmp_dir_obj.cleanup()


def canonicalise(data):
    """Return (canonical_text, is_json). Non-JSON output is compared as
    raw bytes and flagged, rather than silently treated as equal."""
    try:
        return canonical_json(json.loads(data.decode("utf-8"))), True
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, False


def compare_tool(name, entry, root_a, root_b, timeout):
    if not entry["runnable"]:
        return {"tool": name, "status": "skipped", "codes": [],
                "detail": {"reason": entry["reason"]}}

    red = Redactor(root_a, root_b)
    result_a = run_once(os.path.join(root_a, name), entry["command"], entry["report_mode"], timeout)
    result_b = run_once(os.path.join(root_b, name), entry["command"], entry["report_mode"], timeout)

    if not result_a["ok"] or not result_b["ok"]:
        detail = {}
        if not result_a["ok"]:
            detail["error_a"] = red.text(result_a["error"])
        if not result_b["ok"]:
            detail["error_b"] = red.text(result_b["error"])
        return {"tool": name, "status": "error", "codes": [C_ERROR], "detail": detail}

    codes = []
    detail = {"exit_code_a": result_a["exit_code"], "exit_code_b": result_b["exit_code"]}
    if result_a["exit_code"] != result_b["exit_code"]:
        codes.append(C_EXIT)

    leaked = red.scan(result_a["bytes"]) or red.scan(result_b["bytes"])
    if leaked:
        codes.append(C_LEAK)

    canon_a, json_a = canonicalise(result_a["bytes"])
    canon_b, json_b = canonicalise(result_b["bytes"])
    if not (json_a and json_b):
        codes.append(C_NONJSON)
        detail["raw_sha256_a"] = sha256_hex(result_a["bytes"])
        detail["raw_sha256_b"] = sha256_hex(result_b["bytes"])
        if result_a["bytes"] != result_b["bytes"]:
            codes.append(C_HASH)
    else:
        detail["canonical_sha256_a"] = sha256_hex(canon_a)
        detail["canonical_sha256_b"] = sha256_hex(canon_b)
        detail["raw_sha256_a"] = sha256_hex(result_a["bytes"])
        detail["raw_sha256_b"] = sha256_hex(result_b["bytes"])
        if canon_a != canon_b:
            codes.append(C_HASH)
            diffs = json_pointer_diffs(json.loads(canon_a), json.loads(canon_b))
            detail["differing_count"] = len(diffs)
            detail["differences"] = [
                {"pointer": p, "a": red.text(clip(x)), "b": red.text(clip(y))}
                for p, x, y in diffs[:MAX_REPORTED_DIFFS]
            ]
        elif result_a["bytes"] != result_b["bytes"]:
            # Same meaning, different bytes: real, and invisible to a
            # byte-hash checker's baseline only by luck.
            codes.append(C_NONJSON)
            detail["note"] = ("canonical JSON is identical but the raw bytes differ"
                              " (formatting/key order is not stable)")

    codes = sorted(set(codes))
    status = "identical" if not codes else "divergent"
    return {"tool": name, "status": status, "codes": codes, "detail": detail}


# --------------------------------------------------------------------------
# Copies
# --------------------------------------------------------------------------

def make_copies(root, work_dir):
    """Copy root into two differently named directories under work_dir."""
    if not os.path.isdir(root):
        raise SetupError("--root is not a directory: %s" % root)
    a = os.path.join(work_dir, DIR_A)
    b = os.path.join(work_dir, DIR_B)
    if len(a) == len(b):
        raise SetupError("internal: the two copy paths must differ in length")
    ignore = shutil.ignore_patterns("__pycache__", ".git")
    try:
        shutil.copytree(root, a, ignore=ignore)
        shutil.copytree(root, b, ignore=ignore)
    except (OSError, shutil.Error) as exc:
        raise SetupError("could not copy --root: %s" % exc)
    return a, b


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def discover_tool_dirs(root):
    """Immediate subdirectories of root that look like tool directories.

    Used only to account for coverage -- the runner never invents a command
    for a directory it finds, because guessing an invocation is how you end
    up verifying the wrong thing. Directories with no manifest entry are
    reported by name so "0 divergences" can never be read as "everything
    was checked".
    """
    if not os.path.isdir(root):
        return []
    names = []
    for name in sorted(os.listdir(root)):
        if name.startswith(".") or name == "__pycache__":
            continue
        if os.path.isdir(os.path.join(root, name)):
            names.append(name)
    return names


def build_coverage(root, tools, results):
    """Explicit execution accounting: what ran, what did not, and why."""
    dirs = discover_tool_dirs(root)
    by_name = {r["tool"]: r for r in results}
    executed, not_executed = [], []
    for name in sorted(tools):
        r = by_name.get(name)
        if r is not None and r["status"] in ("identical", "divergent"):
            executed.append(name)
        else:
            reason = "unknown"
            if r is not None and r["status"] == "skipped":
                reason = "manifest marks it unbaselineable: %s" % r["detail"]["reason"]
            elif r is not None and r["status"] == "error":
                detail = r["detail"]
                reason = detail.get("error_a") or detail.get("error_b") or "execution error"
            not_executed.append({"tool": name, "reason": reason})
    no_entry = [d for d in dirs if d not in tools]
    return {
        "directories_under_root": len(dirs),
        "manifest_entries": len(tools),
        "executed_both_paths": len(executed),
        "not_executed": len(not_executed),
        "directories_with_no_manifest_entry": len(no_entry),
        "executed_tools": executed,
        "not_executed_tools": not_executed,
        "tools_with_no_manifest_entry": no_entry,
    }


def build_report(results, mode, coverage=None):
    summary = {"identical": 0, "divergent": 0, "error": 0, "skipped": 0}
    code_counts = {c: 0 for c in sorted(ALL_CODES)}
    for r in results:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
        for c in r["codes"]:
            code_counts[c] += 1
    any_divergent = summary["divergent"] > 0
    any_error = summary["error"] > 0
    status = "error" if any_error else ("divergent" if any_divergent else "identical")
    report = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": status,
        "mode": mode,
        "tools_compared": len(results),
        "summary": summary,
        "code_counts": code_counts,
        "results": sorted(results, key=lambda r: r["tool"]),
    }
    if coverage is not None:
        report["coverage"] = coverage
    return report


def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="crosspath.py",
        description="Run every documented tool command from two copies of the tree at "
                    "two different absolute paths and compare canonical JSON hashes.")
    p.add_argument("--root", default=".", help="tree to copy twice (default: .)")
    p.add_argument("--manifest", default=None,
                   help="tools manifest (default: <root>/regression-checker/baselines.json); "
                        "regression-checker's baselines.json is accepted unchanged")
    p.add_argument("--path-a", default=None,
                   help="use this existing checkout as copy A instead of copying --root")
    p.add_argument("--path-b", default=None,
                   help="use this existing checkout as copy B instead of copying --root")
    p.add_argument("--only", default=None, help="comma-separated subset of tool names")
    p.add_argument("--work", default=None,
                   help="directory to create the two copies in (default: a temp dir)")
    p.add_argument("-o", "--output", default=None, help="write the report JSON here")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                   help="per-run subprocess timeout in seconds (default: %d)" % DEFAULT_TIMEOUT)
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    tmp_obj = None
    try:
        manifest_path = args.manifest or os.path.join(
            args.root, "regression-checker", "baselines.json")
        tools = load_manifest(manifest_path)

        if args.only:
            wanted = [t.strip() for t in args.only.split(",") if t.strip()]
            missing = sorted(set(wanted) - set(tools))
            if missing:
                raise SetupError("--only names tools absent from the manifest: %s"
                                 % ", ".join(missing))
            tools = {k: v for k, v in tools.items() if k in wanted}

        if (args.path_a is None) != (args.path_b is None):
            raise SetupError("--path-a and --path-b must be given together")

        if args.path_a:
            root_a, root_b = os.path.abspath(args.path_a), os.path.abspath(args.path_b)
            for p in (root_a, root_b):
                if not os.path.isdir(p):
                    raise SetupError("not a directory: %s" % p)
            if root_a == root_b:
                raise SetupError("--path-a and --path-b are the same directory; two runs "
                                 "from one path prove nothing about path dependence")
            if len(root_a) == len(root_b):
                raise SetupError("--path-a and --path-b have the same length; use paths "
                                 "of different lengths so a length-dependent artefact "
                                 "cannot cancel out")
            mode = "given-paths"
        else:
            if args.work:
                work = os.path.abspath(args.work)
                os.makedirs(work, exist_ok=True)
            else:
                tmp_obj = tempfile.TemporaryDirectory(prefix="crosspath_")
                work = tmp_obj.name
            root_a, root_b = make_copies(os.path.abspath(args.root), work)
            mode = "copied"

        results = [compare_tool(name, tools[name], root_a, root_b, args.timeout)
                   for name in sorted(tools)]
        coverage = build_coverage(root_a, tools, results)
        report = build_report(results, mode, coverage)
        text = canonical_json(report)

        if args.output:
            try:
                with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
            except OSError as exc:
                raise SetupError("could not write --output %s: %s" % (args.output, exc))
        else:
            sys.stdout.write(text)

        if report["summary"]["error"]:
            return 2
        return 1 if report["summary"]["divergent"] else 0

    except SetupError as exc:
        text = canonical_json({"schema_version": SCHEMA_VERSION, "tool": TOOL_NAME,
                               "status": "error", "error": str(exc)})
        if args.output:
            try:
                with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
            except OSError:
                sys.stderr.write(text)
        else:
            sys.stdout.write(text)
        return 2
    finally:
        if tmp_obj is not None:
            tmp_obj.cleanup()


if __name__ == "__main__":
    sys.exit(main())
