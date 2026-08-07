#!/usr/bin/env bash
# Builds the fixture Git repositories this directory's transcript uses.
#
#   fixture_repo.sh clean  <dir>   a repository that SATISFIES the rule
#   fixture_repo.sh break  <dir>   breaks that repository in BOTH directions
#
# The fixtures are built at run time rather than committed, and that is a
# necessity rather than a preference: the whole subject of this tool is the
# executable bit that Git records, and this repository has no committed
# file carrying one. A committed fixture would therefore be unable to
# express the SM001 case at all. `git update-index --chmod=+x` sets the
# index mode directly, which is exactly what the checker reads.
#
# Only paths passed in by the caller are written to, and nothing is
# deleted here -- capture.sh creates the temporary directory and removes
# the same path it created.
set -eu

usage() {
    echo "usage: fixture_repo.sh {clean|break} <dir>" >&2
    exit 2
}

[ $# -eq 2 ] || usage
action=$1
dir=$2

case "$action" in
clean)
    mkdir -p "$dir"
    git -C "$dir" init -q
    # executable AND shebanged: conforms
    printf '#!/usr/bin/env python3\nprint("hi")\n' > "$dir/tool.py"
    # neither executable nor shebanged: conforms
    printf '# a plain document\n' > "$dir/NOTES.md"
    printf 'name,value\na,1\n' > "$dir/data.csv"
    # binary, marked executable, no shebang: must be SKIPPED, not reported
    printf '\177ELF\000\000\000payload' > "$dir/blob.bin"
    git -C "$dir" add -A
    git -C "$dir" update-index --chmod=+x -- tool.py
    git -C "$dir" update-index --chmod=+x -- blob.bin
    ;;
break)
    # 1. a shebanged file left non-executable -> SM002
    printf '#!/usr/bin/env bash\necho deploy\n' > "$dir/deploy.sh"
    git -C "$dir" add -- deploy.sh
    # 2. an executable file with no shebang -> SM001
    printf 'this is not a script\n' > "$dir/runme"
    git -C "$dir" add -- runme
    git -C "$dir" update-index --chmod=+x -- runme
    ;;
*)
    usage
    ;;
esac

git -C "$dir" ls-files -s
