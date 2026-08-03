#!/usr/bin/env python3
"""Toy artefact shipped inside the deliberately incomplete evidence bundle."""
import sys


def longest_word(text):
    words = text.split()
    if not words:
        return ""
    return max(words, key=len)


if __name__ == "__main__":
    print(longest_word(sys.stdin.read()))
