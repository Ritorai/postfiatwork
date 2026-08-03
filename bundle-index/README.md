# Bundle Index CLI

Standard-library Python 3 only. No third-party packages, no network access.

`bundle_index.py` walks a submission ("bundle") directory and produces a
deterministic, byte-reproducible JSON index: every file's relative path,
SHA-256, size, line count and detected content type, plus the bundle's
rerun-command block extracted from `README.md` and a fixed set of
review-blocking findings (missing README, empty files, duplicate content,
suspicious build/VCS artifacts, unreadable files, missing rerun block).

It answers *"what is actually in this bundle, and is it obviously
reviewable?"* -- a narrower, faster question than "is this bundle
authentic and untampered?" (see **Relationship to `evidence-manifest`**
below).

---

## How this differs from `evidence-manifest`

`evidence-manifest` (the sibling tool in this repository) builds a
Merkle-tree manifest over a *batch of already-canonicalized submission
records* and answers **"has anything in this manifest been tampered with
since it was built?"** It hashes canonical *objects*, chains leaf digests
into a root under an explicit domain-separated hashing scheme
(`leaf:`/`node:`), and its `verify` subcommand exists specifically to
detect drift against a previously captured root.

`bundle_index` is a different, narrower tool. It hashes *files on disk*,
one at a time, with plain `SHA-256(file_bytes)` -- there is no tree, no
root hash, no `verify` subcommand, and no tamper-detection story at all.
It exists to answer a different, prior question: **"before anyone checks
authenticity, does this bundle even contain the things a reviewer needs,
in a form a reviewer can act on?"** That means per-file line counts and
detected types (which a Merkle manifest has no reason to carry), a
rerun-command extractor (which a Merkle manifest has no reason to have),
and bundle-hygiene findings like `SUSPICIOUS_ARTIFACT` and
`DUPLICATE_CONTENT` that are about review friction, not integrity.

Both tools independently compute SHA-256 over file content and both
emit the same canonical-JSON convention (`sort_keys=True,
separators=(",",":"),ensure_ascii=True` plus one trailing newline), and
both are exit-code `0`/`1`/`2` tools with an `-o` flag -- that convergence
is deliberate, not an accident: it means a reviewer who has already
learned one tool's report shape can read the other's immediately. The
overlap stops at "we both hash files and speak the same JSON dialect."
Nothing in `bundle_index`'s output is a substitute for
`evidence-manifest`'s root-hash tamper check, and nothing in
`evidence-manifest`'s output tells a reviewer whether a bundle has a
usable rerun block, a duplicate file, or a stray `__pycache__` directory.
A submission bundle can reasonably be run through both: `bundle_index`
first (is this reviewable at all?), `evidence-manifest` second (has this
specific, now-indexed content been tampered with since?).

---

## Usage

```
python3 bundle_index.py <bundle_dir> [-o PATH]
```

| Argument | Meaning |
| --- | --- |
| `bundle_dir` | path to the directory to index |
| `-o`, `--output PATH` | also write the canonical JSON report to `PATH` (parent directories are created); the identical bytes are always printed to stdout regardless |

**Do not point `-o` inside the directory you are indexing.** The report
file did not exist when the bundle was walked, but it will exist for the
*next* run -- which will then index the previous run's own report as an
extra bundle file and produce a different result. This is exactly why
the verification commands below write `r1.json`/`r2.json` as siblings of
`bundle_bad/`, never inside it. (This surfaced as a genuine failing test
while building this tool -- see "A real bug" below.)

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | index built, `finding_count == 0` -- bundle is clean |
| `1` | index built, one or more findings -- see the `findings` array |
| `2` | invalid input/usage: `bundle_dir` does not exist, is not a directory, argparse usage error (e.g. missing argument), or `-o` could not be written. Nothing was indexed, so no verdict (0/1) exists. |

## Report shape

```
{"exit_code":...,"file_count":...,"files":[...],"finding_count":...,
 "findings":[...],"rerun_command":{...},"schema_version":1,"status":...,
 "tool":"bundle_index","tool_version":"1.0.0"}
```

Serialization is `json.dumps(report, sort_keys=True,
separators=(",",":"), ensure_ascii=True)` plus a single trailing `\n`,
written as raw bytes (`sys.stdout.buffer.write`) so no platform line-ending
translation can touch it. `files` is sorted by `relative_path`; `findings`
is explicitly sorted by `(code, paths)` -- `sort_keys` only orders object
keys, not array elements, so both arrays are sorted by the tool itself,
not left to incidental filesystem walk order.

Each entry in `files`:

| Field | Meaning |
| --- | --- |
| `relative_path` | POSIX-style (`/`-separated) path relative to `bundle_dir`, never absolute |
| `sha256` | hex digest of the raw file bytes, or `null` if the file was unreadable |
| `size_bytes` | byte count, or `null` if unreadable |
| `line_count` | see "Line count rule" below; `null` for binary or unreadable files |
| `detected_type` | see "Detected type rule" below |

## No absolute paths, ever

Every path in the report -- `files[].relative_path`, every `findings[].paths`
entry, the implicit "README.md" reference -- is computed with
`os.path.relpath(file, bundle_root)` and is relative to the indexed
directory. The tool never calls `.resolve()` or `os.path.abspath()` on a
discovered file and never embeds `bundle_dir` (as given or resolved) in
the JSON body. This is asserted directly by
`TestDeterminismContract.test_report_contains_no_absolute_path_of_root`
and `test_report_has_no_common_absolute_prefixes`, and demonstrated twice
more in `captured_output.txt`: the `grep -c "/sessions\|/tmp\|/home"`
check against a real report (must print `0`), and the relocation test
(copy the bundle to an unrelated absolute path, re-index, get a
byte-identical report).

The one place an absolute path *can* legitimately appear is an exit-2
error message on stderr echoing back the path argument exactly as the
user typed it (e.g. `bundle directory '/nonexistent_dir' does not
exist`) -- that string is the user's own CLI input, not something the
tool derived, and it never enters the JSON report because exit-2 runs
produce no report at all.

## No mtime, no wall-clock, no host identity

The report contains no timestamps, no file modification/access/creation
times, no hostname, and no file ownership (uid/gid/owner). This is
deliberate, not an oversight: **mtime is the single most tempting field
to add to a file index**, and it is also the single field that would
silently and completely destroy the determinism claim -- `git clone`,
`cp`, `tar`, and every zip tool routinely produce different mtimes for
byte-identical content, so an index that includes it would differ between
two checkouts of the exact same bundle. Two runs of `bundle_index` over
the same bytes, on different machines, years apart, must produce the same
report; timestamps are the one field guaranteed to break that, so they
are not there.

## Detected type rule

Classification happens in two stages:

1. **Content sniffing decides binary vs. text.** A file is `binary` if
   its bytes contain a `0x00` byte, or if the bytes fail strict UTF-8
   decoding. The NUL check is decisive and comes first: a lone `0x00`
   byte is technically legal UTF-8 (it decodes to U+0000) but essentially
   never appears in a genuine text file, so its presence is treated as a
   binary signal even when the rest of the file would otherwise decode.
2. **Extension refines the label, but only for files already classified
   as text.** `.py` -> `python`, `.json` -> `json`, `.md`/`.markdown` ->
   `markdown`, `.sh`/`.bash` -> `shell`, `.yml`/`.yaml` -> `yaml`, `.csv`/
   `.tsv` -> `csv`, `.html`/`.htm` -> `html`, `.css` -> `css`, `.js` ->
   `javascript`, `.ts` -> `typescript`, `.toml` -> `toml`, `.ini`/`.cfg`
   -> `ini`, `.xml` -> `xml`, `.rst` -> `restructuredtext`, `.c` -> `c`,
   `.h` -> `c-header`, `.cpp`/`.hpp` -> `cpp`/`cpp-header`, `.java` ->
   `java`, `.go` -> `go`, `.rs` -> `rust`, `.sql` -> `sql`, `.txt`/`.log`/
   anything unrecognised/no extension -> `text`.

A file classified as `binary` is never given an extension-based label
(a `.pyc` is `binary`, not `python`) -- the sniffed content, not the
name, is authoritative for the binary/text axis.

**Zero-byte files get their own type, `empty`**, rather than being
guessed as `text` (an empty byte string trivially decodes as UTF-8 and
contains no NUL, so it would sniff as text by the rule above) or
`binary`. There is no content to classify, so the type says that plainly
instead of implying a guess. `size_bytes` and `line_count` are `0`;
`sha256` is still the real digest of the empty string
(`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`),
which is why two independently-created empty files always collide as
`DUPLICATE_CONTENT`.

A file that could not be opened or read gets `detected_type:"unreadable"`,
and `sha256`/`size_bytes`/`line_count` are all `null` -- there was no
content to hash, size, or count lines in.

## Line count rule (and the off-by-one trap)

```
line_count = len(file_bytes.splitlines())
```

`bytes.splitlines()` splits on `\n`, `\r`, `\r\n` (as one terminator, not
two), and a handful of rarer control-character terminators
(`\v`/`0x0b`, `\f`/`0x0c`, `0x1c`, `0x1d`, `0x1e`); it does not add a
phantom empty final entry for a file that ends with a terminator.

This is deliberately **not** `file_bytes.count(b"\n")` (effectively what
`wc -l` reports), because that undercounts by one for any file whose last
line has no trailing newline -- the final, real, non-empty line simply
never gets counted. `count_lines` was written and tested against this
trap directly:

| Input bytes | `count(b"\n")` (`wc -l`-style) | `splitlines()` length (this tool) |
| --- | --- | --- |
| `b"one\ntwo\nthree"` (no trailing `\n`) | 2 -- **wrong**, drops "three" | **3** -- correct |
| `b"a\n"` | 1 | 1 |
| `b"\n"` (exactly one newline) | 1 | **1** -- one blank line, not zero |
| `b""` | 0 | 0 |

`line_count` is `null` (not `0`) for every file classified as `binary`.
A binary file has no meaningful notion of "lines" -- counting `\n` bytes
in an image or a `.pyc` would produce a number that looks like data but
means nothing, and `0` specifically would be actively misleading (it
reads as "this is an empty-ish file," which a multi-kilobyte PNG is not).
`null` says, correctly, "this question does not apply here."

## Rerun-command extraction rule

Read `README.md` from the bundle root (exact filename, exact
case, top level only -- `readme.md`, `README.MD`, or `docs/README.md` all
count as **no README** for this purpose) as UTF-8 text, then:

1. Scan every fenced code block (```` ``` ```` ... ```` ``` ````) in
   document order. The **first** block whose info-string, stripped and
   lowercased, is exactly `bash`, `sh`, or `console` is the rerun block.
   (Deliberately narrow set, matching the task brief's literal wording;
   `shell`/`zsh`/etc. are not included even though they're common
   aliases -- see Limitations.)
2. If no block matched by language, scan for a heading line
   (`#` through `######`) whose text matches `/rerun|reproduce|commands/i`
   anywhere in it, in document order. For the **first** such heading, the
   **first** fenced block that *starts* after that heading ends is the
   rerun block, regardless of its language tag (including no tag at all).
3. If neither rule finds a block, there is no rerun-command block.

Case 3 -- and the "README.md exists but is unreadable/not valid UTF-8"
case -- both produce a `NO_RERUN_BLOCK` finding and
`"rerun_command":{"found":false,"language":null,"text":null}`; the field
is never a silently-empty string standing in for "nothing here." If
`README.md` does not exist at all, both `MISSING_README` and
`NO_RERUN_BLOCK` are reported (the second is strictly implied by the
first, but is reported explicitly rather than folded away, since
`NO_RERUN_BLOCK` should always mean the same thing regardless of why the
block is missing).

The extracted `text` is the fenced block's interior verbatim, with no
further stripping or reformatting. Because this content is a faithful
copy of whatever the bundle author wrote, it is **not** filtered for
absolute paths the way the tool's own generated fields are -- if a
submission's own rerun block contains a hardcoded absolute path, that is
evidence about the submission, and hiding it would defeat the point of
extracting it verbatim.

## Finding codes

| Code | Trigger |
| --- | --- |
| `MISSING_README` | no `README.md` at the bundle root |
| `NO_RERUN_BLOCK` | no qualifying fenced block found (see extraction rule); always accompanies `MISSING_README` |
| `EMPTY_FILE` | a file is exactly zero bytes |
| `UNREADABLE_FILE` | opening/reading the file raised `OSError` (permission denied, dangling symlink, race-condition removal, etc.) |
| `DUPLICATE_CONTENT` | two or more files share the same SHA-256; one finding per group, `paths` lists every member |
| `SUSPICIOUS_ARTIFACT` | a path component is `__pycache__` or `.git`, the filename is `.DS_Store`, or the filename ends in `.pyc` -- one finding *per matched reason per file*, so a `__pycache__/x.pyc` file produces two `SUSPICIOUS_ARTIFACT` entries (directory reason and suffix reason), not one |

Findings are sorted by `(code, paths)` so the array order never depends
on filesystem walk order.

---

## The fixtures

* `bundle_ok/` -- one README (with a `bash`-tagged rerun block under a
  "## Rerun commands" heading), one `.py`, one `.json`, one `.txt`.
  Indexes clean: `finding_count:0`, exit `0`.
* `bundle_bad/` -- deliberately hits all six finding codes at once:
  * no `README.md` at all -> `MISSING_README` + `NO_RERUN_BLOCK`
  * `empty.txt` and `zero.bin`, both zero bytes -> two `EMPTY_FILE` findings
  * `empty.txt` and `zero.bin` are also byte-identical (both empty) ->
    `DUPLICATE_CONTENT`
  * `src/main.py` and `src/main_copy.py` are byte-identical on purpose ->
    a second `DUPLICATE_CONTENT`
  * `image.bin` -- real binary content (PNG-style magic bytes + a
    high-byte run that is not valid UTF-8) -> `detected_type:"binary"`,
    `line_count:null`
  * `__pycache__/main.cpython-310.pyc`, `.DS_Store`, `.git/HEAD`,
    `.git/config` -> five `SUSPICIOUS_ARTIFACT` findings
  * `broken_link.txt` -- a symlink to a target that does not exist ->
    `UNREADABLE_FILE`

  12 findings total, exit `1`. Full breakdown with real paths and hashes
  is in `captured_output.txt`.

## A real bug (well: a real, reproducible footgun)

While writing the CLI-level test for "`-o` output is byte-identical
across two runs," the test wrote `r1.json` and `r2.json` **inside** the
temp bundle directory it was indexing. The test failed: the first run's
report differed from the second's, because by the time the second run
started, `r1.json` existed as a brand-new file *inside the very
directory being indexed* -- it got hashed, sized, and counted as bundle
content that the first run never saw. The tool was not wrong: given a
directory whose contents genuinely changed between two invocations, two
different reports is the *correct*, determinism-preserving answer. The
bug was in the test's setup, not in `bundle_index.py`'s logic, so the fix
was to move `r1.json`/`r2.json` to a directory outside the bundle (see
`TestCliExitCodes.test_two_runs_produce_byte_identical_output_files` in
`test_bundle_index.py`), matching exactly what the verification commands
below already do for `bundle_bad`. It's flagged here anyway because it's
a genuine way to accidentally corrupt a "two runs are identical" proof:
point `-o` at the bundle you're indexing and the tool will faithfully
report that the bundle changed, because it did.

## Limitations a reviewer should scrutinise

1. **The rerun-block heading fallback is not markdown-structure-aware.**
   `RERUN_HEADING_RE` matches any line starting with `#`, anywhere in the
   raw text, including inside a fenced code block. A README containing a
   code sample that itself shows a fake `## Rerun` heading (e.g.
   documenting *how the tool detects headings*) could, in a contrived
   case with no real `bash`/`sh`/`console`-tagged block anywhere, cause
   the wrong or no block to be selected. In practice this is defused by
   rule 1 (language-tag match) running first and matching almost every
   real-world case; it's a real gap only for a README with no
   correctly-tagged block at all.
2. **The rerun-block language allowlist is deliberately narrow** (`bash`,
   `sh`, `console` -- exactly the task brief's wording). `shell`, `zsh`,
   `bash session`, `terminal`, and similar common aliases are *not*
   matched by rule 1 and fall through to the heading-based rule 2, which
   requires a `rerun`/`reproduce`/`commands` heading to be present. A
   README that fences its commands as ```` ```shell ```` under a
   "## Usage" heading (no "rerun"/"reproduce"/"commands" wording) would
   get `NO_RERUN_BLOCK` even though a human reader would clearly find the
   commands. This is a judgement call, not an oversight -- the fix is a
   one-line set update if a reviewer wants `shell` included.
3. **`SUSPICIOUS_ARTIFACT` walks into `.git` and `__pycache__` and hashes
   every file it finds inside them**, rather than treating the directory
   as an opaque single artifact. A bundle with a real, large `.git`
   history would produce one `SUSPICIOUS_ARTIFACT` finding per object
   file inside `.git/objects/...` (each also fully read and SHA-256'd),
   which is correct but potentially very noisy and slow compared to a
   design that flags `.git/` once and skips descending into it. The
   fixture `bundle_bad/.git/` is intentionally tiny (two files) so this
   doesn't show up in the demo, but a reviewer pointing this tool at a
   real accidentally-committed `.git` directory should expect a large,
   repetitive `findings` array, not a single clean signal.

Two structural limitations noted for completeness, not novel: like the
sibling `evidence-harness`, this tool does not descend into symlinked
directories (`os.walk(followlinks=False)`), so a symlink pointing at a
directory is invisible to the index rather than being flagged or
followed; and an empty directory (no files, however deeply nested)
contributes nothing to the index and produces no finding, since the tool
is entirely file-oriented.

## Repository layout

```
bundle_index.py         the tool
test_bundle_index.py    170 unit tests
bundle_ok/               fixture bundle that indexes clean (exit 0)
bundle_bad/              fixture bundle that triggers all six finding codes (exit 1)
captured_output.txt      verbatim transcript of the verification + relocation commands
README.md               this file
```
