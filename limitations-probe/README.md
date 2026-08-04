# limitations-probe

`evidence-manifest`, `reward-reconciler` and `schema-checker` were the three
tools in this repository that disclosed **no limitations at all**, while every
comparably-sized tool here discloses several. That is not evidence they have
none; it is an absence of evidence either way.

This directory is the run that settled it. `probe.py` builds twelve
adversarial inputs from scratch, invokes each tool exactly as its own README
documents, and records what actually happened. Every Limitations section added
to those three READMEs points back at a probe id here.

## Requirements

Python 3 standard library only: `argparse`, `json`, `os`, `subprocess`, `sys`,
`tempfile`. No third-party packages, no network. `node` and `jq` appear in
`captured_output.txt` only, for the EM-3 cross-check, and are not needed to
run the probes.

## Usage

```
python3 limitations-probe/probe.py                       # human transcript
python3 limitations-probe/probe.py -o probe_report.json  # canonical JSON
```

| Exit | Meaning |
|---|---|
| `0` | every probe reproduced its recorded outcome |
| `1` | at least one probe did **not** reproduce |
| `2` | setup error — a sibling tool is missing, so nothing was proved |

**Exit `1` is the interesting one.** A probe that stops reproducing means the
finding it documents was fixed, or the tool moved in some other direction. In
either case the matching README section is now wrong. That is the reason this
is a committed, runnable harness rather than a paragraph describing a run I
did once: a reviewer re-derives every claim with one command, and the repo
notices when a claim goes stale.

## What it found

12 probes, **12 reproduced**. Nine are limitations; three are negative results
kept deliberately.

| Probe | Tool | Finding |
|---|---|---|
| EM-1 | evidence-manifest | four evidence strings differing only in whitespace share one batch root |
| EM-2 | evidence-manifest | `" S1 "` and `"S1"` are the same `submission_id` |
| EM-3 | evidence-manifest | a bare `NaN` survives into the manifest; strict RFC 8259 readers reject the file |
| EM-4 | evidence-manifest | duplicate `submission_id`s are accepted silently |
| RR-1 | reward-reconciler | a discrepancy below 6 dp is quantized away and reported `balanced` |
| RR-2 | reward-reconciler | `"1E+999999999"` raises an uncaught exception and exits `1`, not `2` |
| RR-3 | reward-reconciler | negative amounts reconcile cleanly; no sign or range check |
| RR-4 | reward-reconciler | a split payout to the **wrong wallet** is reported without ever naming that wallet |
| SC-1 | schema-checker | `pattern` is a search, not a full match: `[0-9]{4}` accepts `XX1234XX` |
| SC-2 | schema-checker | `max_length: 4` accepts 16 UTF-8 bytes |
| SC-3 | schema-checker | a schema-supplied pattern can run forever; no match-time bound |
| SC-4 | schema-checker | **negative result** — `integer` correctly rejects `true`, `5.0` and `1e400` |

The two most serious are **RR-4** and **SC-3**. RR-4 is a defect, not a
trade-off: when a task's payout is split across records paid to a different
wallet than expected, the report emits `DUPLICATE_PAYOUT` naming the
*expected* wallet, and the string that was actually paid never appears
anywhere in the output. SC-3 lets one line of an untrusted schema hang the
checker indefinitely.

## One claim I got wrong, and corrected

My first draft of EM-3 asserted that `jq` rejects a manifest containing a bare
`NaN`. **It does not.** `jq` 1.7 reads the root back without complaint. So
does Python's default parser. Node 22's `JSON.parse` and Python with a raising
`parse_constant` both reject it.

All four runs are in `captured_output.txt`, and the corrected, narrower claim
is what stands in `evidence-manifest/README.md`. Recording the contradiction
is cheaper than shipping a confident sentence that a reviewer can falsify in
ten seconds.

## Determinism

The JSON report contains no timestamps and no durations; two runs on unchanged
code produce byte-identical output, checked by `cmp` in `captured_output.txt`.

## 3 limitations of this harness itself

1. **A probe proves a behaviour exists; it does not measure how often it
   bites.** EM-1 shows whitespace-differing evidence collides. It says nothing
   about whether any real batch in this repository contains such a pair — I
   did not check, because I have no corpus of real submissions here.

2. **SC-3's timeout belongs to the harness, not to the tool.** `probe.py`
   kills the run after 20 seconds so the suite terminates. The tool has no
   such bound; "20 seconds" is an artefact of this file and must not be read
   as a property of `schema_check.py`. A slower machine changes that number
   and nothing else.

3. **Twelve probes is not an audit.** These are the cases I thought to try
   against three tools in one sitting. The set is biased toward
   canonicalization, numeric edge cases and regex behaviour because that is
   where this repository's other tools have historically been wrong. Areas I
   did not probe at all: concurrency, file-descriptor and memory limits,
   locale, `PYTHONHASHSEED`, and anything requiring inputs larger than a few
   kilobytes.
