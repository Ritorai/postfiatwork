"""Predict which committed reports a set of changed paths will invalidate.

Stdlib-only Python 3. No third-party packages, no network, no filesystem
access at all: the answer is a pure function of `dependency_map.json` and the
paths you pass in, so the same inputs give the same bytes on any machine and
you can ask the question before the change exists.

WHY THIS TOOL EXISTS

Several tools in this repository commit a report about OTHER directories, and
a committed check compares that report against a fresh computation. Change a
file the report counted, and a test in a directory you never opened turns red.
Three of those relationships are load-bearing and non-obvious:

  * `doc-validator/option_report.json` records the LINE NUMBER of every
    argparse option site in the repository. Add one top-level `import` to any
    tool's CLI and every option below it shifts by one.
  * `index-generator/pipe_classification_report.json` pins
    `total_command_records` and `transcript_files_scanned`, summed over every
    `captured_output.txt` exactly one level under the root. Name a new
    evidence file `captured_output.txt` and both numbers move; name it
    anything else and they do not.
  * `weak-assertion-scanner/README.md` quotes `files_scanned` and
    `tests_scanned` in prose, and its own regen test re-derives them from the
    committed report. Add a test module and the README -- not just the report
    -- has to be hand-edited.

None of the three is discoverable by reading the directory you are editing.

WHAT IT ANSWERS

    $ python3 predict_impact.py link-integrity/test_new_thing.py

    -> nondeterminism-scanner/self_scan_report.json   (it is a .py)
       weak-assertion-scanner/self_scan_report.json   (it is a test module)
       weak-assertion-scanner/README.md               (transitively)
       claim-crosscheck/sample_run.json               (transitively)

DIRECT AND TRANSITIVE

A report is impacted DIRECTLY when a changed path matches one of its triggers.
It is impacted TRANSITIVELY when regenerating an already-impacted report
rewrites an artifact that is itself a changed path for some other report --
`nondeterminism-scanner/self_scan_report.json` is a `.json` sitting one level
down, so rewriting it is a change that `claim-crosscheck` scans. Explicit
`propagations` in the map cover the one edge that is not path-derivable
(weak-assertion-scanner's README is re-derived from its own report by a test,
not by a scan). The fixed point is taken with cycle protection, because
claim-crosscheck's own report is inside claim-crosscheck's own scan scope.

PINNED REPORTS ARE NEVER IMPACTED

`report-freshness` marks two reports `kind: "pinned"`: they are point-in-time
evidence and must never be regenerated. They are listed separately in the
output and never appear in `impacted`, no matter what changed. Reporting them
as stale would be the single most damaging thing this tool could get wrong.

EXIT CODES

  0  no report is impacted
  1  at least one report is impacted -- this is the finding, not a failure
  2  usage error: bad flags, an unreadable or malformed map, an absolute path,
     a path that escapes the repository root

DETERMINISM

Output is `json.dumps(obj, sort_keys=True, separators=(",", ":"),
ensure_ascii=True)` plus one trailing newline. Every list in it is sorted by
an explicit key. Nothing carries a timestamp, a hostname, a working directory
or a process id, and nothing is read from the filesystem except the map you
name, so two runs over the same inputs are byte-identical.
"""
import argparse
import json
import os
import sys

SCHEMA_VERSION = "1.0"

EXIT_CLEAN = 0
EXIT_IMPACTED = 1
EXIT_USAGE = 2

#: Every match kind `dependency_map.json` may use. A map naming anything else
#: is a malformed map, not a silently-ignored rule -- see `_match`.
MATCH_KINDS = ("extension", "basename", "test-module", "new-top-level",
               "gone-top-level", "baselined-tool", "declared-input",
               "producer")

#: Match kinds whose answer depends on WHICH report is being considered, not
#: on the path alone. They are evaluated with the candidate report in hand.
REPORT_SPECIFIC = ("declared-input", "producer")

#: The kinds of change a path can represent. `unknown` is the default and is
#: the conservative one: an edge counts as exact under `unknown` only if it is
#: exact for every kind.
CHANGE_KINDS = ("add", "remove", "edit", "unknown")
ALL_REAL_KINDS = ("add", "edit", "remove")


class UsageError(Exception):
    """Something the caller fixes by changing the command line or the map."""


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True) + "\n"


# --------------------------------------------------------------------------
# Path normalisation
# --------------------------------------------------------------------------

def normalize_path(raw):
    """Return a repository-relative POSIX path, or raise UsageError.

    Accepts the spellings a caller actually produces -- `git diff --name-only`
    output, a shell glob, a copy-paste with `./` on the front, a Windows-style
    separator -- and reduces them all to one form so that `./a/b.py`,
    `a//b.py`, `a\\b.py` and `a/c/../b.py` are one path and not four.

    Rejects what it cannot answer honestly: an absolute path (the map is
    repository-relative and this tool never touches the filesystem, so it
    cannot know where the repository root is), and any path that climbs above
    the root.
    """
    if not isinstance(raw, str) or raw.strip() == "":
        raise UsageError("empty path")
    # Deliberately NOT raw.strip(): a trailing space is part of the filename,
    # and stripping it silently answered about a different file --
    # `link-integrity/probe.py ` is not a .py to any scanner here, and the
    # stripped form was reported `certain` for a report it does not move.
    # Only the newline a line-oriented caller leaves behind is removed.
    text = raw.rstrip("\n").rstrip("\r").replace("\\", "/")
    if text.startswith("/"):
        raise UsageError(
            "absolute path '%s': pass repository-relative paths, e.g. "
            "'link-integrity/link_integrity.py'" % raw)
    if len(text) > 1 and text[1] == ":":
        raise UsageError("drive-letter path '%s': pass a repository-relative path" % raw)

    parts = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise UsageError("path '%s' escapes the repository root" % raw)
            parts.pop()
            continue
        parts.append(part)
    if not parts:
        raise UsageError("path '%s' resolves to the repository root itself" % raw)
    return "/".join(parts)


def top_level(path):
    return path.split("/", 1)[0]


def depth(path):
    """Number of directory components above the file. `a/b.py` -> 1."""
    return path.count("/")


def is_test_module(basename):
    """weak-assertion-scanner/weakassert.py:87-90, transcribed."""
    if not basename.endswith(".py"):
        return False
    return basename.startswith("test_") or basename.endswith("_test.py")


# --------------------------------------------------------------------------
# Rule evaluation
# --------------------------------------------------------------------------

def _ignored(rule, parts):
    """Is any directory component pruned by THIS rule's own consumer?

    Each rule carries its own `ignores`, because the repository has no single
    ignore list: ndscan prunes 11 names and not `.hg` or `.svn`; weakassert
    prunes those plus every dot-prefixed name; and the tools that count
    top-level directories prune only dot names and `__pycache__`, so `build/`
    and `dist/` are ordinary directories to them. A global list produced a
    measured false negative -- creating `build/notes.md` moves two reports.
    """
    ignores = rule.get("ignores", [])
    dotted = "<any name starting with a dot>" in ignores
    for part in parts[:-1]:
        if part in ignores:
            return True
        if dotted and part.startswith("."):
            return True
    return False


def _match(rule_id, rule, path, ctx, report=None):
    """Does `path` satisfy `rule`? Returns (bool, reason-or-None).

    `report` is the candidate report, needed only for the report-specific
    kinds -- a path is a "declared input" or a "producer" of something, never
    in the abstract.
    """
    parts = path.split("/")
    base = parts[-1]

    if _ignored(rule, parts):
        return False, None

    kind = rule.get("match")
    if kind not in MATCH_KINDS:
        raise UsageError("rule '%s' has unknown match kind '%s'" % (rule_id, kind))

    wanted_depth = rule.get("depth", "any")
    if wanted_depth != "any" and depth(path) != int(wanted_depth):
        return False, None

    if kind == "extension":
        if not base.endswith(rule["value"]):
            return False, None
        return True, "%s is a %s file%s" % (
            path, rule["value"],
            "" if wanted_depth == "any" else " directly inside a tool directory")

    if kind == "basename":
        if base != rule["value"]:
            return False, None
        return True, "%s is a %s at depth %s" % (path, rule["value"], wanted_depth)

    if kind == "test-module":
        if not is_test_module(base):
            return False, None
        return True, "%s is a test module (test_*.py or *_test.py)" % path

    if kind == "declared-input":
        if report is None:
            return False, None
        declared = ctx["declared_inputs"].get(report["id"], [])
        if path not in declared:
            return False, None
        return True, ("%s is a declared input of %s in "
                      "report-freshness/manifest.json" % (path, report["id"]))

    if kind == "producer":
        if report is None or not report.get("producer"):
            return False, None
        if path != report["producer"]:
            return False, None
        return True, "%s is the script that generates %s" % (path, report["id"])

    if kind == "gone-top-level":
        head = top_level(path)
        if head not in ctx["gone_dirs"]:
            return False, None
        return True, ("%s is inside '%s', which this change deletes" % (path, head))

    if kind == "baselined-tool":
        head = top_level(path)
        if head not in ctx["baselined"]:
            return False, None
        return True, ("%s is inside '%s', which regression-checker/baselines.json "
                      "baselines and coverage_audit re-runs" % (path, head))

    # new-top-level
    head = top_level(path)
    if head in ctx["known"]:
        return False, None
    return True, "%s introduces the new top-level directory '%s'" % (path, head)


def matching_rules(path, rules, ctx, report=None):
    """-> sorted list of (rule_id, reason) the path satisfies.

    Without a `report`, the report-specific kinds are skipped; the callers
    that need them pass one.
    """
    out = []
    for rule_id in sorted(rules):
        rule = rules[rule_id]
        if report is None and rule.get("match") in REPORT_SPECIFIC:
            continue
        ok, reason = _match(rule_id, rule, path, ctx, report)
        if ok:
            out.append((rule_id, reason))
    return out


def edge_is_exact(trigger, change_kind):
    """Does a path alone settle this edge, for this kind of change?

    `exact_for` is the measured list of change kinds for which the edge always
    moves the report. Under `unknown` the caller has not said whether the path
    is being added, removed or edited, so the edge only counts as exact if it
    holds for all three -- the counting scanners are exact for an add or a
    remove but not for an in-place edit of a file that produces no findings.
    """
    exact_for = trigger.get("exact_for", [])
    if change_kind == "unknown":
        return all(k in exact_for for k in ALL_REAL_KINDS)
    return change_kind in exact_for


# --------------------------------------------------------------------------
# The map
# --------------------------------------------------------------------------

def load_map(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        raise UsageError("no such map: %s" % path)
    except IsADirectoryError:
        raise UsageError("not a file: %s" % path)
    except ValueError as exc:
        raise UsageError("map %s is not valid JSON: %s" % (path, exc))
    except OSError as exc:
        raise UsageError("cannot read %s: %s" % (path, exc.strerror or exc))
    validate_map(doc, path)
    return doc


def validate_map(doc, where):
    """Fail loudly on a malformed map rather than predicting from nonsense.

    A predictor that silently returns "nothing is impacted" because its map
    lost a key is worse than one that refuses to answer: the caller acts on
    the empty answer.
    """
    if not isinstance(doc, dict):
        raise UsageError("map %s must be a JSON object" % where)
    for key, typ in (("rules", dict), ("reports", list),
                     ("known_tool_directories", list),
                     ("baselined_tools", list),
                     ("declared_inputs", dict),
                     ("propagations", list)):
        if key not in doc:
            raise UsageError("map %s is missing '%s'" % (where, key))
        if not isinstance(doc[key], typ):
            raise UsageError("map %s: '%s' has the wrong type" % (where, key))

    ids = set()
    for report in doc["reports"]:
        if not isinstance(report, dict):
            raise UsageError("map %s: every report must be a JSON object" % where)
        for key in ("id", "artifact", "pinned", "triggers"):
            if key not in report:
                raise UsageError("map %s: report is missing '%s'" % (where, key))
        if report["id"] in ids:
            raise UsageError("map %s: duplicate report id '%s'" % (where, report["id"]))
        ids.add(report["id"])
        if not isinstance(report["pinned"], bool):
            raise UsageError("map %s: report '%s' has a non-boolean 'pinned'"
                             % (where, report["id"]))
        for trigger in report["triggers"]:
            if not isinstance(trigger, dict) or "rule" not in trigger:
                raise UsageError("map %s: report '%s' has a trigger that is not "
                                 "an object with a 'rule'" % (where, report["id"]))
            if trigger["rule"] not in doc["rules"]:
                raise UsageError("map %s: report '%s' triggers on unknown rule '%s'"
                                 % (where, report["id"], trigger["rule"]))
            for kind in trigger.get("exact_for", None) or []:
                if kind not in ALL_REAL_KINDS:
                    raise UsageError(
                        "map %s: report '%s' trigger '%s' has exact_for kind "
                        "'%s'" % (where, report["id"], trigger["rule"], kind))
            if "exact_for" not in trigger:
                raise UsageError(
                    "map %s: report '%s' trigger '%s' has no exact_for; every "
                    "edge must say for which kinds of change a path settles it"
                    % (where, report["id"], trigger["rule"]))
            if trigger.get("precision") not in ("exact", "over-approximate"):
                raise UsageError(
                    "map %s: report '%s' trigger '%s' has precision '%s'; every edge "
                    "must declare whether a path can settle it exactly"
                    % (where, report["id"], trigger["rule"],
                       trigger.get("precision")))
        if report["pinned"] and report["triggers"]:
            raise UsageError(
                "map %s: report '%s' is pinned but declares triggers; a pinned "
                "report is never regenerated, so a trigger on it is a "
                "contradiction" % (where, report["id"]))

    for prop in doc["propagations"]:
        for key in ("from", "to", "why"):
            if key not in prop:
                raise UsageError("map %s: propagation is missing '%s'" % (where, key))
        for key in ("from", "to"):
            if prop[key] not in ids:
                raise UsageError("map %s: propagation %s names unknown report '%s'"
                                 % (where, key, prop[key]))


# --------------------------------------------------------------------------
# Prediction
# --------------------------------------------------------------------------

def _edge(report, rule_id):
    """The trigger spec linking `report` to `rule_id`, or None.

    Precision lives on the EDGE, not on the rule, because the same rule is
    exact for one consumer and approximate for another: a new
    `captured_output.txt` always moves index-generator's
    `transcript_files_scanned`, while an edit inside an existing one moves it
    only if a `=== $ command ===` header line changed.
    """
    for trigger in report["triggers"]:
        if trigger["rule"] == rule_id:
            return trigger
    return None


def predict(doc, paths, new_dirs=(), change_kind="unknown", gone_dirs=()):
    """Return the report dict for a list of already-normalised paths.

    `new_dirs` are top-level names to treat as NOT yet existing even though
    the committed map lists them. The map describes the tree as committed, so
    without this a directory cannot ask about its own creation -- which is
    exactly the question this tool's own delivery had to answer. Measured:
    creating report-impact-predictor/ moved regression-checker's and
    transcript-schema's reports, and running the committed map over its own
    file list reported neither until `--new-dir report-impact-predictor` was
    passed.
    """
    if change_kind not in CHANGE_KINDS:
        raise UsageError("unknown change kind '%s'" % change_kind)
    rules = doc["rules"]
    ctx = {
        "known": set(doc["known_tool_directories"]) - set(new_dirs),
        "baselined": set(doc["baselined_tools"]),
        "gone_dirs": set(gone_dirs),
        "declared_inputs": doc.get("declared_inputs", {}),
    }
    reports = {r["id"]: r for r in doc["reports"]}
    live = {rid: r for rid, r in reports.items() if not r["pinned"]}

    # reason records, keyed by report id
    reasons = {}
    chains = {}

    def note(report_id, reason, chain):
        reasons.setdefault(report_id, []).append(reason)
        chains.setdefault(report_id, []).append(chain)

    # --- direct ----------------------------------------------------------
    for path in paths:
        for rid in sorted(live):
            report = live[rid]
            for rule_id, why in matching_rules(path, rules, ctx, report):
                edge = _edge(report, rule_id)
                if edge is None:
                    continue
                note(rid, {"kind": "direct", "path": path,
                           "rule": rule_id, "detail": why,
                           "precision": ("exact"
                                         if edge_is_exact(edge, change_kind)
                                         else "over-approximate"),
                           "exact_for": edge.get("exact_for", []),
                           "precision_note": edge.get("note")},
                     [path, rid])

    # --- transitive, to a fixed point ------------------------------------
    # Regenerating an impacted report rewrites its artifact. That artifact is
    # a path like any other, so it can trigger further reports. Iterate until
    # nothing new appears; `seen_edges` stops the cycle that exists because
    # claim-crosscheck's own report sits inside claim-crosscheck's scan scope.
    propagations = doc["propagations"]
    seen_edges = set()
    changed = True
    while changed:
        changed = False
        for src in sorted(list(reasons)):
            src_report = reports[src]
            artifact = src_report["artifact"]
            src_chain = min(chains[src], key=lambda c: (len(c), c))
            # A chain is only as strong as its weakest link. If the source is
            # itself only a `possible` impact, nothing downstream of it can be
            # certain -- the regeneration that would rewrite `artifact` might
            # never happen.
            src_exact = any(r["precision"] == "exact" for r in reasons[src])

            for rid in sorted(live):
                report = live[rid]
                for rule_id, why in matching_rules(artifact, rules, ctx, report):
                    spec = _edge(report, rule_id)
                    if spec is None:
                        continue
                    if rid == src or rid in src_chain:
                        continue
                    key = (src, rid, rule_id)
                    if key in seen_edges:
                        continue
                    seen_edges.add(key)
                    note(rid, {"kind": "transitive", "path": artifact,
                               "rule": rule_id,
                               "detail": "regenerating %s rewrites %s, and %s"
                                         % (src, artifact, why),
                               "precision": ("exact"
                                             if (src_exact and
                                                 edge_is_exact(spec, change_kind))
                                             else "over-approximate"),
                               "exact_for": spec.get("exact_for", []),
                               "precision_note": spec.get("note")},
                         src_chain + [rid])
                    changed = True

            for prop in propagations:
                if prop["from"] != src:
                    continue
                rid = prop["to"]
                if rid not in live or rid == src or rid in src_chain:
                    continue
                key = (src, rid, "propagation")
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                note(rid, {"kind": "propagation", "path": artifact,
                           "rule": None, "detail": prop["why"],
                           "precision": ("exact"
                                         if (src_exact and
                                             edge_is_exact(prop, change_kind))
                                         else "over-approximate"),
                           "exact_for": prop.get("exact_for", []),
                           "precision_note": None},
                     src_chain + [rid])
                changed = True

    # --- render ----------------------------------------------------------
    impacted = []
    for rid in sorted(reasons):
        report = reports[rid]
        deduped = _dedupe(reasons[rid])
        impacted.append({
            "report_id": rid,
            "artifact": report["artifact"],
            "producer": report.get("producer"),
            "impact": ("direct" if any(r["kind"] == "direct" for r in deduped)
                       else "transitive"),
            # `certain` when at least one reason came from an exact rule.
            # `possible` when every reason this report has came from a rule
            # that over-approximates, i.e. it may be a false positive and the
            # named check is what settles it.
            "confidence": ("certain"
                           if any(r["precision"] == "exact" for r in deduped)
                           else "possible"),
            "reasons": deduped,
            "chains": sorted(_dedupe(chains[rid]), key=lambda c: (len(c), c)),
            "regenerate": report.get("regenerate"),
            "enforced_by": report.get("enforced_by", []),
        })

    unaffected = sorted(rid for rid in live if rid not in reasons)
    pinned = sorted(rid for rid, r in reports.items() if r["pinned"])

    return {
        "schema_version": SCHEMA_VERSION,
        "map_version": doc.get("map_version"),
        "changed_paths": list(paths),
        "change_kind": change_kind,
        "treated_as_new_directories": sorted(set(new_dirs)),
        "treated_as_deleted_directories": sorted(set(gone_dirs)),
        "impacted": impacted,
        "unaffected_reports": unaffected,
        "pinned_reports_never_impacted": pinned,
        "summary": {
            "changed_paths": len(paths),
            "impacted_reports": len(impacted),
            "direct": sum(1 for i in impacted if i["impact"] == "direct"),
            "transitive": sum(1 for i in impacted if i["impact"] == "transitive"),
            "certain": sum(1 for i in impacted if i["confidence"] == "certain"),
            "possible": sum(1 for i in impacted if i["confidence"] == "possible"),
            "unaffected_reports": len(unaffected),
            "pinned_reports": len(pinned),
        },
    }


def _dedupe(items):
    """Order-preserving dedupe of JSON-able items, then sorted canonically."""
    seen = {}
    for item in items:
        seen.setdefault(canonical(item), item)
    return [seen[k] for k in sorted(seen)]


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def read_paths(args):
    if args.stdin:
        if args.paths:
            raise UsageError("--stdin takes the paths from standard input; "
                             "do not also pass them as arguments")
        raw = sys.stdin.read().split("\n")
    else:
        raw = list(args.paths)
    cleaned = [r for r in raw if r.strip() != ""]
    if not cleaned:
        raise UsageError("no paths given")
    normalised = sorted(set(normalize_path(r) for r in cleaned))
    return normalised


def build_parser():
    parser = argparse.ArgumentParser(
        prog="predict_impact.py",
        description="Predict which committed reports a set of changed "
                    "repository paths will invalidate.")
    parser.add_argument("paths", nargs="*",
                        help="repository-relative changed paths")
    parser.add_argument("--stdin", action="store_true",
                        help="read the paths from standard input, one per line")
    parser.add_argument("--change-kind", default="unknown",
                        choices=list(CHANGE_KINDS),
                        help="what kind of change these paths represent. "
                             "Default 'unknown', which is the conservative "
                             "reading: an edge counts as certain only if it "
                             "holds for an add, a remove and an edit alike")
    parser.add_argument("--new-dir", action="append", default=[],
                        metavar="NAME", dest="new_dirs",
                        help="treat this top-level directory as one the change "
                             "creates, even if the map already lists it. "
                             "Repeatable")
    parser.add_argument("--gone-dir", action="append", default=[],
                        metavar="NAME", dest="gone_dirs",
                        help="a top-level directory this change DELETES. A "
                             "path list cannot tell one removed file from a "
                             "removed directory, so it has to be said. "
                             "Repeatable")
    parser.add_argument("--map", default=None, metavar="FILE",
                        help="dependency map to use (default: dependency_map.json "
                             "next to this script)")
    parser.add_argument("--output", metavar="FILE",
                        help="write the report here instead of stdout")
    return parser


def default_map_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "dependency_map.json")


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        paths = read_paths(args)
        doc = load_map(args.map or default_map_path())
    except UsageError as exc:
        sys.stderr.write("predict_impact.py: error: %s\n" % exc)
        return EXIT_USAGE

    for name in args.new_dirs + args.gone_dirs:
        if "/" in name or name in ("", ".", ".."):
            sys.stderr.write("predict_impact.py: error: --new-dir takes a "
                             "top-level directory NAME, not a path: '%s'\n" % name)
            return EXIT_USAGE

    try:
        report = predict(doc, paths, args.new_dirs, args.change_kind,
                         args.gone_dirs)
    except UsageError as exc:
        sys.stderr.write("predict_impact.py: error: %s\n" % exc)
        return EXIT_USAGE
    text = canonical(report)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as exc:
            sys.stderr.write("predict_impact.py: error: cannot write %s: %s\n"
                             % (args.output, exc.strerror or exc))
            return EXIT_USAGE
    else:
        sys.stdout.write(text)

    return EXIT_IMPACTED if report["impacted"] else EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())
