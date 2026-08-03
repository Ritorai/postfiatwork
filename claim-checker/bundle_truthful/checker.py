"""A trivial checker script used as bundle content for claimcheck fixtures.

Exits 0 unconditionally. Its only purpose is to exist inside the fixture
bundle so notes_truthful.txt can make a real, verifiable EXIT_CODE_CLAIM
and SHA256_CLAIM about it.
"""
import sys


def main():
    print("checker: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
