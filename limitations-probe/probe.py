#!/usr/bin/env python3
"""probe.py -- adversarial probe harness for the three tools that disclosed
no limitations: evidence-manifest, reward-reconciler, schema-checker.

Every limitation added to those three READMEs was found by running the tool,
not by reading it. This script IS that run. It builds each adversarial input
from scratch in a temp directory, invokes the sibling tool exactly as its own
README documents, and records what actually happened -- exit code, and the
one field of the tool's own report that settles the question.

Run it from a clone:

    python3 limitations-probe/probe.py            # human transcript to stdout
    python3 limitations-probe/probe.py -o probe_report.json   # canonical JSON

Exit codes:
    0  every probe reproduced its recorded outcome
    1  at least one probe did NOT reproduce (a tool changed, or was fixed)
    2  setup error -- a sibling tool is missing, so nothing was proved

Exit 1 is the interesting one. A probe that stops reproducing means the
finding it documents has been fixed (or the tool regressed differently); in
either case the matching README section is now wrong and needs re-checking.
That is the whole point of committing the harness rather than a screenshot.

No third-party packages. No network. No timestamps or durations in the JSON
report -- two runs on the same code produce byte-identical output, except for
PROBE SC-4, whose entire finding is that it does not finish (see below).
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PY = sys.executable or "python3"

MANIFEST = os.path.join(REPO, "evidence-manifest", "manifest.py")
RECONCILE = os.path.join(REPO, "reward-reconciler", "reconcile.py")
SCHEMA_CHECK = os.path.join(REPO, "schema-checker", "schema_check.py")

# SC-4 demonstrates an unbounded regex match. It is bounded HERE, by this
# harness, so the probe suite terminates; the tool itself has no such bound.
SC4_TIMEOUT = 20


class SetupError(Exception):
    pass


def run(argv, cwd=None, timeout=120):
    """Run a command. Returns (exit_code, stdout, stderr, timed_out)."""
    try:
        p = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "", "", True
    return p.returncode, p.stdout.decode("utf-8", "replace"), \
        p.stderr.decode("utf-8", "replace"), False


def write_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh)


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def jload(text):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


# --------------------------------------------------------------------------
# evidence-manifest
# --------------------------------------------------------------------------

def em_root(work, records, name):
    path = os.path.join(work, name)
    write_json(path, records)
    code, out, err, _ = run([PY, MANIFEST, "build", path], cwd=work)
    doc = jload(out)
    return code, (doc or {}).get("batch_root"), doc, err


def probe_em1(work):
    """Whitespace-only differences in evidence text collide on one root."""
    variants = {
        "two spaces": "hash  abc",
        "one space": "hash abc",
        "tab": "hash\tabc",
        "newline": "hash\nabc",
    }
    roots = {}
    for label, text in variants.items():
        _, root, _, _ = em_root(work, [{"submission_id": "S1", "evidence": text}],
                                "em1_%s.json" % label.replace(" ", "_"))
        roots[label] = root
    distinct = sorted(set(roots.values()))
    return {
        "id": "EM-1",
        "tool": "evidence-manifest",
        "question": "Do four evidence strings that differ only in whitespace "
                    "produce four different batch roots?",
        "observed": {"roots_by_variant": roots, "distinct_roots": len(distinct)},
        "expected": {"distinct_roots": 1},
        "reproduced": len(distinct) == 1,
        "means": "Four distinct evidence texts share one batch root, so the "
                 "tamper check cannot tell them apart.",
    }


def probe_em2(work):
    """Leading/trailing whitespace in an identifier collides."""
    _, a, _, _ = em_root(work, [{"submission_id": " S1 ", "evidence": "x"}], "em2_a.json")
    _, b, _, _ = em_root(work, [{"submission_id": "S1", "evidence": "x"}], "em2_b.json")
    return {
        "id": "EM-2",
        "tool": "evidence-manifest",
        "question": "Does ' S1 ' hash differently from 'S1' as a submission_id?",
        "observed": {"padded_id_root": a, "bare_id_root": b, "equal": a == b},
        "expected": {"equal": True},
        "reproduced": a == b and a is not None,
        "means": "An identifier carrying stray whitespace is the same "
                 "submission as far as the manifest is concerned.",
    }


def probe_em3(work):
    """A bare NaN survives into the manifest, which strict JSON parsers reject."""
    path = os.path.join(work, "em3.json")
    write_text(path, '[{"submission_id":"S1","score":NaN}]')
    code, out, _, _ = run([PY, MANIFEST, "build", path], cwd=work)
    has_bare_nan = "NaN" in out
    strict_ok = True
    strict_error = None
    try:
        json.loads(out, parse_constant=_reject_constant)
    except ValueError as exc:
        strict_ok = False
        strict_error = str(exc)
    return {
        "id": "EM-3",
        "tool": "evidence-manifest",
        "question": "Is a manifest built from a record containing bare NaN "
                    "still valid RFC 8259 JSON?",
        "observed": {"build_exit_code": code, "manifest_contains_bare_NaN": has_bare_nan,
                     "strict_parser_accepts": strict_ok, "strict_parser_error": strict_error},
        "expected": {"build_exit_code": 0, "manifest_contains_bare_NaN": True,
                     "strict_parser_accepts": False},
        "reproduced": code == 0 and has_bare_nan and not strict_ok,
        "means": "The build succeeds and emits a manifest containing a bare "
                 "NaN token. Node 22's JSON.parse and Python's json with a "
                 "raising parse_constant both reject it; Python's default "
                 "parser and jq 1.7 both accept it. Measured, not assumed -- "
                 "jq contradicted the first guess. See captured_output.txt.",
    }


def _reject_constant(name):
    raise ValueError("bare %s token" % name)


def probe_em4(work):
    """Two records with the same submission_id are accepted without comment."""
    code, root, doc, _ = em_root(
        work, [{"submission_id": "S1", "evidence": "a"},
               {"submission_id": "S1", "evidence": "b"}], "em4.json")
    entries = len((doc or {}).get("entries", []))
    return {
        "id": "EM-4",
        "tool": "evidence-manifest",
        "question": "Does a batch containing the same submission_id twice "
                    "produce a warning or a non-zero exit?",
        "observed": {"exit_code": code, "entries": entries},
        "expected": {"exit_code": 0, "entries": 2},
        "reproduced": code == 0 and entries == 2,
        "means": "Duplicate identifiers are baked into the root silently; "
                 "uniqueness is the caller's problem and is not stated.",
    }


# --------------------------------------------------------------------------
# reward-reconciler
# --------------------------------------------------------------------------

def rr(work, expected, payouts, tag):
    ep = os.path.join(work, "rr_%s_e.json" % tag)
    pp = os.path.join(work, "rr_%s_p.json" % tag)
    write_json(ep, expected)
    write_json(pp, payouts)
    code, out, err, _ = run([PY, RECONCILE, ep, pp], cwd=work)
    return code, jload(out), out, err


def probe_rr1(work):
    """A sub-microPFT discrepancy is quantized away and reported balanced."""
    code, doc, _, _ = rr(work,
                         [{"task_id": "T1", "wallet": "rW1", "amount": "1.0000004"}],
                         [{"task_id": "T1", "wallet": "rW1", "amount": "1.0000001"}],
                         "1")
    status = (doc or {}).get("status")
    findings = len((doc or {}).get("findings", []))
    return {
        "id": "RR-1",
        "tool": "reward-reconciler",
        "question": "Is an expected 1.0000004 against a paid 1.0000001 "
                    "reported as a mismatch?",
        "observed": {"exit_code": code, "status": status, "findings": findings},
        "expected": {"exit_code": 0, "status": "balanced", "findings": 0},
        "reproduced": code == 0 and status == "balanced" and findings == 0,
        "means": "Both amounts quantize to 1.000000, so a real difference "
                 "below the settlement scale disappears rather than being "
                 "rejected or reported.",
    }


def probe_rr2(work):
    """An out-of-range exponent is rejected with exit 2. REPAIRED FINDING.

    This probe used to assert the defect: exit 1, a Python traceback, no JSON.
    reward-reconciler now guards value.quantize(SCALE), so it asserts the
    repair instead. The entry is kept rather than deleted because a probe that
    pins a fix is what stops the fix from being undone -- and because
    reward-reconciler/README.md still carries the item, now marked FIXED, and
    the two have to agree.
    """
    code, doc, out, err = rr(work,
                             [{"task_id": "T1", "wallet": "rW1", "amount": "1E+999999999"}],
                             [{"task_id": "T1", "wallet": "rW1", "amount": "1"}],
                             "2")
    traceback = "Traceback" in err
    last = err.strip().splitlines()[-1] if err.strip() else ""
    try:
        error_code = json.loads(err)["error"]
    except (ValueError, KeyError, TypeError):
        error_code = None
    return {
        "id": "RR-2",
        "tool": "reward-reconciler",
        "question": "Does an amount of '1E+999999999' produce the documented "
                    "INVALID_INPUT report and exit 2?",
        "observed": {"exit_code": code, "python_traceback": traceback,
                     "last_stderr_line": last, "stderr_error_code": error_code},
        "expected": {"exit_code": 2, "python_traceback": False,
                     "stderr_error_code": "INVALID_INPUT"},
        "reproduced": code == 2 and not traceback and error_code == "INVALID_INPUT",
        "means": "value.quantize(SCALE) used to sit outside the try/except "
                 "that catches InvalidOperation, so a malformed amount raised "
                 "an uncaught exception and exited 1 -- the code a caller "
                 "reads as 'mismatches found', not 'bad input'. It is now "
                 "inside a guard of its own and the run exits 2 with a JSON "
                 "INVALID_INPUT report.",
    }


def probe_rr3(work):
    """Negative amounts are accepted and reported balanced."""
    rec = [{"task_id": "T1", "wallet": "rW1", "amount": "-5"}]
    code, doc, _, _ = rr(work, rec, rec, "3")
    status = (doc or {}).get("status")
    total = ((doc or {}).get("totals") or {}).get("expected_total")
    return {
        "id": "RR-3",
        "tool": "reward-reconciler",
        "question": "Is a negative reward amount rejected?",
        "observed": {"exit_code": code, "status": status, "expected_total": total},
        "expected": {"exit_code": 0, "status": "balanced", "expected_total": "-5.000000"},
        "reproduced": code == 0 and status == "balanced" and total == "-5.000000",
        "means": "There is no sign or range check on amounts. A negative "
                 "expected reward reconciles cleanly against a negative payout.",
    }


def probe_rr4(work):
    """Split payouts to the WRONG wallet report DUPLICATE_PAYOUT naming the
    expected wallet, never the wallet that was actually paid."""
    code, doc, _, _ = rr(work,
                         [{"task_id": "T1", "wallet": "rHONEST", "amount": "3.500000"}],
                         [{"task_id": "T1", "wallet": "rATTACKER", "amount": "1.750000"},
                          {"task_id": "T1", "wallet": "rATTACKER", "amount": "1.750000"}],
                         "4")
    findings = (doc or {}).get("findings", [])
    codes = sorted({f.get("issue") for f in findings})
    wallets = sorted({f.get("wallet") for f in findings})
    report_text = json.dumps(doc, sort_keys=True) if doc else ""
    return {
        "id": "RR-4",
        "tool": "reward-reconciler",
        "question": "When a task's payout is split across two records that go "
                    "to a DIFFERENT wallet than expected, does the report name "
                    "the wallet that was actually paid?",
        "observed": {"exit_code": code, "issues": codes, "wallets_named": wallets,
                     "paid_wallet_appears_in_report": "rATTACKER" in report_text},
        "expected": {"exit_code": 1, "issues": ["DUPLICATE_PAYOUT"],
                     "wallets_named": ["rHONEST"],
                     "paid_wallet_appears_in_report": False},
        "reproduced": (code == 1 and codes == ["DUPLICATE_PAYOUT"]
                       and wallets == ["rHONEST"] and "rATTACKER" not in report_text),
        "means": "In the split-payout branch the wallet check never runs. The "
                 "finding says DUPLICATE_PAYOUT and prints the EXPECTED wallet, "
                 "so a reader triaging the report never learns the money went "
                 "somewhere else. WALLET_MISMATCH does not fire.",
    }


# --------------------------------------------------------------------------
# schema-checker
# --------------------------------------------------------------------------

def sc(work, schema, payload, tag, timeout=120):
    sp = os.path.join(work, "sc_%s_s.json" % tag)
    pp = os.path.join(work, "sc_%s_p.json" % tag)
    write_json(sp, schema)
    write_json(pp, payload)
    code, out, err, timed_out = run([PY, SCHEMA_CHECK, sp, pp], cwd=work, timeout=timeout)
    return code, jload(out), timed_out


def probe_sc1(work):
    """'pattern' is a search, not a full match."""
    schema = {"name": "t", "version": 1, "root": {
        "type": "object", "required": ["id"],
        "properties": {"id": {"type": "string", "pattern": "[0-9]{4}"}}}}
    _, doc, _ = sc(work, schema, {"id": "XX1234XX"}, "1")
    status = (doc or {}).get("status")
    return {
        "id": "SC-1",
        "tool": "schema-checker",
        "question": "Does pattern '[0-9]{4}' reject the value 'XX1234XX'?",
        "observed": {"status": status, "violations": len((doc or {}).get("violations", []))},
        "expected": {"status": "conform", "violations": 0},
        "reproduced": status == "conform",
        "means": "pattern is matched with a SEARCH, so every unanchored pattern "
                 "in a schema is far more permissive than it reads. The repo's "
                 "own fixture schema anchors all of its patterns with ^...$, "
                 "which is exactly why its own tests never surface this.",
    }


def probe_sc2(work):
    """max_length counts code points, not UTF-8 bytes."""
    schema = {"name": "t", "version": 1, "root": {
        "type": "object", "required": ["s"],
        "properties": {"s": {"type": "string", "max_length": 4}}}}
    rows = []
    for label, value in (("4 ASCII", "abcd"),
                         ("4 x U+00E9", "é" * 4),
                         ("4 x U+1F600", "\U0001F600" * 4)):
        _, doc, _ = sc(work, schema, {"s": value}, "2_" + label.replace(" ", "_"))
        rows.append({"case": label, "utf8_bytes": len(value.encode("utf-8")),
                     "status": (doc or {}).get("status")})
    all_conform = all(r["status"] == "conform" for r in rows)
    max_bytes = max(r["utf8_bytes"] for r in rows)
    return {
        "id": "SC-2",
        "tool": "schema-checker",
        "question": "Under max_length 4, how many UTF-8 bytes can a conforming "
                    "string actually occupy?",
        "observed": {"cases": rows, "all_conform": all_conform, "max_bytes_accepted": max_bytes},
        "expected": {"all_conform": True, "max_bytes_accepted": 16},
        "reproduced": all_conform and max_bytes == 16,
        "means": "The unit is Python code points. A field sized against a byte "
                 "budget -- a database column, an on-chain memo -- can be "
                 "overrun 4x by a payload this checker calls conform.",
    }


def probe_sc3(work):
    """A schema-supplied pattern can run for an unbounded time."""
    schema = {"name": "t", "version": 1, "root": {
        "type": "object", "required": ["s"],
        "properties": {"s": {"type": "string", "pattern": "^(a+)+$"}}}}
    code, doc, timed_out = sc(work, schema, {"s": "a" * 32 + "!"}, "3", timeout=SC4_TIMEOUT)
    return {
        "id": "SC-3",
        "tool": "schema-checker",
        "question": "Does the checker terminate on the pattern '^(a+)+$' "
                    "against a 33-character non-matching string?",
        "observed": {"timed_out_after_seconds": SC4_TIMEOUT if timed_out else None,
                     "exit_code": code},
        "expected": {"timed_out_after_seconds": SC4_TIMEOUT},
        "reproduced": timed_out,
        "means": "re.compile() is validated when the schema loads, but there is "
                 "no bound on match time. A schema is an input; a hostile or "
                 "merely careless one hangs the checker indefinitely. The "
                 "timeout in this probe belongs to the HARNESS, not the tool.",
    }


def probe_sc4(work):
    """Negative result, reported as one: bools and integral floats ARE rejected."""
    schema = {"name": "t", "version": 1, "root": {
        "type": "object", "required": ["n"],
        "properties": {"n": {"type": "integer", "minimum": 0}}}}
    rows = []
    for label, value in (("true", True), ("5.0", 5.0), ("1e400", float("1e400"))):
        _, doc, _ = sc(work, schema, {"n": value}, "4_" + label)
        codes = sorted({v.get("code") for v in (doc or {}).get("violations", [])})
        rows.append({"case": label, "status": (doc or {}).get("status"), "codes": codes})
    all_rejected = all(r["status"] == "violations" for r in rows)
    return {
        "id": "SC-4",
        "tool": "schema-checker",
        "question": "Does type 'integer' wrongly accept JSON true, or an "
                    "integral float like 5.0?",
        "observed": {"cases": rows, "all_rejected": all_rejected},
        "expected": {"all_rejected": True},
        "reproduced": all_rejected,
        "means": "NEGATIVE RESULT, recorded deliberately. bool is a subclass of "
                 "int in Python and 5.0 == 5, so both are easy to get wrong. "
                 "This checker gets both right. No limitation is claimed here.",
    }


PROBES = [probe_em1, probe_em2, probe_em3, probe_em4,
          probe_rr1, probe_rr2, probe_rr3, probe_rr4,
          probe_sc1, probe_sc2, probe_sc3, probe_sc4]


def check_tools():
    missing = [p for p in (MANIFEST, RECONCILE, SCHEMA_CHECK) if not os.path.isfile(p)]
    if missing:
        raise SetupError("sibling tool(s) not found, nothing can be proved: %s"
                         % ", ".join(os.path.relpath(m, REPO) for m in missing))


def build_report():
    check_tools()
    with tempfile.TemporaryDirectory(prefix="probe_") as work:
        results = [fn(work) for fn in PROBES]
    by_tool = {}
    for r in results:
        by_tool.setdefault(r["tool"], {"probes": 0, "reproduced": 0})
        by_tool[r["tool"]]["probes"] += 1
        by_tool[r["tool"]]["reproduced"] += 1 if r["reproduced"] else 0
    all_ok = all(r["reproduced"] for r in results)
    return {
        "schema_version": 1,
        "tool": "limitations-probe",
        "status": "all_reproduced" if all_ok else "divergent",
        "probes": len(results),
        "reproduced": sum(1 for r in results if r["reproduced"]),
        "by_tool": by_tool,
        "results": sorted(results, key=lambda r: r["id"]),
    }, all_ok


def transcript(report):
    lines = []
    lines.append("limitations-probe -- %d probes, %d reproduced"
                 % (report["probes"], report["reproduced"]))
    lines.append("=" * 70)
    for r in report["results"]:
        lines.append("")
        lines.append("%s  [%s]  %s" % (r["id"], r["tool"],
                                       "REPRODUCED" if r["reproduced"] else "DID NOT REPRODUCE"))
        lines.append("  Q: " + r["question"])
        lines.append("  observed: " + json.dumps(r["observed"], sort_keys=True))
        lines.append("  means:    " + r["means"])
    lines.append("")
    lines.append("=" * 70)
    for tool in sorted(report["by_tool"]):
        c = report["by_tool"][tool]
        lines.append("%-20s %d/%d reproduced" % (tool, c["reproduced"], c["probes"]))
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="probe.py")
    ap.add_argument("-o", "--out", default=None,
                    help="write the canonical JSON report here (default: human transcript to stdout)")
    args = ap.parse_args(argv)
    try:
        report, all_ok = build_report()
    except SetupError as exc:
        sys.stdout.write(json.dumps(
            {"schema_version": 1, "tool": "limitations-probe",
             "status": "error", "error": str(exc)},
            sort_keys=True, separators=(",", ":")) + "\n")
        return 2
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(report, sort_keys=True, separators=(",", ":"),
                                ensure_ascii=True) + "\n")
    else:
        sys.stdout.write(transcript(report))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
