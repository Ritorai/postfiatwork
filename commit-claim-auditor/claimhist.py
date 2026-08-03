#!/usr/bin/env python3
"""claimhist.py -- commit-claim-auditor

Audits *historical claims* embedded in README.md and captured_output.txt
files under a directory tree, and checks whether those claims are still
true of the current artifacts on disk. Two claim families are handled,
and only these two (a general README/CLI validator that checks flags and
exit codes is a separate tool, "doc-validator" -- this tool is scoped
strictly to hashes and test counts):

  SHA256_CLAIM   -- a 64 hex character string claimed to be the SHA-256
                    digest of some named file.
  TESTCOUNT_CLAIM -- a stated number of unit tests ("Ran N tests in ...").

Supported claim shapes (documented precisely, on purpose -- this tool
does not attempt to understand arbitrary prose):

  SHA256_CLAIM:
    1. "sha256sum transcript" form (primary case): a line consisting of
       a 64-hex-char digest, one or more spaces/tabs, an optional '*'
       (binary-mode marker used by sha256sum/coreutils), and the rest of
       the line taken verbatim as the filename. Example:
           e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85  report.txt
    2. "prose association" form: a bare 64-hex-char token appears
       somewhere in a line that is not in transcript form. The tool looks
       for a filename-like token to associate it with, in this order:
         a. backtick-quoted tokens containing a dot, on the SAME line
            (`report.txt`), leftmost first;
         b. otherwise, bare word tokens containing a dot, on the SAME
            line (report.txt, sub/dir/report.txt);
         c. if the same line has zero candidates, the previous
            non-blank line is checked, then the next non-blank line,
            using the same backtick-then-bare search.
       If a line (same or the chosen neighbour) yields two or more
       *distinct* filename candidates, the claim is MALFORMED
       (reason "ambiguous_filename_association") rather than guessed at.
       If no candidate is found anywhere, the claim is MALFORMED with
       reason "no_filename_association".

  TESTCOUNT_CLAIM, tried in this priority order per line (first match
  wins -- a line contributes at most one TESTCOUNT_CLAIM):
    1. "Ran N tests in" -- literal unittest verbose/summary output, e.g.
       "Ran 137 tests in 0.045s".
    2. "**N tests across M tools**" -- bold markdown aggregate claim.
    3. "N tests" -- bare fallback, e.g. "137 tests".
  In every shape, if the matched digit run is immediately preceded by a
  comma and a digit (e.g. the "234" inside "1,234 tests"), the claim is
  MALFORMED with reason "ambiguous_number_format" instead of silently
  parsing a truncated number -- comma-grouped counts are not supported.

Provenance: for every claim, the tool records the source file (path
relative to --root), the 1-based line number, and -- when the target is
inside a git repository and git is on PATH -- the commit SHA and author
date (ISO-8601) of the last change to that exact line, obtained via
`git blame --porcelain -L<line>,<line> -- <file>` followed by
`git show -s --format=%aI <sha>`. When git is unavailable, --root is not
inside a git work tree, or blame/show fail, the commit fields are set to
null and provenance.note is set to "GIT_UNAVAILABLE" -- this never
aborts the run. If the blamed line is uncommitted (all-zero SHA),
provenance.note is set to "UNCOMMITTED_LINE" instead.

Recomputation:
  SHA256_CLAIM   -- the referenced file (resolved first relative to the
                    directory of the claiming file, then relative to
                    --root) is hashed with SHA-256 and compared byte for
                    byte against the claim.
  TESTCOUNT_CLAIM -- recomputation requires *running a test suite*, which
                    this tool refuses to do unless --run-tests is passed
                    explicitly. Without --run-tests, every TESTCOUNT_CLAIM
                    is reported NOT_RECOMPUTED / test_execution_not_requested.
                    With --run-tests, the tool looks in the directory of
                    the claiming file for files named test_*.py: if
                    exactly one exists, it runs
                    `python3 -m unittest <module> -v` in that directory
                    and parses the LAST "Ran N tests in" line from the
                    combined stdout+stderr; zero matches yields
                    MISSING_SOURCE/no_test_module_found; more than one
                    yields MALFORMED/ambiguous_test_module; a run that
                    produces no parseable summary line yields
                    MALFORMED/unittest_execution_failed. The count is
                    never guessed.

Statuses: CURRENT, STALE, MISSING_SOURCE, NOT_RECOMPUTED, MALFORMED.

Exit codes:
  0 -- every claim is CURRENT or NOT_RECOMPUTED.
  1 -- at least one claim is STALE, MISSING_SOURCE, or MALFORMED.
  2 -- invalid input or execution failure (bad --root, unwritable -o,
       bad CLI arguments -- the last of which argparse itself already
       reports with exit code 2).

Determinism: this file reads no wall clock, anywhere, in any form --
that includes avoiding the literal spelling of such calls even inside
comments and docstrings such as this one. All paths written to the report are relative
to --root; the absolute location of --root is never emitted anywhere in
the report, so scanning the same tree from two different absolute
locations produces byte-identical output. Every list in the report is
explicitly sorted, and every sort key ends with the canonical JSON dump
of the item itself as a final tiebreaker, which guarantees a total
order even between findings that are identical on every documented
field (see `_sort_key_for_claim`).
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

SCHEMA_VERSION = 1

TARGET_FILENAMES = ("README.md", "captured_output.txt")

SHA_TOKEN_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])")
SHA256SUM_LINE_RE = re.compile(r"^\s*([0-9a-fA-F]{64})[ \t]+\*?(\S.*)$")

FILENAME_BACKTICK_RE = re.compile(r"`([\w\-./]+\.[A-Za-z0-9_]{1,10})`")
FILENAME_BARE_RE = re.compile(
    r"(?<![\w/.\-])([\w][\w\-./]*\.[A-Za-z0-9_]{1,10})(?![\w])"
)

TESTCOUNT_PATTERNS = (
    ("ran_tests_in", re.compile(r"Ran\s+(\d+)\s+tests?\s+in")),
    (
        "bold_across_tools",
        re.compile(r"\*\*(\d+)\s+tests?\s+across\s+\d+\s+tools?\*\*"),
    ),
    ("bare_tests", re.compile(r"(\d+)\s+tests?\b")),
)

STATUS_CURRENT = "CURRENT"
STATUS_STALE = "STALE"
STATUS_MISSING_SOURCE = "MISSING_SOURCE"
STATUS_NOT_RECOMPUTED = "NOT_RECOMPUTED"
STATUS_MALFORMED = "MALFORMED"

ALL_STATUSES = (
    STATUS_CURRENT,
    STATUS_STALE,
    STATUS_MISSING_SOURCE,
    STATUS_NOT_RECOMPUTED,
    STATUS_MALFORMED,
)


def canonical_dumps(obj):
    """Canonical JSON: sorted keys, tight separators, ASCII-only, no
    trailing newline (callers append exactly one "\n" when writing to
    a stream/file, per the tool's on-disk contract)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def read_text_lines(path):
    """Read a file as UTF-8 (replacing undecodable bytes rather than
    raising), and split into logical lines without line endings,
    handling \\n, \\r\\n and bare \\r uniformly via str.splitlines()."""
    with open(path, "rb") as fh:
        raw = fh.read()
    text = raw.decode("utf-8", errors="replace")
    return text.splitlines()


def _mask_hash_tokens(line):
    """Blank out every 64-hex-char token in `line` (replacing it with an
    equal-length run of '#', a character that is neither a hex digit nor
    matched by the filename regexes) before filename-candidate search.
    Without this, a hash glued directly to an extension with no
    separator (e.g. "<64hex>.report") is itself indistinguishable from a
    dotted filename token, and gets picked up as its own "associated"
    filename -- a bug this masking step exists specifically to prevent."""
    return SHA_TOKEN_RE.sub(lambda m: "#" * len(m.group(0)), line)


def find_filename_candidates(line):
    line = _mask_hash_tokens(line)
    backticks = FILENAME_BACKTICK_RE.findall(line)
    if backticks:
        seen = []
        for b in backticks:
            if b not in seen:
                seen.append(b)
        return seen
    bares = []
    for m in FILENAME_BARE_RE.findall(line):
        stripped = m.strip("*_():,;")
        if stripped and stripped not in bares:
            bares.append(stripped)
    return bares


def _nearest_non_blank(lines, idx, step):
    i = idx + step
    while 0 <= i < len(lines) and lines[i].strip() == "":
        i += step
    return i if 0 <= i < len(lines) else None


def associate_filename(lines, idx):
    """Return (filename_or_None, reason_or_None) for the hash claim on
    0-based line index `idx`, using the heuristic documented at module
    level: same line first, then the nearest previous non-blank line
    (skipping over any number of blank lines), then the nearest next
    non-blank line."""
    same = find_filename_candidates(lines[idx])
    if len(same) == 1:
        return same[0], None
    if len(same) >= 2:
        return None, "ambiguous_filename_association"
    for neighbor_idx in (
        _nearest_non_blank(lines, idx, -1),
        _nearest_non_blank(lines, idx, +1),
    ):
        if neighbor_idx is None:
            continue
        cands = find_filename_candidates(lines[neighbor_idx])
        if len(cands) == 1:
            return cands[0], None
        if len(cands) >= 2:
            return None, "ambiguous_filename_association"
    return None, "no_filename_association"


def match_testcount(line):
    """Return a dict describing the first matching TESTCOUNT_CLAIM shape
    on this line, or None if the line contains no such claim. See the
    module docstring for the exact shapes and the comma-guard rule."""
    for shape, pattern in TESTCOUNT_PATTERNS:
        m = pattern.search(line)
        if not m:
            continue
        start = m.start(1)
        if start >= 2 and line[start - 1] == "," and line[start - 2].isdigit():
            return {"malformed": True, "reason": "ambiguous_number_format", "shape": shape}
        return {"malformed": False, "value": int(m.group(1)), "shape": shape}
    return None


def resolve_target(root, source_dir, filename):
    """Resolve a claimed filename to an existing path, first relative to
    the directory containing the claiming file, then relative to --root.
    Returns an absolute path, or None if neither location has the file."""
    filename = filename.strip()
    if not filename:
        return None
    for base in (source_dir, root):
        candidate = os.path.normpath(os.path.join(base, filename))
        # Guard against escaping outside of anything sane via silly input;
        # os.path.isfile is the actual existence check.
        if os.path.isfile(candidate):
            return candidate
    return None


def sha256_of_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_test_module(source_dir):
    try:
        entries = sorted(
            f
            for f in os.listdir(source_dir)
            if f.startswith("test_") and f.endswith(".py") and os.path.isfile(os.path.join(source_dir, f))
        )
    except OSError:
        entries = []
    return entries


def run_test_module(source_dir, module_name):
    """Run `python3 -m unittest <module_name> -v` in source_dir and parse
    the LAST "Ran N tests in" line from the combined output. Returns
    (count_or_None, reason_or_None)."""
    try:
        proc = subprocess.run(
            ["python3", "-m", "unittest", module_name, "-v"],
            cwd=source_dir,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError):
        return None, "unittest_execution_failed"
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    matches = re.findall(r"Ran (\d+) tests? in", combined)
    if not matches:
        return None, "unittest_execution_failed"
    return int(matches[-1]), None


def git_provenance(root, rel_path, line_no, git_ok_cache):
    """Return a provenance dict {"commit": ..., "author_date": ...,
    "note": ...} for the given repo-relative path and 1-based line
    number. Never raises; falls back to GIT_UNAVAILABLE on any problem.
    `git_ok_cache` memoizes the "--root is inside a git work tree" check
    across calls (it never depends on which file/line is being asked
    about)."""
    if "git_present" not in git_ok_cache:
        git_ok_cache["git_present"] = shutil.which("git") is not None
    if not git_ok_cache["git_present"]:
        return {"commit": None, "author_date": None, "note": "GIT_UNAVAILABLE"}

    if "is_worktree" not in git_ok_cache:
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            git_ok_cache["is_worktree"] = r.returncode == 0 and r.stdout.strip() == "true"
        except (OSError, subprocess.SubprocessError):
            git_ok_cache["is_worktree"] = False
    if not git_ok_cache["is_worktree"]:
        return {"commit": None, "author_date": None, "note": "GIT_UNAVAILABLE"}

    try:
        blame = subprocess.run(
            ["git", "blame", "--porcelain", "-L", f"{line_no},{line_no}", "--", rel_path],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "author_date": None, "note": "GIT_UNAVAILABLE"}
    if blame.returncode != 0 or not blame.stdout:
        return {"commit": None, "author_date": None, "note": "GIT_UNAVAILABLE"}

    first_line = blame.stdout.splitlines()[0]
    sha = first_line.split(" ")[0] if first_line else ""
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        return {"commit": None, "author_date": None, "note": "GIT_UNAVAILABLE"}
    if set(sha) == {"0"}:
        return {"commit": None, "author_date": None, "note": "UNCOMMITTED_LINE"}

    try:
        show = subprocess.run(
            ["git", "show", "-s", "--format=%H|%aI", sha],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "author_date": None, "note": "GIT_UNAVAILABLE"}
    if show.returncode != 0 or "|" not in show.stdout:
        return {"commit": None, "author_date": None, "note": "GIT_UNAVAILABLE"}
    full_sha, author_date = show.stdout.strip().split("|", 1)
    return {"commit": full_sha, "author_date": author_date, "note": None}


def iter_target_files(root):
    """Yield (rel_path, abs_path) for every README.md / captured_output.txt
    under root, skipping .git and __pycache__ directories. rel_path uses
    forward slashes regardless of OS."""
    results = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in (".git", "__pycache__"))
        for fn in TARGET_FILENAMES:
            if fn in filenames:
                abs_path = os.path.join(dirpath, fn)
                rel_path = os.path.relpath(abs_path, root).replace(os.sep, "/")
                results.append((rel_path, abs_path))
    results.sort()
    return results


def build_hash_claim(root, rel_source, abs_source_dir, lines, idx, git_cache):
    line = lines[idx]
    line_no = idx + 1
    claim = {
        "claim_type": "SHA256_CLAIM",
        "source_file": rel_source,
        "line": line_no,
        "raw_text": line,
        "claim_shape": None,
        "claimed_hash": None,
        "claimed_count": None,
        "associated_target": None,
        "resolved_target": None,
        "status": None,
        "reason": None,
        "recomputed_hash": None,
        "recomputed_count": None,
        "provenance": git_provenance(root, rel_source, line_no, git_cache),
    }

    transcript = SHA256SUM_LINE_RE.match(line)
    if transcript:
        claimed_hash = transcript.group(1).lower()
        filename = transcript.group(2).rstrip()
        claim["claim_shape"] = "sha256sum_transcript"
        claim["claimed_hash"] = claimed_hash
        claim["associated_target"] = filename
        _finish_hash_claim(claim, root, abs_source_dir, filename)
        return [claim]

    # Not a transcript line: every stray hex token on the line is its
    # own claim, using the prose-association heuristic.
    claims = []
    for hm in SHA_TOKEN_RE.finditer(line):
        c = dict(claim)
        c["provenance"] = dict(claim["provenance"])
        claimed_hash = hm.group(0).lower()
        c["claim_shape"] = "prose_association"
        c["claimed_hash"] = claimed_hash
        filename, reason = associate_filename(lines, idx)
        if filename is None:
            c["associated_target"] = None
            c["status"] = STATUS_MALFORMED
            c["reason"] = reason
        else:
            c["associated_target"] = filename
            _finish_hash_claim(c, root, abs_source_dir, filename)
        claims.append(c)
    return claims


def _finish_hash_claim(claim, root, abs_source_dir, filename):
    resolved = resolve_target(root, abs_source_dir, filename)
    if resolved is None:
        claim["resolved_target"] = None
        claim["status"] = STATUS_MISSING_SOURCE
        claim["reason"] = "referenced_file_not_found"
        return
    rel_resolved = os.path.relpath(resolved, root).replace(os.sep, "/")
    claim["resolved_target"] = rel_resolved
    recomputed = sha256_of_file(resolved)
    claim["recomputed_hash"] = recomputed
    if recomputed == claim["claimed_hash"]:
        claim["status"] = STATUS_CURRENT
    else:
        claim["status"] = STATUS_STALE
        claim["reason"] = "hash_mismatch"


def build_testcount_claim(root, rel_source, abs_source_dir, lines, idx, git_cache, run_tests):
    line = lines[idx]
    line_no = idx + 1
    tc = match_testcount(line)
    if tc is None:
        return None
    claim = {
        "claim_type": "TESTCOUNT_CLAIM",
        "source_file": rel_source,
        "line": line_no,
        "raw_text": line,
        "claim_shape": tc["shape"],
        "claimed_hash": None,
        "claimed_count": None,
        "associated_target": None,
        "resolved_target": None,
        "status": None,
        "reason": None,
        "recomputed_hash": None,
        "recomputed_count": None,
        "provenance": git_provenance(root, rel_source, line_no, git_cache),
    }
    if tc["malformed"]:
        claim["status"] = STATUS_MALFORMED
        claim["reason"] = tc["reason"]
        return claim

    claim["claimed_count"] = tc["value"]

    if not run_tests:
        claim["status"] = STATUS_NOT_RECOMPUTED
        claim["reason"] = "test_execution_not_requested"
        return claim

    modules = find_test_module(abs_source_dir)
    if len(modules) == 0:
        claim["status"] = STATUS_MISSING_SOURCE
        claim["reason"] = "no_test_module_found"
        return claim
    if len(modules) > 1:
        claim["status"] = STATUS_MALFORMED
        claim["reason"] = "ambiguous_test_module"
        return claim

    module_file = modules[0]
    claim["associated_target"] = module_file
    claim["resolved_target"] = os.path.relpath(
        os.path.join(abs_source_dir, module_file), root
    ).replace(os.sep, "/")
    module_name = module_file[:-3]
    count, reason = run_test_module(abs_source_dir, module_name)
    if reason is not None:
        claim["status"] = STATUS_MALFORMED
        claim["reason"] = reason
        return claim
    claim["recomputed_count"] = count
    if count == claim["claimed_count"]:
        claim["status"] = STATUS_CURRENT
    else:
        claim["status"] = STATUS_STALE
        claim["reason"] = "test_count_mismatch"
    return claim


def scan_file(root, rel_source, abs_source, git_cache, run_tests):
    lines = read_text_lines(abs_source)
    abs_source_dir = os.path.dirname(abs_source)
    claims = []
    for idx in range(len(lines)):
        line = lines[idx]
        if SHA_TOKEN_RE.search(line):
            claims.extend(
                build_hash_claim(root, rel_source, abs_source_dir, lines, idx, git_cache)
            )
        tc_claim = build_testcount_claim(
            root, rel_source, abs_source_dir, lines, idx, git_cache, run_tests
        )
        if tc_claim is not None:
            claims.append(tc_claim)
    return claims


def _sort_key_for_claim(claim):
    """Sort key: a human-meaningful field tuple, followed by the
    canonical JSON dump of the entire claim as the final tiebreaker.
    This guarantees a total order across the list even when two claims
    are identical on every documented field (in which case the tiebreak
    dump is identical too and either position is a valid, deterministic
    choice for Python's stable sort)."""
    return (
        claim["claim_type"],
        claim["source_file"],
        claim["line"],
        claim.get("associated_target") or "",
        claim["status"],
        canonical_dumps(claim),
    )


def build_report(root, run_tests):
    git_cache = {}
    all_claims = []
    for rel_source, abs_source in iter_target_files(root):
        all_claims.extend(scan_file(root, rel_source, abs_source, git_cache, run_tests))

    all_claims.sort(key=_sort_key_for_claim)

    summary = {status: 0 for status in ALL_STATUSES}
    summary["total_claims"] = len(all_claims)
    notes = set()
    for c in all_claims:
        summary[c["status"]] += 1
        note = c["provenance"].get("note")
        if note:
            notes.add(note)

    report = {
        "schema_version": SCHEMA_VERSION,
        "claims": all_claims,
        "summary": summary,
        "notes": sorted(notes),
    }

    if any(c["status"] in (STATUS_STALE, STATUS_MISSING_SOURCE, STATUS_MALFORMED) for c in all_claims):
        exit_code = 1
    else:
        exit_code = 0
    report["exit_code"] = exit_code
    return report, exit_code


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="claimhist.py",
        description="Audit SHA-256 and test-count claims in README.md / captured_output.txt files.",
    )
    parser.add_argument("--root", required=True, help="Directory to scan.")
    parser.add_argument(
        "-o", "--output", required=False, default=None, help="Write report here (default: stdout)."
    )
    parser.add_argument(
        "--run-tests",
        action="store_true",
        help="Actually execute discovered test modules to recompute TESTCOUNT_CLAIMs.",
    )
    args = parser.parse_args(argv)

    root = args.root
    if not os.path.isdir(root):
        sys.stderr.write(f"claimhist: --root is not a directory: {args.root}\n")
        return 2

    root = os.path.normpath(root)

    try:
        report, exit_code = build_report(root, args.run_tests)
    except Exception as exc:  # defensive: unexpected crash is an execution failure
        sys.stderr.write(f"claimhist: internal error: {type(exc).__name__}: {exc}\n")
        return 2

    payload = canonical_dumps(report) + "\n"

    if args.output is None:
        sys.stdout.write(payload)
    else:
        try:
            with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(payload)
        except OSError as exc:
            sys.stderr.write(f"claimhist: cannot write output {args.output!r}: {exc}\n")
            return 2

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
