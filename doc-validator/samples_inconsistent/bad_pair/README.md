# tool.py -- deliberately inconsistent fixture

This pair exists purely to trip every DOC00x finding code docval knows
about. Nothing here is meant to look like good documentation.

## Flags

- `input` (positional) -- path to an input file
- `-o` -- write the report here instead of stdout (its long form is never
  mentioned anywhere in this file, on purpose)
- `--config` -- path to a config file (this flag does not exist in `tool.py`
  at all -- argparse never defines it)
- `-x` -- another flag that does not exist

This README never mentions the other two options `tool.py` actually
defines via argparse (deliberately, to trip DOC001_UNDOCUMENTED_FLAG).

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | success |
| `9` | catastrophic failure (this code can never actually happen) |

## Rerun commands

```
python3 tool.py x.json -o report.json
python3 tool.py x.json --secret-flag boom ; echo "exit=$?"
python3 missing_tool.py x.json ; echo "exit=$?"
python3 -m tool x.json
echo hi | python3 tool.py x.json
```
