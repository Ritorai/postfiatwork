"""Subject module for the weak sample suite (mirrors samples_strong/subject.py)."""


def add(a, b):
    return a + b


def compute(x):
    return x * x + 1


class Widget:
    def __init__(self, n):
        self.n = n

    def scale(self, factor):
        return self.n * factor
