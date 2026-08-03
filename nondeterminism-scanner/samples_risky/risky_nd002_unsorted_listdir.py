"""Trips ND002_UNSORTED_LISTDIR: none of these are immediately sorted()."""
import glob
import os


def list_stuff(d):
    a = os.listdir(d)
    b = os.scandir(d)
    c = list(os.walk(d))
    e = glob.glob(d + "/*.txt")
    return a, b, c, e
