#!/usr/bin/env python3
"""Drive one partial write through readmeindex.main() and report the damage.

Run from a directory containing the `readmeindex.py` you want to test:

    python3 demo_partial_write.py

This exists so the defect can be seen without reading a test suite, and so the
*same* driver can be pointed at the pre-fix source and at the fixed one. It
never touches the tool's own files: everything happens in a fresh temp
directory that is removed on the way out.

It deliberately goes through `main()` rather than through any helper, because
the pre-fix source has no helper to call -- the direct `open(path, "w")` is
inside `main()`. That also means this driver stays honest if the internals are
reorganised again.

Every line it prints is fixed-width by construction, so the output is stable
enough to commit.
"""

import io
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.getcwd())
import readmeindex as R  # noqa: E402

SENTINEL = b"PREVIOUS OUTPUT -- MUST SURVIVE A FAILED WRITE\n" * 6
ALLOW = 40

ROOT_README = """# demo

## The tools

| Tool | Tests | What it checks |
|------|------:|-------|
| [`ghost`](ghost) | 3 | Ghost |

## Judgement calls, collected

text
"""


class Boom(OSError):
    pass


class TruncatingHandle:
    """Commit a prefix to disk, then fail the way a full disk fails."""

    def __init__(self, handle, allow):
        self._handle = handle
        self._allow = allow

    def write(self, text):
        self._handle.write(text[: self._allow])
        self._handle.flush()
        raise Boom("No space left on device")

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self._handle.close()
        return False


def failing_open(file, mode="r", *args, **kwargs):
    handle = open(file, mode, *args, **kwargs)
    if "w" not in mode:
        return handle
    return TruncatingHandle(handle, ALLOW)


def main():
    work = tempfile.mkdtemp(prefix="readmeindex_demo_")
    try:
        os.makedirs(os.path.join(work, "ghost"))
        with open(os.path.join(work, "ghost", "README.md"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write("# Ghost\n\n**3 tests, `OK`**\n")
        root_readme = os.path.join(work, "ROOT.md")
        with open(root_readme, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(ROOT_README)

        out_dir = os.path.join(work, "out")
        os.makedirs(out_dir)
        dest = os.path.join(out_dir, "regenerated.md")
        with open(dest, "wb") as fh:
            fh.write(SENTINEL)

        print("destination before:      %d bytes" % os.path.getsize(dest))

        R.open = failing_open
        try:
            with contextlib_redirect():
                R.main(["--root", work, "--root-readme", root_readme,
                        "--rewrite", dest])
        except Boom:
            print("run raised:              OSError, as a full disk would")
        else:
            print("run raised:              NOTHING -- the injector never fired")
        finally:
            del R.open

        after = open(dest, "rb").read()
        print("destination after:       %d bytes" % len(after))
        print("destination unchanged:   %s" % (after == SENTINEL))
        strays = [n for n in os.listdir(out_dir) if n.startswith(".readmeindex-")]
        print("temp files left behind:  %d" % len(strays))
    finally:
        # Only the directory this process created. Never its parent: that is
        # the system temp directory itself.
        shutil.rmtree(work, ignore_errors=True)
    return 0


class contextlib_redirect:
    """Swallow the report main() prints when no -o was given."""

    def __enter__(self):
        self._saved = sys.stdout
        sys.stdout = io.StringIO()
        return self

    def __exit__(self, *exc_info):
        sys.stdout = self._saved
        return False


if __name__ == "__main__":
    sys.exit(main())
