# Objective Evidence Quality Scorer

Stdlib-only Python 3. No third-party packages, no network, no model. Every
component is a countable property of the text, so a reviewer can recompute any
score by hand.

## Exact rerun commands

```
python3 -m unittest test_score_evidence -v
python3 score_evidence.py evidence_pass.json  -o report_pass.json       ; echo "exit=$?"
python3 score_evidence.py evidence_mixed.json -o report_mixed_run1.json ; echo "exit=$?"
python3 score_evidence.py evidence_mixed.json -o report_mixed_run2.json ; echo "exit=$?"
sha256sum report_mixed_run1.json report_mixed_run2.json
cmp report_mixed_run1.json report_mixed_run2.json && echo BYTE-IDENTICAL
python3 score_evidence.py evidence_mixed.json -c config_strict.json ; echo "exit=$?"
python3 score_evidence.py evidence_mixed.json --threshold 0.0 ; echo "exit=$?"
python3 score_evidence.py /nonexistent.json ; echo "exit=$?"
```

## Expected results

| step | result |
|------|--------|
| tests | `Ran 39 tests` / `OK` |
| pass fixture | `status=pass passed=2 failed=0`, exit **0** |
| mixed fixture (both runs) | `status=fail passed=1 failed=4`, exit **1** |
| both reports SHA-256 | `07da05086eaca2e6321cca9c97065af621ba1d5db7f076881f0a31aa5bbd7534` |
| `cmp` | BYTE-IDENTICAL |
| `--threshold 0.0` | exit **0** |
| missing file | `INVALID_INPUT`, exit **2** |

## Signals

| signal | measures | default weight |
|--------|----------|----------------|
| `artifacts` | code fences, shell prompts, `exit=N`, 64-hex hashes, CIDs, paths, URLs — saturating at `--artifact-target` | 0.35 |
| `specificity` | share of tokens carrying digits, dots, slashes or identifier shape | 0.25 |
| `length` | characters, saturating at `--target-length` | 0.15 |
| `originality` | 1 − fraction of this record's sentences that appear verbatim in *other* records | 0.25 |

Score = weighted sum. Records below `--threshold` fail; any failure exits 1.

## Scores on the mixed fixture (default config)

```
boiler_a   score=0.0281 pass=False art=0.000 spec=0.000 len=0.188 orig=0.000
boiler_b   score=0.0281 pass=False art=0.000 spec=0.000 len=0.188 orig=0.000
tiny_1     score=0.2509 pass=False art=0.000 spec=0.000 len=0.006 orig=1.000
vague_1    score=0.2776 pass=False art=0.000 spec=0.000 len=0.184 orig=1.000
strong_1   score=0.8013 pass=True  art=1.000 spec=0.270 len=0.891 orig=1.000
```

`boiler_a` and `boiler_b` are the *same* generic text submitted twice, so
originality collapses to 0 for both. That is the specific failure mode this tool
exists to catch.

## A bug found and fixed during development

The first implementation scored the record `"Done."` at **0.50 and passed it**.
Two causes, both now fixed and both pinned by tests:

1. The tokenizer treated the trailing full stop as making `Done.` a "specific"
   token, so a five-character record scored 100% specificity.
   → tokens are now stripped of surrounding punctuation before classification
   (`test_trailing_punctuation_not_specific`).
2. A one-token sample is not 100% specific, it is *unmeasurable*. The ratio is
   now damped by `min(1, tokens / MIN_TOKENS_FOR_CONFIDENCE)` with
   `MIN_TOKENS_FOR_CONFIDENCE = 20` (`test_tiny_sample_is_damped`,
   `test_full_confidence_at_threshold`).

`tiny_1` now scores 0.2509 and correctly fails.

## Honest limits

This measures *form*, not *truth*. A submission can be dense with hashes, paths
and exit codes and still be wrong or fabricated — the scorer cannot tell. It is
a triage filter for spotting low-effort and copy-pasted submissions, not a
correctness check, and a low score should prompt a human look rather than an
automatic rejection. Conversely a genuinely good short answer can score low.

## Exit codes

0 = all records at/above threshold · 1 = one or more below · 2 = invalid input
