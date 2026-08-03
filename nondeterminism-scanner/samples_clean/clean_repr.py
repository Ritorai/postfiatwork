"""repr()/!r/%r used only on literals or safe-builtin call results."""


def clean_repr_calls():
    a = repr(42)
    b = repr("hello")
    c = repr([1, 2, 3])
    d = repr({"x": 1, "y": 2})
    e = repr(str(3.14))
    f = repr(1 == 1)
    return a, b, c, d, e, f


def clean_fstring_repr(n):
    return f"{3!r} and {'literal'!r} and {n == 1!r}"


def clean_percent_repr():
    return "%s" % (42,)  # no %r in the format string at all
