"""Trips ND006_FLOAT_IN_MONEY via float literal, float() on money ident, and both."""


def compute(raw_amount):
    amount = 10.5
    total_price = float(raw_amount)
    payout = float(amount)
    return amount, total_price, payout
