#!/usr/bin/env python3
"""
XRPL Classic and X-Address Validator (standard library only).

Validates XRPL addresses structurally and cryptographically:
  * XRPL base58 alphabet (Ripple's own ordering, NOT the Bitcoin alphabet)
  * decoded payload length
  * version/type prefix
  * 4-byte checksum = first 4 bytes of SHA256(SHA256(payload))
  * classic r-address vs X-address discrimination
  * optional denylist of structurally valid but blocked addresses

Issue codes:
    BAD_ALPHABET        contains a character outside the XRPL base58 alphabet
    BAD_LENGTH          decoded payload is not the expected size
    BAD_PREFIX          unrecognised version/type prefix byte(s)
    BAD_CHECKSUM        trailing 4 bytes do not match the double-SHA256 prefix
    DENYLISTED          structurally valid but present on the supplied denylist
    MALFORMED_RECORD    element is not a string / empty

Exit codes: 0 all valid and allowed | 1 issues found | 2 unreadable input.
"""
import argparse
import hashlib
import json
import sys

# XRPL ("Ripple") base58 alphabet. Note it starts 'rpsh', not '123' like Bitcoin.
ALPHABET = "rpshnaf39wBUDNEGHJKLM4PQRST7VWXYZ2bcdeCg65jkm8oFqi1tuvAxyz"
ALPHABET_MAP = {c: i for i, c in enumerate(ALPHABET)}

CLASSIC_PREFIX = 0x00        # 'r' addresses, 20-byte account id
XADDR_PREFIX_MAIN = (0x05, 0x44)   # 'X...' mainnet
XADDR_PREFIX_TEST = (0x04, 0x93)   # 'T...' testnet

CLASSIC_PAYLOAD_LEN = 21     # 1 version + 20 account id
XADDR_PAYLOAD_LEN = 31       # 2 prefix + 20 account id + 1 flag + 8 tag

BAD_ALPHABET = "BAD_ALPHABET"
BAD_LENGTH = "BAD_LENGTH"
BAD_PREFIX = "BAD_PREFIX"
BAD_CHECKSUM = "BAD_CHECKSUM"
DENYLISTED = "DENYLISTED"
MALFORMED_RECORD = "MALFORMED_RECORD"


class InputError(Exception):
    pass


def b58decode(s):
    """Decode XRPL base58 to bytes. Raises ValueError on a bad character."""
    num = 0
    for ch in s:
        if ch not in ALPHABET_MAP:
            raise ValueError(f"invalid base58 character {ch!r}")
        num = num * 58 + ALPHABET_MAP[ch]
    body = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    pad = 0
    for ch in s:
        if ch == ALPHABET[0]:
            pad += 1
        else:
            break
    return b"\x00" * pad + body


def double_sha256(data):
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def classify(payload):
    """Return ('classic'|'xaddress-main'|'xaddress-test'|None, expected_len)."""
    if not payload:
        return None, None
    if payload[0] == CLASSIC_PREFIX:
        return "classic", CLASSIC_PAYLOAD_LEN
    if len(payload) >= 2:
        pair = (payload[0], payload[1])
        if pair == XADDR_PREFIX_MAIN:
            return "xaddress-main", XADDR_PAYLOAD_LEN
        if pair == XADDR_PREFIX_TEST:
            return "xaddress-test", XADDR_PAYLOAD_LEN
    return None, None


def validate_address(addr, denylist):
    """Return (issues, kind). issues is a list of code strings."""
    issues = []
    try:
        raw = b58decode(addr)
    except ValueError:
        return [BAD_ALPHABET], None

    if len(raw) < 5:
        return [BAD_LENGTH], None

    payload, checksum = raw[:-4], raw[-4:]
    kind, expected = classify(payload)

    if kind is None:
        issues.append(BAD_PREFIX)
    elif len(payload) != expected:
        issues.append(BAD_LENGTH)

    if double_sha256(payload)[:4] != checksum:
        issues.append(BAD_CHECKSUM)

    if not issues and addr in denylist:
        issues.append(DENYLISTED)

    return issues, kind


def load_addresses(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise InputError(f"file not found: {path}")
    except json.JSONDecodeError as exc:
        raise InputError(f"invalid JSON: {exc}")
    if not isinstance(data, list):
        raise InputError(f"expected a JSON array, got {type(data).__name__}")
    return data


def load_denylist(path):
    if not path:
        return set()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise InputError(f"denylist file not found: {path}")
    except json.JSONDecodeError as exc:
        raise InputError(f"denylist invalid JSON: {exc}")
    if not isinstance(data, list):
        raise InputError("denylist must be a JSON array of address strings")
    for i, a in enumerate(data):
        if not isinstance(a, str) or not a.strip():
            raise InputError(f"denylist[{i}]: must be a non-empty string")
    return set(data)


def audit(addresses, denylist):
    results = []
    for i, addr in enumerate(addresses):
        if not isinstance(addr, str) or not addr.strip():
            results.append({"index": i, "address": None, "kind": None,
                            "issues": [MALFORMED_RECORD], "valid": False})
            continue
        issues, kind = validate_address(addr, denylist)
        results.append({"index": i, "address": addr, "kind": kind,
                        "issues": sorted(issues), "valid": not issues})

    results.sort(key=lambda r: (r["index"],))
    counts = {}
    for r in results:
        for c in r["issues"]:
            counts[c] = counts.get(c, 0) + 1
    bad = [r for r in results if not r["valid"]]

    return {
        "report_version": "1.0",
        "totals": {"addresses": len(results),
                   "valid": len(results) - len(bad),
                   "invalid": len(bad)},
        "issue_counts": dict(sorted(counts.items())),
        "addresses": results,
        "status": "clean" if not bad else "issues",
    }


def serialize(report):
    return json.dumps(report, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Validate XRPL classic and X-addresses.")
    ap.add_argument("addresses")
    ap.add_argument("-d", "--denylist")
    ap.add_argument("-o", "--out")
    args = ap.parse_args(argv)
    try:
        report = audit(load_addresses(args.addresses), load_denylist(args.denylist))
    except InputError as exc:
        sys.stderr.write(f"UNREADABLE_INPUT: {exc}\n")
        return 2
    text = serialize(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        sys.stdout.write(f"status={report['status']} valid={report['totals']['valid']} "
                         f"invalid={report['totals']['invalid']}\n")
    else:
        sys.stdout.write(text)
    return 0 if report["status"] == "clean" else 1


if __name__ == "__main__":
    sys.exit(main())
