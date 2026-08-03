# Weak-Assertion Repair Notes

Scope: the 12 findings (8 WA001_NO_ASSERTION + 4 WA002_CALL_ONLY, spread across
8 unique test methods — 4 of them tripped both categories at once) flagged by
`weakassert.py` in the real tool test suites under `/sessions/sharp-stoic-knuth/mnt/outputs`.
Excluded from scope: `weak-assertion-scanner/samples_weak/test_subject.py` and
`weak-assertion-scanner/demo_category/test_only_no_assertion.py`. Those are the
scanner's *own* fixtures — `test_weakassert.py::test_full_scan_of_samples_weak_directory_trips_all_four`
asserts that scanning `samples_weak/` trips all four WA categories, and the
README documents `demo_category/` as "a minimal fixture used only to
demonstrate `--category`". Fixing them would break the scanner's own test
suite and defeat the fixtures' purpose. See baseline/after reports: these
findings are unchanged, on purpose.

For every entry below: "before" is a paraphrase (none of these tests had a
real assertion — that's what WA001/WA002 mean), "after" is the literal new
assertion(s), and the "why" is a concrete mutation the assertion would catch.

---

## 1. bundle-index/test_bundle_index.py:428 — `TestCanonicalJsonBytes.test_output_is_valid_json`
**Before:** called `bi.canonical_json_bytes(...)` and `json.loads(...)` on the result with a comment "must not raise" — no assertion.
**After:**
```python
original = {"a": [1, 2, {"b": None}]}
out = bi.canonical_json_bytes(original)
parsed = json.loads(out.decode("utf-8"))
self.assertEqual(parsed, original)
```
**Why it would catch a regression:** if `canonical_json_bytes` truncated the payload, dropped a key, mangled the nested dict, or emitted valid-but-wrong JSON (e.g. serialized only part of the structure), `json.loads` would still succeed but `parsed != original` and the test would fail. The old version passed even if the function returned `b"{}\n"` — this one does not.

## 2. claim-checker/test_claimcheck.py:1083 — `TestCanonicalJsonBytes.test_is_pure_ascii_bytes`
**Before:** `out.decode("ascii")  # must not raise UnicodeDecodeError` — decode result discarded, nothing asserted.
**After:**
```python
decoded = out.decode("ascii")
self.assertTrue(all(b < 128 for b in out))
self.assertIn("\\u00e9", decoded)  # é
self.assertIn("\\u00f6", decoded)  # ö
```
**Why it would catch a regression:** the `decode("ascii")` call alone only proves absence of bytes ≥ 0x80 (a mutation that fed the accented text straight through with `ensure_ascii=False` would raise, so that check is real — but it was structured as an unasserted expression, which the scanner correctly treats as `WA002_CALL_ONLY` because a future refactor to `assertRaises`-free code or a silent `try/except` around it would make the check vanish). The added `assertIn` checks pin the *specific* Unicode escape sequences, so a bug that dropped or corrupted a non-ASCII character (e.g. replaced it with `?` or a different escape) would be caught even if the surrounding bytes stayed under 128.

## 3. dup-detector/test_dupdetect.py:468 — `TestCanonicalJson.test_output_is_pure_ascii`
**Before:** `canonical_json(report).encode("ascii")` as a bare expression statement — return value discarded, nothing asserted.
**After:**
```python
text = canonical_json(report)
encoded = text.encode("ascii")
self.assertTrue(all(b < 128 for b in encoded))
self.assertIn("\\u00e9", text)  # é from "café"
```
**Why it would catch a regression:** verified against the live report — the "café" text produces an `overlapping_shingles` entry containing `café café ...`, so the `é` check is grounded in real output, not guessed. A regression that stopped escaping non-ASCII (e.g. `ensure_ascii=False`) would make `encode("ascii")` raise before either assertion even runs; a regression that escaped the character incorrectly (e.g. to `Ã©` from double-encoding) would pass the byte check but fail `assertIn`.

## 4. loop-health/test_loop_health.py:1304 — `TestCLI.test_healthy_fixture_stdout_is_valid_json`
**Before:** `json.loads(result.stdout)` — parsed but never inspected.
**After:**
```python
report = json.loads(result.stdout)
self.assertEqual(report["findings"], [])
self.assertEqual(report["summary"]["total_findings"], 0)
self.assertEqual(set(report["summary"]["counts_by_code"]), set(lh.ALL_CODES))
```
**Why it would catch a regression:** confirmed against `loop_health.py`'s `main()` (exit code 0 iff `total_findings == 0`), so for the "healthy" fixture the findings list must be empty. If a bug caused a spurious finding to appear for a genuinely healthy history (or dropped one of the code buckets from `counts_by_code`), this test would now fail even though the process still exits 0 and prints syntactically valid JSON.

## 5. regression-checker/test_regress.py:689 — `TestBuildReport.test_report_is_json_serializable`
**Before:** `json.dumps(report)  # must not raise`
**After:**
```python
dumped = json.dumps(report)
round_tripped = json.loads(dumped)
self.assertEqual(round_tripped, report)
self.assertEqual(round_tripped["status"], "clean")
self.assertEqual(round_tripped["tools_checked"], 0)
self.assertEqual(round_tripped["results"], [])
```
**Why it would catch a regression:** round-tripping and comparing to the original catches any dict values that are silently coerced or dropped during serialization (e.g. non-string dict keys, NaN/Infinity floats that `json` would emit as invalid literals). The added `status`/`tools_checked`/`results` checks pin the expected shape of an empty-root report — a bug that miscounted tools or defaulted `status` to something other than `"clean"` on an empty root would now be caught (this duplicates part of the coverage in `test_empty_root_empty_baselines`, which is fine — the point here is that the *serialization* path itself is exercised end-to-end, not just the in-memory dict).

## 6. regression-checker/test_regress.py:905 — `TestCLIOutputHandling.test_output_flag_long_form`
**Before:** ran the CLI with `--output`, opened the file, called `json.load(fh)` and discarded the result.
**After:**
```python
self.assertEqual(out.strip(), "")
with open(out1) as fh:
    report = json.load(fh)
self.assertEqual(report["status"], "clean")
self.assertEqual(code, 0)
```
**Why it would catch a regression:** if `--output`/`--long-form` stopped suppressing stdout (writing the report to both places, or to neither), `out.strip() == ""` would fail. If the CLI wrote a malformed or empty report to the file, or exited non-zero for a clean baseline set, the new assertions would catch it — previously the test passed even if the file were `"{}"` or the process exited with an error code.

## 7. snapshot-diff/test_snapdiff.py:144 — `TestJsonify.test_jsonify_output_is_json_serializable`
**Before:** `json.dumps(out)  # must not raise`
**After:**
```python
self.assertEqual(out, {"reward": "123456789012345678.123456789"})
dumped = json.dumps(out)
self.assertEqual(json.loads(dumped), out)
```
**Why it would catch a regression:** this test exists specifically to probe a `Decimal` with 18 integer digits and 9 fractional digits — a magnitude/precision combination a `float` cast would silently corrupt (float64 has ~15-17 significant decimal digits). The old test would still pass if `jsonify` used `float(value)` instead of `str(value)`, since `json.dumps` of a float never raises — it would just be wrong. The new assertion pins the exact string value, so precision loss is caught directly.

## 8. thread-check/test_thread_check.py:1153 — `TestBuildReport.test_report_is_json_serializable`
**Before:** `json.dumps(report)  # must not raise`
**After:**
```python
dumped = json.dumps(report)
round_tripped = json.loads(dumped)
self.assertEqual(round_tripped, report)
self.assertEqual(len(round_tripped["findings"]), total)
self.assertEqual(round_tripped["thread_summaries"][0]["task_id"], "T-1")
```
**Why it would catch a regression:** the round-trip equality catches silent value coercion during serialization; `len(findings) == total` catches a mismatch between the `total` count `build_report` returns and the actual findings list it embeds (a classic "off-by-one in the summary count" bug); the `task_id` check catches the thread summary for the one input record being dropped, mislabeled, or reordered.

---

## Findings map to WA002 as well as WA001

Four of the eight tests above (bundle-index #1, claim-checker #2, dup-detector
#3, regression-checker #5) were double-flagged: WA001 (no `self.assert*`
anywhere in the body) *and* WA002 (the only statement is a bare/discarded
call to a `json.dumps`/`json.loads`/`.decode`/`.encode` builtin — the
scanner's "call-only" heuristic). Adding a real `self.assertEqual`/`assertIn`
after the call clears both categories for the same line in one edit; that is
why the after-report shows 8 WA001 + 3 WA002 remaining (all inside the
scanner's own excluded fixtures) instead of 8 + 4 minus the 4 fixed.
