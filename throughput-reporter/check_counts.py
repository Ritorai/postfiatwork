"""Stale-number check for this tool's committed reports.

`report_ok.json`, `report_breach_run1.json` and `report_breach_run2.json`
are each a function of one event fixture and the flags the run was given.
Those numbers are worth something only while they still describe
`events_ok.json` and `events_breach.json`, and nothing in this directory
would have noticed when they stopped. This check recomputes all three
reports from the fixtures and the documented rerun commands, and refuses
to pass when a committed value and its source inputs have drifted apart.

There are no exemptions. Every path in a committed report is either
recomputed from the fixture or pinned to the invocation `README.md`
documents; the two are flattened to dotted paths and the sets compared,
so a value added to a report later is reported rather than passed over.
The file's canonical form is checked as well, because a report that has
been reformatted no longer reproduces byte for byte even when every value
in it is right.

It deliberately does NOT import `throughput.py`, so that a stale report
is caught by something other than the code that wrote it, and it is
honest about what that buys: this is a transliteration of `analyze()`
into a second file, not an independent derivation. It detects the report
and the fixture drifting apart. It does not detect a bug shared with the
tool, and neither does the agreement test, which compares the copy with
its original. `report-freshness/manifest.json` does not track this tool
at all, so before this there was nothing at either level.

The recomputation, mirroring `analyze()`:

  * Events are grouped by contributor first and by `task_id` second, so a
    task touched by two contributors is counted once for each of them.
  * Per contributor-task, the timestamp kept for a state is the EARLIEST
    one carrying it. `analyze()` reaches the same value by sorting on
    `(timestamp, state)` and keeping the first of each state; the
    secondary key only orders different states sharing a timestamp, so
    the per-state minimum is the same.
  * `accepted` and `submitted` each add one when that state appears at
    all. `rewarded` wins over `refused`: a task carrying both counts as
    rewarded and not as refused. `terminal` is `rewarded + refused`.
  * `accept -> submit` and `submit -> terminal` durations are collected
    only when both ends exist and the difference is not negative, then
    reduced to a median in hours rounded to 4dp, or null when no pair
    survived. The terminal end is `rewarded` if present, else `refused`.
  * `refusal_rate` is `refused / terminal` rounded to 6dp, or 0.0 with no
    terminal outcome. Grades apply in order: INSUFFICIENT_DATA under
    `min_tasks` terminal outcomes, then A, B, C, D. `over_ceiling`
    additionally requires `min_tasks` terminal outcomes.
  * Contributors sort by `(-refusal_rate, contributor)`; that order is
    checked too. `status` is `ceiling_breach` when any contributor is
    over the ceiling.

Usage: python3 check_counts.py [DIRECTORY]

DIRECTORY defaults to the directory holding this file. Tests point it at
a temporary copy with one value edited.

Exit codes: 0 every value reproduces | 1 at least one does not | 2 a file
is missing or unreadable, a fixture the tool itself would refuse, a shape
this check does not recognise, or a usage error.
"""
import json
import os
import statistics
import sys
from datetime import datetime, timezone

PROG = "check_counts.py"

#: The version string serialize() writes. Pinned rather than trusted: a
#: report carrying a different one was not produced by this tool.
REPORT_VERSION = "1.0"

#: (event fixture, committed report, the flags README.md documents for
#: producing it). All three committed reports come from a command with no
#: flags, so both values are argparse's defaults.
PAIRS = (
    ("events_ok.json", "report_ok.json",
     {"refusal_ceiling": 0.5, "min_tasks": 2}),
    ("events_breach.json", "report_breach_run1.json",
     {"refusal_ceiling": 0.5, "min_tasks": 2}),
    ("events_breach.json", "report_breach_run2.json",
     {"refusal_ceiling": 0.5, "min_tasks": 2}),
)

#: The states load_events() accepts. A fixture carrying anything else is
#: one the tool itself refuses, so this check refuses it too rather than
#: printing expected values the tool could never have produced.
KNOWN_STATES = frozenset((
    "proposed", "accepted", "submitted", "verification_requested",
    "rewarded", "refused", "cancelled",
))


class CheckError(Exception):
    """Input this check cannot read or does not recognise."""


def read_text(path):
    name = os.path.basename(path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        raise CheckError("file not found: %s" % name)
    except UnicodeDecodeError as exc:
        raise CheckError("%s is not UTF-8: %s" % (name, exc))
    except OSError as exc:
        raise CheckError("cannot read %s: %s" % (name, exc.strerror or exc))


def read_json(path):
    """(parsed, exact file text) for one JSON file."""
    text = read_text(path)
    try:
        return json.loads(text), text
    except json.JSONDecodeError as exc:
        raise CheckError("invalid JSON in %s: %s"
                         % (os.path.basename(path), exc))


def _value(obj):
    """One value, rendered the way every message in this file renders it.

    `ensure_ascii=True` matches serialize() and keeps every message
    printable on an ASCII stdout, which a non-ASCII contributor name
    would otherwise break.
    """
    return json.dumps(obj, sort_keys=True, ensure_ascii=True)


def canonical(report):
    """serialize()'s contract, restated: what the file should contain."""
    return json.dumps(report, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True) + "\n"


def _timestamp(value, where):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise CheckError("%s: occurred_at is not ISO-8601: %s"
                         % (where, _value(value)))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def load_fixture(events, where):
    """Validate a fixture the way load_events() does, and index it.

    Returns {contributor: {task_id: {state: earliest timestamp}}}.
    """
    if not isinstance(events, list):
        raise CheckError("%s: expected a JSON array, got %s"
                         % (where, type(events).__name__))
    indexed = {}
    for i, record in enumerate(events):
        at = "%s: events[%d]" % (where, i)
        if not isinstance(record, dict):
            raise CheckError("%s: record must be an object" % at)
        for field in ("task_id", "contributor", "state", "occurred_at"):
            if field not in record:
                raise CheckError("%s: missing required field %s"
                                 % (at, _value(field)))
            if not isinstance(record[field], str) or not record[field].strip():
                raise CheckError("%s: %s must be a non-empty string"
                                 % (at, _value(field)))
        if record["state"] not in KNOWN_STATES:
            raise CheckError("%s: unknown state %s"
                             % (at, _value(record["state"])))
        when = _timestamp(record["occurred_at"], at)
        states = indexed.setdefault(record["contributor"], {}) \
                        .setdefault(record["task_id"], {})
        if record["state"] not in states or when < states[record["state"]]:
            states[record["state"]] = when
    return indexed


def _median_hours(deltas):
    if not deltas:
        return None
    hours = sorted(d.total_seconds() / 3600.0 for d in deltas)
    return round(statistics.median(hours), 4)


def _grade(refusal_rate, median_submit_hours, terminal, cfg):
    if terminal < cfg["min_tasks"]:
        return "INSUFFICIENT_DATA"
    if (refusal_rate <= 0.10 and median_submit_hours is not None
            and median_submit_hours <= 24):
        return "A"
    if refusal_rate <= 0.25:
        return "B"
    if refusal_rate <= cfg["refusal_ceiling"]:
        return "C"
    return "D"


def recompute(events, cfg, where):
    """The whole report a run over *events* under *cfg* should produce."""
    indexed = load_fixture(events, where)

    contributors = []
    for name in sorted(indexed):
        tasks = indexed[name]
        accepted = submitted = rewarded = refused = 0
        accept_to_submit = []
        submit_to_terminal = []
        for task_id in sorted(tasks):
            first = tasks[task_id]
            if "accepted" in first:
                accepted += 1
            if "submitted" in first:
                submitted += 1
            if "rewarded" in first:
                rewarded += 1
            elif "refused" in first:
                refused += 1
            if "accepted" in first and "submitted" in first:
                gap = first["submitted"] - first["accepted"]
                if gap.total_seconds() >= 0:
                    accept_to_submit.append(gap)
            terminal_at = first.get("rewarded", first.get("refused"))
            if "submitted" in first and terminal_at is not None:
                gap = terminal_at - first["submitted"]
                if gap.total_seconds() >= 0:
                    submit_to_terminal.append(gap)

        terminal = rewarded + refused
        rate = round(refused / terminal, 6) if terminal else 0.0
        median_submit = _median_hours(accept_to_submit)
        contributors.append({
            "contributor": name,
            "counts": {"accepted": accepted, "refused": refused,
                       "rewarded": rewarded, "submitted": submitted,
                       "tasks_seen": len(tasks), "terminal": terminal},
            "refusal_rate": rate,
            "median_accept_to_submit_hours": median_submit,
            "median_submit_to_terminal_hours": _median_hours(submit_to_terminal),
            "grade": _grade(rate, median_submit, terminal, cfg),
            "over_ceiling": (terminal >= cfg["min_tasks"]
                             and rate > cfg["refusal_ceiling"]),
        })

    contributors.sort(key=lambda c: (-c["refusal_rate"], c["contributor"]))
    breaches = [c for c in contributors if c["over_ceiling"]]
    grades = {}
    for entry in contributors:
        grades[entry["grade"]] = grades.get(entry["grade"], 0) + 1
    return {
        "report_version": REPORT_VERSION,
        "config": {"refusal_ceiling": cfg["refusal_ceiling"],
                   "min_tasks": cfg["min_tasks"]},
        "totals": {"events": len(events), "contributors": len(contributors),
                   "over_ceiling": len(breaches)},
        "grade_counts": dict(sorted(grades.items())),
        "contributors": contributors,
        "status": "ok" if not breaches else "ceiling_breach",
    }


def flatten(prefix, value, out):
    """Collect every leaf of *value* into *out* under a dotted path."""
    if isinstance(value, dict):
        for key in sorted(value):
            flatten("%s.%s" % (prefix, key) if prefix else key,
                    value[key], out)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            flatten("%s[%d]" % (prefix, i), item, out)
    else:
        out[prefix] = value
    return out


def flat_report(report_name, report):
    """(dotted path -> value, contributor order, repeated names).

    A repeated contributor name is disambiguated as `name#2`, `name#3`
    rather than overwritten, so a value hiding behind a twin entry is
    still compared instead of silently replaced by the last one.
    """
    if not isinstance(report, dict):
        raise CheckError("%s: expected a JSON object, got %s"
                         % (report_name, type(report).__name__))
    entries = report.get("contributors")
    if not isinstance(entries, list):
        raise CheckError("%s: no contributors array" % report_name)

    out = {}
    flatten("", {k: v for k, v in report.items() if k != "contributors"}, out)

    order = []
    seen = {}
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise CheckError("%s: contributors[%d] is not an object"
                             % (report_name, i))
        name = entry.get("contributor")
        if not isinstance(name, str) or not name.strip():
            raise CheckError("%s: contributors[%d] has no contributor name"
                             % (report_name, i))
        order.append(name)
        seen[name] = seen.get(name, 0) + 1
        key = name if seen[name] == 1 else "%s#%d" % (name, seen[name])
        flatten("contributors[%s]" % key,
                {k: v for k, v in entry.items() if k != "contributor"}, out)

    repeated = sorted(n for n, count in seen.items() if count > 1)
    return out, order, repeated


def compare(report_name, report, text, expected):
    """(problems, paths_checked) for one committed report."""
    found, order, repeated = flat_report(report_name, report)
    want, want_order, _ = flat_report(report_name, expected)

    problems = []
    for name in repeated:
        problems.append("DUPLICATE %s contributors carries %s more than once"
                        % (report_name, _value(name)))

    checked = 0
    for path in sorted(want):
        if path not in found:
            problems.append("MISSING %s %s expected=%s found=absent"
                            % (report_name, path, _value(want[path])))
            continue
        checked += 1
        if _value(found[path]) != _value(want[path]):
            problems.append("STALE %s %s expected=%s found=%s"
                            % (report_name, path, _value(want[path]),
                               _value(found[path])))
    for path in sorted(found):
        if path not in want:
            problems.append("UNEXPECTED %s %s not produced by the "
                            "recomputation in %s" % (report_name, path, PROG))

    checked += 1
    if order != want_order:
        problems.append("STALE %s contributors.order expected=%s found=%s"
                        % (report_name, _value(want_order), _value(order)))

    checked += 1
    if text is not None and text != canonical(report):
        problems.append("FORMAT %s the file is not the canonical rendering "
                        "of its own contents: sort_keys, compact separators, "
                        "ASCII escapes, one trailing newline" % report_name)
    return problems, checked


def run(directory):
    """(problems, paths_checked) for every pair in *directory*."""
    problems = []
    checked = 0
    for events_name, report_name, cfg in PAIRS:
        events, _ = read_json(os.path.join(directory, events_name))
        report, text = read_json(os.path.join(directory, report_name))
        expected = recompute(events, cfg, events_name)
        found, count = compare(report_name, report, text, expected)
        problems.extend(found)
        checked += count
    return problems, checked


KINDS = ("stale", "missing", "unexpected", "duplicate", "format")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) > 1:
        sys.stderr.write("usage: python3 %s [DIRECTORY]\n" % PROG)
        return 2
    if argv and argv[0] in ("-h", "--help"):
        sys.stdout.write(__doc__)
        return 0
    directory = argv[0] if argv else os.path.dirname(os.path.abspath(__file__))
    try:
        problems, checked = run(directory)
    except CheckError as exc:
        sys.stderr.write("INVALID_INPUT: %s\n" % exc)
        return 2
    tally = dict.fromkeys(KINDS, 0)
    for line in problems:
        sys.stdout.write(line + "\n")
        tally[line.split(" ", 1)[0].lower()] += 1
    sys.stdout.write("checked=%d %s\n"
                     % (checked,
                        " ".join("%s=%d" % (k, tally[k]) for k in KINDS)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
