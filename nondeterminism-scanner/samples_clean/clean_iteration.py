"""Set/dict iteration here is either sorted, or the body never accumulates."""


def sorted_set_accum():
    out = []
    for item in sorted({3, 1, 2}):
        out.append(item)
    return out


def sorted_dict_items_accum():
    d = {"b": 2, "a": 1}
    out = []
    for k, v in sorted(d.items()):
        out.append((k, v))
    return out


def set_iterated_without_accum():
    # Iterates a set literal but the body does not append/extend/add/update
    # anything, so ordering cannot leak into any output -> no ND003 finding.
    total = 0
    for item in {1, 2, 3}:
        total += item
    return total


def list_iteration_is_never_flagged():
    # Plain list iteration is never an ND003 target regardless of the body.
    out = []
    for item in [3, 1, 2]:
        out.append(item)
    return out
