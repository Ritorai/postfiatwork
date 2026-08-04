#!/usr/bin/env python3
"""leakscan.py -- find environment-specific strings in tracked documentation.

A document that says "/sessions/sharp-stoic-knuth/mnt/outputs" is not
reproducible: nobody else has that path, and a reader following the
instruction gets a different result from the author. This scans tracked
documentation for five categories of that mistake.

CATEGORIES
  absolute_path    an absolute filesystem path (POSIX or Windows)
  home_directory   a home directory reference: ~/, $HOME, /home/x, /Users/x
  temp_directory   a temporary directory: /tmp, /var/folders, %TEMP%, TMPDIR
  hostname         a machine or container identity: localhost, *.local,
                   ip-10-0-0-1, a /sessions/<name>/ container root
  username         a login name embedded in a path or prompt

REVIEW, NOT SUPPRESSION
Step 3 of the brief asks that detected matches be reviewed to separate real
leaks from harmless examples. That review is DATA, not code: review.json maps
"<path>:<line>:<matched text>" to a verdict and a reason. A match with no
review entry is reported as a leak. Fail-closed is the whole point -- a new
leak cannot appear "benign" by default, and every benign call is a written,
reviewable sentence rather than a silently-tuned regex.

Exit codes:
    0  no confirmed leaks
    1  at least one confirmed leak
    2  setup error (--root missing, review.json unreadable, --output unwritable)

CANDIDATE MODE AND WHY IT EXISTS
--candidates emits, as JSON, every line matching PREFILTER_RE -- a deliberately
over-broad superset of what any rule can match. It exists because the container
this repository is maintained from has no outbound network access, so the full
text of every tracked document cannot be fetched into it. The candidate set can
be produced anywhere the files are readable and scanned here, and --scan-candidates
classifies it with the identical rules.

The prefilter is only sound if it can never drop a line a rule would match.
Two tests enforce that: every rule fixture must match PREFILTER_RE, and for
the files available in full, scanning the full text and scanning the
prefiltered candidates must produce IDENTICAL findings. That equality is
measured, not asserted -- see README.md.
"""

import argparse
import json
import os
import re
import sys

SCHEMA_VERSION = 1
TOOL_NAME = "env-leak-scanner"

C_ABS = "absolute_path"
C_HOME = "home_directory"
C_TEMP = "temp_directory"
C_HOST = "hostname"
C_USER = "username"
CATEGORIES = (C_ABS, C_HOME, C_TEMP, C_HOST, C_USER)

# A boundary a real path starts at. Excludes "/" itself, which is what keeps
# "https://github.com/x/y" and relative paths like "tool/file.py" out.
B = r"(?:^|[\s\"'`(\[<=,;:])"

RULES = (
    # ---- home directory (checked before the generic absolute path so the
    # more specific category wins) --------------------------------------
    {
        "id": "EL-HOME-TILDE",
        "category": C_HOME,
        "pattern": B + r"(~/[^\s\"'`)\]>]*)",
        "harm": "~ expands to whoever runs the command; the path is different "
                "for every reader and for root.",
    },
    {
        "id": "EL-HOME-ENV",
        "category": C_HOME,
        "pattern": r"(\$HOME\b|\$\{HOME\}|%USERPROFILE%)",
        "harm": "resolves against the running user's environment, so the "
                "document describes a different location per reader.",
    },
    {
        "id": "EL-HOME-ABS",
        "category": C_HOME,
        "pattern": B + r"((?:/home|/Users|/root)/[^\s\"'`)\]>]*)",
        "harm": "a specific account's home directory; nobody else has it, and "
                "it usually carries the author's login name with it.",
    },
    {
        "id": "EL-HOME-WIN",
        "category": C_HOME,
        "pattern": r"([A-Za-z]:\\Users\\[^\s\"'`)\]>]*)",
        "harm": "a specific Windows profile directory, naming the account.",
    },
    # ---- temporary directories -----------------------------------------
    {
        "id": "EL-TEMP-POSIX",
        "category": C_TEMP,
        "pattern": B + r"((?:/tmp|/var/tmp|/var/folders|/private/var)"
                       r"(?:/[^\s\"'`)\]>]*)?)",
        "harm": "temporary directories are cleared between runs and differ per "
                "platform; a path there is stale by the time it is read.",
    },
    {
        "id": "EL-TEMP-ENV",
        "category": C_TEMP,
        "pattern": r"(\$TMPDIR\b|%TEMP%|%TMP%|AppData\\Local\\Temp)",
        "harm": "resolves to a per-user, per-session temporary location.",
    },
    # ---- container / session roots --------------------------------------
    {
        "id": "EL-HOST-SESSION",
        "category": C_HOST,
        "pattern": B + r"(/sessions/[^\s\"'`)\]>]*)",
        "harm": "a sandbox session root. The session name identifies one "
                "ephemeral machine that no longer exists after the run.",
    },
    {
        "id": "EL-HOST-LOCAL",
        "category": C_HOST,
        "pattern": r"\b(localhost(?::\d+)?|127\.0\.0\.1(?::\d+)?|"
                   r"[A-Za-z0-9-]+\.local\b)",
        "harm": "names a machine only reachable from the author's network "
                "namespace; a reader following it reaches nothing, or worse, "
                "something else.",
    },
    {
        "id": "EL-HOST-CLOUD",
        "category": C_HOST,
        "pattern": r"\b(ip-\d{1,3}-\d{1,3}-\d{1,3}-\d{1,3}|"
                   r"[a-z0-9-]+\.ec2\.internal|[a-z0-9-]+\.compute\.internal)\b",
        "harm": "an instance-private hostname; it is meaningless outside the "
                "VPC it was minted in and leaks the deployment topology.",
    },
    # ---- usernames -------------------------------------------------------
    {
        "id": "EL-USER-PATH",
        "category": C_USER,
        "pattern": r"(?:/home|/Users)/([A-Za-z][A-Za-z0-9._-]{1,31})\b",
        "harm": "the author's login name, recoverable from a path in a public "
                "document.",
    },
    {
        "id": "EL-USER-WINPATH",
        "category": C_USER,
        "pattern": r"[A-Za-z]:\\Users\\([A-Za-z][A-Za-z0-9._-]{1,31})\b",
        "harm": "the author's Windows account name.",
    },
    {
        "id": "EL-USER-PROMPT",
        "category": C_USER,
        "pattern": r"(?m)^([A-Za-z][A-Za-z0-9._-]{1,31})@[A-Za-z0-9][A-Za-z0-9.-]*"
                   r"[:~]",
        "harm": "a copied shell prompt carries both the login name and the "
                "machine name of the author's terminal.",
    },
    # ---- generic absolute paths (last: the specific categories above take
    # precedence for the same span) ---------------------------------------
    {
        "id": "EL-ABS-POSIX",
        "category": C_ABS,
        "pattern": B + r"(/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._+-]+)+/?)",
        "harm": "an absolute path assumes a filesystem layout the reader does "
                "not have; the same command run elsewhere fails or, worse, "
                "silently touches a different file.",
    },
    {
        "id": "EL-ABS-WIN",
        "category": C_ABS,
        "pattern": r"([A-Za-z]:\\(?:[^\s\"'`)\]>\\]+\\?)+)",
        "harm": "a drive-letter path is specific to one Windows machine's "
                "layout and does not exist on POSIX at all.",
    },
    {
        "id": "EL-ABS-UNC",
        "category": C_ABS,
        "pattern": r"(\\\\[A-Za-z0-9._-]+\\[^\s\"'`)\]>]*)",
        "harm": "a UNC share path names one file server on one network.",
    },
)

# Deliberately over-broad superset of every RULES pattern. A line that matches
# no rule may still match this; a line that matches ANY rule MUST match this.
# Tested both ways -- see test_leakscan.py TestPrefilterIsSuperset.
PREFILTER_RE = re.compile(
    r"(?:^|[\s\"'`(\[<=,;:])/[A-Za-z0-9._-]"   # a POSIX absolute path start
    r"|~/"
    r"|\$HOME|\$\{HOME\}|\$TMPDIR"
    r"|%USERPROFILE%|%TEMP%|%TMP%"
    r"|[A-Za-z]:\\"                             # a drive-letter path
    r"|\\\\[A-Za-z0-9]"                         # a UNC path
    r"|AppData\\"
    r"|localhost|127\.0\.0\.1|\.local\b"
    r"|\bip-\d{1,3}-\d{1,3}"
    r"|\.ec2\.internal|\.compute\.internal"
    r"|^[A-Za-z][A-Za-z0-9._-]{1,31}@"          # a shell prompt
)

DOC_SUFFIXES = (".md", ".txt")
SKIP_DIRS = frozenset({".git", "__pycache__", "node_modules", ".venv"})

COMPILED = tuple((r, re.compile(r["pattern"])) for r in RULES)


class SetupError(Exception):
    pass


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True) + "\n"


def read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def review_key(path, line, text):
    return "%s:%d:%s" % (path, line, text)


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------

def scan_line(path, lineno, line):
    """Return findings for one line. A span already claimed by an earlier
    (more specific) rule is not reported again by a later one, so
    /home/rito/x is a home_directory + username, not also an absolute_path."""
    out = []
    claimed = []

    def overlaps(a, b):
        return any(not (b <= s or a >= e) for s, e in claimed)

    for rule, rx in COMPILED:
        for m in rx.finditer(line):
            start, end = m.span(1)
            if rule["category"] == C_ABS and overlaps(start, end):
                continue
            claimed.append((start, end))
            out.append({
                "file": path,
                "line": lineno,
                "column": start + 1,
                "rule": rule["id"],
                "category": rule["category"],
                "matched": m.group(1),
                "harms_reproducibility": rule["harm"],
            })
    return out


def scan_text(path, text):
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not PREFILTER_RE.search(line):
            continue
        out.extend(scan_line(path, lineno, line))
    return out


def discover(root):
    if not os.path.isdir(root):
        raise SetupError("--root is not a directory: %s" % root)
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS
                             and not d.startswith("."))
        for name in sorted(filenames):
            if name.endswith(DOC_SUFFIXES):
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                found.append((rel, full))
    return sorted(found)


def emit_candidates(root):
    """Every line matching PREFILTER_RE, with its 1-based line number."""
    out = {}
    for rel, full in discover(root):
        lines = [[i, ln] for i, ln in enumerate(read_text(full).splitlines(), 1)
                 if PREFILTER_RE.search(ln)]
        if lines:
            out[rel] = lines
    return out


def load_review(path):
    if path is None:
        return {}
    if not os.path.isfile(path):
        raise SetupError("--review not found: %s" % path)
    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        raise SetupError("--review is not valid JSON: %s" % exc)
    if not isinstance(data, dict):
        raise SetupError("--review must be a JSON object")
    for key, entry in data.items():
        if not isinstance(entry, dict) or "verdict" not in entry \
                or "reason" not in entry:
            raise SetupError("--review entry %r needs both 'verdict' and "
                             "'reason'" % key)
        if entry["verdict"] not in ("leak", "benign"):
            raise SetupError("--review entry %r has verdict %r; expected "
                             "'leak' or 'benign'" % (key, entry["verdict"]))
        if not str(entry["reason"]).strip():
            raise SetupError("--review entry %r has an empty reason" % key)
    return data


def build_report(findings, review, coverage):
    confirmed, benign = [], []
    used = set()
    for f in findings:
        key = review_key(f["file"], f["line"], f["matched"])
        entry = review.get(key)
        if entry:
            used.add(key)
        f = dict(f, review_key=key)
        if entry and entry["verdict"] == "benign":
            f["verdict"] = "benign"
            f["review_reason"] = entry["reason"]
            benign.append(f)
        else:
            f["verdict"] = "leak"
            if entry:
                f["review_reason"] = entry["reason"]
            confirmed.append(f)

    order = (lambda f: (f["file"], f["line"], f["column"], f["rule"]))
    confirmed.sort(key=order)
    benign.sort(key=order)

    by_category = {c: 0 for c in CATEGORIES}
    for f in confirmed:
        by_category[f["category"]] += 1

    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": "leaks" if confirmed else "clean",
        "confirmed_leaks": confirmed,
        "reviewed_benign": benign,
        "counts": {
            "confirmed": len(confirmed),
            "benign": len(benign),
            "by_category": by_category,
        },
        "stale_review_entries": sorted(set(review) - used),
        "coverage": coverage,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="leakscan.py")
    ap.add_argument("--root", default=".")
    ap.add_argument("--review", default=None,
                    help="JSON {\"<file>:<line>:<match>\": {verdict, reason}}")
    ap.add_argument("--candidates", action="store_true",
                    help="emit prefiltered candidate lines instead of scanning")
    ap.add_argument("--scan-candidates", default=None,
                    help="scan a candidate JSON file produced by --candidates")
    ap.add_argument("-o", "--output", default=None)
    args = ap.parse_args(argv)

    try:
        if args.candidates:
            text = canonical_json(emit_candidates(args.root))
        elif args.scan_candidates:
            if not os.path.isfile(args.scan_candidates):
                raise SetupError("--scan-candidates not found: %s"
                                 % args.scan_candidates)
            try:
                cand = json.loads(read_text(args.scan_candidates))
            except json.JSONDecodeError as exc:
                raise SetupError("--scan-candidates is not valid JSON: %s" % exc)
            findings = []
            for rel in sorted(cand):
                for lineno, line in cand[rel]:
                    findings.extend(scan_line(rel, lineno, line))
            coverage = {
                "mode": "candidates",
                "files_with_candidate_lines": len(cand),
                "candidate_lines": sum(len(v) for v in cand.values()),
                "note": "Only lines matching PREFILTER_RE were available. "
                        "Lines matching no rule are indistinguishable from "
                        "lines never transferred; the prefilter is a proven "
                        "superset of every rule, so no rule match can be lost.",
            }
            report = build_report(findings, load_review(args.review), coverage)
            text = canonical_json(report)
            rc = 1 if report["confirmed_leaks"] else 0
        else:
            files = discover(args.root)
            findings = []
            for rel, full in files:
                findings.extend(scan_text(rel, read_text(full)))
            coverage = {
                "mode": "full",
                "files_scanned": len(files),
                "files": [rel for rel, _ in files],
            }
            report = build_report(findings, load_review(args.review), coverage)
            text = canonical_json(report)
            rc = 1 if report["confirmed_leaks"] else 0

        if args.candidates:
            rc = 0
        if args.output:
            try:
                with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
            except OSError as exc:
                raise SetupError("could not write --output %s: %s"
                                 % (args.output, exc))
        else:
            sys.stdout.write(text)
        return rc
    except SetupError as exc:
        sys.stdout.write(canonical_json({
            "schema_version": SCHEMA_VERSION, "tool": TOOL_NAME,
            "status": "error", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
