#!/usr/bin/env python3
"""entry.py -- fixture CLI for the DOC009 valid control.

Does nothing except parse its one flag and exit 0. docval executes this
file for real during a normal (non---no-run) scan, so it must stay inert:
no writes, no network, no subprocesses.
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
