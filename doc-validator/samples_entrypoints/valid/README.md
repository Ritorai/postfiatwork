# entry.py -- DOC009 valid control

Every command line below names a file that is really here. `docval.py`
must report **zero** `DOC009_BROKEN_ENTRYPOINT` findings for this
directory. It is the control half of the pair: without it, a DOC009 that
never fires and a DOC009 that always fires look the same.

Two of these three lines are still refused by the command safety gate
(`bash` is not `python3`; `-m` is a module invocation, not a file), so
`DOC006_COMMAND_BLOCK_UNPARSEABLE` findings are expected here and are not
a defect. That is exactly the point of the new check: a refusal says only
"docval will not run this", and it said the same thing whether or not the
file existed. DOC009 answers the other question.

`DOC002_PHANTOM_FLAG: documents flag '-m'` is also expected here and is
pre-existing behaviour unrelated to this fixture: DOC002 scans the README
for flag-shaped tokens, and the `-m` inside `python3 -m unittest` looks
like one. (This paragraph deliberately spells out no other flag: an
example flag written here would itself become a second DOC002 finding,
which is a neat demonstration of why that code needs a human.) It is listed in the tool's own Limitations as the highest
false-positive code. The fixture is asserted on its DOC009 count, not on
its total.

```bash
bash capture.sh
python3 -m unittest test_entry -v
python3 entry.py --check
```

## Flags

- `--check` -- do nothing, successfully.

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | fixture ran |
| `2` | argparse usage error |
