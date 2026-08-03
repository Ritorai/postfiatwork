#!/usr/bin/env python3
"""
Configurable Sybil Wallet-Cluster Detector.

Reads submission records and identifies likely coordinated wallet clusters using
three independent signals. Weights, tolerances and the alert threshold are all
configurable via CLI flags or a JSON config file.

SIGNALS
  shared_cid    two wallets submitted the same evidence CID
  length_match  two wallets' evidence lengths are within --length-tolerance
                (relative), suggesting templated content
  burst_timing  two wallets submitted within --burst-window seconds of each other

Pairwise signal scores are summed with their weights into a pair score. Wallets
are then clustered by union-find over pairs whose score >= --link-threshold, and
each cluster receives a score = the maximum pair score inside it.

Exit codes: 0 no cluster at/above --alert-threshold | 1 alert | 2 invalid input.
"""
import argparse
import json
import sys
from datetime import datetime, timezone

REQUIRED = ("submission_id", "wallet", "cid", "evidence_length", "submitted_at")

SHARED_CID = "shared_cid"
LENGTH_MATCH = "length_match"
BURST_TIMING = "burst_timing"

DEFAULTS = {
    "weights": {SHARED_CID: 0.6, LENGTH_MATCH: 0.2, BURST_TIMING: 0.2},
    "length_tolerance": 0.05,
    "burst_window": 300,
    "link_threshold": 0.5,
    "alert_threshold": 0.8,
}


class InputError(Exception):
    pass


def _parse_ts(value, where):
    try:
        v = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
    except (ValueError, AttributeError):
        raise InputError(f"{where}: submitted_at is not ISO-8601: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def load_records(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise InputError(f"file not found: {path}")
    except json.JSONDecodeError as exc:
        raise InputError(f"invalid JSON: {exc}")
    if not isinstance(data, list):
        raise InputError(f"expected a JSON array, got {type(data).__name__}")

    out = []
    for i, r in enumerate(data):
        where = f"records[{i}]"
        if not isinstance(r, dict):
            raise InputError(f"{where}: record must be an object")
        for f in REQUIRED:
            if f not in r:
                raise InputError(f"{where}: missing required field '{f}'")
        for f in ("submission_id", "wallet", "cid", "submitted_at"):
            if not isinstance(r[f], str) or not r[f].strip():
                raise InputError(f"{where}: '{f}' must be a non-empty string")
        if isinstance(r["evidence_length"], bool) or not isinstance(r["evidence_length"], int):
            raise InputError(f"{where}: 'evidence_length' must be an integer")
        if r["evidence_length"] < 0:
            raise InputError(f"{where}: 'evidence_length' must be non-negative")
        out.append({
            "submission_id": r["submission_id"],
            "wallet": r["wallet"],
            "cid": r["cid"],
            "evidence_length": r["evidence_length"],
            "submitted_at": r["submitted_at"],
            "_ts": _parse_ts(r["submitted_at"], where),
        })
    return out


def load_config(path, overrides):
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy
    if path:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                user = json.load(fh)
        except FileNotFoundError:
            raise InputError(f"config file not found: {path}")
        except json.JSONDecodeError as exc:
            raise InputError(f"config file invalid JSON: {exc}")
        if not isinstance(user, dict):
            raise InputError("config must be a JSON object")
        for k, v in user.items():
            if k == "weights":
                if not isinstance(v, dict):
                    raise InputError("config.weights must be an object")
                for sk, sv in v.items():
                    if sk not in DEFAULTS["weights"]:
                        raise InputError(f"config.weights: unknown signal {sk!r}")
                    cfg["weights"][sk] = float(sv)
            elif k in DEFAULTS:
                cfg[k] = float(v)
            else:
                raise InputError(f"config: unknown key {k!r}")
    for k, v in overrides.items():
        if v is None:
            continue
        cfg[k] = float(v)
    return cfg


def pair_signals(a, b, cfg):
    """Return the list of signal names firing for this wallet pair's records."""
    fired = []
    if a["cid"] == b["cid"]:
        fired.append(SHARED_CID)

    la, lb = a["evidence_length"], b["evidence_length"]
    hi = max(la, lb)
    if hi == 0:
        if la == lb:
            fired.append(LENGTH_MATCH)
    elif abs(la - lb) / hi <= cfg["length_tolerance"]:
        fired.append(LENGTH_MATCH)

    if abs((a["_ts"] - b["_ts"]).total_seconds()) <= cfg["burst_window"]:
        fired.append(BURST_TIMING)
    return fired


def analyze(records, cfg):
    wallets = sorted({r["wallet"] for r in records})
    pairs = []
    best = {}

    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            a, b = records[i], records[j]
            if a["wallet"] == b["wallet"]:
                continue
            fired = pair_signals(a, b, cfg)
            if not fired:
                continue
            score = round(sum(cfg["weights"][s] for s in fired), 6)
            key = tuple(sorted((a["wallet"], b["wallet"])))
            cand = {
                "wallets": list(key),
                "score": score,
                "signals": sorted(fired),
                "submissions": sorted([a["submission_id"], b["submission_id"]]),
            }
            if key not in best or score > best[key]["score"]:
                best[key] = cand

    pairs = sorted(best.values(), key=lambda p: (-p["score"], p["wallets"]))

    parent = {w: w for w in wallets}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[max(rx, ry)] = min(rx, ry)

    linked = [p for p in pairs if p["score"] >= cfg["link_threshold"]]
    for p in linked:
        union(p["wallets"][0], p["wallets"][1])

    groups = {}
    for w in wallets:
        groups.setdefault(find(w), []).append(w)

    clusters = []
    for root in sorted(groups):
        members = sorted(groups[root])
        if len(members) < 2:
            continue
        member_set = set(members)
        inner = [p for p in linked if set(p["wallets"]) <= member_set]
        score = round(max(p["score"] for p in inner), 6) if inner else 0.0
        sigs = sorted({s for p in inner for s in p["signals"]})
        clusters.append({
            "wallets": members,
            "size": len(members),
            "score": score,
            "signals": sigs,
            "alert": score >= cfg["alert_threshold"],
            "pairs": sorted(inner, key=lambda p: (-p["score"], p["wallets"])),
        })

    clusters.sort(key=lambda c: (-c["score"], c["wallets"]))
    alerts = [c for c in clusters if c["alert"]]

    return {
        "report_version": "1.0",
        "config": {
            "weights": {k: cfg["weights"][k] for k in sorted(cfg["weights"])},
            "length_tolerance": cfg["length_tolerance"],
            "burst_window": cfg["burst_window"],
            "link_threshold": cfg["link_threshold"],
            "alert_threshold": cfg["alert_threshold"],
        },
        "totals": {
            "records": len(records),
            "wallets": len(wallets),
            "scored_pairs": len(pairs),
            "linked_pairs": len(linked),
            "clusters": len(clusters),
            "alerting_clusters": len(alerts),
        },
        "clusters": clusters,
        "status": "alert" if alerts else "clear",
    }


def serialize(report):
    return json.dumps(report, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Detect coordinated Sybil wallet clusters.")
    ap.add_argument("records")
    ap.add_argument("-c", "--config")
    ap.add_argument("-o", "--out")
    ap.add_argument("--length-tolerance", type=float)
    ap.add_argument("--burst-window", type=float)
    ap.add_argument("--link-threshold", type=float)
    ap.add_argument("--alert-threshold", type=float)
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config, {
            "length_tolerance": args.length_tolerance,
            "burst_window": args.burst_window,
            "link_threshold": args.link_threshold,
            "alert_threshold": args.alert_threshold,
        })
        report = analyze(load_records(args.records), cfg)
    except InputError as exc:
        sys.stderr.write(f"INVALID_INPUT: {exc}\n")
        return 2

    text = serialize(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        sys.stdout.write(f"status={report['status']} "
                         f"clusters={report['totals']['clusters']} "
                         f"alerting={report['totals']['alerting_clusters']}\n")
    else:
        sys.stdout.write(text)
    return 1 if report["status"] == "alert" else 0


if __name__ == "__main__":
    sys.exit(main())
