"""Trips ND003_UNORDERED_ITERATION: set literal and dict, both accumulating."""


def collect_from_set():
    out = []
    for item in {3, 1, 2}:
        out.append(item)
    return out


def collect_from_dict_keys():
    d = {"b": 2, "a": 1}
    out = []
    for k in d.keys():
        out.append(k)
    return out
