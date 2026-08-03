# Evidence bundle: `sum_lines.py` (GOOD fixture)

This is a deliberately *complete* evidence bundle. It is the fixture the
Evidence Verification Harness is expected to pass.

## Contents

| File | Purpose |
| --- | --- |
| `sum_lines.py` | the artefact under review |
| `test_sum_lines.py` | its unit tests (12 tests) |
| `verification.txt` | verbatim captured output of every command below |
| `README.md` | this file |

## Commands run

```
$ cd bundle_good
$ python3 -m unittest discover -v
$ printf '1\n2\n3\n' | python3 sum_lines.py
$ echo '{"artefact":"sum_lines.py","tests":12}' | python3 -m json.tool
$ sha256sum sum_lines.py test_sum_lines.py
```

Every command in `verification.txt` is followed by a visible `exit=$?` line.

## Results

* `Ran 12 tests in 0.000s` / `OK`, `exit=0`
* smoke run printed `6`, `exit=0`
* `python3 -m json.tool` reformatted the payload, `exit=0`
* sha256 digests of both artefacts are recorded in `verification.txt`

## Digests

```
078fd1389f695e00c7a0fb03e8fac36c5a0a4953b93b9cfa393941e1f8ef1d5d  sum_lines.py
61d8f294247ffd26d7d477a0156d778efab30466493b28d4307cfca3ea746a67  test_sum_lines.py
```
