#!/usr/bin/env python3
"""bundle_index.py -- deterministic, stdlib-only submission-directory indexer.

Walks a bundle directory, records each file's relative path, SHA-256, size,
line count and a detected content type, extracts the bundle's documented
rerun-command block, and flags a fixed set of review-blocking findings.

Standard library only. No third-party packages. No network access.

Exit codes:
  0  index built, zero findings ("clean")
  1  index built, one or more findings
  2  invalid input / usage error (nothing was indexed, no verdict exists)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

TOOL_NAME = "bundle_index"
TOOL_VERSION = "1.0.0"
SCHEMA_VERSION = 1

README_NAME = "README.md"

# Languages that mark a fenced block as *the* rerun-command block outright.
# Deliberately narrow -- see README.md "Rerun-command extraction rule".
RERUN_FENCE_LANGS = {"bash", "sh", "console"}

# Heading text that, when a fenced block follows it, marks that block as the
# rerun-command block even if its language tag is missing or unrecognised.
RERUN_HEADING_RE = re.compile(r"^#{1,6}[ \t]+.*(?:rerun|reproduce|commands).*$", re.IGNORECASE | re.MULTILINE)

FENCE_RE = re.compile(r"^```([^\n`]*)\n(.*?)\n```[ \t]*$", re.MULTILINE | re.DOTALL)

# Filename-suffix -> detected_type for files classified as text content.
# Only consulted when content sniffing has already decided "text"; see
# README.md "Detected type rule".
EXTENSION_TYPE_MAP = {
    ".py": "python",
    ".json": "json",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".log": "text",
    ".sh": "shell",
    ".bash": "shell",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".csv": "csv",
    ".tsv": "csv",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".js": "javascript",
    ".ts": "typescript",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".xml": "xml",
    ".rst": "restructuredtext",
    ".c": "c",
    ".h": "c-header",
    ".cpp": "cpp",
    ".hpp": "cpp-header",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".sql": "sql",
}

SUSPICIOUS_DIR_NAMES = {"__pycache__", ".git"}
SUSPICIOUS_FILENAMES = {".DS_Store"}
SUSPICIOUS_SUFFIXES = {".pyc"}


class InputError(Exception):
    """Raised for conditions that map to exit code 2."""


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

def discover_files(root: Path) -> List[str]:
    """Return every regular-ish file under root as sorted, root-relative,
    forward-slash paths. Directories that raise OSError while being listed
    are skipped rather than aborting the whole walk. Symlinked directories
    are not descended into (os.walk default followlinks=False), so a
    symlink pointing at a directory is invisible to the index; a symlink
    pointing at a file (even a dangling one) is still discovered as a file.
    """
    relpaths: List[str] = []
    root_str = str(root)
    for dirpath, dirnames, filenames in os.walk(root_str, onerror=lambda e: None):
        for name in filenames:
            abspath = Path(dirpath) / name
            rel = os.path.relpath(str(abspath), root_str)
            relpaths.append(Path(rel).as_posix())
    relpaths.sort()
    return relpaths


# --------------------------------------------------------------------------
# Content sniffing
# --------------------------------------------------------------------------

def is_binary(data: bytes) -> bool:
    """A file is binary if it contains a NUL byte, or is not valid UTF-8.

    NUL-byte check comes first and is decisive on its own: a single NUL
    byte is technically legal UTF-8 (U+0000) but essentially never appears
    in a genuine text file, so it is treated as a binary signal regardless
    of what the rest of the decode does.
    """
    if b"\x00" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def count_lines(data: bytes) -> int:
    """Line count = len(bytes.splitlines()).

    This is deliberately NOT `data.count(b"\\n")` (that is what `wc -l`
    effectively does and it undercounts a file with no trailing newline by
    one -- the final, unterminated line is real content and must count).
    bytes.splitlines() also collapses a CRLF pair into a single line
    terminator instead of double-counting it, and treats a file consisting
    of exactly one "\\n" as one (blank) line rather than zero.
    """
    return len(data.splitlines())


def detected_type_for_text(relpath: str) -> str:
    suffix = Path(relpath).suffix.lower()
    return EXTENSION_TYPE_MAP.get(suffix, "text")


# --------------------------------------------------------------------------
# Suspicious artifacts
# --------------------------------------------------------------------------

def suspicious_reasons(relpath: str) -> List[str]:
    parts = relpath.split("/")
    dir_parts, filename = parts[:-1], parts[-1]
    reasons: List[str] = []
    for d in dir_parts:
        if d in SUSPICIOUS_DIR_NAMES:
            reasons.append("path contains '%s' directory" % d)
    if filename in SUSPICIOUS_FILENAMES:
        reasons.append("filename is '%s'" % filename)
    for suf in SUSPICIOUS_SUFFIXES:
        if filename.endswith(suf):
            reasons.append("filename has suspicious suffix '%s'" % suf)
    return reasons


# --------------------------------------------------------------------------
# Rerun-command block extraction
# --------------------------------------------------------------------------

def extract_rerun_block(readme_text: str) -> Optional[Tuple[str, str]]:
    """Return (language, text) for the bundle's rerun-command block, or
    None if no such block exists. See README.md for the precise rule this
    implements; summary:

      1. Scan fenced code blocks (``` ... ```) in document order. The
         first one whose info-string, stripped and lowercased, is exactly
         "bash", "sh" or "console" wins.
      2. Otherwise, scan headings matching /rerun|reproduce|commands/i in
         document order; for the first matching heading, the first fenced
         block that starts anywhere after it wins (its language, whatever
         it is, including none).
      3. Otherwise there is no rerun-command block.
    """
    fences = list(FENCE_RE.finditer(readme_text))

    for m in fences:
        lang = m.group(1).strip().lower()
        if lang in RERUN_FENCE_LANGS:
            return lang, m.group(2)

    heading_match = RERUN_HEADING_RE.search(readme_text)
    if heading_match is not None:
        for m in fences:
            if m.start() > heading_match.end():
                lang = m.group(1).strip().lower()
                return (lang if lang else None), m.group(2)

    return None


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------

def make_finding(code: str, paths: Sequence[str], detail: str) -> Dict[str, object]:
    return {"code": code, "detail": detail, "paths": sorted(set(paths))}


def sort_findings(findings: List[Dict[str, object]]) -> List[Dict[str, object]]:
    return sorted(findings, key=lambda f: (f["code"], tuple(f["paths"])))


# --------------------------------------------------------------------------
# Report construction
# --------------------------------------------------------------------------

def build_report(bundle_dir: Path) -> Tuple[Dict[str, object], int]:
    if not bundle_dir.exists():
        raise InputError("bundle directory '%s' does not exist" % bundle_dir)
    if not bundle_dir.is_dir():
        raise InputError("bundle path '%s' is not a directory" % bundle_dir)

    relpaths = discover_files(bundle_dir)

    files_data: List[Dict[str, object]] = []
    findings: List[Dict[str, object]] = []
    hashes_seen: Dict[str, List[str]] = {}

    for relpath in relpaths:
        abspath = bundle_dir / relpath
        try:
            data = abspath.read_bytes()
        except OSError as exc:
            files_data.append({
                "relative_path": relpath,
                "sha256": None,
                "size_bytes": None,
                "line_count": None,
                "detected_type": "unreadable",
            })
            findings.append(make_finding(
                "UNREADABLE_FILE", [relpath],
                "file could not be read (%s)" % type(exc).__name__,
            ))
            continue

        size = len(data)
        sha = hashlib.sha256(data).hexdigest()
        hashes_seen.setdefault(sha, []).append(relpath)

        if size == 0:
            line_count: Optional[int] = 0
            dtype = "empty"
            findings.append(make_finding("EMPTY_FILE", [relpath], "file is zero bytes"))
        elif is_binary(data):
            line_count = None
            dtype = "binary"
        else:
            line_count = count_lines(data)
            dtype = detected_type_for_text(relpath)

        for reason in suspicious_reasons(relpath):
            findings.append(make_finding("SUSPICIOUS_ARTIFACT", [relpath], reason))

        files_data.append({
            "relative_path": relpath,
            "sha256": sha,
            "size_bytes": size,
            "line_count": line_count,
            "detected_type": dtype,
        })

    for sha, paths in hashes_seen.items():
        if len(paths) > 1:
            findings.append(make_finding(
                "DUPLICATE_CONTENT", paths,
                "%d files share SHA-256 %s" % (len(paths), sha),
            ))

    readme_path = bundle_dir / README_NAME
    rerun_command: Dict[str, object]
    if not readme_path.is_file():
        findings.append(make_finding("MISSING_README", [README_NAME], "no README.md at bundle root"))
        findings.append(make_finding("NO_RERUN_BLOCK", [README_NAME], "no README.md to extract a rerun block from"))
        rerun_command = {"found": False, "language": None, "text": None}
    else:
        try:
            readme_bytes = readme_path.read_bytes()
            readme_text = readme_bytes.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            findings.append(make_finding("NO_RERUN_BLOCK", [README_NAME], "README.md is unreadable or not valid UTF-8"))
            rerun_command = {"found": False, "language": None, "text": None}
        else:
            extracted = extract_rerun_block(readme_text)
            if extracted is None:
                findings.append(make_finding("NO_RERUN_BLOCK", [README_NAME], "no matching fenced rerun-command block found"))
                rerun_command = {"found": False, "language": None, "text": None}
            else:
                lang, text = extracted
                rerun_command = {"found": True, "language": lang, "text": text}

    findings = sort_findings(findings)
    finding_count = len(findings)
    status = "clean" if finding_count == 0 else "findings"
    exit_code = 0 if finding_count == 0 else 1

    report = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "file_count": len(files_data),
        "files": files_data,
        "finding_count": finding_count,
        "findings": findings,
        "rerun_command": rerun_command,
        "status": status,
        "exit_code": exit_code,
    }
    return report, exit_code


def canonical_json_bytes(obj: object) -> bytes:
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return (text + "\n").encode("utf-8")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bundle_index.py",
        description="Index a submission bundle directory for fast reviewer verification.",
    )
    parser.add_argument("bundle_dir", help="path to the bundle directory to index")
    parser.add_argument("-o", "--output", help="also write the canonical JSON report to this path")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)  # argparse itself exits 2 on usage errors

    bundle_dir = Path(args.bundle_dir)

    try:
        report, exit_code = build_report(bundle_dir)
    except InputError as exc:
        sys.stderr.write("bundle_index: input error: %s\n" % exc)
        return 2

    out_bytes = canonical_json_bytes(report)

    if args.output:
        out_path = Path(args.output)
        try:
            if out_path.parent and str(out_path.parent) not in ("", "."):
                out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "wb") as fh:
                fh.write(out_bytes)
        except OSError as exc:
            sys.stderr.write("bundle_index: input error: could not write output file (%s)\n" % type(exc).__name__)
            return 2

    sys.stdout.buffer.write(out_bytes)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
