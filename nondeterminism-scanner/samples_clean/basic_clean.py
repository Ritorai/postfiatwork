"""A clean module: nothing here should trip any ND rule.

Deliberately contains the *words* os.listdir(x), repr(obj), random.random(),
datetime.now() and time.time() inside string/comment text only, to prove the
scanner is AST-based and does not pattern-match inside strings or comments.
"""
import os


def list_files(directory):
    # sorted() immediately wraps the listdir() call -> no ND002 finding.
    return sorted(os.listdir(directory))


def safe_repr_use():
    # repr() of a literal int is judged "obviously safe" -> no ND004 finding.
    return repr(42)


def mentions_pattern_in_string():
    # This text mentions os.listdir(x), repr(obj), random.random() and
    # datetime.now() but it is just a string literal, not code.
    return "os.listdir(x) repr(obj) random.random() datetime.now() time.time()"


def wall_clock_word_only():
    # the phrase "time.time()" appears here only as a comment, not code.
    return "static-value"
