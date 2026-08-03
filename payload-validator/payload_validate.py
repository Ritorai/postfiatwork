#!/usr/bin/env python3
"""
Post Fiat payload / memo validator (standard library only).

Validates an array of XRPL memo + transaction payload records before they
reach Post Fiat task processing:

  * memo_hex decodes as hex and the decoded byte length is within a
    configurable limit (default 1024 bytes, see --max-memo-bytes)
  * decoded memo bytes are valid UTF-8
  * required fields are present: payload_id, memo_hex, account,
    destination, and exactly one of amount_drops / amount_pft
  * account / destination are structurally and cryptographically valid
    XRPL Base58Check addresses (classic 'r'-address or X-address) --
    this reuses the base58 decode / double-SHA256 checksum / address
    classification logic from the sibling xrpl-address/address_validate.py
    tool (see README "Reuse" section for the exact boundary)
  * account != destination (no self-payments)
  * amount_drops / amount_pft are valid, non-negative decimal.Decimal
    values; drops must be a whole number and must not exceed the XRPL
    total-supply ceiling of 1e17 drops (100 billion XRP)
  * payload_id is unique across the input array

Finding codes:
    INVALID_HEX             memo_hex is not a string, has odd length, or
                             contains non-hexadecimal characters
    MEMO_TOO_LARGE           decoded memo exceeds --max-memo-bytes (">" ,
                             not ">=" -- a memo exactly at the limit passes)
    MEMO_NOT_UTF8            decoded memo bytes are not valid UTF-8
    MISSING_REQUIRED_FIELD   a required field is absent, or (payload_id /
                             account / destination only) present but not
                             a non-empty string
    INVALID_ADDRESS          account/destination fails XRPL Base58Check:
                             bad alphabet, bad length, bad prefix, or a
                             bad double-SHA256 checksum
    SELF_PAYMENT             account == destination (raw string equality)
    INVALID_AMOUNT           negative / NaN / Infinity / non-numeric /
                             fractional-drops value, or both/neither of
                             amount_drops + amount_pft supplied
    AMOUNT_OUT_OF_RANGE      amount_drops > 100_000_000_000_000_000
                             (100 billion XRP expressed in drops)
    DUPLICATE_PAYLOAD_ID     payload_id repeats an earlier record's
                             payload_id (first occurrence is canonical)
    MALFORMED_RECORD         an array element is not a JSON object

Exit codes: 0 no findings | 1 findings present | 2 invalid input/usage.
"""
import argparse
import decimal
import hashlib
import json
import sys

# ============================================================================
# REUSED from xrpl-address/address_validate.py, verbatim algorithm and names:
#   ALPHABET, ALPHABET_MAP, b58decode(), double_sha256(), classify(),
#   and the checksum/prefix/length checks that formed validate_address().
# See README "What was reused vs. what is new" for the precise diff.
# ============================================================================

ALPHABET = "rpshnaf39wBUDNEGHJKLM4PQRST7VWXYZ2bcdeCg65jkm8oFqi1tuvAxyz"
ALPHABET_MAP = {c: i for i, c in enumerate(ALPHABET)}

CLASSIC_PREFIX = 0x00
XADDR_PREFIX_MAIN = (0x05, 0x44)
XADDR_PREFIX_TEST = (0x04, 0x93)

CLASSIC_PAYLOAD_LEN = 21
XADDR_PAYLOAD_LEN = 31

BAD_ALPHABET = "BAD_ALPHABET"
BAD_LENGTH = "BAD_LENGTH"
BAD_PREFIX = "BAD_PREFIX"
BAD_CHECKSUM = "BAD_CHECKSUM"


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


def address_subissues(addr):
    """Return a sorted list of BAD_* sub-codes for an XRPL address string.
    Empty list means the address is structurally and cryptographically
    valid. This is address_validate.validate_address()'s core logic,
    minus the denylist step (out of scope here) and minus MALFORMED_RECORD
    (type/emptiness is handled by the caller before this is invoked).
    The caller collapses any non-empty result into a single INVALID_ADDRESS
    finding.
    """
    try:
        raw = b58decode(addr)
    except ValueError:
        return [BAD_ALPHABET]

    if len(raw) < 5:
        return [BAD_LENGTH]

    payload, checksum = raw[:-4], raw[-4:]
    kind, expected = classify(payload)

    issues = []
    if kind is None:
        issues.append(BAD_PREFIX)
    elif len(payload) != expected:
        issues.append(BAD_LENGTH)

    if double_sha256(payload)[:4] != checksum:
        issues.append(BAD_CHECKSUM)

    return sorted(issues)


# ============================================================================
# NEW: memo / field / amount validation and the CLI harness.
# ============================================================================

DEFAULT_MAX_MEMO_BYTES = 1024
MAX_DROPS = 10 ** 17  # 100 billion XRP, expressed in drops
HEXDIGITS = set("0123456789abcdefABCDEF")

INVALID_HEX = "INVALID_HEX"
MEMO_TOO_LARGE = "MEMO_TOO_LARGE"
MEMO_NOT_UTF8 = "MEMO_NOT_UTF8"
MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
INVALID_ADDRESS = "INVALID_ADDRESS"
SELF_PAYMENT = "SELF_PAYMENT"
INVALID_AMOUNT = "INVALID_AMOUNT"
AMOUNT_OUT_OF_RANGE = "AMOUNT_OUT_OF_RANGE"
DUPLICATE_PAYLOAD_ID = "DUPLICATE_PAYLOAD_ID"
MALFORMED_RECORD = "MALFORMED_RECORD"

ALL_CODES = [
    INVALID_HEX, MEMO_TOO_LARGE, MEMO_NOT_UTF8, MISSING_REQUIRED_FIELD,
    INVALID_ADDRESS, SELF_PAYMENT, INVALID_AMOUNT, AMOUNT_OUT_OF_RANGE,
    DUPLICATE_PAYLOAD_ID, MALFORMED_RECORD,
]

_MISSING = object()  # sentinel: "key absent from record", distinct from None/""


class InputError(Exception):
    """Raised for exit-code-2 conditions: unreadable/invalid/unusable input."""


def finding(code, field=None, detail=""):
    return {"code": code, "field": field, "detail": detail}


def fmt_decimal(d):
    """Canonical fixed-point string for a Decimal amount (never exponential)."""
    return format(d, "f")


def _reject_constant(name):
    # json.loads calls this for the bare tokens NaN / Infinity / -Infinity.
    # Raising here fails the whole parse, so such payloads are an exit-2
    # invalid-input condition rather than a per-record finding. A *quoted*
    # string "NaN" in an amount field is different -- see validate_amount_*.
    raise ValueError(f"disallowed JSON constant: {name}")


def parse_json_text(text):
    try:
        return json.loads(text, parse_float=decimal.Decimal,
                           parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise InputError(f"invalid JSON: {exc}")


def _strip_bom(text):
    """Strip a single leading UTF-8 BOM (U+FEFF) if present.
    Windows editors (Notepad, some PowerShell redirections) commonly
    prepend one; json.loads treats it as invalid JSON otherwise, which
    is a confusing failure mode for a perfectly valid payload file.
    """
    if text.startswith("\ufeff"):
        return text[1:]
    return text


def read_input_text(path):
    if path == "-":
        try:
            return _strip_bom(sys.stdin.read())
        except Exception as exc:
            raise InputError(f"could not read stdin: {exc}")
    try:
        # utf-8-sig transparently strips a leading BOM if present, and
        # behaves exactly like utf-8 if there is none.
        with open(path, "r", encoding="utf-8-sig") as fh:
            return fh.read()
    except FileNotFoundError:
        raise InputError(f"file not found: {path}")
    except IsADirectoryError:
        raise InputError(f"is a directory: {path}")
    except OSError as exc:
        raise InputError(f"could not read {path}: {exc}")


def _amount_to_decimal(value):
    """Coerce a JSON-parsed amount field to Decimal.
    Returns (decimal_or_None, error_detail_or_None).
    bool is explicitly rejected even though bool is a subclass of int in
    Python -- True/False must never be silently treated as 1/0 drops.
    """
    if isinstance(value, bool):
        return None, "amount must be numeric, got bool"
    if isinstance(value, int):
        return decimal.Decimal(value), None
    if isinstance(value, decimal.Decimal):
        return value, None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None, "amount string is empty"
        try:
            return decimal.Decimal(s), None
        except decimal.InvalidOperation:
            return None, f"amount is not a valid decimal number: {value!r}"
    return None, f"amount must be numeric, got {type(value).__name__}"


def validate_amount_drops(value):
    """Return (findings, drops_decimal_or_None) for an amount_drops value."""
    d, err = _amount_to_decimal(value)
    if d is None:
        return [finding(INVALID_AMOUNT, "amount_drops", err)], None
    if d.is_nan() or d.is_infinite():
        return [finding(INVALID_AMOUNT, "amount_drops",
                         "amount_drops is NaN/Infinity")], None
    if d < 0:
        return [finding(INVALID_AMOUNT, "amount_drops",
                         "amount_drops is negative")], None
    if d != d.to_integral_value():
        return [finding(INVALID_AMOUNT, "amount_drops",
                         "amount_drops must be a whole number of drops")], None
    drops = d.to_integral_value()
    findings = []
    if drops > MAX_DROPS:
        findings.append(finding(
            AMOUNT_OUT_OF_RANGE, "amount_drops",
            f"amount_drops {fmt_decimal(drops)} exceeds {MAX_DROPS} "
            f"(100 billion XRP expressed in drops)"))
    return findings, drops


def validate_amount_pft(value):
    """Return (findings, pft_decimal_or_None) for an amount_pft value.
    Unlike drops, PFT may be fractional; there is no upper-range check
    defined for it in this tool (see README limitations).
    """
    d, err = _amount_to_decimal(value)
    if d is None:
        return [finding(INVALID_AMOUNT, "amount_pft", err)], None
    if d.is_nan() or d.is_infinite():
        return [finding(INVALID_AMOUNT, "amount_pft",
                         "amount_pft is NaN/Infinity")], None
    if d < 0:
        return [finding(INVALID_AMOUNT, "amount_pft",
                         "amount_pft is negative")], None
    return [], d


def validate_memo_hex(value, max_memo_bytes):
    """Return (findings, decoded_bytes_or_None).
    No implicit whitespace stripping: internal or surrounding whitespace
    is treated as an invalid character, not tolerated the way
    bytes.fromhex() alone would tolerate it.
    """
    if not isinstance(value, str):
        return [finding(INVALID_HEX, "memo_hex",
                         f"memo_hex must be a string, got "
                         f"{type(value).__name__}")], None
    if len(value) % 2 != 0:
        return [finding(INVALID_HEX, "memo_hex",
                         f"memo_hex has odd length ({len(value)})")], None
    if value and not all(c in HEXDIGITS for c in value):
        return [finding(INVALID_HEX, "memo_hex",
                         "memo_hex contains non-hexadecimal characters")], None

    decoded = bytes.fromhex(value)

    findings = []
    if len(decoded) > max_memo_bytes:
        findings.append(finding(
            MEMO_TOO_LARGE, "memo_hex",
            f"decoded memo is {len(decoded)} bytes, limit is "
            f"{max_memo_bytes} (limit is inclusive: > is rejected, == is not)"))
    try:
        decoded.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        findings.append(finding(MEMO_NOT_UTF8, "memo_hex", str(exc)))
    return findings, decoded


def validate_record(record, index, max_memo_bytes):
    """Validate a single array element. Returns a result dict with keys:
    index, payload_id, findings, amount_drops, amount_pft.
    """
    if not isinstance(record, dict):
        return {
            "index": index,
            "payload_id": None,
            "findings": [finding(
                MALFORMED_RECORD, None,
                f"array element is not a JSON object, got "
                f"{type(record).__name__}")],
            "amount_drops": None,
            "amount_pft": None,
        }

    findings = []

    # --- payload_id ---------------------------------------------------
    payload_id = None
    if "payload_id" not in record:
        findings.append(finding(MISSING_REQUIRED_FIELD, "payload_id",
                                 "required field is missing"))
    else:
        pid = record["payload_id"]
        if isinstance(pid, str) and pid != "":
            payload_id = pid
        else:
            findings.append(finding(MISSING_REQUIRED_FIELD, "payload_id",
                                     "payload_id must be a non-empty string"))

    # --- memo_hex --------------------------------------------------------
    if "memo_hex" not in record:
        findings.append(finding(MISSING_REQUIRED_FIELD, "memo_hex",
                                 "required field is missing"))
    else:
        mfindings, _decoded = validate_memo_hex(record["memo_hex"], max_memo_bytes)
        findings.extend(mfindings)

    # --- account / destination --------------------------------------------
    account = record.get("account", _MISSING)
    destination = record.get("destination", _MISSING)
    for fname, fval in (("account", account), ("destination", destination)):
        if fval is _MISSING or fval == "":
            findings.append(finding(MISSING_REQUIRED_FIELD, fname,
                                     "required field is missing or empty"))
        elif not isinstance(fval, str):
            findings.append(finding(
                INVALID_ADDRESS, fname,
                f"{fname} must be a string, got {type(fval).__name__}"))
        else:
            sub = address_subissues(fval)
            if sub:
                findings.append(finding(
                    INVALID_ADDRESS, fname,
                    f"{fname} failed XRPL Base58Check: " + ",".join(sub)))

    if (isinstance(account, str) and isinstance(destination, str)
            and account != "" and destination != "" and account == destination):
        findings.append(finding(SELF_PAYMENT, "destination",
                                 "account and destination are identical"))

    # --- amount_drops / amount_pft ---------------------------------------
    has_drops = "amount_drops" in record
    has_pft = "amount_pft" in record
    amount_drops_out = None
    amount_pft_out = None
    if has_drops and has_pft:
        findings.append(finding(
            INVALID_AMOUNT, "amount",
            "both amount_drops and amount_pft are present; exactly one "
            "is required"))
    elif not has_drops and not has_pft:
        findings.append(finding(
            MISSING_REQUIRED_FIELD, "amount",
            "one of amount_drops or amount_pft is required"))
    elif has_drops:
        afindings, drops = validate_amount_drops(record["amount_drops"])
        findings.extend(afindings)
        if drops is not None:
            amount_drops_out = fmt_decimal(drops)
    else:
        afindings, pft = validate_amount_pft(record["amount_pft"])
        findings.extend(afindings)
        if pft is not None:
            amount_pft_out = fmt_decimal(pft)

    return {
        "index": index,
        "payload_id": payload_id,
        "findings": findings,
        "amount_drops": amount_drops_out,
        "amount_pft": amount_pft_out,
    }


def build_report(data, max_memo_bytes):
    results = [validate_record(r, i, max_memo_bytes) for i, r in enumerate(data)]

    # Duplicate payload_id detection: first occurrence is canonical; every
    # later record sharing that payload_id is flagged.
    seen = {}
    for r in results:
        pid = r["payload_id"]
        if pid is None:
            continue
        if pid not in seen:
            seen[pid] = r["index"]
        else:
            r["findings"].append(finding(
                DUPLICATE_PAYLOAD_ID, "payload_id",
                f"duplicate of payload_id first seen at index {seen[pid]}"))

    finding_counts = {}
    flat_findings = []
    for r in results:
        r["findings"].sort(key=lambda f: (f["code"], f["field"] or "", f["detail"]))
        r["ok"] = not r["findings"]
        for f in r["findings"]:
            finding_counts[f["code"]] = finding_counts.get(f["code"], 0) + 1
            flat_findings.append({
                "index": r["index"],
                "payload_id": r["payload_id"],
                "code": f["code"],
                "field": f["field"],
                "detail": f["detail"],
            })

    flat_findings.sort(key=lambda f: (f["index"], f["code"], f["field"] or "", f["detail"]))

    ok_count = sum(1 for r in results if r["ok"])
    total = len(results)

    return {
        "schema_version": "1.0",
        "max_memo_bytes": max_memo_bytes,
        "totals": {
            "payloads": total,
            "ok": ok_count,
            "with_findings": total - ok_count,
            "findings": len(flat_findings),
        },
        "finding_counts": finding_counts,
        "findings": flat_findings,
        "results": results,
        "status": "clean" if not flat_findings else "issues",
    }


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True) + "\n"


def nonneg_int(s):
    try:
        v = int(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid int value: {s!r}")
    if v < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return v


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Validate Post Fiat XRPL memo/transaction payloads "
                     "before task processing.")
    ap.add_argument("input", help='payload JSON file, or "-" for stdin')
    ap.add_argument("-o", "--output",
                     help="write report JSON to this file instead of stdout")
    ap.add_argument("--max-memo-bytes", type=nonneg_int,
                     default=DEFAULT_MAX_MEMO_BYTES,
                     help=f"max decoded memo size in bytes "
                          f"(default {DEFAULT_MAX_MEMO_BYTES})")
    args = ap.parse_args(argv)

    try:
        text = read_input_text(args.input)
        data = parse_json_text(text)
        if not isinstance(data, list):
            raise InputError(
                f"expected a JSON array at top level, got {type(data).__name__}")
    except InputError as exc:
        sys.stderr.write(f"INVALID_INPUT: {exc}\n")
        return 2

    report = build_report(data, args.max_memo_bytes)
    text_out = canonical_json(report)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text_out)
        sys.stdout.write(
            f"status={report['status']} ok={report['totals']['ok']} "
            f"findings={report['totals']['findings']}\n")
    else:
        sys.stdout.write(text_out)

    return 0 if report["status"] == "clean" else 1


if __name__ == "__main__":
    sys.exit(main())
