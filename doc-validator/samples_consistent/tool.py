#!/usr/bin/env python3
"""Tiny, deliberately boring CLI used as docval's "fully consistent" fixture.
Reads a JSON file of findings and reports how many there are."""
import argparse
import json
import sys


def build_parser():
    p = argparse.ArgumentParser(
        prog="tool.py",
        description="Count findings in a JSON report file.",
    )
    p.add_argument("input", help="path to the input JSON file")
    p.add_argument("-o", "--output", help="write the report here instead of stdout")
    p.add_argument("--strict", action="store_true", help="exit 1 if any findings are present")
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        with open(args.input, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        sys.stderr.write("tool.py: error: cannot read input: %s\n" % exc)
        return 2
    except json.JSONDecodeError as exc:
        sys.stderr.write("tool.py: error: invalid JSON: %s\n" % exc)
        return 2

    findings = data.get("findings", []) if isinstance(data, dict) else []
    report = {"finding_count": len(findings), "findings": findings}
    text = json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)

    if findings and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
