"""A trivial checker script used as bundle content for claimcheck fixtures.

Exits 0 unconditionally. Its only purpose is to exist inside the fixture
bundle so notes_false.txt can make several claims about it -- some true,
most deliberately false or unverifiable.
"""
import sys


def main():
    print("checker: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
