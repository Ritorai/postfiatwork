#!/usr/bin/env python3
"""shebangmode.py -- Git shebang/executable-bit consistency checker.

Stdlib-only (argparse, json, os, subprocess, sys). Enforces one rule over
the files Git actually tracks:

    A tracked regular TEXT file is marked executable in the index
    IF AND ONLY IF its first line is a shebang (``#!``).

Both directions are checked, and they fail for different reasons:

  SM001_EXEC_WITHOUT_SHEBANG  the index says mode 100755 but the file does
                              not start with ``#!``. The kernel will refuse
                              to exec it (ENOEXEC) or hand it to the
                              caller's shell, which is not what the mode
                              bit promises.
  SM002_SHEBANG_WITHOUT_EXEC  the file starts with ``#!`` but the index
                              says mode 100644. The shebang is inert: the
                              file has to be invoked through an explicit
                              interpreter, so the line is documentation
                              that the filesystem contradicts.

Modes come from ``git ls-files -s`` and CONTENT comes from the index blob
via ``git cat-file --batch`` -- not from ``os.stat`` and not from the
working tree. The index is what a clone reproduces on every machine; a
working-tree bit can be introduced locally by a umask, an editor, or a
copy from a FAT volume, and an unstaged edit can add or remove a shebang
that nobody else will ever see.

Reading half the predicate from each source is not a small inconsistency,
it is a silent pass: with the mode from the index and the first line from
the working tree, a staged file whose committed blob starts with ``#!``
reports clean the moment someone edits that line locally without staging
it, and the printed fix can be actively wrong in the mirror case. Both
halves now come from the same place.

Binary files are skipped, not reported. "Starts with a shebang" is a
question about text, and a binary whose first two bytes happen to be
``#!`` is not a script. Detection matches Git's own heuristic: a NUL byte
in the first 8000 bytes.

Exclusions are a caller-supplied list of path prefixes, empty by default.
They exist for generated, vendored or otherwise not-hand-maintained trees;
every excluded path is still listed in the report with the prefix that
excluded it, so an exclusion cannot hide a file silently.

Exit codes:
  0 = scan completed, every tracked text file satisfies the rule
  1 = scan completed, one or more mismatches found
  2 = usage or setup error (bad --root, not a Git checkout, git missing,
      unwritable --output, unreadable file)
"""

import argparse
import json
import os
import shlex
import subprocess
import sys

PROG = "shebangmode.py"
EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2

MODE_EXEC = "100755"
MODE_PLAIN = "100644"

SM001 = "SM001_EXEC_WITHOUT_SHEBANG"
SM002 = "SM002_SHEBANG_WITHOUT_EXEC"

SHEBANG = b"#!"
BINARY_SNIFF_BYTES = 8000
GIT_TIMEOUT_SECS = 60


class SetupError(Exception):
    """A condition that must exit 2, never 1."""


# ---------------------------------------------------------------------------
# Canonical JSON
# ---------------------------------------------------------------------------

def canonical_dumps(obj):
    """Deterministic, byte-stable JSON: sorted keys, tight separators,
    ASCII-only, single trailing newline. Nothing time-, machine- or
    path-dependent may be placed in `obj` by the caller."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True) + "\n"


# ---------------------------------------------------------------------------
# Reading the index
# ---------------------------------------------------------------------------

def git_toplevel(root):
    """The absolute root of the checkout containing `root`.

    Raises SetupError when `root` is not inside a Git checkout at all.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", root, "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=GIT_TIMEOUT_SECS,
        )
    except FileNotFoundError:
        raise SetupError("git executable not found on PATH")
    except subprocess.TimeoutExpired:
        raise SetupError("git rev-parse timed out after %d seconds"
                         % GIT_TIMEOUT_SECS)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise SetupError("not inside a Git checkout: %r (%s)"
                         % (root, detail or "git rev-parse failed"))
    return os.path.realpath(proc.stdout.decode("utf-8", "replace").strip())


def require_checkout_root(root):
    """Refuse to scan a directory that is not the top of its checkout.

    ``git -C DIR ls-files`` walks UP to the enclosing repository, so a
    plain directory nested inside a checkout does not fail -- it returns
    the (possibly empty) set of tracked files at or below DIR. Pointing
    this tool at such a directory therefore used to print
    ``"status": "ok"`` and exit 0 while checking nothing at all.

    That silent pass is the exact failure this checker exists to prevent,
    so a non-root --root is a setup error naming the real top. The rule is
    a property of a whole repository; a subtree that happens to contain no
    shebangs is not evidence that the repository satisfies it.
    """
    top = git_toplevel(root)
    if os.path.realpath(root) != top:
        # The checkout root is named RELATIVE to --root, not absolutely.
        # It is just as actionable ("go up two levels") and it keeps this
        # message, which lands in a committed transcript, free of a path
        # that only exists on the machine that ran it.
        raise SetupError(
            "--root %r is inside a Git checkout but is not its root; the "
            "root is %r relative to it. Scanning a subdirectory would "
            "silently check only part of the index and could report 'ok' "
            "for a repository that does not satisfy the rule."
            % (root, os.path.relpath(top, os.path.realpath(root))))
    return top


def git_tracked_modes(root):
    """[(mode, repo_relative_path)] for every file in the index, sorted.

    Raises SetupError rather than returning a partial list: a checker that
    silently reports "no problems" because it could not read the index is
    worse than one that refuses to run.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", root, "ls-files", "-s", "-z"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=GIT_TIMEOUT_SECS,
        )
    except FileNotFoundError:
        raise SetupError("git executable not found on PATH")
    except subprocess.TimeoutExpired:
        raise SetupError("git ls-files timed out after %d seconds"
                         % GIT_TIMEOUT_SECS)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise SetupError("git ls-files failed (exit %d) in %r: %s"
                         % (proc.returncode, root, detail or "no stderr"))

    entries = []
    unmerged = set()
    for record in proc.stdout.split(b"\0"):
        if not record:
            continue
        # "<mode> <object> <stage>\t<path>"
        try:
            meta, raw_path = record.split(b"\t", 1)
            mode_b, oid_b, stage_b = meta.split()[:3]
            mode = mode_b.decode("ascii")
            oid = oid_b.decode("ascii")
            stage = stage_b.decode("ascii")
        except (ValueError, IndexError, UnicodeDecodeError):
            raise SetupError("could not parse a git ls-files record: %r"
                             % record[:120])
        path = raw_path.decode("utf-8", "surrogateescape")
        if stage != "0":
            # A conflicted path appears three times, once per stage.
            # Reporting it three times would inflate every count and print
            # the same finding three times; picking one stage would be
            # inventing an answer about a file that currently has none.
            unmerged.add(path)
            continue
        entries.append((mode, oid, path))
    entries.sort(key=lambda e: e[2])
    return entries, sorted(unmerged)


def git_blob_heads(root, oids, limit=None):
    """{oid: first `limit` bytes of that blob}, read from the index.

    One `git cat-file --batch` process for the whole set: 500-odd
    subprocess spawns would make the tool unusably slow on a real
    repository, and the batch protocol is exact rather than heuristic.
    """
    limit = BINARY_SNIFF_BYTES if limit is None else limit
    wanted = sorted(set(oids))
    if not wanted:
        return {}
    try:
        proc = subprocess.run(
            ["git", "-C", root, "cat-file", "--batch"],
            input=("\n".join(wanted) + "\n").encode("ascii"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=GIT_TIMEOUT_SECS,
        )
    except FileNotFoundError:
        raise SetupError("git executable not found on PATH")
    except subprocess.TimeoutExpired:
        raise SetupError("git cat-file timed out after %d seconds"
                         % GIT_TIMEOUT_SECS)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise SetupError("git cat-file failed (exit %d): %s"
                         % (proc.returncode, detail or "no stderr"))

    out = proc.stdout
    heads = {}
    pos = 0
    for oid in wanted:
        nl = out.find(b"\n", pos)
        if nl == -1:
            raise SetupError("git cat-file output ended early for %s" % oid)
        header = out[pos:nl].decode("ascii", "replace").split()
        if len(header) < 3 or header[1] != "blob":
            raise SetupError("git cat-file did not return a blob for %s "
                             "(got %r)" % (oid, " ".join(header)))
        size = int(header[2])
        body = out[nl + 1:nl + 1 + size]
        heads[oid] = body[:limit]
        pos = nl + 1 + size + 1          # trailing newline after the body
    return heads


# ---------------------------------------------------------------------------
# Classifying one file
# ---------------------------------------------------------------------------

def looks_binary(head):
    """Git's heuristic: a NUL byte anywhere in the sniffed prefix."""
    return b"\0" in head


def has_shebang(head):
    """True when the file's first two bytes are ``#!``.

    Deliberately not ``lstrip()``ed. ``#!`` is only honoured by the kernel
    at byte 0, so a leading blank line means the file is not executable in
    practice no matter what it looks like to a reader.
    """
    return head.startswith(SHEBANG)


def first_line(head):
    """The first line, for the report. Truncated; never the whole file."""
    line = head.split(b"\n", 1)[0]
    return line.decode("utf-8", "replace")[:120]


def matching_prefix(path, prefixes):
    """The first exclusion prefix covering `path`, or None.

    A prefix matches a whole path or a whole leading directory component,
    so ``doc`` does not exclude ``doc-validator/``.
    """
    for prefix in prefixes:
        if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
            return prefix
    return None


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------

def scan(root, exclude_prefixes=()):
    """Returns the report dict. Raises SetupError for exit-2 conditions."""
    exclude_prefixes = tuple(sorted(set(exclude_prefixes)))
    require_checkout_root(root)
    entries, unmerged = git_tracked_modes(root)
    heads = git_blob_heads(root, [oid for _, oid, _ in entries])

    findings = []
    counts = {
        "tracked": 0,
        "checked": 0,
        "skipped_binary": 0,
        "skipped_excluded": 0,
        "skipped_not_a_regular_file": 0,
        "skipped_unmerged": 0,
        "executable": 0,
        "with_shebang": 0,
    }
    skipped = []

    for path in unmerged:
        counts["tracked"] += 1
        counts["skipped_unmerged"] += 1
        skipped.append({"path": path, "reason": "unmerged",
                        "mode": "(conflicted)"})

    for mode, oid, path in entries:
        counts["tracked"] += 1

        if mode not in (MODE_EXEC, MODE_PLAIN):
            # Symlinks (120000), gitlinks (160000). The rule is about
            # regular files; reporting a submodule as "missing a shebang"
            # would be noise, so it is skipped WITH its reason recorded.
            counts["skipped_not_a_regular_file"] += 1
            skipped.append({"path": path, "reason": "not a regular file",
                            "mode": mode})
            continue

        excluded_by = matching_prefix(path, exclude_prefixes)
        if excluded_by is not None:
            counts["skipped_excluded"] += 1
            skipped.append({"path": path, "reason": "excluded",
                            "prefix": excluded_by})
            continue

        head = heads.get(oid)
        if head is None:
            raise SetupError("git cat-file returned nothing for %s (%s)"
                             % (path, oid))

        if looks_binary(head):
            counts["skipped_binary"] += 1
            skipped.append({"path": path, "reason": "binary"})
            continue

        counts["checked"] += 1
        is_exec = mode == MODE_EXEC
        shebanged = has_shebang(head)
        if is_exec:
            counts["executable"] += 1
        if shebanged:
            counts["with_shebang"] += 1

        if is_exec and not shebanged:
            findings.append({
                "code": SM001,
                "path": path,
                "mode": mode,
                "first_line": first_line(head),
                "detail": "marked executable but the file does not begin "
                          "with '#!'",
                "fix": "git update-index --chmod=-x -- %s"
                       % shlex.quote(path),
            })
        elif shebanged and not is_exec:
            findings.append({
                "code": SM002,
                "path": path,
                "mode": mode,
                "first_line": first_line(head),
                "detail": "begins with a shebang but is not marked "
                          "executable, so the shebang is inert",
                "fix": "git update-index --chmod=+x -- %s"
                       % shlex.quote(path),
            })

    findings.sort(key=lambda f: (f["code"], f["path"]))
    skipped.sort(key=lambda s: (s["reason"], s["path"]))

    by_code = {}
    for f in findings:
        by_code[f["code"]] = by_code.get(f["code"], 0) + 1

    return {
        "tool": "shebangmode",
        "schema_version": 1,
        "exclude_prefixes": list(exclude_prefixes),
        "counts": counts,
        "findings_by_code": dict(sorted(by_code.items())),
        "findings_total": len(findings),
        "findings": findings,
        "skipped": skipped,
        "status": "ok" if not findings else "mismatches",
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Check that Git-tracked text files are marked "
                    "executable if and only if they start with a shebang.")
    parser.add_argument("--root", default=".",
                        help="Path to the Git checkout to scan "
                             "(default: the current directory).")
    parser.add_argument("-o", "--output",
                        help="Write the canonical JSON report to this file "
                             "instead of stdout. When set, stdout gets a "
                             "one-line summary instead.")
    parser.add_argument("--exclude", action="append", default=[],
                        metavar="PREFIX",
                        help="Skip paths at or under this repo-relative "
                             "prefix. May be repeated. Every skipped path "
                             "is still listed in the report.")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress the stdout summary. The exit code "
                             "is unchanged.")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    try:
        # Deliberately NOT abspath()ed. Every message this raises can end
        # up in a committed transcript, and echoing the caller's absolute
        # path would put the scanning machine's directory layout there.
        # git -C and os.path.join both accept a relative root.
        if not os.path.isdir(args.root):
            raise SetupError("--root %r is not a directory" % args.root)
        report = scan(args.root, args.exclude)
    except SetupError as exc:
        sys.stderr.write("%s: error: %s\n" % (PROG, exc))
        return EXIT_ERROR

    blob = canonical_dumps(report)
    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(blob)
        except OSError as exc:
            # Exit 2, not 1: an unwritable output path is a setup problem
            # and must not be confused with "mismatches found".
            sys.stderr.write("%s: error: cannot write --output: %s\n"
                             % (PROG, exc))
            return EXIT_ERROR
        if not args.quiet:
            sys.stdout.write("status=%s findings=%d checked=%d\n"
                             % (report["status"], report["findings_total"],
                                report["counts"]["checked"]))
    elif not args.quiet:
        sys.stdout.write(blob)

    return EXIT_FINDINGS if report["findings"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
