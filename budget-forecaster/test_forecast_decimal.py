"""Focused tests for Decimal-precise JSON parsing in forecast.py.

These cover the change from a plain `json.load` to
`json.load(fh, parse_float=_JsonDecimal, parse_constant=_reject_nonfinite)`.

The behavioural contract is deliberately UNCHANGED: float-shaped tokens are
still refused, because an unquoted number in a money field means the author has
not thought about precision. What changed is that the refusal now happens on an
exact value, and that bare NaN / Infinity tokens are intercepted before they can
become a float.

Run:  python3 -m unittest test_forecast_decimal -v
"""

import json
import os
import tempfile
import unittest
from decimal import Decimal

import forecast as F


def _write(recs):
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(recs, fh)
    fh.close()
    return fh.name


def _write_raw(text):
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    fh.write(text)
    fh.close()
    return fh.name


def _load(path):
    try:
        return F.load_history(path)
    finally:
        os.unlink(path)


def _rec(reward, task_id="t1", ts="2026-07-01T00:00:00Z"):
    return {"task_id": task_id, "reward": reward, "rewarded_at": ts}


class TestParseBoundaryIsExact(unittest.TestCase):
    """The parse boundary must preserve the digits in the file."""

    def test_float_token_becomes_exact_decimal_not_binary_float(self):
        # 0.1 has no exact binary representation. Parsed with parse_float the
        # value carries the file's digits; parsed as a float it would not.
        path = _write_raw('[{"task_id":"t","reward":0.1,'
                          '"rewarded_at":"2026-07-01T00:00:00Z"}]')
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh, parse_float=F._JsonDecimal,
                                parse_constant=F._reject_nonfinite)
        finally:
            os.unlink(path)
        value = raw[0]["reward"]
        self.assertIsInstance(value, Decimal)
        self.assertEqual(str(value), "0.1")
        # The plain-float path loses this: str(float("0.1")) == "0.1" but the
        # stored value is not 1/10. Compare against the exact decimal.
        self.assertNotEqual(Decimal(0.1), Decimal("0.1"))
        self.assertEqual(value, Decimal("0.1"))

    def test_high_precision_token_survives_the_parse(self):
        digits = "0.1000000000000000055511151231257827"
        path = _write_raw('[{"task_id":"t","reward":' + digits +
                          ',"rewarded_at":"2026-07-01T00:00:00Z"}]')
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh, parse_float=F._JsonDecimal,
                                parse_constant=F._reject_nonfinite)
        finally:
            os.unlink(path)
        self.assertEqual(str(raw[0]["reward"]), digits)

    def test_decimal_str_idiom_would_have_corrupted_it(self):
        """Why parse_float matters rather than Decimal(str(x)) afterwards."""
        digits = "0.1000000000000000055511151231257827"
        too_late = Decimal(str(json.loads(digits)))       # float built first
        at_boundary = json.loads(digits, parse_float=Decimal)
        self.assertNotEqual(str(too_late), digits)
        self.assertEqual(str(at_boundary), digits)


class TestFloatTokensStillRefused(unittest.TestCase):
    """The documented stance on money is unchanged."""

    def test_float_reward_still_rejected(self):
        with self.assertRaises(F.InputError):
            _load(_write([_rec(3.5)]))

    def test_refusal_message_names_float(self):
        with self.assertRaises(F.InputError) as ctx:
            _load(_write([_rec(3.5)]))
        self.assertIn("float", str(ctx.exception))

    def test_quoted_decimal_string_still_accepted(self):
        recs = _load(_write([_rec("3.50")]))
        self.assertEqual(len(recs), 1)

    def test_integer_still_accepted(self):
        recs = _load(_write([_rec(4)]))
        self.assertEqual(len(recs), 1)

    def test_bool_still_rejected(self):
        with self.assertRaises(F.InputError):
            _load(_write([_rec(True)]))


class TestNonFiniteTokens(unittest.TestCase):
    """Bare NaN / Infinity / -Infinity are intercepted before becoming floats."""

    def test_nan_token_rejected(self):
        path = _write_raw('[{"task_id":"t","reward":NaN,'
                          '"rewarded_at":"2026-07-01T00:00:00Z"}]')
        with self.assertRaises(F.InputError):
            _load(path)

    def test_infinity_token_rejected(self):
        path = _write_raw('[{"task_id":"t","reward":Infinity,'
                          '"rewarded_at":"2026-07-01T00:00:00Z"}]')
        with self.assertRaises(F.InputError):
            _load(path)

    def test_negative_infinity_token_rejected(self):
        path = _write_raw('[{"task_id":"t","reward":-Infinity,'
                          '"rewarded_at":"2026-07-01T00:00:00Z"}]')
        with self.assertRaises(F.InputError):
            _load(path)

    def test_nonfinite_message_names_the_token(self):
        path = _write_raw('[{"task_id":"t","reward":Infinity,'
                          '"rewarded_at":"2026-07-01T00:00:00Z"}]')
        with self.assertRaises(F.InputError) as ctx:
            _load(path)
        msg = str(ctx.exception)
        self.assertIn("Infinity", msg)
        self.assertIn("not permitted", msg)

    def test_nonfinite_never_reaches_decimal(self):
        """A Decimal('NaN') constructs without raising, so the guard matters."""
        self.assertTrue(Decimal("NaN").is_nan())
        path = _write_raw('[{"task_id":"t","reward":NaN,'
                          '"rewarded_at":"2026-07-01T00:00:00Z"}]')
        with self.assertRaises(F.InputError) as ctx:
            _load(path)
        self.assertIn("NaN", str(ctx.exception))


class TestDecimalSensitiveForecast(unittest.TestCase):
    """An end-to-end forecast whose total is only correct under exact decimals."""

    def test_sum_of_tenths_is_exact(self):
        # 0.1 summed ten times is 0.9999999999999999 in binary floating point.
        recs = [_rec("0.10", task_id="t%d" % i,
                     ts="2026-07-%02dT00:00:00Z" % (i + 1)) for i in range(10)]
        loaded = _load(_write(recs))
        total = sum((r["reward"] for r in loaded), Decimal(0))
        self.assertEqual(total, Decimal("1.00"))
        self.assertEqual(str(total.quantize(Decimal("0.01"))), "1.00")
        # The float route does not land on 1.0.
        self.assertNotEqual(sum([0.1] * 10), 1.0)

    def test_report_emits_exact_total(self):
        recs = [_rec("0.10", task_id="t%d" % i,
                     ts="2026-07-%02dT00:00:00Z" % (i + 1)) for i in range(10)]
        path = _write(recs)
        try:
            hist = F.load_history(path)
            cfg = {"horizon_weeks": Decimal(1), "budget_cap": None}
            report = F.forecast(hist, [], cfg)
        finally:
            os.unlink(path)
        self.assertEqual(Decimal(report["history"]["total_rewarded"]),
                         Decimal("1.00"))


if __name__ == "__main__":
    unittest.main()
