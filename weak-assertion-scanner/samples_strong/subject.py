"""A tiny subject module used only by the sample test suites."""


def add(a, b):
    return a + b


def divide(a, b):
    if b == 0:
        raise ValueError("division by zero")
    return a / b


class Greeter:
    def __init__(self, name):
        self.name = name

    def greet(self):
        return "hello, %s" % self.name
