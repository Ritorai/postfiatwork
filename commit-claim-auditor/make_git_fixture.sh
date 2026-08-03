#!/bin/sh
# Builds a throwaway git repo from ./fixture so claimhist.py's git-provenance
# path can be exercised. Commit SHAs and author dates depend on WHEN and WHERE
# you run this, so the resulting report is deliberately NOT byte-reproducible
# and is not committed as a determinism baseline. See README.md "Git provenance".
set -e
dest="$1"
[ -n "$dest" ] || { echo "usage: sh make_git_fixture.sh <dest-dir>" >&2; exit 2; }
mkdir -p "$dest"
cp -r fixture/. "$dest"/
cd "$dest"
git init -q .
git config user.email fixture@example.invalid
git config user.name "Fixture Author"
git add -A
git commit -q -m "fixture: initial claims"
printf '\nA later edit so blame has two commits.\n' >> README.md
git add -A
git commit -q -m "fixture: touch README so provenance has history"
