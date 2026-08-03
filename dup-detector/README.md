# dupdetect

A standalone, **standard-library-only** Python 3 CLI that reads JSON evidence
records and detects lightly reworded submissions using **Jaccard similarity
over configurable token k-gram shingles**. It emits canonical, byte-stable
JSON and returns a non-zero exit code when any pair is flagged.

No third-party packages, no network access, no configuration files.
Tested on Python 3.10.

---

## 1. Quick start

```bash
python3 dupdetect.py records_dupes.json                 # report to stdout
python3 dupdetect.py records_dupes.json -o report.json  # report to a file
```

### CLI

```
usage: dupdetect.py [-h] [-o OUT] [--shingle-size SHINGLE_SIZE]
                    [--threshold THRESHOLD] [--version]
                    records.json
```

| Option | Default | Meaning |
| --- | --- | --- |
| `records.json` | (required) | Path to a JSON array of evidence records. |
| `-o`, `--out` | stdout | Write the report here. A one-line summary goes to stderr. |
| `--shingle-size N` | `5` | Tokens per shingle (k). Must be `>= 1`. |
| `--threshold F` | `0.6` | Flag a pair when `score >= F`. Must be in `[0.0, 1.0]`. |
| `--version` | – | Print the version and exit 0. |

Report bytes always go to exactly one destination: stdout **or** the `--out`
file, never both. The bytes are identical either way.

---

## 2. Exact rerun commands

Run these from the directory containing the files. The recorded console
transcript for these exact commands is in `captured_output.txt`.

```bash
# 1. Test suite (110 tests)
python3 -m unittest test_dupdetect -v

# 2. Clean corpus -> nothing flagged, exit 0
python3 dupdetect.py records_clean.json
echo "exit=$?"

# 3. Duplicate corpus, run twice to two files -> exit 1 both times
python3 dupdetect.py records_dupes.json -o report_run1.json
echo "exit=$?"
python3 dupdetect.py records_dupes.json -o report_run2.json
echo "exit=$?"

# 4. Prove the two reports are byte-identical
sha256sum report_run1.json report_run2.json
cmp report_run1.json report_run2.json && echo "IDENTICAL"

# 5. Raising the threshold changes the verdict
python3 dupdetect.py records_dupes.json --threshold 0.99
echo "exit=$?"

# 6. Nonexistent input -> exit 2
python3 dupdetect.py no_such_file.json
echo "exit=$?"
```

### Expected results

| Command | Records | Flagged pairs | Exit |
| --- | --- | --- | --- |
| `dupdetect.py records_clean.json` | 5 | 0 | **0** |
| `dupdetect.py records_dupes.json` | 5 | 2 | **1** |
| `dupdetect.py records_dupes.json --threshold 0.99` | 5 | 1 | **1** |
| `dupdetect.py records_dupes.json --shingle-size 9` | 5 | 2 | **1** |
| `dupdetect.py no_such_file.json` | – | – | **2** |
| `python3 -m unittest test_dupdetect` | – | 110 tests, all pass | **0** |

Pairs found in `records_dupes.json` at the defaults (k=5, threshold=0.6):

| Pair | Score | Overlapping shingles | Nature |
| --- | --- | --- | --- |
| `SUB-1001` / `SUB-1002` | `1.0` | 81 | Byte-for-byte identical text |
| `SUB-1003` / `SUB-1005` | `0.777778` | 70 | Two words swapped (`coordinator`→`manager`, `response`→`reply`) |

`SUB-1004` is an unrelated calibration record and is never flagged.

Both runs of the duplicate corpus produce a byte-identical 5019-byte report:

```
45aa1f80d3304103b703fa7527ff58b3f9c313f03b5bd41e4ec3cbfa3a879081  report_run1.json
45aa1f80d3304103b703fa7527ff58b3f9c313f03b5bd41e4ec3cbfa3a879081  report_run2.json
```

(SHA-256 as recorded in `captured_output.txt`. The digest covers the report
for `records_dupes.json` at the default k=5 / threshold 0.6.)

---

## 3. Input record format

The input file must be **UTF-8** and must decode to a **JSON array**. Each
element must be a **JSON object** with at least these two fields:

| Field | Type | Constraint |
| --- | --- | --- |
| `submission_id` | string | Non-empty after stripping whitespace; unique across the file. |
| `text` | string | Any string, including `""`. Must not be `null` or a number. |

Any other fields (`author`, `submitted_at`, …) are **ignored**, not rejected.

```json
[
  {"submission_id": "SUB-1001", "text": "The complainant states that ..."},
  {"submission_id": "SUB-1002", "text": "The complainant states that ..."}
]
```

An empty array `[]` is valid input and produces a report with zero records
and exit code 0.

Anything else — a top-level object, a non-object element, a missing or
non-string `submission_id`/`text`, an empty `submission_id`, a repeated
`submission_id`, malformed JSON, a missing file, a directory path, or
non-UTF-8 bytes — is **invalid input** and exits **2** with a message on
stderr naming the offending record index.

---

## 4. Token normalization

`text` is converted to a token list by these rules, in order:

1. **Unicode NFKC normalization** — compatibility forms are folded, so
   fullwidth `ＡＢ` and ASCII `AB` compare equal.
2. **Lowercase** via `str.lower()`.
3. **Split on whitespace** using `str.split()`, which splits on any run of
   whitespace and discards leading/trailing whitespace. This is the
   "collapse whitespace" step: `"a   b"` and `"a b"` tokenize identically.
4. **Strip non-alphanumeric characters from both edges** of each token.
   A character counts as alphanumeric if `str.isalnum()` is true, so this is
   Unicode-aware rather than ASCII-only.
5. **Drop tokens that are empty** after stripping (e.g. a bare `---`).

Consequences worth knowing:

- **Interior** punctuation is preserved: `don't` and `well-known` stay whole.
  Only the edges are stripped.
- Trailing punctuation is invisible to the comparison, so `"The end."` and
  `"the END!"` produce identical tokens.
- `"Section 12(b)."` tokenizes to `["section", "12(b"]` — the trailing `)` and
  `.` are stripped but the interior `(` survives. This is a deliberate
  simplification, not a bug (see judgement calls).
- No stemming, no stop-word removal, no synonym handling. A reworder who
  changes word *order* or *vocabulary* rather than punctuation will score
  lower.

### Shingles

The token list is converted into the **set** of contiguous k-token windows
(`--shingle-size`, default 5), each rendered as the tokens joined by a single
space. A token list of length `n >= k` yields `n - k + 1` windows before
deduplication; repeated windows collapse because the result is a set.

**A token list shorter than k yields the empty set.** Such a record is
uncomparable and can never be flagged.

### Similarity

`score = |A ∩ B| / |A ∪ B|` over the two shingle sets. If either set is
empty the score is defined as **0.0** — including the case where *both* are
empty, which is deliberately **not** treated as a perfect match.

---

## 5. Threshold semantics

- A pair is flagged when **`round(score, 6) >= threshold`**. The comparison
  uses the *rounded* score, so the boundary is exactly what the report shows;
  a pair whose report reads `0.5` is flagged at `--threshold 0.5`.
- **Additionally, a pair with zero overlapping shingles is never flagged**,
  even at `--threshold 0.0`. Without this guard, `--threshold 0.0` would
  accuse every possible pair of duplication.
- `--threshold 1.0` flags only pairs whose rounded score is exactly `1.0`.
- Thresholds outside `[0.0, 1.0]`, or a non-numeric value, exit **2**.

---

## 6. Output format and ordering

The report is a single JSON object:

```json
{"comparison_count":10,"config":{"shingle_size":5,"threshold":0.6},
 "flagged_count":2,"flagged_pairs":[...],"record_count":5,"version":"1.0.0"}
```

| Key | Meaning |
| --- | --- |
| `version` | Tool version that produced the report. |
| `config.shingle_size` | k actually used. |
| `config.threshold` | Threshold actually used, rounded to 6 dp. |
| `record_count` | Number of input records. |
| `comparison_count` | Unordered pairs compared, i.e. `n*(n-1)/2`. |
| `flagged_count` | Length of `flagged_pairs`. |
| `flagged_pairs` | The flagged pairs, ordered as below. |

Each entry of `flagged_pairs`:

| Key | Meaning |
| --- | --- |
| `submission_id_a` | The lexicographically **smaller** id of the pair. |
| `submission_id_b` | The lexicographically **larger** id of the pair. |
| `score` | JSON **number**, rounded to 6 decimal places. |
| `overlapping_shingles` | The shared shingles, **sorted** ascending. |
| `overlap_count` | Length of `overlapping_shingles`. |

Only flagged pairs appear. Sub-threshold pairs are omitted entirely.

### Ordering rules

1. Within a pair, the two ids are sorted lexicographically, so
   `submission_id_a < submission_id_b` always holds.
2. Pairs are sorted by **`(-score, submission_id_a, submission_id_b)`** —
   highest score first, then ids ascending. This is a total order over
   distinct pairs, so the ordering is fully deterministic.
3. `overlapping_shingles` is sorted ascending by the joined string.

Because every list is sorted by content rather than by input order,
**reordering the input records does not change a single output byte.**

### Canonical serialization

```python
json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
```

Object keys are sorted, there is no insignificant whitespace, all non-ASCII
characters are `\uXXXX`-escaped, and the file ends with exactly one `\n`
(written with `newline="\n"`, so no CRLF translation on Windows). Scores are
rounded to 6 decimals before serialization, so the float repr is stable.
Two runs over the same input produce byte-identical output, which is
verified by `cmp` in `captured_output.txt`.

---

## 7. Exit codes

| Code | Meaning |
| --- | --- |
| **0** | Ran successfully; **no** pair scored at or above the threshold. |
| **1** | Ran successfully; **at least one** pair was flagged. The report was still written. |
| **2** | Invalid input: unreadable/missing file, non-UTF-8, malformed JSON, schema violation, duplicate `submission_id`, bad option value, or missing argument. No report is written. |

Exit code 1 means "a human should look at this", not "fraud occurred".

---

## 8. Edge cases, handled explicitly

| Case | Behaviour |
| --- | --- |
| Zero records (`[]`) | Valid. `comparison_count` 0, exit 0. |
| One record | Valid. No pairs to compare, exit 0. |
| Text shorter than the shingle size | Yields no shingles; never flagged. |
| Empty text (`""`) | Yields no tokens, no shingles; never flagged, even against another empty text. |
| Two identical texts | Score exactly `1.0`. |
| Texts differing only in case/punctuation/whitespace | Score exactly `1.0`. |
| Duplicate `submission_id` | **Rejected**, exit 2. |
| Unrelated texts sharing individual words | Zero shared k-grams, so score `0.0`; never flagged. |
| `--shingle-size` larger than every document | All shingle sets empty; nothing flagged, exit 0. |

---

## 9. Judgement calls a reviewer should scrutinise

These are choices where a different, defensible decision would change who
gets flagged. They matter because a flag is an accusation.

1. **Short documents are excluded rather than compared.** A record with fewer
   than k tokens produces no shingles and is never flagged. The alternative —
   treating the whole short token list as one shingle — would let two
   three-word answers like "yes I agree" score `1.0` against each other. For
   short-answer corpora that would generate a flood of false accusations, so
   the tool stays silent instead. The cost is a genuine blind spot: **a
   copied one-line submission is invisible at the default k=5.** Lower
   `--shingle-size` if your corpus is short-form, and be aware that doing so
   raises the false-positive rate sharply.

2. **Both-empty is scored 0.0, not 1.0.** Two records with empty `text` are
   arguably "identical". Reporting them as a perfect-score duplicate pair
   would be technically true and practically defamatory, so `0/0` is defined
   as 0.0.

3. **Zero-overlap pairs are suppressed unconditionally.** Even at
   `--threshold 0.0`, a pair sharing no shingle is not reported. This means
   `--threshold 0.0` does *not* mean "report everything"; it means "report
   anything with any overlap at all".

4. **Common boilerplate is not discounted.** There is no IDF weighting, no
   stop-word removal, and no template subtraction. If every submission in
   your corpus contains the same mandatory 200-word preamble, disclaimer, or
   quoted question, **every pair will share those shingles and scores will be
   inflated corpus-wide** — potentially past 0.6 for documents that share
   nothing original. This is the single most likely source of a false
   accusation with this tool. Strip shared boilerplate before running, or
   raise the threshold, and always read `overlapping_shingles` to check
   whether the overlap is boilerplate or substance.

5. **The default threshold of 0.6 is a convention, not a calibrated value.**
   It was not fitted to any labelled dataset. What counts as "lightly
   reworded" is corpus-dependent; treat 0.6 as a starting point to be tuned
   against known-good and known-bad examples from your own data.

6. **Jaccard is symmetric and length-sensitive.** A short document quoted
   verbatim inside a much longer one scores low, because the union is
   dominated by the longer document's unique shingles. This tool detects
   *whole-document* near-duplication, not partial plagiarism or containment.
   A containment measure (`|A ∩ B| / min(|A|,|B|)`) would catch those and was
   deliberately not used, since the brief specifies Jaccard.

7. **Word order and vocabulary changes defeat it; punctuation changes do
   not.** Because normalization strips edge punctuation and lowercases, a
   reworder who only changes formatting is caught with score `1.0`. Someone
   who reorders clauses or substitutes synonyms every few words can drop
   below the threshold while the substance is unchanged. **A low score is not
   evidence of independence.**

8. **Edge-stripping is crude.** `"12(b)."` becomes `12(b` because only the
   *edges* are stripped and the interior `(` survives. Tokens like `U.S.A.`
   become `u.s.a`. This is consistent across all records, so it does not bias
   any particular pair, but it does mean tokens are not always words.

9. **Duplicate `submission_id` is a hard error, not a warning.** Two records
   sharing an id would make the output ambiguous about which is which, so the
   whole run is rejected. If your pipeline legitimately emits repeated ids,
   you must disambiguate them before running this tool.

10. **All-pairs comparison is O(n²).** Fine for hundreds of records;
    for tens of thousands it will be slow and memory-hungry, since every
    document's full shingle set is held in memory at once. There is no
    MinHash/LSH approximation — exact sets were chosen so scores are exact
    and reproducible rather than probabilistic.

---

## 10. Files

| File | Purpose |
| --- | --- |
| `dupdetect.py` | The CLI and all logic. Stdlib only. |
| `test_dupdetect.py` | 110 unittest tests, including CLI exit codes via `subprocess`. |
| `records_clean.json` | 5 records, no pair at or above threshold (exit 0). |
| `records_dupes.json` | 5 records: an exact duplicate pair, a lightly reworded pair, and an unrelated record (exit 1). |
| `README.md` | This file. |
| `captured_output.txt` | Verbatim console transcript of the runs above, with `exit=$?` shown. |
