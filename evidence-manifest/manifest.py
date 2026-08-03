#!/usr/bin/env python3
"""
Deterministic Batch Evidence Manifest CLI.

build  : JSON array of submission records -> manifest with canonical records,
         per-record SHA-256 leaf digests, and a Merkle batch root.
verify : recompute every digest and the root from a manifest; non-zero exit on drift.

CANONICALIZATION RULES (documented contract)
  1. Object keys sorted lexicographically (byte order), recursively.
  2. String values: leading/trailing whitespace stripped; internal runs of
     whitespace collapsed to a single space (U+0020). Applied recursively.
  3. Serialized with separators (",", ":"), ensure_ascii=True, no trailing newline.
  4. Leaf digest = SHA-256 over b"leaf:" + canonical_bytes. Domain-separated to
     stop leaf/parent second-preimage confusion.
  5. Leaf ORDER = the input array order. Order is part of the batch identity, so
     reordering the input changes the root by design.
  6. Parent = SHA-256 over b"node:" + left_digest_bytes + right_digest_bytes.
  7. ODD NODE: a level with an odd count promotes the final node unchanged to the
     next level (it is NOT duplicated, which avoids the CVE-2012-2459 style
     duplicate-leaf root collision).
  8. Empty batch root = SHA-256 over b"empty:" (constant, defined for completeness).

Exit codes: 0 ok | 1 verification failure (drift) | 2 invalid input.
"""
import argparse
import hashlib
import json
import re
import sys

LEAF_PREFIX = b"leaf:"
NODE_PREFIX = b"node:"
EMPTY_PREFIX = b"empty:"
_WS = re.compile(r"\s+")


class InputError(Exception):
    pass


def canonicalize(value):
    """Recursively normalize a JSON value per rules 1-2."""
    if isinstance(value, dict):
        return {k: canonicalize(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [canonicalize(v) for v in value]
    if isinstance(value, str):
        return _WS.sub(" ", value).strip()
    return value


def canonical_bytes(record):
    return json.dumps(canonicalize(record), sort_keys=True,
                      separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def leaf_digest(record):
    return hashlib.sha256(LEAF_PREFIX + canonical_bytes(record)).hexdigest()


def merkle_root(leaves):
    """Compute the batch root from a list of hex leaf digests."""
    if not leaves:
        return hashlib.sha256(EMPTY_PREFIX).hexdigest()
    level = [bytes.fromhex(h) for h in leaves]
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level) - 1, 2):
            nxt.append(hashlib.sha256(NODE_PREFIX + level[i] + level[i + 1]).digest())
        if len(level) % 2:
            nxt.append(level[-1])  # promote odd tail unchanged
        level = nxt
    return level[0].hex()


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
    for i, r in enumerate(data):
        if not isinstance(r, dict):
            raise InputError(f"record[{i}] must be an object, got {type(r).__name__}")
    return data


def build_manifest(records):
    entries = []
    for i, rec in enumerate(records):
        c = canonicalize(rec)
        entries.append({
            "index": i,
            "canonical": c,
            "leaf_digest": leaf_digest(rec),
        })
    return {
        "manifest_version": "1.0",
        "algorithm": "sha256",
        "leaf_prefix": LEAF_PREFIX.decode(),
        "node_prefix": NODE_PREFIX.decode(),
        "odd_node_policy": "promote",
        "record_count": len(entries),
        "entries": entries,
        "batch_root": merkle_root([e["leaf_digest"] for e in entries]),
    }


def serialize(manifest):
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True) + "\n"


def verify_manifest(manifest):
    """Return a list of human-readable drift descriptions (empty == clean)."""
    problems = []
    if not isinstance(manifest, dict):
        raise InputError("manifest must be a JSON object")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise InputError("manifest.entries must be an array")

    if manifest.get("record_count") != len(entries):
        problems.append(
            f"record_count mismatch: header says {manifest.get('record_count')}, "
            f"found {len(entries)} entries")

    recomputed = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or "canonical" not in e or "leaf_digest" not in e:
            raise InputError(f"entries[{i}] missing 'canonical' or 'leaf_digest'")
        expect = e["leaf_digest"]
        actual = leaf_digest(e["canonical"])
        recomputed.append(actual)
        if actual != expect:
            problems.append(
                f"entries[{i}] leaf_digest drift: stored {expect}, recomputed {actual}")

    root = merkle_root(recomputed)
    if root != manifest.get("batch_root"):
        problems.append(
            f"batch_root drift: stored {manifest.get('batch_root')}, recomputed {root}")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description="Deterministic batch evidence manifest.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="Build a manifest from submission records.")
    b.add_argument("records")
    b.add_argument("-o", "--out")
    v = sub.add_parser("verify", help="Verify a manifest's digests and root.")
    v.add_argument("manifest")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "build":
            man = build_manifest(load_records(args.records))
            text = serialize(man)
            if args.out:
                with open(args.out, "w", encoding="utf-8") as fh:
                    fh.write(text)
                sys.stdout.write(f"batch_root={man['batch_root']}\nrecords={man['record_count']}\n")
            else:
                sys.stdout.write(text)
            return 0

        try:
            with open(args.manifest, "r", encoding="utf-8") as fh:
                man = json.load(fh)
        except FileNotFoundError:
            raise InputError(f"file not found: {args.manifest}")
        except json.JSONDecodeError as exc:
            raise InputError(f"invalid JSON: {exc}")

        problems = verify_manifest(man)
        if problems:
            sys.stderr.write("VERIFICATION FAILED\n")
            for p in problems:
                sys.stderr.write(f"  - {p}\n")
            return 1
        sys.stdout.write(f"VERIFIED batch_root={man['batch_root']} records={man['record_count']}\n")
        return 0
    except InputError as exc:
        sys.stderr.write(f"INVALID_INPUT: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
