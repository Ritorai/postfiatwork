#!/usr/bin/env python3
"""bundleverify.py -- verify the integrity of a submitted evidence bundle.

This tool checks that a bundle's manifest (a JSON file listing every file in
the bundle together with its size and SHA-256 digest) actually matches the
bytes present on disk. It detects tampering, drift, and manifest/disk
mismatches. It does NOT judge whether the evidence itself is truthful,
relevant, or admissible -- see README.md, section "Limitations".

Exit codes:
  0  the bundle verified clean (zero findings)
  1  one or more findings were produced (see the report's "findings" list)
  2  the harness could not run at all (bad arguments, missing bundle
     directory, missing/unparseable manifest, unwritable --output path)

See README.md for the full manifest schema, the meaning of every finding
code, and the exact total ordering applied to the findings list before it is
serialized.
"""

import argparse
import hashlib
import json
import os
import re
import sys

REPORT_SCHEMA_VERSION = 1
SUPPORTED_MANIFEST_SCHEMA_VERSION = 1
DEFAULT_MANIFEST_NAME = "manifest.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CHUNK_SIZE = 1024 * 1024  # 1 MiB, so hashing never loads a whole file into memory


class HarnessError(Exception):
    """Raised for conditions that make it impossible to produce a report at
    all (exit code 2). Never raised for anything that belongs in the
    findings list."""


# --------------------------------------------------------------------------
# Path safety helpers
# --------------------------------------------------------------------------

def _string_path_problem(path):
    """Return a human-readable reason the manifest path string is unsafe to
    use as a bundle-relative path, or None if the string form is fine.

    This check operates purely on the string -- it never touches the
    filesystem -- so a manifest can be refused before we ever open anything
    on disk, even if the referenced file does not exist.
    """
    if path == "":
        return "path is empty"
    if "\x00" in path:
        return "path contains a NUL byte"
    if path.startswith("/"):
        return "path is absolute (starts with '/')"
    if os.path.isabs(path):
        return "path is absolute"
    if re.match(r"^[A-Za-z]:[\\/]", path) or path.startswith("\\\\"):
        return "path looks like a Windows-style absolute path"
    parts = path.split("/")
    if any(p == ".." for p in parts):
        return "path contains a '..' component"
    if any(p == "" for p in parts):
        return "path contains an empty component (e.g. '//' or a trailing '/')"
    if any(p == "." for p in parts):
        return "path contains a '.' component"
    return None


def _to_rel_posix(root, full):
    rel = os.path.relpath(full, root)
    return rel.replace(os.sep, "/")


def _is_within(real_child, real_root):
    return real_child == real_root or real_child.startswith(real_root + os.sep)


# --------------------------------------------------------------------------
# Hashing
# --------------------------------------------------------------------------

def compute_sha256(path):
    """Hash a file's bytes in fixed-size chunks so a large file never has to
    be fully resident in memory."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------
# Manifest entry validation
# --------------------------------------------------------------------------

def validate_entry_structure(entry):
    """Validate the structural shape of one manifest "files" entry.

    Returns (path_or_None, problems_str_or_None, size_bytes_or_None,
    sha256_or_None). problems_str is None iff the entry is well-formed
    (correct types, sha256 is 64 lowercase hex chars, size_bytes is a
    non-negative integer). path_or_None is populated whenever a usable path
    string could be extracted, even if some other field is broken, so that
    MALFORMED_ENTRY findings can still carry a useful location.
    """
    if not isinstance(entry, dict):
        return None, "entry is not a JSON object (type={0})".format(type(entry).__name__), None, None

    problems = []
    path = None
    if "path" not in entry:
        problems.append("missing 'path'")
    elif not isinstance(entry["path"], str):
        problems.append("'path' is not a string")
    elif entry["path"] == "":
        problems.append("'path' is empty")
    else:
        path = entry["path"]

    sha256 = None
    if "sha256" not in entry:
        problems.append("missing 'sha256'")
    elif not isinstance(entry["sha256"], str):
        problems.append("'sha256' is not a string")
    elif not SHA256_RE.match(entry["sha256"]):
        problems.append("'sha256' is not 64 lowercase hex characters")
    else:
        sha256 = entry["sha256"]

    size_bytes = None
    if "size_bytes" not in entry:
        problems.append("missing 'size_bytes'")
    elif isinstance(entry["size_bytes"], bool) or not isinstance(entry["size_bytes"], int):
        problems.append("'size_bytes' is not an integer")
    elif entry["size_bytes"] < 0:
        problems.append("'size_bytes' is negative")
    else:
        size_bytes = entry["size_bytes"]

    if problems:
        return path, "; ".join(problems), size_bytes, sha256
    return path, None, size_bytes, sha256


# --------------------------------------------------------------------------
# Disk scan
# --------------------------------------------------------------------------

def scan_disk(bundle_root, manifest_rel_path):
    """Walk the bundle directory tree.

    Returns (on_disk_paths, escaping): on_disk_paths is the set of
    bundle-relative posix paths for every regular file found (excluding the
    manifest file itself and excluding anything classified as escaping).
    escaping is a dict of bundle-relative posix path -> reason, for every
    symlink (file or directory) encountered whose resolved target lies
    outside the bundle root. Directory symlinks are never traversed
    (followlinks=False), regardless of where they point.
    """
    bundle_root_real = os.path.realpath(bundle_root)
    on_disk = set()
    escaping = {}

    for dirpath, dirnames, filenames in os.walk(bundle_root, followlinks=False):
        for d in dirnames:
            full = os.path.join(dirpath, d)
            if os.path.islink(full):
                rel = _to_rel_posix(bundle_root, full)
                real = os.path.realpath(full)
                if not _is_within(real, bundle_root_real):
                    escaping[rel] = "directory symlink target resolves outside the bundle root"
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = _to_rel_posix(bundle_root, full)
            if rel == manifest_rel_path:
                continue
            if os.path.islink(full):
                real = os.path.realpath(full)
                if not _is_within(real, bundle_root_real):
                    escaping[rel] = "symlink target resolves outside the bundle root"
                    continue
            on_disk.add(rel)

    return on_disk, escaping


# --------------------------------------------------------------------------
# Core verification
# --------------------------------------------------------------------------

def verify_bundle(bundle_dir, manifest_name):
    """Run the full verification and return the list of finding dicts
    (unsorted) plus the raw count of entries listed in the manifest."""
    manifest_path = os.path.join(bundle_dir, manifest_name)

    if not os.path.isdir(bundle_dir):
        raise HarnessError("bundle directory not found: {0}".format(bundle_dir))

    # Security check: refuse (never follow) a manifest.json that is itself a
    # symlink resolving outside the bundle root. Without this check, a
    # bundle could carry a "manifest.json" that is really a symlink to an
    # attacker-controlled file elsewhere on the filesystem, letting whoever
    # planted the bundle substitute an arbitrary, out-of-bundle manifest
    # (e.g. an empty one) and have this tool silently trust it -- exactly
    # the kind of tamper this tool exists to catch. This is deliberately a
    # harness error (exit 2), not a PATH_ESCAPES_BUNDLE finding, because we
    # cannot produce a meaningful integrity report once the manifest's own
    # identity cannot be trusted.
    if os.path.islink(manifest_path):
        bundle_root_real = os.path.realpath(bundle_dir)
        manifest_real = os.path.realpath(manifest_path)
        if not _is_within(manifest_real, bundle_root_real):
            raise HarnessError(
                "refusing to follow manifest file: {0} is a symlink that resolves "
                "outside the bundle root".format(manifest_path)
            )

    if not os.path.isfile(manifest_path):
        raise HarnessError("manifest file not found: {0}".format(manifest_path))

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            raw_text = f.read()
    except OSError as e:
        raise HarnessError("could not read manifest file: {0}".format(e))
    except UnicodeDecodeError as e:
        raise HarnessError("manifest file is not valid UTF-8: {0}".format(e))

    try:
        manifest = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise HarnessError("manifest file is not valid JSON: {0}".format(e))

    if not isinstance(manifest, dict):
        raise HarnessError("manifest top level must be a JSON object")
    if "schema_version" not in manifest:
        raise HarnessError("manifest is missing required key 'schema_version'")
    if manifest["schema_version"] != SUPPORTED_MANIFEST_SCHEMA_VERSION:
        raise HarnessError(
            "unsupported manifest schema_version: {0!r} (this tool supports {1})".format(
                manifest["schema_version"], SUPPORTED_MANIFEST_SCHEMA_VERSION
            )
        )
    if "files" not in manifest:
        raise HarnessError("manifest is missing required key 'files'")
    if not isinstance(manifest["files"], list):
        raise HarnessError("manifest key 'files' must be a JSON array")

    files = manifest["files"]
    findings = []
    reported_escape_paths = set()

    def add_escape_finding(path, detail):
        if path in reported_escape_paths:
            return
        reported_escape_paths.add(path)
        findings.append({
            "code": "PATH_ESCAPES_BUNDLE",
            "path": path,
            "detail": detail,
        })

    # Phase 1: structural validation + string-level path safety.
    entries_by_path = {}
    for index, entry in enumerate(files):
        path, problem, size_bytes, sha256 = validate_entry_structure(entry)
        if problem is not None:
            findings.append({
                "code": "MALFORMED_ENTRY",
                "path": path if path is not None else "",
                "detail": problem,
                "manifest_index": index,
            })
            continue

        string_problem = _string_path_problem(path)
        if string_problem is not None:
            add_escape_finding(path, string_problem)
            continue

        entries_by_path.setdefault(path, []).append({
            "index": index,
            "size_bytes": size_bytes,
            "sha256": sha256,
        })

    # Phase 2: duplicate detection.
    for path, occurrences in entries_by_path.items():
        if len(occurrences) > 1:
            indices = sorted(o["index"] for o in occurrences)
            findings.append({
                "code": "DUPLICATE_PATH",
                "path": path,
                "detail": "path appears {0} times in the manifest".format(len(occurrences)),
                "manifest_indices": indices,
            })

    # Phase 3: pick one representative entry per unique path (the first
    # occurrence by manifest order) for disk verification.
    candidate_paths = {}
    for path, occurrences in entries_by_path.items():
        occurrences_sorted = sorted(occurrences, key=lambda o: o["index"])
        candidate_paths[path] = occurrences_sorted[0]

    # Phase 4: disk scan.
    on_disk_paths, walk_escaping = scan_disk(bundle_dir, manifest_name)
    for path, reason in walk_escaping.items():
        add_escape_finding(path, reason)

    # Phase 5: verify each candidate path against disk.
    bundle_root_real = os.path.realpath(bundle_dir)
    verified_paths = set()
    for path, entry in candidate_paths.items():
        full = os.path.join(bundle_dir, path)
        real = os.path.realpath(full)
        if not _is_within(real, bundle_root_real):
            add_escape_finding(path, "resolves outside the bundle root (symlink)")
            continue

        verified_paths.add(path)

        if not os.path.lexists(full):
            findings.append({
                "code": "MISSING_FILE",
                "path": path,
                "detail": "file listed in manifest is not present on disk",
            })
            continue
        if not os.path.isfile(full):
            findings.append({
                "code": "MISSING_FILE",
                "path": path,
                "detail": "path exists on disk but is not a regular file",
            })
            continue

        actual_size = os.path.getsize(full)
        if actual_size != entry["size_bytes"]:
            findings.append({
                "code": "SIZE_MISMATCH",
                "path": path,
                "detail": "manifest size_bytes does not match the file on disk",
                "expected_size_bytes": entry["size_bytes"],
                "actual_size_bytes": actual_size,
            })

        actual_sha256 = compute_sha256(full)
        if actual_sha256 != entry["sha256"]:
            findings.append({
                "code": "DIGEST_MISMATCH",
                "path": path,
                "detail": "manifest sha256 does not match the file on disk",
                "expected_sha256": entry["sha256"],
                "actual_sha256": actual_sha256,
            })

    # Phase 6: files present on disk but never listed (successfully) in the
    # manifest.
    unlisted = on_disk_paths - verified_paths
    for path in unlisted:
        findings.append({
            "code": "UNLISTED_FILE",
            "path": path,
            "detail": "file is present in the bundle but not listed in the manifest",
        })

    # Phase 7: empty manifest.
    if len(files) == 0:
        findings.append({
            "code": "EMPTY_BUNDLE",
            "path": "",
            "detail": "manifest 'files' array is empty",
        })

    return findings, len(files)


# --------------------------------------------------------------------------
# Ordering + report assembly
# --------------------------------------------------------------------------

def _canonical_dump(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sort_key(finding):
    """Total order: (code, path, full canonical JSON dump of the finding).

    The canonical-JSON tiebreak makes the order fully deterministic even
    when two findings share the same code and path but differ in some other
    field (e.g. two MALFORMED_ENTRY findings at different manifest indices
    with no usable path).
    """
    return (finding.get("code", ""), finding.get("path", ""), _canonical_dump(finding))


def build_report(findings, num_files_listed, manifest_name):
    findings_sorted = sorted(findings, key=sort_key)
    exit_code = 1 if findings_sorted else 0
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "manifest_path": manifest_name,
        "num_files_listed": num_files_listed,
        "num_findings": len(findings_sorted),
        "status": "clean" if not findings_sorted else "findings_present",
        "exit_code": exit_code,
        "findings": findings_sorted,
    }
    return report, exit_code


def write_report(report, output_path):
    text = _canonical_dump(report) + "\n"
    if output_path is None:
        sys.stdout.write(text)
        return
    try:
        with open(output_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    except OSError as e:
        raise HarnessError("could not write report to {0}: {1}".format(output_path, e))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="bundleverify.py",
        description="Verify the integrity of an evidence bundle against its manifest.",
    )
    parser.add_argument("--bundle", required=True, metavar="DIR", help="path to the bundle directory")
    parser.add_argument("-o", "--output", metavar="FILE", default=None,
                         help="path to write the JSON report to (default: stdout)")
    parser.add_argument("--manifest-name", default=DEFAULT_MANIFEST_NAME, metavar="NAME",
                         help="name of the manifest file inside the bundle directory (default: manifest.json)")
    return parser


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    # argparse.parse_args() calls sys.exit(2) itself on malformed arguments.

    manifest_name = args.manifest_name
    if "/" in manifest_name or "\\" in manifest_name or manifest_name in ("", ".", ".."):
        sys.stderr.write("bundleverify.py: error: --manifest-name must be a plain filename\n")
        return 2

    try:
        findings, num_files_listed = verify_bundle(args.bundle, manifest_name)
        report, exit_code = build_report(findings, num_files_listed, manifest_name)
        write_report(report, args.output)
    except HarnessError as e:
        sys.stderr.write("bundleverify.py: error: {0}\n".format(e))
        return 2

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
