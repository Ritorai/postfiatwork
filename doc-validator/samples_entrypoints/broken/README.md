# entry.py -- DOC009 broken fixture

Three of the four command lines below cannot start, each for a different
reason, and `docval.py` must report **three**
`DOC009_BROKEN_ENTRYPOINT` findings for this directory.

1. `bash capture.sh` -- there is no `capture.sh` here. Before DOC009 this
   line and a correct `bash capture.sh` produced the same single finding,
   `DOC006 ... refused: not a python3 invocation (got 'bash')`.
2. `python3 -m unittest test_entry` -- there is no `test_entry.py` here.
   Same story: refused as a module invocation, existence never checked.
3. `./run.sh` -- `run.sh` **is** here, but every file this repository can
   commit lands at mode `100644`, so the shell cannot execute it.
4. `python3 entry.py --check` -- correct, and must stay silent. A check
   that flagged all four lines would prove nothing.

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
python3 -m unittest test_entry
./run.sh
python3 entry.py --check
```

## Flags

- `--check` -- do nothing, successfully.

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | fixture ran |
| `2` | argparse usage error |
