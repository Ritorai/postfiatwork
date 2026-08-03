"""random usage is always preceded, somewhere in the module, by random.seed()."""
import random


def seeded_choice(seq):
    random.seed(1234)
    return random.choice(seq)


def seeded_shuffle(items):
    random.seed(0)
    random.shuffle(items)
    return items
