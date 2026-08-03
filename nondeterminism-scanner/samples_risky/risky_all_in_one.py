"""A single file that trips all six rules at once."""
import datetime
import glob
import os
import random
import secrets


class Account:
    pass


def snapshot(directory, acct: Account):
    stamp = datetime.datetime.now()                 # ND001
    entries = os.listdir(directory)                  # ND002
    matches = glob.glob(directory + "/*.log")         # ND002

    out = []
    for entry in {"c", "a", "b"}:                     # ND003
        out.append(entry)

    label = repr(acct)                                # ND004
    tag = f"{acct!r}"                                  # ND004

    roll = random.random()                             # ND005 (no seed anywhere)
    token = secrets.token_hex(4)                        # ND005 (always)

    amount = 19.99                                       # ND006
    total_price = float(directory)                        # ND006 (name matches, arg irrelevant type)

    return stamp, entries, matches, out, label, tag, roll, token, amount, total_price
