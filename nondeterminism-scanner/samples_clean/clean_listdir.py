"""Every directory-listing call here is immediately wrapped in sorted()."""
import glob
import os


def all_entries(d):
    a = sorted(os.listdir(d))
    b = sorted(os.scandir(d), key=lambda e: e.name)
    c = sorted(os.walk(d))
    e = sorted(glob.glob(d + "/*.txt"))
    return a, b, c, e


def aliased_but_sorted(d):
    from os import listdir as _listdir
    return sorted(_listdir(d))
