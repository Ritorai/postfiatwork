#!/usr/bin/env bash
# Present, and committed at mode 100644 like every other file in this
# repository, so `./run.sh` in README.md cannot start: the shell needs the
# executable bit and there is not one. This is the "non-executable"
# half of DOC009, and it is not hypothetical -- see shebang-mode.
set -u
echo "run.sh: fixture"
