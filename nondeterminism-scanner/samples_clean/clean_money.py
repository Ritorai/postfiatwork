"""Money-like identifiers here only ever hold int/Decimal values, never float."""
from decimal import Decimal


def compute_total(cents_list):
    total = sum(cents_list)  # int, no float() and no float literal
    return total


def make_price(dollars, cents):
    price = Decimal(dollars) + Decimal(cents) / Decimal(100)
    return price


def payout_from_int(raw):
    payout = int(raw)
    return payout
