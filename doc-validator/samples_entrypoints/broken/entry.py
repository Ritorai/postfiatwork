#!/usr/bin/env python3
"""entry.py -- fixture CLI for the DOC009 broken fixture.

Identical in behaviour to the valid control's entry.py: inert, exits 0.
It is present so that this directory has a discoverable argparse CLI and
so that the README carries one entrypoint line that is CORRECT, proving
DOC009 reports the broken lines and not simply every line in the block.
"""
import argparse
import sys


def main(argv=None):
    p = argparse.ArgumentParser(prog="entry.py", description="fixture CLI")
    p.add_argument("--check", action="store_true", help="do nothing, successfully")
    p.parse_args(argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
