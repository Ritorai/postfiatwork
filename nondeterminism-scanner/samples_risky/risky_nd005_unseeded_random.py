"""Trips ND005_UNSEEDED_RANDOM: random.* with no seed, plus secrets.* (always)."""
import random
import secrets


def pick():
    a = random.random()
    b = random.choice([1, 2, 3])
    c = secrets.token_hex(8)
    return a, b, c
