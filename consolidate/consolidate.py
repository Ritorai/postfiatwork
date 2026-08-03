#!/usr/bin/env python3
"""consolidate.py -- stdlib-only CLI that discovers per-tool JSON reports under
a directory tree and produces one canonical, deterministic consolidated report.

See README.md for the full design write-up, the observed report shapes this
tool was built against, and the severity-normalisation mapping table.

Exit codes:
  0 -- scan succeeded and zero findings remain after severity filtering
  1 -- scan succeeded and at least one finding remains after severity filtering
  2 -- invalid input / usage error (bad CLI args, missing/non-directory root,
       or an output file that could not be written)

Determinism contract:
  * File discovery is fully sorted (final sort of relative, forward-slash
    paths), independent of the underlying OS's directory-walk order.
  * No absolute paths ever appear in the output -- only paths relative to the
    scanned root are recorded.
  * No wall-clock timestamps, PIDs, hostnames, or other machine-specific
    values are emitted.
  * Output is serialised with json.dumps(obj, sort_keys=True,
    separators=(",", ":"), ensure_ascii=True) plus a single trailing newline.
"""
import argparse
import json
import os
import sys

SEVERITY_LEVELS = ["info", "warning", "error", "critical"]
SEVERITY_RANK = {name: i for i, name in enumerate(SEVERITY_LEVELS)}

EXIT_NO_FINDINGS = 0
EXIT_FINDINGS = 1
EXIT_USAGE_ERROR = 2

UNKNOWN_TOOL = "unknown"


# --------------------------------------------------------------------------
# canonical serialisation
# --------------------------------------------------------------------------

def canonical_dumps(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _detail_from(item, exclude):
    """Build a deterministic, human-readable detail string from an item's
    remaining fields when the source report has no explicit detail/message
    field. Keys are sorted for determinism; values are JSON-encoded so the
    result is stable regardless of the underlying Python value's repr."""
    parts = []
    for k in sorted(item.keys()):
        if k in exclude:
            continue
        v = item[k]
        try:
            encoded = json.dumps(v, sort_keys=True, ensure_ascii=True)
        except TypeError:
            encoded = json.dumps(str(v), ensure_ascii=True)
        parts.append("%s=%s" % (k, encoded))
    return "; ".join(parts) if parts else "(no additional detail)"


def make_finding(source_tool, source_report, task_id, code, severity, detail):
    if severity not in SEVERITY_RANK:
        severity = "error"
    return {
        "source_tool": source_tool,
        "source_report": source_report,
        "task_id": task_id,
        "code": code,
        "severity": severity,
        "detail": detail,
    }


def worst_severity(severities):
    best = None
    for s in severities:
        if s not in SEVERITY_RANK:
            continue
        if best is None or SEVERITY_RANK[s] > SEVERITY_RANK[best]:
            best = s
    return best


def _as_list(v):
    return v if isinstance(v, list) else []


def _as_dict(v):
    return v if isinstance(v, dict) else {}


# --------------------------------------------------------------------------
# adapter layer
#
# Each adapter receives the parsed top-level JSON value and the report's
# path (relative to the scanned root) and returns either:
#   * None                -- this report does not match this adapter's shape
#   * a (possibly empty) list of normalised finding dicts (via make_finding)
#
# None of the observed sibling-tool reports carry a field that names the
# producing tool, so adapters fingerprint reports structurally using a
# combination of keys chosen to be distinctive against the *other* observed
# shapes. See README.md "Observed report shapes" for the concrete samples
# each adapter was built from.
# --------------------------------------------------------------------------


def adapt_sybil_detector(data, relpath):
    if not isinstance(data, dict):
        return None
    if "clusters" not in data or not isinstance(data.get("clusters"), list):
        return None
    if "totals" not in data or "config" not in data:
        return None
    if "alert_threshold" not in _as_dict(data.get("config")):
        return None
    findings = []
    for cluster in _as_list(data.get("clusters")):
        if not isinstance(cluster, dict):
            continue
        if cluster.get("alert") is True:
            wallets = cluster.get("wallets")
            wallets_sorted = sorted(wallets) if isinstance(wallets, list) else wallets
            detail = "wallets=%s score=%s signals=%s size=%s" % (
                json.dumps(wallets_sorted, sort_keys=True, ensure_ascii=True),
                json.dumps(cluster.get("score"), ensure_ascii=True),
                json.dumps(cluster.get("signals"), sort_keys=True, ensure_ascii=True),
                json.dumps(cluster.get("size"), ensure_ascii=True),
            )
            findings.append(make_finding(
                "sybil-detector", relpath, None, "SYBIL_CLUSTER_ALERT", "critical", detail))
    return findings


def adapt_xrpl_auditor(data, relpath):
    if not isinstance(data, dict):
        return None
    if "issues" not in data or not isinstance(data.get("issues"), list):
        return None
    totals = _as_dict(data.get("totals"))
    if "roster_tasks" not in totals or "well_formed_payouts" not in totals:
        return None
    findings = []
    for item in _as_list(data.get("issues")):
        if not isinstance(item, dict):
            continue
        findings.append(make_finding(
            "xrpl-auditor", relpath, item.get("task_id"),
            item.get("issue", "UNKNOWN_CODE"), "error", item.get("detail", "")))
    return findings


def adapt_reward_reconciler(data, relpath):
    if not isinstance(data, dict):
        return None
    if "findings" not in data or not isinstance(data.get("findings"), list):
        return None
    totals = _as_dict(data.get("totals"))
    if "payout_records" not in totals or "expected_records" not in totals:
        return None
    findings = []
    for item in _as_list(data.get("findings")):
        if not isinstance(item, dict):
            continue
        code = item.get("issue", "UNKNOWN_CODE")
        detail = _detail_from(item, exclude={"issue", "task_id"})
        findings.append(make_finding(
            "reward-reconciler", relpath, item.get("task_id"), code, "error", detail))
    return findings


def adapt_lifecycle_linter(data, relpath):
    if not isinstance(data, dict):
        return None
    if "findings" not in data or not isinstance(data.get("findings"), list):
        return None
    if "finding_counts" not in data:
        return None
    totals = _as_dict(data.get("totals"))
    if "events" not in totals or "tasks" not in totals:
        return None
    findings = []
    for item in _as_list(data.get("findings")):
        if not isinstance(item, dict):
            continue
        findings.append(make_finding(
            "lifecycle-linter", relpath, item.get("task_id"),
            item.get("code", "UNKNOWN_CODE"), "error", item.get("detail", "")))
    return findings


def adapt_queue_auditor(data, relpath):
    if not isinstance(data, dict):
        return None
    if "findings" not in data or not isinstance(data.get("findings"), list):
        return None
    if "finding_count" not in data or "result" not in data:
        return None
    findings = []
    for item in _as_list(data.get("findings")):
        if not isinstance(item, dict):
            continue
        findings.append(make_finding(
            "queue-auditor", relpath, item.get("task_id"),
            item.get("code", "UNKNOWN_CODE"), "error", item.get("detail", "")))
    return findings


def adapt_wallet_reconciler(data, relpath):
    if not isinstance(data, dict):
        return None
    if "findings" not in data or not isinstance(data.get("findings"), list):
        return None
    if "trace" not in data or "ledger_version" not in data:
        return None
    findings = []
    for item in _as_list(data.get("findings")):
        if not isinstance(item, dict):
            continue
        code = item.get("code", "UNKNOWN_CODE")
        severity = "critical" if code in ("CLOSING_BALANCE_MISMATCH", "NEGATIVE_RUNNING_BALANCE") else "error"
        detail = item.get("detail")
        if not detail:
            detail = _detail_from(item, exclude={"code"})
        findings.append(make_finding(
            "wallet-reconciler", relpath, None, code, severity, detail))
    return findings


def adapt_preflight(data, relpath):
    if not isinstance(data, dict):
        return None
    if "issues" not in data or not isinstance(data.get("issues"), list):
        return None
    if "ready" not in data:
        return None
    findings = []
    for item in _as_list(data.get("issues")):
        if not isinstance(item, dict):
            continue
        findings.append(make_finding(
            "preflight", relpath, item.get("task_id"),
            item.get("code", "UNKNOWN_CODE"), "error", item.get("message", "")))
    return findings


def adapt_link_integrity(data, relpath):
    if not isinstance(data, dict):
        return None
    if "violations" not in data or not isinstance(data.get("violations"), list):
        return None
    if "schema_version" not in data:
        return None
    summary = _as_dict(data.get("summary"))
    if "is_clean" not in summary:
        return None
    findings = []
    for item in _as_list(data.get("violations")):
        if not isinstance(item, dict):
            continue
        # Some violations (e.g. DUPLICATE_SUBMISSION_ID) reference multiple
        # task_ids via a plural "task_ids" list rather than a single
        # "task_id". We deliberately do NOT fan those out into one finding
        # per task (that would manufacture findings that don't exist in the
        # source report); they land in task_id=None / ungrouped_findings.
        # See README "Limitations".
        findings.append(make_finding(
            "link-integrity", relpath, item.get("task_id"),
            item.get("code", "UNKNOWN_CODE"), "error", item.get("message", "")))
    return findings


def adapt_schema_checker(data, relpath):
    if not isinstance(data, dict):
        return None
    if "violations" not in data or not isinstance(data.get("violations"), list):
        return None
    if "schema_source" not in data or "payload_source" not in data:
        return None
    findings = []
    for item in _as_list(data.get("violations")):
        if not isinstance(item, dict):
            continue
        message = item.get("message", "")
        pointer = item.get("pointer")
        detail = "%s (pointer=%s)" % (message, pointer) if pointer is not None else message
        findings.append(make_finding(
            "schema-checker", relpath, None,
            item.get("code", "UNKNOWN_CODE"), "error", detail))
    return findings


def adapt_staleness_monitor(data, relpath):
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("findings"), dict):
        return None
    if "windows" not in data or "generated_at" not in data:
        return None
    findings = []
    for bucket_name in sorted(data.get("findings", {}).keys()):
        bucket_items = data["findings"][bucket_name]
        if not isinstance(bucket_items, list):
            continue
        for item in bucket_items:
            if not isinstance(item, dict):
                continue
            severity = item.get("bucket", bucket_name)
            if severity not in SEVERITY_RANK:
                severity = "error"
            findings.append(make_finding(
                "staleness-monitor", relpath, item.get("task_id"),
                item.get("code", "UNKNOWN_CODE"), severity, item.get("message", "")))
    return findings


def adapt_xrpl_address(data, relpath):
    if not isinstance(data, dict):
        return None
    if "addresses" not in data or not isinstance(data.get("addresses"), list):
        return None
    totals = _as_dict(data.get("totals"))
    if "valid" not in totals or "invalid" not in totals:
        return None
    findings = []
    for item in _as_list(data.get("addresses")):
        if not isinstance(item, dict):
            continue
        issues = item.get("issues")
        if not isinstance(issues, list):
            continue
        for issue_code in issues:
            severity = "critical" if issue_code == "DENYLISTED" else "error"
            detail = "address=%s kind=%s index=%s valid=%s" % (
                json.dumps(item.get("address"), ensure_ascii=True),
                json.dumps(item.get("kind"), ensure_ascii=True),
                json.dumps(item.get("index"), ensure_ascii=True),
                json.dumps(item.get("valid"), ensure_ascii=True),
            )
            findings.append(make_finding(
                "xrpl-address", relpath, None, str(issue_code), severity, detail))
    return findings


def adapt_event_linter(data, relpath):
    if not isinstance(data, dict):
        return None
    if "tasks" not in data or not isinstance(data.get("tasks"), list):
        return None
    if "report_version" not in data or "status" not in data:
        return None
    findings = []
    for task in _as_list(data.get("tasks")):
        if not isinstance(task, dict):
            continue
        outer_task_id = task.get("task_id")
        for viol in _as_list(task.get("violations")):
            if not isinstance(viol, dict):
                continue
            task_id = viol.get("task_id")
            if task_id is None:
                task_id = outer_task_id
            findings.append(make_finding(
                "event-linter", relpath, task_id,
                viol.get("violation", "UNKNOWN_CODE"), "error", viol.get("detail", "")))
    return findings


def adapt_evidence_harness(data, relpath):
    if not isinstance(data, dict):
        return None
    if "checks" not in data or not isinstance(data.get("checks"), list):
        return None
    if "bundle_dir" not in data or "bundle_files" not in data:
        return None
    findings = []
    for item in _as_list(data.get("checks")):
        if not isinstance(item, dict):
            continue
        if item.get("status") == "fail":
            check = item.get("check", "unknown_check")
            code = "CHECK_FAILED_%s" % str(check).upper()
            findings.append(make_finding(
                "evidence-harness", relpath, None, code, "error", item.get("detail", "")))
    return findings


def adapt_evidence_scorer(data, relpath):
    if not isinstance(data, dict):
        return None
    if "records" not in data or not isinstance(data.get("records"), list):
        return None
    totals = _as_dict(data.get("totals"))
    if "passed" not in totals or "failed" not in totals:
        return None
    config = _as_dict(data.get("config"))
    threshold = config.get("threshold")
    findings = []
    for item in _as_list(data.get("records")):
        if not isinstance(item, dict):
            continue
        if item.get("passed") is False:
            detail = "submission_id=%s score=%s threshold=%s" % (
                json.dumps(item.get("submission_id"), ensure_ascii=True),
                json.dumps(item.get("score"), ensure_ascii=True),
                json.dumps(threshold, ensure_ascii=True),
            )
            findings.append(make_finding(
                "evidence-scorer", relpath, None,
                "EVIDENCE_SCORE_BELOW_THRESHOLD", "warning", detail))
    return findings


def adapt_throughput_reporter(data, relpath):
    if not isinstance(data, dict):
        return None
    if "contributors" not in data or not isinstance(data.get("contributors"), list):
        return None
    if "grade_counts" not in data:
        return None
    config = _as_dict(data.get("config"))
    findings = []
    for item in _as_list(data.get("contributors")):
        if not isinstance(item, dict):
            continue
        if item.get("over_ceiling") is True:
            detail = "contributor=%s refusal_rate=%s grade=%s refusal_ceiling=%s" % (
                json.dumps(item.get("contributor"), ensure_ascii=True),
                json.dumps(item.get("refusal_rate"), ensure_ascii=True),
                json.dumps(item.get("grade"), ensure_ascii=True),
                json.dumps(config.get("refusal_ceiling"), ensure_ascii=True),
            )
            findings.append(make_finding(
                "throughput-reporter", relpath, None,
                "REFUSAL_CEILING_BREACH", "warning", detail))
    return findings


def adapt_budget_forecaster(data, relpath):
    if not isinstance(data, dict):
        return None
    if "over_budget" not in data or "projection" not in data or "history" not in data:
        return None
    findings = []
    if data.get("over_budget") is True:
        proj = _as_dict(data.get("projection"))
        config = _as_dict(data.get("config"))
        detail = "projected_total=%s budget_cap=%s status=%s" % (
            json.dumps(proj.get("projected_total"), ensure_ascii=True),
            json.dumps(config.get("budget_cap"), ensure_ascii=True),
            json.dumps(data.get("status"), ensure_ascii=True),
        )
        findings.append(make_finding(
            "budget-forecaster", relpath, None, "BUDGET_OVER_PROJECTION", "critical", detail))
    return findings


def adapt_dup_detector(data, relpath):
    if not isinstance(data, dict):
        return None
    if "flagged_pairs" not in data or not isinstance(data.get("flagged_pairs"), list):
        return None
    if "comparison_count" not in data or "flagged_count" not in data:
        return None
    findings = []
    for item in _as_list(data.get("flagged_pairs")):
        if not isinstance(item, dict):
            continue
        detail = "submission_id_a=%s submission_id_b=%s score=%s overlap_count=%s" % (
            json.dumps(item.get("submission_id_a"), ensure_ascii=True),
            json.dumps(item.get("submission_id_b"), ensure_ascii=True),
            json.dumps(item.get("score"), ensure_ascii=True),
            json.dumps(item.get("overlap_count"), ensure_ascii=True),
        )
        findings.append(make_finding(
            "dup-detector", relpath, None, "DUPLICATE_CANDIDATE", "warning", detail))
    return findings


# Fixed, documented adapter order. Matchers check enough distinctive keys
# that order should not matter for any observed real report, but a fixed
# order keeps behaviour fully deterministic in the presence of a
# pathological/ambiguous fixture.
ADAPTERS = [
    ("sybil-detector", adapt_sybil_detector),
    ("xrpl-auditor", adapt_xrpl_auditor),
    ("reward-reconciler", adapt_reward_reconciler),
    ("lifecycle-linter", adapt_lifecycle_linter),
    ("queue-auditor", adapt_queue_auditor),
    ("wallet-reconciler", adapt_wallet_reconciler),
    ("preflight", adapt_preflight),
    ("link-integrity", adapt_link_integrity),
    ("schema-checker", adapt_schema_checker),
    ("staleness-monitor", adapt_staleness_monitor),
    ("xrpl-address", adapt_xrpl_address),
    ("event-linter", adapt_event_linter),
    ("evidence-harness", adapt_evidence_harness),
    ("evidence-scorer", adapt_evidence_scorer),
    ("throughput-reporter", adapt_throughput_reporter),
    ("budget-forecaster", adapt_budget_forecaster),
    ("dup-detector", adapt_dup_detector),
]


def run_adapters(data, relpath):
    """Return (tool_name, findings) for the first matching adapter, or
    (None, None) if no adapter recognises this report's shape."""
    if not isinstance(data, dict):
        return None, None
    for tool_name, adapter in ADAPTERS:
        result = adapter(data, relpath)
        if result is not None:
            return tool_name, result
    return None, None


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

def discover_json_files(root, exclude_realpath=None):
    """Return a fully sorted list of paths to *.json files under root,
    expressed relative to root using forward slashes. Sorting happens both
    during the walk (for readability) and, decisively, on the final flat
    list -- so results are identical across operating systems and
    filesystem orderings.

    If exclude_realpath is given (the resolved absolute path of the file
    this run is about to write via -o/--output), any discovered file that
    resolves to that same path is skipped. Without this, writing -o inside
    the scanned root would make a report "discover" its own prior output on
    the next run, corrupting the provenance/finding counts with a report
    that did not exist when the scan of *inputs* conceptually began."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            if name.lower().endswith(".json"):
                abspath = os.path.join(dirpath, name)
                if exclude_realpath is not None and os.path.realpath(abspath) == exclude_realpath:
                    continue
                rel = os.path.relpath(abspath, root)
                rel = rel.replace(os.sep, "/")
                out.append(rel)
    out.sort()
    return out


# --------------------------------------------------------------------------
# core pipeline
# --------------------------------------------------------------------------

def _finding_sort_key(f):
    return (
        f["source_tool"],
        f["source_report"],
        f["task_id"] if f["task_id"] is not None else "",
        f["code"],
        f["severity"],
        f["detail"],
    )


def build_report(root, severity_threshold, exclude_output_path=None):
    exclude_realpath = os.path.realpath(exclude_output_path) if exclude_output_path else None
    files = discover_json_files(root, exclude_realpath=exclude_realpath)
    all_findings = []
    reports_meta = []

    for rel in files:
        abspath = os.path.join(root, rel)
        try:
            with open(abspath, "r", encoding="utf-8") as fh:
                raw = fh.read()
        except OSError as exc:
            f = [make_finding(UNKNOWN_TOOL, rel, None, "UNREADABLE_FILE", "critical", str(exc))]
            all_findings.extend(f)
            reports_meta.append({
                "source_report": rel, "source_tool": UNKNOWN_TOOL,
                "recognised": False, "finding_count": len(f),
            })
            continue

        try:
            data = json.loads(raw)
        except ValueError as exc:
            f = [make_finding(UNKNOWN_TOOL, rel, None, "INVALID_JSON", "critical",
                               "failed to parse JSON: %s" % exc)]
            all_findings.extend(f)
            reports_meta.append({
                "source_report": rel, "source_tool": UNKNOWN_TOOL,
                "recognised": False, "finding_count": len(f),
            })
            continue

        tool_name, findings = run_adapters(data, rel)
        if tool_name is None:
            top_type = type(data).__name__
            f = [make_finding(UNKNOWN_TOOL, rel, None, "UNRECOGNISED_REPORT_SHAPE", "critical",
                               "no adapter matched this report shape (top-level JSON type: %s)" % top_type)]
            all_findings.extend(f)
            reports_meta.append({
                "source_report": rel, "source_tool": UNKNOWN_TOOL,
                "recognised": False, "finding_count": len(f),
            })
        else:
            all_findings.extend(findings)
            reports_meta.append({
                "source_report": rel, "source_tool": tool_name,
                "recognised": True, "finding_count": len(findings),
            })

    threshold_rank = SEVERITY_RANK[severity_threshold] if severity_threshold else 0
    kept = [f for f in all_findings if SEVERITY_RANK[f["severity"]] >= threshold_rank]

    task_groups = {}
    ungrouped = []
    for f in kept:
        tid = f["task_id"]
        if tid is None:
            ungrouped.append(f)
        else:
            task_groups.setdefault(tid, []).append(f)

    findings_by_task = []
    for tid in sorted(task_groups.keys()):
        flist = sorted(task_groups[tid], key=_finding_sort_key)
        sources_set = sorted({(x["source_tool"], x["source_report"]) for x in flist})
        findings_by_task.append({
            "task_id": tid,
            "worst_severity": worst_severity(x["severity"] for x in flist),
            "contributing_sources": [
                {"source_tool": s[0], "source_report": s[1]} for s in sources_set
            ],
            "findings": flist,
        })

    ungrouped_sorted = sorted(ungrouped, key=_finding_sort_key)

    counts = {lvl: 0 for lvl in SEVERITY_LEVELS}
    for f in kept:
        counts[f["severity"]] += 1

    total_findings = len(kept)
    exit_code = EXIT_NO_FINDINGS if total_findings == 0 else EXIT_FINDINGS

    result = {
        "consolidated_version": "1.0",
        "exit_code": exit_code,
        "params": {"severity_threshold": severity_threshold},
        "reports": sorted(reports_meta, key=lambda r: r["source_report"]),
        "severity_rollup": {
            "counts": counts,
            "worst_severity": worst_severity(f["severity"] for f in kept),
        },
        "totals": {
            "reports_scanned": len(reports_meta),
            "reports_recognised": sum(1 for r in reports_meta if r["recognised"]),
            "reports_unrecognised": sum(1 for r in reports_meta if not r["recognised"]),
            "findings_total": total_findings,
            "tasks_with_findings": len(findings_by_task),
        },
        "findings_by_task": findings_by_task,
        "ungrouped_findings": ungrouped_sorted,
    }
    return result, exit_code


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="consolidate.py",
        description=(
            "Discover per-tool JSON reports under ROOT and produce one "
            "canonical, deterministic consolidated report."
        ),
    )
    parser.add_argument("root", help="directory to scan recursively for *.json report files")
    parser.add_argument("-o", "--output", metavar="FILE",
                         help="write the canonical JSON report to FILE instead of stdout")
    parser.add_argument("--severity-threshold", choices=SEVERITY_LEVELS, default=None,
                         help="only roll up findings at or above this severity "
                              "(default: no filtering, i.e. 'info' and above)")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    root = args.root
    if not os.path.exists(root):
        print("consolidate: error: path does not exist: %s" % root, file=sys.stderr)
        return EXIT_USAGE_ERROR
    if not os.path.isdir(root):
        print("consolidate: error: not a directory: %s" % root, file=sys.stderr)
        return EXIT_USAGE_ERROR

    result, exit_code = build_report(root, args.severity_threshold, exclude_output_path=args.output)
    text = canonical_dumps(result)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        except OSError as exc:
            print("consolidate: error: cannot write output file: %s" % exc, file=sys.stderr)
            return EXIT_USAGE_ERROR
    else:
        sys.stdout.write(text)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
