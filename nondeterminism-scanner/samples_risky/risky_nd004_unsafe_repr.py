"""Trips ND004_UNSAFE_REPR via repr(), f-string !r, and %r on non-literals."""


class Widget:
    pass


def show(widget, other):
    a = repr(widget)
    b = f"{other!r}"
    c = "%r" % widget
    return a, b, c
