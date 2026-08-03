#!/usr/bin/env python3
"""contradict.py -- cross-checker contradiction detector.

Runs a fixed set of *existing* stdlib-only checker tools (vendored,
unmodified, under ``checkers/`` -- or a live checkout pointed to with
``--checkers-root``) against the same case data and reports where two
checkers make INCOMPATIBLE claims about the same proposition.

This tool never re-implements or overrides checker logic. It only:
  1. discovers which checkers are applicable to a case (their required
     input files are present),
  2. runs them as subprocesses and parses their canonical JSON reports,
  3. runs a small set of *comparators* -- each comparator understands
     exactly two checkers whose scopes are known (from reading their
     source and READMEs) to genuinely overlap on one proposition -- and
  4. emits CONTRADICTION issues only when both checkers assert something
     about the SAME proposition (same task_id / submission_id / field /
     value) and their claims cannot both be true, or SCOPE_DIVERGENCE
     when they merely differ because their scopes differ (not a
     contradiction, does not affect the exit code).

See README.md for the full contradiction/scope-divergence definitions
and the overlap map this tool encodes.

Python 3 standard library only.
"""

import argparse
import json
import os
import subprocess
import sys

TOOL_VERSION = "1.0.0"
REPORT_VERSION = "1.0"

# Fixed, non-wall-clock reference timestamp handed to staleness-monitor's
# required --now flag. It is a constant baked into this tool, never read
# from the system clock, so repeat runs are always byte-identical.
REFERENCE_NOW = "2026-06-01T00:00:00Z"

DEFAULT_TIMEOUT = 20.0

EXIT_AGREE = 0
EXIT_CONTRADICTIONS = 1
EXIT_ERROR = 2

# --------------------------------------------------------------------------
# Contradiction / scope-divergence codes
# --------------------------------------------------------------------------
VALIDITY_CONTRADICTION = "VALIDITY_CONTRADICTION"
LINKAGE_CONTRADICTION = "LINKAGE_CONTRADICTION"
AMOUNT_CONTRADICTION = "AMOUNT_CONTRADICTION"
TIMESTAMP_CONTRADICTION = "TIMESTAMP_CONTRADICTION"
IDENTITY_CONTRADICTION = "IDENTITY_CONTRADICTION"
EXECUTION_FAILURE = "EXECUTION_FAILURE"
CHECKER_UNAVAILABLE = "CHECKER_UNAVAILABLE"
SCOPE_DIVERGENCE = "SCOPE_DIVERGENCE"

CONTRADICTION_CODES = frozenset({
    VALIDITY_CONTRADICTION,
    LINKAGE_CONTRADICTION,
    AMOUNT_CONTRADICTION,
    TIMESTAMP_CONTRADICTION,
    IDENTITY_CONTRADICTION,
})
EXECUTION_CODES = frozenset({EXECUTION_FAILURE, CHECKER_UNAVAILABLE})
ALL_CODES = CONTRADICTION_CODES | EXECUTION_CODES | frozenset({SCOPE_DIVERGENCE})


class InputError(Exception):
    """Raised for anything that should make contradict.py exit 2."""


# --------------------------------------------------------------------------
# Canonical JSON
# --------------------------------------------------------------------------

def canonical_dumps(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True) + "\n"


# --------------------------------------------------------------------------
# Checker adapter registry
#
# Each adapter describes how to invoke ONE vendored checker against a
# case directory. ``required_inputs`` are case-relative filenames that
# must all be present for the checker to be "applicable" to that case.
# ``args`` are the argv tokens passed after the script path; they are
# case-relative filenames or literal flags -- never absolute paths, and
# the subprocess is always launched with cwd=case_dir so any path a
# checker itself echoes back (e.g. in an error message) stays relative.
# --------------------------------------------------------------------------

ADAPTERS = {
    "preflight": {
        "dirname": "preflight",
        "script": "preflight.py",
        "required_inputs": ("tasks.json", "evidence.json"),
        "args": ("tasks.json", "evidence.json"),
    },
    "link-integrity": {
        "dirname": "link-integrity",
        "script": "link_integrity.py",
        "required_inputs": ("lifecycle.json", "evidence.json"),
        "args": ("lifecycle.json", "evidence.json"),
    },
    "lifecycle-linter": {
        "dirname": "lifecycle-linter",
        "script": "lifecycle_lint.py",
        "required_inputs": ("events.jsonl",),
        "args": ("events.jsonl",),
    },
    "event-linter": {
        "dirname": "event-linter",
        "script": "event_lint.py",
        "required_inputs": ("events.json",),
        "args": ("events.json",),
    },
    "queue-auditor": {
        "dirname": "queue-auditor",
        "script": "queue_audit.py",
        "required_inputs": ("queue_tasks.json",),
        "args": ("queue_tasks.json",),
    },
    "staleness-monitor": {
        "dirname": "staleness-monitor",
        "script": "staleness.py",
        "required_inputs": ("staleness_tasks.json",),
        "args": ("staleness_tasks.json", "--now", REFERENCE_NOW),
    },
    "reward-reconciler": {
        "dirname": "reward-reconciler",
        "script": "reconcile.py",
        "required_inputs": ("expected_rewards.json", "recorded_payouts.json"),
        "args": ("expected_rewards.json", "recorded_payouts.json"),
    },
    "reward-anomaly": {
        "dirname": "reward-anomaly",
        "script": "reconcile_anomaly.py",
        "required_inputs": ("reward_tasks.json", "reward_payouts.json"),
        "args": ("reward_tasks.json", "reward_payouts.json", "--tolerance", "0"),
    },
    "evidence-scorer": {
        "dirname": "evidence-scorer",
        "script": "score_evidence.py",
        "required_inputs": ("submissions.json",),
        "args": ("submissions.json",),
    },
    "dup-detector": {
        "dirname": "dup-detector",
        "script": "dupdetect.py",
        "required_inputs": ("submissions.json",),
        "args": ("submissions.json", "--threshold", "0.6"),
    },
}

KNOWN_INPUT_FILENAMES = frozenset(
    name for adapter in ADAPTERS.values() for name in adapter["required_inputs"]
)

DEFAULT_CHECKERS_ROOT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "checkers"
)


def discover_checker_path(adapter_id, checkers_root):
    """Return the absolute script path for adapter_id, or None if missing.

    Always returns an absolute path (even if ``checkers_root`` was given
    as a relative path) -- run_checker() launches the subprocess with
    cwd set to the *case* directory, not the caller's original working
    directory, so a relative script path would silently resolve against
    the wrong base and make Python itself fail to find the script
    (observed in practice: it exits 2 with "can't open file", which
    run_checker then -- correctly, but for the wrong underlying reason
    -- reports as the checker rejecting its input). Resolving to an
    absolute path here up front removes that trap entirely.
    """
    adapter = ADAPTERS[adapter_id]
    path = os.path.abspath(
        os.path.join(checkers_root, adapter["dirname"], adapter["script"])
    )
    if os.path.isfile(path):
        return path
    return None


def is_applicable(adapter_id, case_dir):
    adapter = ADAPTERS[adapter_id]
    return all(
        os.path.isfile(os.path.join(case_dir, name))
        for name in adapter["required_inputs"]
    )


# --------------------------------------------------------------------------
# Running a checker
# --------------------------------------------------------------------------

def run_checker(adapter_id, case_dir, checkers_root, timeout=DEFAULT_TIMEOUT,
                 python_executable=None):
    """Run one checker against a case. Never raises for ordinary failures.

    Returns a dict:
      {"id": adapter_id, "state": "ok"|"unavailable"|"execution_failure",
       "returncode": int|None, "report": dict|None, "detail": str|None}

    ``detail`` is always a tool-authored, generic sentence -- never raw
    subprocess stderr/stdout -- so absolute paths a checker might print
    in its own error messages can never leak into contradict.py's report.
    """
    script_path = discover_checker_path(adapter_id, checkers_root)
    if script_path is None:
        return {
            "id": adapter_id, "state": "unavailable", "returncode": None,
            "report": None,
            "detail": "checker script not found under checkers root",
        }

    adapter = ADAPTERS[adapter_id]
    exe = python_executable or sys.executable or "python3"
    argv = [exe, script_path] + list(adapter["args"])

    try:
        proc = subprocess.run(
            argv, cwd=case_dir, capture_output=True, text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "id": adapter_id, "state": "execution_failure", "returncode": None,
            "report": None,
            "detail": "checker timed out after {}s".format(timeout),
        }
    except OSError:
        return {
            "id": adapter_id, "state": "execution_failure", "returncode": None,
            "report": None,
            "detail": "checker could not be started",
        }

    rc = proc.returncode

    if rc not in (0, 1, 2):
        return {
            "id": adapter_id, "state": "execution_failure", "returncode": rc,
            "report": None,
            "detail": "checker exited with unexpected code {}".format(rc),
        }

    if rc == 2:
        return {
            "id": adapter_id, "state": "execution_failure", "returncode": rc,
            "report": None,
            "detail": "checker reported invalid input or a usage error (exit 2)",
        }

    try:
        report = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return {
            "id": adapter_id, "state": "execution_failure", "returncode": rc,
            "report": None,
            "detail": "checker produced non-JSON output on stdout",
        }

    if not isinstance(report, dict):
        return {
            "id": adapter_id, "state": "execution_failure", "returncode": rc,
            "report": None,
            "detail": "checker's JSON output was not an object",
        }

    return {
        "id": adapter_id, "state": "ok", "returncode": rc,
        "report": report, "detail": None,
    }


# --------------------------------------------------------------------------
# Issue construction helper
# --------------------------------------------------------------------------

def make_issue(code, checkers, subject=None, claims=None, message=""):
    """Build one uniformly-shaped issue dict.

    ``checkers`` is sorted so issue identity does not depend on which
    order comparators were registered in. ``subject`` and ``claims`` may
    be omitted (None) for execution-category issues that concern a whole
    checker rather than one proposition.
    """
    return {
        "checkers": sorted(checkers),
        "claims": claims or {},
        "code": code,
        "message": message,
        "subject": subject or {},
    }


def issue_sort_key(issue):
    return (
        issue["code"],
        tuple(issue["checkers"]),
        json.dumps(issue["subject"], sort_keys=True, ensure_ascii=True),
        json.dumps(issue["claims"], sort_keys=True, ensure_ascii=True),
        issue["message"],
    )


# --------------------------------------------------------------------------
# Amount parsing helper (avoid float; compare via string normalization
# through Decimal so "12" and "12.000000" compare equal)
# --------------------------------------------------------------------------

def _amounts_differ(a, b):
    from decimal import Decimal, InvalidOperation
    if a is None or b is None:
        return a != b
    try:
        return Decimal(a) != Decimal(b)
    except (InvalidOperation, TypeError, ValueError):
        return a != b


def _safe_get(d, *path):
    cur = d
    for p in path:
        if not isinstance(cur, dict) and not isinstance(cur, list):
            return None
        try:
            cur = cur[p]
        except (KeyError, IndexError, TypeError):
            return None
    return cur


# --------------------------------------------------------------------------
# Comparators
#
# Each comparator is registered against exactly the two checker ids whose
# scopes were confirmed (by reading their source) to overlap on one real
# proposition. A comparator is only invoked when BOTH checkers ran
# successfully (state == "ok") for the case; if either is unavailable or
# failed to execute, no comparison is attempted (that failure is already
# surfaced as its own CHECKER_UNAVAILABLE / EXECUTION_FAILURE issue).
# --------------------------------------------------------------------------

def compare_linkage(reports):
    """preflight vs link-integrity: does evidence for submission S link to
    a known task?

    preflight's answer comes from its own task export (``tasks.json``);
    link-integrity's answer comes from a lifecycle export
    (``lifecycle.json``). Both are asked, in effect, "have you heard of
    the task this evidence claims to belong to?" -- that is the same
    proposition even though they consult different source files, which
    is exactly the kind of split-brain a real pipeline can develop (a
    task exists in the task queue but never got a lifecycle event, or
    vice versa).
    """
    issues = []
    pf = reports["preflight"]
    li = reports["link-integrity"]

    pf_orphans = set()
    for iss in pf.get("issues", []):
        if iss.get("code") == "ORPHAN_EVIDENCE":
            pf_orphans.add(iss.get("submission_id"))

    li_unknown = {}
    for v in li.get("violations", []):
        if v.get("code") == "UNKNOWN_TASK_REFERENCE":
            li_unknown[v.get("submission_id")] = v.get("task_id")

    all_submission_ids = pf_orphans | set(li_unknown)
    for sid in sorted(sid for sid in all_submission_ids if sid is not None):
        pf_says_orphan = sid in pf_orphans
        li_says_orphan = sid in li_unknown
        if pf_says_orphan != li_says_orphan:
            issues.append(make_issue(
                LINKAGE_CONTRADICTION,
                ("preflight", "link-integrity"),
                subject={"submission_id": sid},
                claims={
                    "preflight": "orphaned" if pf_says_orphan else "linked",
                    "link-integrity": "orphaned" if li_says_orphan else "linked",
                },
                message=(
                    "preflight and link-integrity disagree on whether "
                    "evidence submission {!r} links to a known task: "
                    "preflight's task export says {}, link-integrity's "
                    "lifecycle export says {}".format(
                        sid,
                        "orphaned" if pf_says_orphan else "linked",
                        "orphaned" if li_says_orphan else "linked",
                    )
                ),
            ))

    # SCOPE_DIVERGENCE: preflight checks evidence_type against the task's
    # required_evidence list; link-integrity deliberately does not (its
    # README states it does not validate evidence_type/value shape at
    # all). A mismatch here is not a contradiction -- link-integrity
    # never claimed anything about evidence_type.
    for iss in pf.get("issues", []):
        if iss.get("code") == "EVIDENCE_TYPE_MISMATCH":
            issues.append(make_issue(
                SCOPE_DIVERGENCE,
                ("preflight", "link-integrity"),
                subject={"submission_id": iss.get("submission_id"),
                         "task_id": iss.get("task_id")},
                claims={"preflight": "EVIDENCE_TYPE_MISMATCH"},
                message=(
                    "preflight flagged an evidence_type mismatch for "
                    "submission {!r}; link-integrity has no evidence_type "
                    "concept (out of its scope by design), so it is silent "
                    "-- not a disagreement.".format(iss.get("submission_id"))
                ),
            ))
    return issues


def compare_amount(reports):
    """reward-reconciler vs reward-anomaly: what amount was actually paid
    out for task T?

    Both tools independently read a payout record for the same task_id
    and, when they flag a mismatch, both state the amount they saw paid.
    If those stated amounts differ, two systems of record disagree about
    a fact that can only have one true value -- a genuine AMOUNT_CONTRADICTION,
    not just "both found a problem".
    """
    issues = []
    rr = reports["reward-reconciler"]
    ra = reports["reward-anomaly"]

    rr_amounts = {}
    for f in rr.get("findings", []):
        if f.get("issue") == "AMOUNT_MISMATCH":
            rr_amounts[f.get("task_id")] = f.get("payout_amount")

    ra_amounts = {}
    for f in ra.get("findings", []):
        if f.get("code") in ("AMOUNT_ABOVE_PRICE", "AMOUNT_BELOW_PRICE"):
            ra_amounts[f.get("task_id")] = f.get("amount")

    for task_id in sorted(set(rr_amounts) & set(ra_amounts)):
        a = rr_amounts[task_id]
        b = ra_amounts[task_id]
        if _amounts_differ(a, b):
            issues.append(make_issue(
                AMOUNT_CONTRADICTION,
                ("reward-reconciler", "reward-anomaly"),
                subject={"task_id": task_id},
                claims={"reward-reconciler": a, "reward-anomaly": b},
                message=(
                    "reward-reconciler and reward-anomaly report different "
                    "paid amounts for task {!r}: {!r} vs {!r}".format(
                        task_id, a, b
                    )
                ),
            ))

    # SCOPE_DIVERGENCE: wallet-identity claims are exclusive to
    # reward-reconciler; reward-anomaly's record schema has no wallet
    # field at all, so it can neither confirm nor contradict a
    # WALLET_MISMATCH finding.
    for f in rr.get("findings", []):
        if f.get("issue") == "WALLET_MISMATCH":
            issues.append(make_issue(
                SCOPE_DIVERGENCE,
                ("reward-reconciler", "reward-anomaly"),
                subject={"task_id": f.get("task_id")},
                claims={"reward-reconciler": "WALLET_MISMATCH"},
                message=(
                    "reward-reconciler flagged a wallet mismatch for task "
                    "{!r}; reward-anomaly has no wallet field in its "
                    "record schema and cannot judge this claim.".format(
                        f.get("task_id")
                    )
                ),
            ))
    return issues


def compare_timestamp(reports):
    """link-integrity vs lifecycle-linter: is this lifecycle timestamp
    valid?

    link-integrity strictly parses every lifecycle 'at' timestamp with an
    RFC3339-ish regex and flags IMPOSSIBLE_TIMESTAMP for anything that
    does not parse. lifecycle-linter never validates timestamp *format*
    at all -- it only compares occurred_at strings for ordering -- so a
    syntactically-impossible timestamp that still sorts fine lexically
    sails through lifecycle-linter with no complaint whatsoever. That is
    a genuine disagreement about whether the same value is a valid
    timestamp, not a difference of scope: both tools do claim to look at
    task timelines.
    """
    issues = []
    li = reports["link-integrity"]
    ll = reports["lifecycle-linter"]

    ll_task_ids_with_findings = set(
        f.get("task_id") for f in ll.get("findings", [])
    )

    for v in li.get("violations", []):
        if v.get("code") != "IMPOSSIBLE_TIMESTAMP":
            continue
        if v.get("source") != "lifecycle":
            continue
        task_id = v.get("task_id")
        if task_id not in ll_task_ids_with_findings:
            issues.append(make_issue(
                TIMESTAMP_CONTRADICTION,
                ("link-integrity", "lifecycle-linter"),
                subject={"task_id": task_id, "value": v.get("value")},
                claims={
                    "link-integrity": "invalid: {}".format(v.get("reason")),
                    "lifecycle-linter": "accepted (no findings for this task)",
                },
                message=(
                    "link-integrity rejects timestamp {!r} for task {!r} "
                    "as impossible ({}), but lifecycle-linter's report for "
                    "the same task has no findings at all -- it treated the "
                    "same value as an ordinary, valid point in the "
                    "timeline.".format(v.get("value"), task_id, v.get("reason"))
                ),
            ))
    return issues


def compare_lifecycle_scope(reports):
    """lifecycle-linter vs event-linter: same transition graph, same
    input events, but genuinely different vocabulary for "duplicate".

    lifecycle-linter's DUPLICATE_STATE fires on any immediate back-to-back
    repeat of a state regardless of timestamp. event-linter's
    DUPLICATE_EVENT only fires when both state AND occurred_at repeat
    exactly. A same-state-different-timestamp repeat is therefore
    DUPLICATE_STATE-only by design -- both READMEs document this as a
    deliberate granularity difference, not a bug. This is the textbook
    SCOPE_DIVERGENCE case: it must never raise a contradiction.
    """
    issues = []
    ll = reports["lifecycle-linter"]
    el = reports["event-linter"]

    el_dup_task_ids = set()
    for task in el.get("tasks", []):
        for v in task.get("violations", []):
            if v.get("violation") == "DUPLICATE_EVENT":
                el_dup_task_ids.add(task.get("task_id"))

    for f in ll.get("findings", []):
        if f.get("code") != "DUPLICATE_STATE":
            continue
        task_id = f.get("task_id")
        if task_id not in el_dup_task_ids:
            issues.append(make_issue(
                SCOPE_DIVERGENCE,
                ("event-linter", "lifecycle-linter"),
                subject={"task_id": task_id},
                claims={"lifecycle-linter": "DUPLICATE_STATE"},
                message=(
                    "lifecycle-linter flagged an immediate back-to-back "
                    "state repeat for task {!r}; event-linter's "
                    "DUPLICATE_EVENT only fires on an exact (state, "
                    "occurred_at) repeat, so a repeat with a different "
                    "timestamp is invisible to it by design -- not a "
                    "disagreement.".format(task_id)
                ),
            ))
    return issues


def compare_validity(reports):
    """queue-auditor vs staleness-monitor: is this record's deadline
    field structurally valid?

    queue-auditor requires 'deadline' to be present and a non-empty
    string; a null deadline is a MALFORMED_RECORD. staleness-monitor's
    own docstring calls out deadline as "the only [key] allowed to hold
    null" -- a null deadline is fully valid to it (it just means no
    checks are run). Same field, same record, same value, opposite
    validity verdicts.
    """
    issues = []
    qa = reports["queue-auditor"]
    sm = reports["staleness-monitor"]

    qa_deadline_malformed_tasks = set()
    for f in qa.get("findings", []):
        if f.get("code") == "MALFORMED_RECORD" and "'deadline'" in (f.get("detail") or ""):
            qa_deadline_malformed_tasks.add(f.get("task_id"))

    sm_findings_by_task = {}
    for bucket in ("critical", "warning", "info"):
        for f in sm.get("findings", {}).get(bucket, []):
            sm_findings_by_task.setdefault(f.get("task_id"), []).append(f)

    for task_id in sorted(t for t in qa_deadline_malformed_tasks if t is not None):
        sm_task_findings = sm_findings_by_task.get(task_id, [])
        sm_flagged_deadline = any(
            f.get("code") == "MALFORMED_DEADLINE" for f in sm_task_findings
        )
        if not sm_flagged_deadline:
            issues.append(make_issue(
                VALIDITY_CONTRADICTION,
                ("queue-auditor", "staleness-monitor"),
                subject={"task_id": task_id, "field": "deadline"},
                claims={
                    "queue-auditor": "malformed (missing/null deadline)",
                    "staleness-monitor": "valid (null deadline is permitted)",
                },
                message=(
                    "queue-auditor treats task {!r}'s null/missing "
                    "'deadline' as a MALFORMED_RECORD; staleness-monitor "
                    "treats a null deadline on the same record as fully "
                    "valid.".format(task_id)
                ),
            ))

    # SCOPE_DIVERGENCE: staleness-monitor's time-window breaches
    # (OVERDUE_PROPOSED / STALE_ACCEPTED / STALE_SUBMITTED) have no
    # counterpart in queue-auditor, which never reasons about elapsed
    # time at all.
    for task_id, findings in sm_findings_by_task.items():
        for f in findings:
            if f.get("code") in ("OVERDUE_PROPOSED", "STALE_ACCEPTED", "STALE_SUBMITTED"):
                issues.append(make_issue(
                    SCOPE_DIVERGENCE,
                    ("queue-auditor", "staleness-monitor"),
                    subject={"task_id": task_id},
                    claims={"staleness-monitor": f.get("code")},
                    message=(
                        "staleness-monitor flagged a time-window breach "
                        "({}) for task {!r}; queue-auditor never reasons "
                        "about elapsed time and has nothing to say about "
                        "it.".format(f.get("code"), task_id)
                    ),
                ))
    return issues


def compare_identity(reports):
    """evidence-scorer vs dup-detector: do these two submissions
    represent the same underlying work?

    dup-detector's token-shingle Jaccard tolerates small edits, so a
    lightly-reworded near-duplicate pair still scores above its
    threshold and gets flagged. evidence-scorer's originality signal is
    exact-sentence-match only; the same reworded pair shares zero
    identical sentences, so evidence-scorer scores both records fully
    original (originality == 1.0). One tool says "these two are
    essentially the same submission", the other says "these two are
    unrelated, both original" -- an IDENTITY_CONTRADICTION about whether
    the two ids denote the same content.
    """
    issues = []
    es = reports["evidence-scorer"]
    dd = reports["dup-detector"]

    originality = {}
    for r in es.get("records", []):
        originality[r.get("submission_id")] = _safe_get(r, "components", "originality")

    for pair in dd.get("flagged_pairs", []):
        a = pair.get("submission_id_a")
        b = pair.get("submission_id_b")
        oa = originality.get(a)
        ob = originality.get(b)
        if oa == 1.0 and ob == 1.0:
            issues.append(make_issue(
                IDENTITY_CONTRADICTION,
                ("dup-detector", "evidence-scorer"),
                subject={"submission_id_a": a, "submission_id_b": b},
                claims={
                    "dup-detector": "near-duplicate (score={})".format(pair.get("score")),
                    "evidence-scorer": "both fully original (originality=1.0)",
                },
                message=(
                    "dup-detector flags {!r} and {!r} as a near-duplicate "
                    "pair (score={}), but evidence-scorer scores both as "
                    "fully original with zero shared sentences -- the two "
                    "tools disagree on whether these submissions are the "
                    "same underlying content.".format(a, b, pair.get("score"))
                ),
            ))
    return issues


COMPARATORS = (
    ("linkage", ("link-integrity", "preflight"), compare_linkage),
    ("amount", ("reward-anomaly", "reward-reconciler"), compare_amount),
    ("timestamp", ("lifecycle-linter", "link-integrity"), compare_timestamp),
    ("lifecycle_scope", ("event-linter", "lifecycle-linter"), compare_lifecycle_scope),
    ("validity", ("queue-auditor", "staleness-monitor"), compare_validity),
    ("identity", ("dup-detector", "evidence-scorer"), compare_identity),
)


# --------------------------------------------------------------------------
# Case discovery and processing
# --------------------------------------------------------------------------

def discover_cases(case_root):
    """Return a sorted list of (case_id, case_dir) pairs.

    If ``case_root`` itself directly contains a recognized input file it
    is treated as a single case (case_id = its own basename). Otherwise
    every immediate subdirectory is treated as one case (sorted by
    name), including subdirectories with no recognized input files at
    all (an "empty case" -- zero applicable checkers, zero issues, not
    an error).
    """
    if not os.path.isdir(case_root):
        raise InputError("not a directory: {}".format(case_root))

    try:
        entries = sorted(os.listdir(case_root))
    except OSError as exc:
        raise InputError("could not list directory {}: {}".format(case_root, exc))

    direct_files = set(entries) & KNOWN_INPUT_FILENAMES
    if direct_files:
        case_id = os.path.basename(os.path.normpath(case_root))
        return [(case_id, case_root)]

    subdirs = [e for e in entries if os.path.isdir(os.path.join(case_root, e))]
    return [(name, os.path.join(case_root, name)) for name in sorted(subdirs)]


def process_case(case_id, case_dir, checkers_root, timeout):
    applicable = sorted(aid for aid in ADAPTERS if is_applicable(aid, case_dir))
    not_applicable = sorted(aid for aid in ADAPTERS if aid not in applicable)

    run_results = {}
    issues = []

    for aid in applicable:
        result = run_checker(aid, case_dir, checkers_root, timeout=timeout)
        run_results[aid] = result
        if result["state"] == "unavailable":
            issues.append(make_issue(
                CHECKER_UNAVAILABLE, (aid,),
                subject={"checker": aid},
                message="{}: {}".format(aid, result["detail"]),
            ))
        elif result["state"] == "execution_failure":
            issues.append(make_issue(
                EXECUTION_FAILURE, (aid,),
                subject={"checker": aid},
                message="{}: {}".format(aid, result["detail"]),
            ))

    for comp_id, (a, b), func in COMPARATORS:
        if a not in applicable or b not in applicable:
            continue
        if run_results[a]["state"] != "ok" or run_results[b]["state"] != "ok":
            continue
        reports = {a: run_results[a]["report"], b: run_results[b]["report"]}
        issues.extend(func(reports))

    issues.sort(key=issue_sort_key)

    contradiction_issues = [i for i in issues if i["code"] in CONTRADICTION_CODES]
    execution_issues = [i for i in issues if i["code"] in EXECUTION_CODES]
    scope_issues = [i for i in issues if i["code"] == SCOPE_DIVERGENCE]

    return {
        "case_id": case_id,
        "checkers_applicable": applicable,
        "checkers_not_applicable": not_applicable,
        "contradiction_count": len(contradiction_issues),
        "execution_issue_count": len(execution_issues),
        "issue_count": len(issues),
        "issues": issues,
        "scope_divergence_count": len(scope_issues),
    }


def build_report(case_reports):
    case_reports = sorted(case_reports, key=lambda c: c["case_id"])

    code_counts = {code: 0 for code in sorted(ALL_CODES)}
    total_contradictions = 0
    total_execution = 0
    total_scope = 0
    total_issues = 0

    for case in case_reports:
        for issue in case["issues"]:
            code_counts[issue["code"]] += 1
        total_contradictions += case["contradiction_count"]
        total_execution += case["execution_issue_count"]
        total_scope += case["scope_divergence_count"]
        total_issues += case["issue_count"]

    if total_execution > 0:
        status = "execution_error"
    elif total_contradictions > 0:
        status = "contradictions_found"
    else:
        status = "agree"

    return {
        "cases": case_reports,
        "code_counts": code_counts,
        "known_checkers": sorted(ADAPTERS),
        "report_version": REPORT_VERSION,
        "status": status,
        "summary": {
            "case_count": len(case_reports),
            "total_contradictions": total_contradictions,
            "total_execution_issues": total_execution,
            "total_issues": total_issues,
            "total_scope_divergence": total_scope,
        },
        "tool_version": TOOL_VERSION,
    }


STATUS_TO_EXIT_CODE = {
    "agree": EXIT_AGREE,
    "contradictions_found": EXIT_CONTRADICTIONS,
    "execution_error": EXIT_ERROR,
}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="contradict.py",
        description=(
            "Run applicable existing checkers against the same case data "
            "and report incompatible results between them, without "
            "replacing those checkers."
        ),
    )
    parser.add_argument(
        "case_root",
        help=(
            "Path to a case directory (containing recognized input files "
            "directly) or a directory containing one or more such case "
            "subdirectories."
        ),
    )
    parser.add_argument(
        "-o", "--output", default=None, metavar="FILE",
        help="write the canonical JSON report to FILE instead of stdout",
    )
    parser.add_argument(
        "--checkers-root", default=None, metavar="PATH",
        help=(
            "directory containing the checker tool subdirectories "
            "(default: the bundled checkers/ shipped next to this script)"
        ),
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT, metavar="SECONDS",
        help="per-checker subprocess timeout in seconds (default: %(default)s)",
    )
    return parser


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)  # argparse itself sys.exit(2) on usage errors

    if args.timeout <= 0:
        print("contradict.py: error: --timeout must be positive", file=sys.stderr)
        return EXIT_ERROR

    checkers_root = args.checkers_root if args.checkers_root else DEFAULT_CHECKERS_ROOT

    try:
        cases = discover_cases(args.case_root)
    except InputError as exc:
        print("contradict.py: error: {}".format(exc), file=sys.stderr)
        return EXIT_ERROR

    case_reports = [
        process_case(case_id, case_dir, checkers_root, args.timeout)
        for case_id, case_dir in cases
    ]

    report = build_report(case_reports)
    text = canonical_dumps(report)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        except OSError as exc:
            print(
                "contradict.py: error: could not write {}: {}".format(
                    args.output, exc
                ),
                file=sys.stderr,
            )
            return EXIT_ERROR
    else:
        sys.stdout.write(text)

    return STATUS_TO_EXIT_CODE[report["status"]]


if __name__ == "__main__":
    sys.exit(main())
