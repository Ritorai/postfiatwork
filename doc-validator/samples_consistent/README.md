# tool.py -- consistent fixture

A tiny, deliberately boring CLI. Counts the `findings` array inside a JSON
report file. Used by docval's own test suite as the "fully agrees with its
docs" fixture: validating this directory must produce zero findings.

## Flags

- `input` (positional) -- path to the input JSON file
- `-o`, `--output` -- write the report here instead of stdout
- `--strict` -- exit 1 if any findings are present

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | ran successfully (no findings, or findings but not `--strict`) |
| `1` | ran successfully and `--strict` was given and findings were present |
| `2` | input error: file missing or not valid JSON |

## Rerun commands

```
python3 tool.py sample_clean.json
python3 tool.py sample_clean.json -o out_clean.json
python3 tool.py sample_findings.json --strict ; echo "exit=$?"
python3 tool.py /nonexistent.json ; echo "exit=$?"
```
