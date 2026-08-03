# bundle-verifier

A stdlib-only Python 3 command-line tool that verifies the **integrity** of
a submitted evidence bundle: that a bundle's manifest of files and SHA-256
digests actually matches the bytes on disk, that nothing was added or
removed since the manifest was written, and that the bundle's own
self-describing claims are internally consistent.

**This tool checks integrity, not truth.** A bundle can verify perfectly
clean (exit code 0, zero findings) and still contain entirely fabricated,
misleading, or irrelevant evidence -- `bundleverify.py` only proves that the
bytes on disk are exactly the bytes the manifest says they are, and that no
extra or missing files exist. It says nothing about whether those bytes are
honest. See "Limitations" below.

## Rerun command

```
python3 bundleverify.py --bundle DIR [-o REPORT.json] [--manifest-name NAME]
```

Concretely, to reproduce the sample reports shipped in this directory:

```
python3 bundleverify.py --bundle fixtures/clean_bundle    -o sample_report_clean.json
python3 bundleverify.py --bundle fixtures/tampered_bundle -o sample_report_tampered.json
```

To run the test suite:

```
python3 -m unittest test_bundleverify -v
```

No third-party packages are required or used; only the Python 3 standard
library (`argparse`, `hashlib`, `json`, `os`, `re`, `sys`, plus `unittest`
for the tests). No network access is performed or required.

## Command-line options

| Option | Required | Meaning |
|---|---|---|
| `--bundle DIR` | yes | Path to the bundle directory to verify. |
| `-o, --output FILE` | no | Path to write the JSON report to. If omitted, the report is printed to stdout. |
| `--manifest-name NAME` | no | Filename of the manifest inside the bundle directory. Default: `manifest.json`. Must be a plain filename (no `/`, no `..`). |

## Manifest schema

The manifest is a single JSON file (`manifest.json` by default) inside the
bundle directory, with this exact shape:

```json
{
  "schema_version": 1,
  "files": [
    {"path": "relative/path/to/file.txt", "sha256": "<64 lowercase hex chars>", "size_bytes": 1234},
    {"path": "another/file.bin", "sha256": "...", "size_bytes": 0}
  ]
}
```

Rules:

- `schema_version` must be the integer `1`. This is the only version this
  tool currently understands; any other value (or a missing key) is a
  harness error (exit code 2), because the tool cannot safely interpret an
  unknown manifest shape.
- `files` must be a JSON array (it may be empty; see `EMPTY_BUNDLE` below).
- Each entry in `files` must be a JSON object with:
  - `path`: a non-empty string, **relative to the bundle root**, using
    forward slashes (`/`) as the separator regardless of host OS. It must
    not be absolute, must not contain a `..` component, empty component
    (e.g. `a//b`), or `.` component, and must not contain a NUL byte.
    Windows-style absolute paths (`C:\...`) and UNC paths (`\\server\...`)
    are also rejected as a defense-in-depth measure even though this tool
    targets POSIX filesystems.
  - `sha256`: a string of exactly 64 lowercase hex characters (the SHA-256
    digest of the file's exact bytes). Uppercase hex is rejected as
    malformed -- it is never silently lowercased, so a manifest that isn't
    byte-for-byte in the documented format is flagged rather than guessed
    at.
  - `size_bytes`: a non-negative JSON integer (not a float, not a boolean,
    not a numeric string) giving the exact file size in bytes.
- Entries may carry additional keys beyond these three; unknown extra keys
  are ignored.
- The manifest file itself does not need to list itself, and is exempt from
  `UNLISTED_FILE` detection whether or not it is listed. It *may* optionally
  be listed as an ordinary entry for self-verification, though note that a
  manifest can never correctly record its own hash after that entry is
  added (the act of writing the entry changes the manifest's own bytes) --
  this will deterministically produce a `DIGEST_MISMATCH` for the manifest
  file itself, which is an inherent, documented limitation of self-listing,
  not a bug.

## Finding codes

Every finding is a JSON object with at least `code`, `path` (bundle-relative,
`""` for bundle-level findings), and `detail` (a human-readable message).
Some codes carry extra fields as noted below.

| Code | Meaning | Extra fields |
|---|---|---|
| `MISSING_FILE` | A file listed in the manifest is not present on disk, or the path on disk exists but is not a regular file (e.g. it is a directory). | -- |
| `DIGEST_MISMATCH` | The file exists with the expected size, but its SHA-256 digest does not match the manifest -- the subtle tamper (bytes changed, length preserved). | `expected_sha256`, `actual_sha256` |
| `SIZE_MISMATCH` | The file's actual size on disk does not match the manifest's `size_bytes` field (checked independently of the digest -- a file can have a matching digest and a wrong recorded size, or vice versa, or both). | `expected_size_bytes`, `actual_size_bytes` |
| `UNLISTED_FILE` | A file exists in the bundle directory tree but is not listed (or not validly listed) in the manifest -- the tamper case of adding evidence after the manifest was written. | -- |
| `DUPLICATE_PATH` | The same bundle-relative path appears more than once among the manifest's structurally-valid, non-escaping entries. Only the first occurrence (by manifest order) is used for the actual disk verification; this is reported so the ambiguity itself is visible. | `manifest_indices` (sorted list of every 0-based index sharing this path) |
| `PATH_ESCAPES_BUNDLE` | A manifest path (or something reachable via it) refuses to stay inside the bundle: an absolute path, a path containing `..`, or a symlink (anywhere along the path, including the referenced file itself, an ancestor directory, or a file discovered on disk that isn't even listed) whose resolved target lies outside the bundle root. Such paths are **never followed or read** -- this is a security control, not a best-effort warning. | -- |
| `MALFORMED_ENTRY` | A `files` array entry is not a well-formed manifest entry (not a JSON object, or missing/wrong-typed `path`/`sha256`/`size_bytes`). Reported and skipped -- one bad row never hides or aborts the rest of the run. | `manifest_index` (0-based position in the `files` array) |
| `EMPTY_BUNDLE` | The manifest's `files` array is an empty list. (This checks the manifest's own claim of "no files", independent of whether the bundle directory happens to contain other unlisted files, which are still reported separately as `UNLISTED_FILE`.) | -- |

## Total order of the `findings` list

The `findings` array is always sorted by the tuple:

```
(code, path, canonical_json_dump_of_the_finding)
```

where `canonical_json_dump_of_the_finding` is
`json.dumps(finding, sort_keys=True, separators=(",",":"), ensure_ascii=True)`
of that individual finding object. `code` and `path` alone are enough to
order the overwhelming majority of real reports, but two findings can
legitimately share both (e.g. two `MALFORMED_ENTRY` findings whose entries
had no extractable `path`, differing only in `manifest_index`); appending
the full canonical JSON dump as a final tiebreak guarantees a total,
deterministic order in every case, with no ambiguity and no reliance on
Python's dict/set iteration order.

## Report format

The report is exactly:

```
json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=True) + "\n"
```

written with `open(path, "w", encoding="utf-8", newline="\n")` (or printed
to stdout with the same content if `-o` is omitted). Top-level keys
(alphabetical order is automatic from `sort_keys=True`, shown here for
readability):

```json
{
  "exit_code": 0,
  "findings": [],
  "manifest_path": "manifest.json",
  "num_files_listed": 4,
  "num_findings": 0,
  "report_schema_version": 1,
  "status": "clean"
}
```

The report contains **only bundle-relative paths** -- never the absolute
path of the bundle directory, never a hostname, never a timestamp or mtime
of any kind. Verifying the exact same bundle from two different absolute
locations produces byte-identical reports (see "Determinism and relocation
proof" in `captured_output.txt`).

## Exit codes

| Code | Meaning |
|---|---|
| `0` | The bundle verified clean: zero findings. |
| `1` | One or more findings were produced. Inspect the report's `findings` list. |
| `2` | The harness itself could not run: missing `--bundle` directory, missing or unparseable/unsupported-schema manifest, bad command-line arguments, an unwritable `-o` path, or a `manifest.json` that is a symlink resolving outside the bundle root (see the bug-hunt note below). No report is written in this case; a message is printed to stderr instead. |

Exit codes 1 and 2 are never conflated: a bundle with real findings always
exits 1, and a harness that could not even attempt verification always
exits 2, regardless of how the two situations might superficially resemble
each other.

## Bug found during the mandatory bug hunt

**Bug:** the original implementation opened `manifest.json` with a plain
`os.path.isfile()` existence check before parsing it, which **follows
symlinks**. A bundle (or an attacker who can plant one file into an
otherwise-untouched bundle directory) could replace `manifest.json` with a
symlink pointing to an arbitrary file *outside* the bundle -- for example,
one declaring `{"schema_version": 1, "files": []}`. The tool would silently
treat that external file as "the manifest" and report the bundle as having
zero files listed, completely hiding any real, tampered, or added files that
actually sit in the bundle directory. This defeated the entire purpose of
the tool on exactly the kind of tamper it exists to catch.

**Triggering input:** a bundle directory containing a real (untracked) file
plus a `manifest.json` that is `os.symlink()`'d to a file outside the bundle
declaring an empty file list.

**Fix:** before ever opening the manifest, `bundleverify.py` now checks
`os.path.islink(manifest_path)` and, if true, resolves it with
`os.path.realpath()` and refuses to proceed (exit code 2, "refusing to
follow manifest file: ... is a symlink that resolves outside the bundle
root") unless the resolved target stays inside the bundle root. A
`manifest.json` that happens to be a symlink to another file *inside* the
bundle is still permitted, since that is not a security escape.

**Pinning test:** `test_bundleverify.py`, class `ExtraMalformedAndEscapeTests`,
method `test_bug_manifest_json_itself_a_symlink_escaping_bundle_is_refused`
(with a companion positive-path test in the same class,
`test_manifest_json_symlink_pointing_inside_bundle_is_allowed`, confirming
the fix does not over-reject legitimate in-bundle symlinks).

## Limitations

Being explicit and honest about what this tool does **not** do:

1. **This checks integrity, not truth.** A bundle can verify perfectly
   (exit 0, zero findings) while every document inside it is fabricated,
   backdated, or entirely irrelevant to whatever it claims to prove.
   `bundleverify.py` only proves "the bytes match what the manifest says
   the bytes are, and nothing extra or missing exists" -- it has no concept
   of whether the manifest was honestly produced from real evidence in the
   first place. A malicious actor with control over *both* the evidence
   files and the manifest at the moment of creation can produce a
   bundle that verifies cleanly forever, no matter how fabricated its
   contents are.
2. **Directory symlinks are never traversed, even when they point inside
   the bundle.** `os.walk(..., followlinks=False)` is used deliberately so
   that a symlinked directory can never be used to smuggle bytes from
   outside the bundle into what looks like a verified file. The side effect
   is that legitimate files reachable *only* through an internal directory
   symlink (e.g. `aliasdir -> realdir`, both inside the bundle, with no
   other path to `realdir`'s contents) will not be discovered by the
   on-disk scan and cannot be verified via that alias path, even though the
   underlying bytes are perfectly fine. This is a deliberate safety/
   simplicity trade-off, not an oversight, but it means bundles that rely
   on internal directory symlinks for their layout will not verify as
   expected.
3. **Time-of-check to time-of-use (TOCTOU) is not defended against.**
   Verification reads each file once, checks each path's safety once, and
   makes no attempt to lock the bundle directory or detect concurrent
   modification. If something else is actively rewriting files in the
   bundle directory while `bundleverify.py` runs (or replaces a safe file
   with a symlink between the safety check and the read), the report can
   reflect a state that never coherently existed on disk at any single
   instant. Bundles should be verified against a directory that is not
   concurrently writable by anything else.
4. **Case-sensitivity and Unicode normalization are filesystem-dependent
   and not reconciled by this tool.** A manifest path and an on-disk
   filename that differ only in case (on a case-insensitive filesystem) or
   only in Unicode normalization form (e.g. NFC vs NFD, which macOS
   sometimes rewrites transparently) will not be recognized as "the same
   file" by this tool's exact byte-for-byte string comparison. This can
   produce a confusing simultaneous `MISSING_FILE` (for the manifest's
   spelling) and `UNLISTED_FILE` (for the disk's spelling) for what a human
   would consider one file. This tool assumes a case-sensitive,
   normalization-preserving filesystem (true of ext4/most Linux setups, not
   guaranteed on macOS or Windows).
5. **`DUPLICATE_PATH` verifies only the first occurrence.** When the same
   path is listed more than once with conflicting `sha256`/`size_bytes`
   values, the tool reports the ambiguity via `DUPLICATE_PATH` but only
   actually checks the first (lowest manifest-index) occurrence against
   disk. It does not attempt to determine which of the conflicting entries,
   if any, the bundle author "really meant".

## What "clean" (exit 0) does and does not certify

Exit code 0 certifies: every path listed in the manifest exists, is a
regular file, has the exact recorded size and SHA-256 digest, no manifest
path escapes the bundle, no path is listed more than once, no file exists
in the bundle tree that isn't listed, and the manifest lists at least one
file. It certifies nothing about provenance, authenticity, or truth of the
evidence itself.
