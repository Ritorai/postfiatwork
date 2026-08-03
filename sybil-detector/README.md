# Configurable Sybil Wallet-Cluster Detector

Stdlib-only Python 3. No third-party packages, no network.

## Exact rerun commands

```
python3 -m unittest test_sybil_detect -v
python3 sybil_detect.py submissions_clean.json -o report_clean.json      ; echo "exit=$?"
python3 sybil_detect.py submissions_sybil.json -o report_sybil_run1.json ; echo "exit=$?"
python3 sybil_detect.py submissions_sybil.json -o report_sybil_run2.json ; echo "exit=$?"
sha256sum report_sybil_run1.json report_sybil_run2.json
cmp report_sybil_run1.json report_sybil_run2.json && echo BYTE-IDENTICAL
python3 sybil_detect.py submissions_sybil.json -c config_strict.json -o report_strict.json ; echo "exit=$?"
python3 sybil_detect.py submissions_sybil.json --alert-threshold 1.5 ; echo "exit=$?"
python3 sybil_detect.py /nonexistent.json ; echo "exit=$?"
```

## Expected results

| step | result |
|------|--------|
| tests | `Ran 29 tests` / `OK` |
| clean fixture | `status=clear clusters=0 alerting=0`, exit **0** |
| sybil fixture (both runs) | `status=alert clusters=1 alerting=1`, exit **1** |
| both reports SHA-256 | `5dac57123e9f654668115f7da7299c1afdf2176c472710069984b0b047fa1a51` |
| `cmp` | BYTE-IDENTICAL |
| strict config | still `alert`, exit **1** |
| `--alert-threshold 1.5` | `clear`, exit **0** |
| missing file | `INVALID_INPUT`, exit **2** |

## Signals

| signal | fires when | default weight |
|--------|-----------|----------------|
| `shared_cid` | two wallets submitted the same evidence CID | 0.6 |
| `length_match` | evidence lengths within `--length-tolerance` (relative) | 0.2 |
| `burst_timing` | submissions within `--burst-window` seconds | 0.2 |

Pair score = sum of firing signal weights. Wallets are clustered by union-find
over pairs scoring >= `--link-threshold` (default 0.5). Cluster score = the
maximum pair score inside it. A cluster alerts at >= `--alert-threshold` (0.8).

## Configurability

Everything is tunable, by JSON config file (`-c`) or CLI flag. CLI beats file,
file beats defaults. Unknown config keys and unknown signal names are rejected
with exit 2 rather than silently ignored, so a typo in a weight name cannot
quietly disable a signal.

## What the fixture demonstrates

`submissions_sybil.json` contains two deliberately different situations:

- **rSyb1 / rSyb2 / rSyb3** — same CID, lengths 2000/2010/1995, all within ~2.5
  minutes. All three signals fire, pair score 1.0, they merge into one cluster
  and it alerts.
- **rTwinA / rTwinB** — similar lengths (5000/5050) and close in time, but
  *different* CIDs. Score 0.4, below the 0.5 link threshold, so they are
  deliberately **not** clustered. This is the intended behaviour: timing plus
  length alone is weak evidence and should not brand two wallets as coordinated.
- **rLoner** — isolated, never clustered.

Raising `--alert-threshold` above 1.0 clears the alert, and the strict config
(higher CID weight, tighter tolerances) still catches the real cluster. Both
directions are exercised in the captured output.

## Flags

| flag | description |
|------|-------------|
| `records` (positional) | Path to a JSON array of evidence submission records. Required. |
| `-c`, `--config PATH` | Path to a JSON config file overriding any of `weights` (object keyed by signal name: `shared_cid`, `length_match`, `burst_timing`), `length_tolerance`, `burst_window`, `link_threshold`, `alert_threshold`. Unknown top-level keys, and unknown signal names inside `weights`, are rejected (`INVALID_INPUT`, exit 2). Optional. |
| `-o`, `--out PATH` | Write the canonical JSON report to this file instead of stdout. When set, stdout instead gets a one-line summary: `status=<status> clusters=<n> alerting=<n>`. |
| `--length-tolerance N` | Overrides `length_tolerance` (default `0.05`), the relative evidence-length difference within which the `length_match` signal fires. Parsed as `float`. |
| `--burst-window N` | Overrides `burst_window` (default `300` seconds), the submission-timestamp gap within which the `burst_timing` signal fires. Parsed as `float`. |
| `--link-threshold N` | Overrides `link_threshold` (default `0.5`), the minimum pair score for two wallets to be linked into the same cluster. Parsed as `float`. |
| `--alert-threshold N` | Overrides `alert_threshold` (default `0.8`), the minimum cluster score for a cluster to alert. Parsed as `float`. |

Precedence: CLI flag > `--config` file value > built-in default (same rule as `evidence-scorer`).

## Exit codes

0 = no cluster at/above alert threshold · 1 = alert · 2 = invalid input
