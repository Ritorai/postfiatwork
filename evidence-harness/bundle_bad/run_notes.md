# Notes on the `tool.py` review (BAD fixture)

This bundle is deliberately incomplete. It exists so that the Evidence
Verification Harness has something realistic to fail against.

## Known shortcomings

1. There is no top-level `README.md` in this bundle.
2. The test suite was invoked directly rather than through the module runner
   the brief asked for, so the exact command string the brief demands never
   appears anywhere in the captured output.
3. Only `sha1sum` was run, so there are no 256-bit digests to compare against.
4. Only four tests exist; the brief asks for at least ten.

## What was captured

See `partial_log.txt`. It does contain visible exit-status lines, including a
non-zero one, so at least the exit-code requirement is satisfied.
