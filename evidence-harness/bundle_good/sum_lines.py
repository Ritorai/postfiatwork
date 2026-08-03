#!/usr/bin/env python3
"""Toy artefact shipped inside the good evidence bundle."""
import sys


def sum_lines(lines):
    total = 0
    for line in lines:
        line = line.strip()
        if line:
            total += int(line)
    return total


if __name__ == "__main__":
    print(sum_lines(sys.stdin))
