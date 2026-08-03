#!/usr/bin/env python3
"""
Deterministic Task-Reward Budget Forecaster.

Reads historical rewarded-task records plus open-task estimates and produces a
canonical JSON forecast: committed spend, projected spend over a horizon,
per-week burn rate, and a variance band derived from the historical spread.

All money is Decimal, quantized to 6 dp. Floats are rejected outright.

Exit codes: 0 within budget | 1 projected spend exceeds the cap | 2 bad input.
"""
import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

SCALE = Decimal("0.000001")
HIST_REQUIRED = ("task_id", "reward", "rewarded_at")
OPEN_REQUIRED = ("task_id", "estimate")


class InputError(Exception):
    pass


def _dec(raw, where, field):
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        raise InputError(f"{where}: '{field}' must be a string or integer, got {type(raw).__name__}")
    try:
        v = Decimal(str(raw))
    except InvalidOperation:
        raise InputError(f"{where}: '{field}' is not a valid decimal: {raw!r}")
    if v != v:
        raise InputError(f"{where}: '{field}' is NaN")
    if v < 0:
        raise InputError(f"{where}: '{field}' must not be negative")
    return v.quantize(SCALE)


def _ts(value, where):
    try:
        v = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
    except (ValueError, AttributeError):
        raise InputError(f"{where}: rewarded_at is not ISO-8601: {value!r}")
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _load(path, label):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise InputError(f"{label}: file not found: {path}")
    except json.JSONDecodeError as exc:
        raise InputError(f"{label}: invalid JSON: {exc}")
    if not isinstance(data, list):
        raise InputError(f"{label}: expected a JSON array, got {type(data).__name__}")
    return data


def load_history(path):
    out = []
    for i, r in enumerate(_load(path, "history")):
        where = f"history[{i}]"
        if not isinstance(r, dict):
            raise InputError(f"{where}: record must be an object")
        for f in HIST_REQUIRED:
            if f not in r:
                raise InputError(f"{where}: missing required field '{f}'")
        if not isinstance(r["task_id"], str) or not r["task_id"].strip():
            raise InputError(f"{where}: 'task_id' must be a non-empty string")
        out.append({"task_id": r["task_id"],
                    "reward": _dec(r["reward"], where, "reward"),
                    "rewarded_at": r["rewarded_at"],
                    "_ts": _ts(r["rewarded_at"], where)})
    return out


def load_open(path):
    if not path:
        return []
    out = []
    for i, r in enumerate(_load(path, "open")):
        where = f"open[{i}]"
        if not isinstance(r, dict):
            raise InputError(f"{where}: record must be an object")
        for f in OPEN_REQUIRED:
            if f not in r:
                raise InputError(f"{where}: missing required field '{f}'")
        if not isinstance(r["task_id"], str) or not r["task_id"].strip():
            raise InputError(f"{where}: 'task_id' must be a non-empty string")
        out.append({"task_id": r["task_id"],
                    "estimate": _dec(r["estimate"], where, "estimate")})
    return out


def forecast(history, open_tasks, cfg):
    committed = sum((o["estimate"] for o in open_tasks), Decimal("0")).quantize(SCALE)
    hist_total = sum((h["reward"] for h in history), Decimal("0")).quantize(SCALE)

    span_days = None
    if len(history) >= 2:
        ts = sorted(h["_ts"] for h in history)
        span_days = (ts[-1] - ts[0]).total_seconds() / 86400.0

    if span_days and span_days > 0:
        weeks = Decimal(str(span_days / 7.0))
        burn_per_week = (hist_total / weeks).quantize(SCALE) if weeks > 0 else None
    elif history:
        burn_per_week = None   # cannot infer a rate from a single point in time
    else:
        burn_per_week = None

    horizon_weeks = Decimal(str(cfg["horizon_weeks"]))
    projected_burn = (burn_per_week * horizon_weeks).quantize(SCALE) if burn_per_week is not None else Decimal("0").quantize(SCALE)
    projected_total = (committed + projected_burn).quantize(SCALE)

    rewards = [float(h["reward"]) for h in history]
    if len(rewards) >= 2:
        stdev = Decimal(str(statistics.stdev(rewards))).quantize(SCALE)
        mean = Decimal(str(statistics.fmean(rewards))).quantize(SCALE)
    elif len(rewards) == 1:
        stdev = Decimal("0").quantize(SCALE)
        mean = Decimal(str(rewards[0])).quantize(SCALE)
    else:
        stdev = None
        mean = None

    if stdev is not None and burn_per_week is not None and hist_total > 0:
        rel = (stdev / mean) if mean > 0 else Decimal("0")
        band = (projected_burn * rel).quantize(SCALE)
    else:
        band = Decimal("0").quantize(SCALE)

    low = (projected_total - band).quantize(SCALE)
    if low < 0:
        low = Decimal("0").quantize(SCALE)
    high = (projected_total + band).quantize(SCALE)

    cap = cfg["budget_cap"]
    over = cap is not None and projected_total > cap

    return {
        "report_version": "1.0",
        "config": {
            "horizon_weeks": str(horizon_weeks),
            "budget_cap": str(cap) if cap is not None else None,
        },
        "history": {
            "records": len(history),
            "total_rewarded": str(hist_total),
            "mean_reward": str(mean) if mean is not None else None,
            "stdev_reward": str(stdev) if stdev is not None else None,
            "span_days": round(span_days, 4) if span_days is not None else None,
            "burn_per_week": str(burn_per_week) if burn_per_week is not None else None,
        },
        "open_tasks": {
            "records": len(open_tasks),
            "committed": str(committed),
        },
        "projection": {
            "projected_burn": str(projected_burn),
            "projected_total": str(projected_total),
            "variance_band": str(band),
            "low": str(low),
            "high": str(high),
        },
        "over_budget": bool(over),
        "status": "over_budget" if over else "within_budget",
    }


def serialize(report):
    return json.dumps(report, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Forecast task-reward budget spend.")
    ap.add_argument("history")
    ap.add_argument("-k", "--open-tasks")
    ap.add_argument("-o", "--out")
    ap.add_argument("--horizon-weeks", default="4")
    ap.add_argument("--budget-cap")
    args = ap.parse_args(argv)
    try:
        try:
            horizon = Decimal(str(args.horizon_weeks))
            if horizon < 0:
                raise InputError("--horizon-weeks must not be negative")
        except InvalidOperation:
            raise InputError(f"--horizon-weeks is not a valid decimal: {args.horizon_weeks!r}")
        cap = None
        if args.budget_cap is not None:
            try:
                cap = Decimal(str(args.budget_cap)).quantize(SCALE)
            except InvalidOperation:
                raise InputError(f"--budget-cap is not a valid decimal: {args.budget_cap!r}")
        cfg = {"horizon_weeks": horizon, "budget_cap": cap}
        report = forecast(load_history(args.history), load_open(args.open_tasks), cfg)
    except InputError as exc:
        sys.stderr.write(f"INVALID_INPUT: {exc}\n")
        return 2
    text = serialize(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        sys.stdout.write(f"status={report['status']} "
                         f"projected_total={report['projection']['projected_total']}\n")
    else:
        sys.stdout.write(text)
    return 1 if report["over_budget"] else 0


if __name__ == "__main__":
    sys.exit(main())
