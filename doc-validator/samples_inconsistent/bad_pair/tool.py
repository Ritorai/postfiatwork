#!/usr/bin/env python3
"""Deliberately inconsistent demo CLI -- docval's own negative fixture.
Every disagreement between this file and README.md in this directory is
intentional; it exists to prove each DOC00x finding code actually fires."""
import argparse
import os
import sys

EXIT_WEIRD = 3  # defined far from the sys.exit() call that uses it


def build_parser():
    p = argparse.ArgumentParser(description="Deliberately inconsistent fixture CLI.")
    p.add_argument("input", help="path to an input file")
    p.add_argument("-o", "--output", help="write the report here instead of stdout")
    p.add_argument("--secret-flag", help="internal only -- never documented on purpose")
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.secret_flag == "boom":
        raise RuntimeError("boom: this crash is deliberate fixture behavior")
    if not os.path.exists(args.input):
        return 2
    if args.secret_flag == "weird":
        return EXIT_WEIRD
    text = "ok\n"
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
