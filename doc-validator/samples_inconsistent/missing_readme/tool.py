#!/usr/bin/env python3
"""A real argparse CLI with no README.md next to it -- triggers DOC007."""
import argparse
import sys


def build_parser():
    p = argparse.ArgumentParser(description="Orphaned CLI with no README.")
    p.add_argument("value")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    print(args.value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
