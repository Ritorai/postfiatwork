#!/usr/bin/env python3
"""pathscan.py -- stdlib-only cross-platform path collision scanner.

Reads a list of repository-relative paths -- by default the Git-tracked
set (`git ls-files -z`), otherwise a NUL-delimited list from a file or
stdin -- and reports names that collide with each other, or break
outright, on a filesystem other than the one they were created on.

Why this exists
---------------
Evidence lives in files. If two tracked paths differ only by case, a
reviewer on macOS or Windows gets one of them, silently: `git checkout`
writes the first, the second overwrites it, and the working tree no
longer matches the commit. If a path is normalisation-equivalent to
another, the same thing happens on macOS, which stores NFD. If a
component is named `aux.json`, Windows cannot create it at all. None of
these produce an error on Linux, which is where they get committed.

Exit codes:
    0  no findings
    1  findings (at least one path is a collision or breakage risk)
    2  setup error (cannot read --paths-from, `git ls-files` failed,
       --root is not a directory, --output cannot be written, malformed
       input)

Determinism
-----------
The report contains no timestamps, no durations, and no absolute paths
that this tool derived: `--root`, the working directory and the
temporary state used to run git are never written into it. Paths in the
report are exactly the repository-relative strings that were scanned.
Findings are ordered by (rule_id, sorted member paths), so two runs over
the same path list -- from any directory, under any name -- produce
byte-identical JSON. That is checked directly in captured_output.txt by
running from two differently named absolute paths and comparing SHA-256.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata

SCHEMA_VERSION = 1
TOOL_NAME = "path-collision-scanner"

R_CASE = "CASE_FOLD_COLLISION"
R_NORM = "UNICODE_NORMALIZATION_COLLISION"
R_RESERVED = "WINDOWS_RESERVED_NAME"
R_TRAILING = "TRAILING_DOT_OR_SPACE"
R_CONTROL = "CONTROL_CHARACTER"
R_ILLEGAL = "WINDOWS_ILLEGAL_CHARACTER"
R_UNSAFE = "UNSAFE_RELATIVE_PATH"
R_DUPLICATE = "DUPLICATE_PATH"

ALL_RULES = (
    R_CASE,
    R_NORM,
    R_RESERVED,
    R_TRAILING,
    R_CONTROL,
    R_ILLEGAL,
    R_UNSAFE,
    R_DUPLICATE,
)

# Devices reserved by the Windows kernel. Reserved as a whole path
# component AND as the stem before the first dot: NUL.txt is reserved.
WINDOWS_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + ["COM%d" % n for n in range(1, 10)]
    + ["LPT%d" % n for n in range(1, 10)]
)

# Characters Windows refuses in a file name. "/" is the separator here so
# it is handled by splitting, not by this set.
WINDOWS_ILLEGAL = '<>:"\\|?*'

DRIVE_RE = re.compile(r"^[A-Za-z]:")


class SetupError(Exception):
    """Problems with the scanner's own setup (exit code 2)."""


# --------------------------------------------------------------------------
# Canonical JSON
# --------------------------------------------------------------------------

def canonical_json(obj):
    """Sorted keys, tight separators, ASCII-only, single trailing newline."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


# --------------------------------------------------------------------------
# Input
# --------------------------------------------------------------------------

def decode_nul_list(data, source_label):
    """Split NUL-delimited bytes into a list of str paths.

    A trailing NUL is normal (git ls-files -z emits one after every entry)
    and is not an empty path. An *interior* empty entry is malformed input
    and is a setup error, not a finding: the scanner cannot tell whether
    the producer meant an empty name or emitted a stray separator.
    """
    if data == b"":
        return []
    if data.endswith(b"\0"):
        data = data[:-1]
    parts = data.split(b"\0")
    out = []
    for i, raw in enumerate(parts):
        if raw == b"":
            raise SetupError(
                "%s: entry %d is empty (stray NUL separator?); refusing to guess"
                % (source_label, i)
            )
        try:
            out.append(raw.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise SetupError(
                "%s: entry %d is not valid UTF-8: %s" % (source_label, i, exc)
            )
    return out


def read_paths_from(path):
    """Read a NUL-delimited path list from a file, or from stdin for '-'."""
    if path == "-":
        try:
            data = sys.stdin.buffer.read()
        except OSError as exc:
            raise SetupError("cannot read path list from stdin: %s" % exc)
        return decode_nul_list(data, "stdin")
    if not os.path.isfile(path):
        raise SetupError("path list not found: %s" % path)
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise SetupError("cannot read path list %s: %s" % (path, exc))
    return decode_nul_list(data, path)


def git_tracked_paths(root):
    """Return the Git-tracked paths under root, as repository-relative strings."""
    if not os.path.isdir(root):
        raise SetupError("--root is not a directory: %s" % root)
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
    except FileNotFoundError:
        raise SetupError(
            "git not found on PATH. Use --paths-from to scan a path list instead."
        )
    except subprocess.TimeoutExpired:
        raise SetupError("`git ls-files -z` timed out after 120s")
    except OSError as exc:
        raise SetupError("could not run `git ls-files -z`: %s" % exc)
    if proc.returncode != 0:
        raise SetupError(
            "`git ls-files -z` failed with exit code %d: %s"
            % (proc.returncode, proc.stderr.decode("utf-8", "replace").strip())
        )
    # git quotes non-ASCII names unless core.quotepath is off, but -z output
    # is raw bytes with no quoting applied, so a plain UTF-8 decode is right.
    return decode_nul_list(proc.stdout, "git ls-files -z")


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

def components(path):
    return path.split("/")


def unsafe_reasons(path):
    """Reasons a path is not a safe repository-relative path. Sorted."""
    reasons = set()
    if path == "":
        reasons.add("empty path")
    if path.startswith("/"):
        reasons.add("absolute path")
    if DRIVE_RE.match(path):
        reasons.add("drive-letter prefix")
    if "\\" in path:
        reasons.add("backslash separator")
    if path.endswith("/"):
        reasons.add("trailing separator")
    parts = components(path)
    for part in parts:
        if part == "":
            if path != "":
                reasons.add("empty path component")
        elif part == ".":
            reasons.add("'.' component")
        elif part == "..":
            reasons.add("'..' component")
    return sorted(reasons)


def reserved_components(path):
    """Components whose stem is a Windows reserved device name. Sorted.

    The stem is the text before the first dot, with trailing spaces
    removed. Both halves of that matter, and only one of them used to be
    here.

    Windows discards trailing spaces and dots from a name before it
    resolves it, so `CON .txt` is not a file called "CON " -- it is the
    console device, and the file cannot be created at all. The dot half
    was already handled, because splitting on the first dot makes
    `CON..txt` and `CON. .txt` reduce to `CON`. The space half was not, so
    `CON .txt`, `AUX .json` and `COM1 .log` scanned clean: a path that
    Windows refuses outright, reported as safe, by the tool whose entire
    job is to catch exactly that. Two of the eight rules missed it
    together -- TRAILING_DOT_OR_SPACE looks at the end of the whole
    component, and `CON .txt` ends in a `t`.

    Leading spaces are deliberately NOT stripped. Windows' handling of
    them is context-dependent (the shell, the API and Explorer do not
    agree), so `  CON.txt` is left alone rather than guessed at; stripping
    it here would be a claim this tool cannot support.

    The rule below is not this author's reading of the documentation. It
    is character-for-character what CPython's own `ntpath._isreservedname`
    does -- `name.partition('.')[0].rstrip(' ').upper() in _reserved_names`
    -- under the comment `# DOS device names are reserved (e.g. "nul" or
    "nul .txt")`. That is the strongest corroboration available from the
    standard library, and it is worth being plain that it is corroboration
    and not proof: no run recorded anywhere in this repository has happened
    on Windows."""
    hits = set()
    for part in components(path):
        if not part:
            continue
        stem = part.split(".")[0].rstrip(" ")
        if stem.upper() in WINDOWS_RESERVED:
            hits.add(part)
    return sorted(hits)


def trailing_components(path):
    hits = set()
    for part in components(path):
        if part and (part.endswith(".") or part.endswith(" ")):
            # "." and ".." are separately reported as UNSAFE_RELATIVE_PATH;
            # reporting them here as well would double-count one defect.
            if part not in (".", ".."):
                hits.add(part)
    return sorted(hits)


def control_chars(path):
    """Sorted list of U+XXXX labels for control characters present."""
    hits = {ord(ch) for ch in path if ord(ch) < 0x20 or ord(ch) == 0x7F}
    return ["U+%04X" % cp for cp in sorted(hits)]


def illegal_chars(path):
    """Windows-illegal characters in the path.

    A leading drive-letter prefix is stripped first: the colon in "C:/x" is
    a drive separator, not an illegal name character, and the path is
    already reported as UNSAFE_RELATIVE_PATH for having the prefix at all.
    A backslash IS still reported here as well as by the unsafe-path rule,
    because it is both a separator problem and an illegal name character
    and the two are not the same defect to fix.
    """
    body = path[2:] if DRIVE_RE.match(path) else path
    hits = {ch for ch in body if ch in WINDOWS_ILLEGAL}
    return sorted(hits)


def group_collisions(paths, keyfunc):
    """Map key -> sorted distinct member paths, for keys with >1 member."""
    buckets = {}
    for p in paths:
        buckets.setdefault(keyfunc(p), set()).add(p)
    return {k: sorted(v) for k, v in buckets.items() if len(v) > 1}


def nfc(path):
    return unicodedata.normalize("NFC", path)


# --------------------------------------------------------------------------
# Scan
# --------------------------------------------------------------------------

def scan(paths, enabled_rules):
    """Return a sorted list of finding dicts."""
    findings = []
    distinct = sorted(set(paths))

    if R_DUPLICATE in enabled_rules:
        counts = {}
        for p in paths:
            counts[p] = counts.get(p, 0) + 1
        for p in sorted(k for k, n in counts.items() if n > 1):
            findings.append({
                "rule_id": R_DUPLICATE,
                "paths": [p],
                "detail": {"occurrences": counts[p]},
            })

    if R_CASE in enabled_rules:
        # Fold on the NFC form so a pair differing in BOTH case and
        # normalisation is still caught here. But if every member of the
        # group has the SAME NFC form, the only difference is normalisation
        # and this is not a case collision -- reporting it here as well
        # would count one defect twice.
        for _key, members in sorted(
            group_collisions(distinct, lambda p: nfc(p).casefold()).items()
        ):
            if len({nfc(m) for m in members}) < 2:
                continue
            findings.append({
                "rule_id": R_CASE,
                "paths": members,
                "detail": {"folded": nfc(members[0]).casefold()},
            })

    if R_NORM in enabled_rules:
        for _key, members in sorted(group_collisions(distinct, nfc).items()):
            # A pure case collision is not a normalisation collision.
            if len({nfc(m) for m in members}) == 1 and len({m for m in members}) > 1:
                findings.append({
                    "rule_id": R_NORM,
                    "paths": members,
                    "detail": {
                        "nfc": nfc(members[0]),
                        "forms": sorted(
                            {("NFC" if unicodedata.is_normalized("NFC", m) else "non-NFC")
                             for m in members}
                        ),
                    },
                })

    for p in distinct:
        if R_UNSAFE in enabled_rules:
            reasons = unsafe_reasons(p)
            if reasons:
                findings.append({
                    "rule_id": R_UNSAFE,
                    "paths": [p],
                    "detail": {"reasons": reasons},
                })
        if R_RESERVED in enabled_rules:
            hits = reserved_components(p)
            if hits:
                findings.append({
                    "rule_id": R_RESERVED,
                    "paths": [p],
                    "detail": {"components": hits},
                })
        if R_TRAILING in enabled_rules:
            hits = trailing_components(p)
            if hits:
                findings.append({
                    "rule_id": R_TRAILING,
                    "paths": [p],
                    "detail": {"components": hits},
                })
        if R_CONTROL in enabled_rules:
            hits = control_chars(p)
            if hits:
                findings.append({
                    "rule_id": R_CONTROL,
                    "paths": [p],
                    "detail": {"code_points": hits},
                })
        if R_ILLEGAL in enabled_rules:
            hits = illegal_chars(p)
            if hits:
                findings.append({
                    "rule_id": R_ILLEGAL,
                    "paths": [p],
                    "detail": {"characters": hits},
                })

    findings.sort(key=lambda f: (f["rule_id"], f["paths"]))
    return findings


def build_report(paths, source, enabled_rules):
    findings = scan(paths, enabled_rules)
    summary = {rule: 0 for rule in sorted(ALL_RULES)}
    for f in findings:
        summary[f["rule_id"]] += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": "findings" if findings else "clean",
        "source": source,
        "paths_scanned": len(paths),
        "distinct_paths": len(set(paths)),
        "rules_enabled": sorted(enabled_rules),
        "summary": summary,
        "findings": findings,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="pathscan.py",
        description="Detect tracked path names that collide or break on other filesystems.",
    )
    p.add_argument("--root", default=".",
                   help="repository root to run `git ls-files -z` in (default: .)")
    p.add_argument("--paths-from", default=None,
                   help="read a NUL-delimited path list from this file instead of git "
                        "('-' reads stdin). Use this for platform-independent fixtures.")
    p.add_argument("-o", "--output", default=None,
                   help="write the report JSON here instead of stdout")
    p.add_argument("--rules", default=None,
                   help="comma-separated subset of rule ids to enable (default: all)")
    p.add_argument("--list-rules", action="store_true",
                   help="print the rule ids, one per line, and exit 0")
    return p


def resolve_rules(spec):
    if spec is None:
        return set(ALL_RULES)
    wanted = [tok.strip() for tok in spec.split(",") if tok.strip()]
    if not wanted:
        raise SetupError("--rules was given but names no rules")
    unknown = sorted(set(wanted) - set(ALL_RULES))
    if unknown:
        raise SetupError(
            "--rules names unknown rule id(s): %s (known: %s)"
            % (", ".join(unknown), ", ".join(sorted(ALL_RULES)))
        )
    return set(wanted)


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.list_rules:
        for rule in sorted(ALL_RULES):
            sys.stdout.write(rule + "\n")
        return 0

    try:
        enabled = resolve_rules(args.rules)
        if args.paths_from is not None:
            paths = read_paths_from(args.paths_from)
            source = "path-list"
        else:
            paths = git_tracked_paths(args.root)
            source = "git"

        report = build_report(paths, source, enabled)
        text = canonical_json(report)

        if args.output:
            try:
                with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
            except OSError as exc:
                raise SetupError("could not write --output %s: %s" % (args.output, exc))
        else:
            sys.stdout.write(text)

        return 1 if report["findings"] else 0

    except SetupError as exc:
        text = canonical_json({
            "schema_version": SCHEMA_VERSION,
            "tool": TOOL_NAME,
            "status": "error",
            "error": str(exc),
        })
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
