#!/usr/bin/env python3
"""snapdiff.py -- stdlib-only task-node snapshot differ.

Diffs two exported task-node snapshots (JSON documents shaped like
{"tasks": [...], "summary": {...}}) taken at different times, and
reports exactly what changed between them:

  * TASK_ADDED           -- a task_id present in "after" but not "before"
  * TASK_REMOVED         -- a task_id present in "before" but not "after"
  * STATUS_TRANSITION    -- a task's "status" field changed (from -> to)
  * REWARD_CHANGED       -- a task's "reward" changed (from, to, signed delta)
  * EVIDENCE_ADDED       -- evidence items present in "after" but not "before"
  * EVIDENCE_REMOVED     -- evidence items present in "before" but not "after"
  * FIELD_CHANGED        -- any other tracked scalar field changed
  * SUMMARY_CHANGED      -- the top-level "summary" object changed

Input shape is REUSED from the sibling tool queue_audit.py (see
README.md, "What we reused from queue-auditor"): a JSON object with a
"tasks" array and a "summary" object. snapdiff.py extends each task
record with an optional "evidence" array (a field queue_audit.py's
schema does not define), since the diff spec explicitly requires an
EVIDENCE_ADDED / EVIDENCE_REMOVED category.

Output is canonical JSON: json.dumps(..., sort_keys=True,
separators=(",", ":"), ensure_ascii=True) followed by a single
trailing newline, with NO runtime-dependent fields anywhere (no
wall-clock timestamps, no hostnames, no absolute paths, no
set/dict-ordering leakage). The report describes the DIFF, not when it
was computed. Running the tool twice on the same two inputs therefore
produces byte-identical output -- see test_two_runs_byte_identical and
the sha256sum/cmp steps in captured_output.txt.

Exit codes:
  0 -- no differences between the two snapshots
  1 -- snapshots were read and parsed successfully; differences found
  2 -- invalid input/usage (missing file, invalid JSON, malformed
       snapshot shape, duplicate task_id, invalid reward, bad CLI usage)

Standard library only: argparse, json, sys, decimal.

MONEY: rewards are parsed with json.loads(..., parse_float=Decimal) so
a JSON number literal with a decimal point/exponent is built directly
from its *source text* into a Decimal, and never touches a 64-bit
float as an intermediate representation. NaN/Infinity/-Infinity are
rejected outright via parse_constant. Reward amounts are emitted as
JSON strings in the report (never bare JSON numbers), so a consumer
re-parsing the report never has to worry about a float round-trip
reintroducing the precision loss this tool exists to avoid.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation, getcontext

# A high-precision context guards against context-precision rounding on
# very large/ many-significant-digit reward literals (the default
# decimal context precision is only 28 significant digits, which is
# uncomfortably close to what an 18-integer-digit + 9-fraction-digit
# reward already uses). This is a defensive choice, not a requirement
# of any fixture in this repo -- see README "Design notes".
getcontext().prec = 80

# --------------------------------------------------------------------------
# Change/entry type codes
# --------------------------------------------------------------------------

TASK_ADDED = "TASK_ADDED"
TASK_REMOVED = "TASK_REMOVED"
STATUS_TRANSITION = "STATUS_TRANSITION"
REWARD_CHANGED = "REWARD_CHANGED"
EVIDENCE_ADDED = "EVIDENCE_ADDED"
EVIDENCE_REMOVED = "EVIDENCE_REMOVED"
FIELD_CHANGED = "FIELD_CHANGED"
SUMMARY_CHANGED = "SUMMARY_CHANGED"

ALL_CHANGE_TYPES = (
    TASK_ADDED,
    TASK_REMOVED,
    STATUS_TRANSITION,
    REWARD_CHANGED,
    EVIDENCE_ADDED,
    EVIDENCE_REMOVED,
    FIELD_CHANGED,
    SUMMARY_CHANGED,
)

# --------------------------------------------------------------------------
# Error codes (all fatal -> exit 2)
# --------------------------------------------------------------------------

MALFORMED_SNAPSHOT = "MALFORMED_SNAPSHOT"
DUPLICATE_TASK_ID = "DUPLICATE_TASK_ID"
INVALID_REWARD = "INVALID_REWARD"

EXIT_NO_CHANGES = 0
EXIT_CHANGES = 1
EXIT_INVALID_INPUT = 2

# Fields that are never diffed generically -- they either have their own
# dedicated change category (status, reward, evidence) or are the
# identity key itself (task_id).
_SPECIAL_FIELDS = frozenset({"task_id", "status", "reward", "evidence"})


class SnapshotError(Exception):
    """Fatal input problem: MALFORMED_SNAPSHOT, DUPLICATE_TASK_ID, or
    INVALID_REWARD. Always maps to exit code 2."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self):
        return "[%s] %s" % (self.code, self.message)


# --------------------------------------------------------------------------
# Canonical JSON
# --------------------------------------------------------------------------


def _canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_dumps(obj):
    """Serialize obj as canonical JSON with a single trailing newline."""
    return _canon(obj) + "\n"


def jsonify(value):
    """Recursively convert Decimal -> str so the result is plain-JSON-safe.

    Everything else (str, int, bool, None, dict, list) passes through.
    Dict keys in a JSON document are always already strings.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: jsonify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonify(v) for v in value]
    return value


# --------------------------------------------------------------------------
# JSON parsing: parse_float=Decimal, reject NaN/Infinity
# --------------------------------------------------------------------------


def _reject_constant(token):
    # json's parse_constant hook is called for the bare tokens
    # "NaN", "Infinity", "-Infinity". We refuse all of them outright,
    # anywhere in the document, rather than letting them become a
    # Python float. There is no dedicated error code in this tool's
    # vocabulary for "non-finite number outside of reward", so this is
    # reported as INVALID_REWARD -- see README Limitations.
    raise SnapshotError(
        INVALID_REWARD,
        "document contains a non-finite numeric literal (%s), which is not "
        "a valid JSON number and is always rejected" % token,
    )


def parse_json_document(text):
    """json.loads with parse_float=Decimal and NaN/Infinity rejection.

    Raises SnapshotError(INVALID_REWARD, ...) for non-finite literals,
    or json.JSONDecodeError for ordinary invalid JSON (left to the
    caller, which is not a SnapshotError since it isn't one of this
    tool's three input-error codes -- it's a plain unparsable document).
    """
    return json.loads(text, parse_float=Decimal, parse_constant=_reject_constant)


# --------------------------------------------------------------------------
# Reward parsing
# --------------------------------------------------------------------------


def parse_reward_field(raw, label, task_id):
    """Parse a task's raw "reward" value into a Decimal, or None.

    None means "no reward tracked" -- covers both an absent "reward"
    key and an explicit JSON null (see README "missing vs null").

    Accepts: JSON null/absent -> None; JSON integer -> Decimal; JSON
    float-looking literal (already a Decimal thanks to
    parse_float=Decimal) -> Decimal (rejecting NaN/Infinite); JSON
    string that parses as a decimal number -> Decimal (parsed directly
    from the string via the Decimal constructor -- never via float).

    Raises SnapshotError(INVALID_REWARD, ...) for booleans, arrays,
    objects, un-parseable strings, or NaN/Infinite values.
    """
    if raw is None:
        return None

    if isinstance(raw, bool):
        raise SnapshotError(
            INVALID_REWARD,
            "%s: task '%s' reward must be numeric, got boolean %r" % (label, task_id, raw),
        )

    if isinstance(raw, int):
        return Decimal(raw)

    if isinstance(raw, Decimal):
        # Normal path for any fractional/exponential JSON number literal
        # when parsed with parse_float=Decimal: built directly from the
        # source text, so full precision is preserved (never round-trips
        # through a 64-bit float).
        if raw.is_nan():
            raise SnapshotError(INVALID_REWARD, "%s: task '%s' reward is NaN" % (label, task_id))
        if raw.is_infinite():
            raise SnapshotError(INVALID_REWARD, "%s: task '%s' reward is infinite" % (label, task_id))
        return raw

    if isinstance(raw, float):
        # Should not normally be reachable: with parse_float=Decimal, a
        # bare finite float should never arrive here. Guard explicitly
        # rather than silently trusting it, so a future refactor that
        # forgets parse_float=Decimal fails loudly instead of
        # reintroducing the precision bug this tool exists to avoid.
        raise SnapshotError(
            INVALID_REWARD,
            "%s: task '%s' reward arrived as a raw float (%r); the parser must use "
            "parse_float=Decimal" % (label, task_id, raw),
        )

    if isinstance(raw, str):
        try:
            value = Decimal(raw)
        except InvalidOperation:
            raise SnapshotError(
                INVALID_REWARD,
                "%s: task '%s' reward string %r is not a valid decimal number" % (label, task_id, raw),
            )
        if value.is_nan():
            raise SnapshotError(INVALID_REWARD, "%s: task '%s' reward is NaN" % (label, task_id))
        if value.is_infinite():
            raise SnapshotError(INVALID_REWARD, "%s: task '%s' reward is infinite" % (label, task_id))
        return value

    if isinstance(raw, list):
        raise SnapshotError(
            INVALID_REWARD, "%s: task '%s' reward must be a JSON number, got an array" % (label, task_id)
        )
    if isinstance(raw, dict):
        raise SnapshotError(
            INVALID_REWARD, "%s: task '%s' reward must be a JSON number, got an object" % (label, task_id)
        )

    raise SnapshotError(
        INVALID_REWARD,
        "%s: task '%s' reward must be a JSON number, got %s" % (label, task_id, type(raw).__name__),
    )


def decimal_str(value):
    """Render a Decimal as a plain fixed-point string, never scientific
    notation.

    Bug found during development: plain str(Decimal(...)) switches to
    scientific notation once the value's adjusted exponent drops below
    -6 (this is standard decimal-string-conversion behaviour, not a
    bug in the decimal module) -- e.g. str(Decimal('123456789012345678.123456790')
    - str(Decimal('123456789012345678.123456789')) == Decimal('1E-9'),
    and str() on that renders "1E-9" instead of "0.000000001". That is
    still numerically exact and round-trips fine through Decimal(), but
    it is a surprising, easy-to-misread shape for a money delta to take
    in a diff report, and it would have shipped that way if this
    formatting helper had not been added. format(value, 'f') forces
    fixed-point notation for any finite Decimal, so amounts and deltas
    in this tool's output are always plain decimal digits.
    """
    return format(value, "f")


def format_signed_delta(delta):
    """Format a Decimal delta with an explicit sign: '+' for >= 0, the
    natural leading '-' (already part of the fixed-point string) for
    negative."""
    text = decimal_str(delta)
    if delta >= 0:
        return "+" + text
    return text


# --------------------------------------------------------------------------
# Shape validation
# --------------------------------------------------------------------------


def validate_shape(document, label):
    """Validate top-level document shape. Returns (tasks_list, summary_dict).

    Raises SnapshotError(MALFORMED_SNAPSHOT, ...) if the document isn't
    shaped like a snapshot at all. This mirrors queue_audit.py's
    top-level shape checks (reused convention): not-a-dict, missing/
    wrong-typed "tasks", wrong-typed "summary" are all fatal -- a
    document that isn't shaped like a snapshot can't be meaningfully
    diffed at all.
    """
    if not isinstance(document, dict):
        raise SnapshotError(MALFORMED_SNAPSHOT, "%s: top-level JSON value must be an object" % label)

    tasks = document.get("tasks")
    if not isinstance(tasks, list):
        raise SnapshotError(MALFORMED_SNAPSHOT, "%s: 'tasks' field must be present and be an array" % label)

    summary = document.get("summary", {})
    if not isinstance(summary, dict):
        raise SnapshotError(MALFORMED_SNAPSHOT, "%s: 'summary' field must be an object" % label)

    return tasks, summary


def validate_and_index_tasks(tasks, label):
    """Validate each task record just enough to establish identity and
    safely parse money, then index by task_id.

    snapdiff.py deliberately performs LIGHTER structural validation than
    queue_audit.py: queue_audit's job is to enforce the full snapshot
    schema (every required field present and well-typed); snapdiff's
    job is only to (a) safely establish task identity via task_id and
    (b) safely parse the reward field as Decimal, then diff everything
    else generically as opaque JSON values. A task record missing e.g.
    'title' entirely is therefore not fatal here -- it just diffs from
    missing (null) to whatever the other snapshot has, surfaced as an
    ordinary FIELD_CHANGED entry. See README Limitations.

    Returns a dict: task_id -> {"record": raw_dict, "reward": Decimal|None}.

    Raises SnapshotError(MALFORMED_SNAPSHOT, ...) if a task record is
    not a JSON object, or has a missing/empty/non-string task_id, or
    has an "evidence" field that is present, non-null, and not a list.

    Raises SnapshotError(DUPLICATE_TASK_ID, ...) if the same task_id
    appears more than once within this one snapshot -- see README
    "DUPLICATE_TASK_ID: exit 2 vs finding" for why this is fatal here.
    """
    index = {}
    for i, record in enumerate(tasks):
        if not isinstance(record, dict):
            raise SnapshotError(
                MALFORMED_SNAPSHOT,
                "%s: task record at index %d is not a JSON object (got %s)"
                % (label, i, type(record).__name__),
            )

        raw_id = record.get("task_id")
        if "task_id" not in record or raw_id is None:
            raise SnapshotError(
                MALFORMED_SNAPSHOT, "%s: task record at index %d is missing 'task_id'" % (label, i)
            )
        if not isinstance(raw_id, str) or raw_id == "":
            raise SnapshotError(
                MALFORMED_SNAPSHOT,
                "%s: task record at index %d has a non-string or empty 'task_id' (%r)"
                % (label, i, raw_id),
            )

        if raw_id in index:
            raise SnapshotError(
                DUPLICATE_TASK_ID, "%s: task_id '%s' appears more than once" % (label, raw_id)
            )

        evidence = record.get("evidence")
        if evidence is not None and not isinstance(evidence, list):
            raise SnapshotError(
                MALFORMED_SNAPSHOT,
                "%s: task '%s' field 'evidence' must be an array (got %s)"
                % (label, raw_id, type(evidence).__name__),
            )

        reward = parse_reward_field(record.get("reward"), label, raw_id)
        index[raw_id] = {"record": record, "reward": reward}

    return index


# --------------------------------------------------------------------------
# Diff
# --------------------------------------------------------------------------


def _evidence_list(record):
    ev = record.get("evidence")
    return ev if isinstance(ev, list) else []


def _evidence_map(record):
    """Map canonical-json-of-item -> item, for set-style comparison.

    Evidence is compared as a SET of distinct items (identity = full
    canonical JSON of the item, not just an 'id' sub-field), so:
      * reordering the same items is never a change (order-independent);
      * an item whose 'type' changed for the same 'id' is reported as
        one item removed (old) plus one item added (new) -- there is no
        separate EVIDENCE_CHANGED category in this tool's vocabulary;
      * a duplicate item appearing twice collapses to one entry (see
        README Limitations -- evidence is a set, not a multiset).
    """
    result = {}
    for item in _evidence_list(record):
        result[_canon(jsonify(item))] = item
    return result


def _diff_task_pair(task_id, before_entry, after_entry, ignore_set, entries):
    brec = before_entry["record"]
    arec = after_entry["record"]

    if "status" not in ignore_set:
        bstatus = brec.get("status")
        astatus = arec.get("status")
        if bstatus != astatus:
            entries.append(
                {
                    "type": STATUS_TRANSITION,
                    "task_id": task_id,
                    "from": jsonify(bstatus),
                    "to": jsonify(astatus),
                }
            )

    if "reward" not in ignore_set:
        brew = before_entry["reward"]
        arew = after_entry["reward"]
        if brew != arew:
            delta = None
            if brew is not None and arew is not None:
                delta = format_signed_delta(arew - brew)
            entries.append(
                {
                    "type": REWARD_CHANGED,
                    "task_id": task_id,
                    "from": decimal_str(brew) if brew is not None else None,
                    "to": decimal_str(arew) if arew is not None else None,
                    "delta": delta,
                }
            )

    if "evidence" not in ignore_set:
        before_map = _evidence_map(brec)
        after_map = _evidence_map(arec)
        added_keys = sorted(k for k in after_map if k not in before_map)
        removed_keys = sorted(k for k in before_map if k not in after_map)
        if added_keys:
            entries.append(
                {
                    "type": EVIDENCE_ADDED,
                    "task_id": task_id,
                    "items": [jsonify(after_map[k]) for k in added_keys],
                }
            )
        if removed_keys:
            entries.append(
                {
                    "type": EVIDENCE_REMOVED,
                    "task_id": task_id,
                    "items": [jsonify(before_map[k]) for k in removed_keys],
                }
            )

    skip = _SPECIAL_FIELDS | ignore_set
    fields = (set(brec.keys()) | set(arec.keys())) - skip
    for field in sorted(fields):
        bval = brec.get(field)
        aval = arec.get(field)
        if bval == aval:
            continue
        entries.append(
            {
                "type": FIELD_CHANGED,
                "task_id": task_id,
                "field": field,
                "from": jsonify(bval),
                "to": jsonify(aval),
            }
        )


def _normalized_task_snapshot(entry):
    """jsonify() a task's raw record, but force the 'reward' key (if
    present at all) to go through decimal_str/None like every other
    reward in this tool's output -- otherwise a TASK_ADDED/TASK_REMOVED
    entry could leak a bare JSON number or a numeric-looking string for
    'reward' straight from the source document, which violates the
    "amounts are always emitted as strings" contract that
    REWARD_CHANGED entries follow. This was caught by
    test_task_added / test_task_removed during development -- see
    README "Bug found during development".
    """
    snap = jsonify(entry["record"])
    if "reward" in entry["record"]:
        reward = entry["reward"]
        snap["reward"] = decimal_str(reward) if reward is not None else None
    return snap


def diff_documents(before_index, after_index, before_summary, after_summary, ignore_set):
    """Compute the list of change entries between two indexed snapshots.

    Returns a fully sorted, deterministic list of plain-JSON-safe dicts.
    """
    entries = []

    before_ids = set(before_index)
    after_ids = set(after_index)

    for task_id in sorted(after_ids - before_ids):
        entries.append(
            {"type": TASK_ADDED, "task_id": task_id, "task": _normalized_task_snapshot(after_index[task_id])}
        )

    for task_id in sorted(before_ids - after_ids):
        entries.append(
            {"type": TASK_REMOVED, "task_id": task_id, "task": _normalized_task_snapshot(before_index[task_id])}
        )

    for task_id in sorted(before_ids & after_ids):
        _diff_task_pair(task_id, before_index[task_id], after_index[task_id], ignore_set, entries)

    if "summary" not in ignore_set and before_summary != after_summary:
        entries.append(
            {
                "type": SUMMARY_CHANGED,
                "task_id": None,
                "from": jsonify(before_summary),
                "to": jsonify(after_summary),
            }
        )

    # Deterministic total order: (type, task_id, canonical dump of the
    # entry itself). The canonical-dump tiebreak is a total order over
    # the entry's own content, so no two distinct entries can ever tie
    # -- ties can only happen for byte-identical entries, which would be
    # indistinguishable in the output anyway.
    def sort_key(entry):
        return (entry["type"], entry["task_id"] or "", _canon(entry))

    entries.sort(key=sort_key)
    return entries


def build_report(changes, task_count_before, task_count_after, ignore_set):
    return {
        "changes": changes,
        "change_count": len(changes),
        "ignored_fields": sorted(ignore_set),
        "result": "identical" if not changes else "changed",
        "task_count_after": task_count_after,
        "task_count_before": task_count_before,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _read_input_text(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


class SingleUse(argparse.Action):
    """Store one value, and refuse a second occurrence of the option.

    argparse's default "store" action is last-one-wins: given
    `-o a.json -o b.json` it silently discards a.json and writes
    b.json, exit 0/1, no warning. For an option documented as taking a
    single value that is a data-loss bug, not a convenience -- the two
    values conflict and there is no way to honour both, so the only
    honest answer is to refuse the invocation.

    This action is deliberately NOT applied to --ignore, which
    README.md documents as repeatable and which uses action="append".
    The two are distinguished by declaration, and
    test_every_optional_action_is_repeatable_or_single_use asserts that
    every optional in the parser is one or the other -- so a future
    option added with a bare store action fails the suite instead of
    quietly reintroducing last-one-wins.

    Repetition is refused even when the two values are equal. `-o x -o x`
    expresses the same mistake as `-o x -o y` -- a caller that believes
    the option accumulates -- and a rule with an "unless they happen to
    match" exception is harder to rely on than one without.

    "Already given" is recorded ON THE NAMESPACE being filled, under a
    private attribute named after the destination, and never on the
    action instance. Two rejected alternatives explain why:

      * Reading the destination back and testing it for None is wrong
        whenever something put a value there first.
        `parser.set_defaults(output=...)` and
        `parse_args(argv, namespace=<pre-populated>)` both do, and a
        value-based probe reads that preset as a first occurrence and
        refuses the caller's only real one -- naming a value the caller
        never supplied in the error.
      * Keeping the marker on the action instance is worse still.
        argparse actions are per-parser, not per-parse, so two threads
        parsing through one parser overwrite each other's marker: a
        genuine `-o A -o B` is silently accepted again, which is the
        exact bug this class exists to remove, and refusals quote
        another caller's argv. Reusing one namespace object across two
        parses breaks the same way in a single thread.

    The namespace is per-parse and per-thread by construction, so a
    marker on it is correct in all of those cases. It does mean the
    attribute is visible in the namespace parse_args returns;
    strip_single_use_markers() below removes it, and run() calls it.
    MARKER_PREFIX and marker_attribute() are public so any other caller
    can do the same.

    Values are quoted for the error message with
    json.dumps(..., ensure_ascii=False), which delimits the value,
    escapes what needs escaping, keeps a non-ASCII path legible, and
    speaks the language this tool speaks everywhere else. It also
    avoids adding a finding to nondeterminism-scanner's ND004 rule,
    which flags %r syntactically -- though note ND004 aims at repr()
    falling back to object.__repr__, and this module already has four
    older %r sites that are, like this one, %r applied to a plain
    string. The choice here is a small preference plus not moving a
    committed repository-wide report, not a claim that the older sites
    are defective.

    One limit worth stating: argparse resolves an abbreviated long
    option to its full spelling before any action runs, so
    `--outp x --outp y` is reported as `--output` twice. The message
    names what argparse resolved, which is the closest thing to "what
    the caller typed" that an action can see.
    """

    #: Prefix for the per-parse marker attribute, e.g. "_single_use__output".
    MARKER_PREFIX = "_single_use__"

    def __init__(self, option_strings, dest, **kwargs):
        if kwargs.get("nargs") == 0:
            raise ValueError(
                "SingleUse is for options that take a value; %s declares "
                "nargs=0" % dest
            )
        super().__init__(option_strings, dest, **kwargs)

    def marker_attribute(self):
        return self.MARKER_PREFIX + self.dest

    def __call__(self, parser, namespace, values, option_string=None):
        marker = self.marker_attribute()
        first = getattr(namespace, marker, None)
        if first is None:
            # option_string is None only for positionals, which this
            # action is not for; the fallback keeps the message sane
            # rather than printing None if one is ever built that way.
            setattr(namespace, marker, option_string or self.option_strings[0])
            setattr(namespace, self.dest, values)
            return
        previous = getattr(namespace, self.dest, None)
        parser.error(
            "%s accepts a single value but was given twice: %s %s, then %s %s. "
            "Pass it once -- repeating it would silently discard %s."
            % (
                "/".join(self.option_strings),
                first,
                json.dumps(previous, ensure_ascii=False),
                option_string,
                json.dumps(values, ensure_ascii=False),
                json.dumps(previous, ensure_ascii=False),
            )
        )


def strip_single_use_markers(parser, namespace):
    """Remove SingleUse's per-parse markers from a parsed namespace.

    SingleUse records "this option has been seen" on the namespace, so
    the state is per-parse and per-thread rather than per-parser. The
    cost is one extra attribute in what parse_args hands back; this
    removes it, so a caller sees only the destinations they declared.

    Written as a function taking a stock ArgumentParser rather than as
    an ArgumentParser subclass on purpose. doc-validator/docval.py
    discovers a tool's CLI by looking for a variable assigned from a
    call literally named ArgumentParser (or add_parser /
    add_subparsers); a subclass under any other name makes this file
    stop being a discoverable CLI to that scanner, and takes every
    option in it out of doc-validator/option_report.json's
    cross-tool comparison. A helper costs one line at the call site and
    keeps the parser construction in the shape the repository's own
    tooling reads.

    Returns the same namespace object, for use as `args = strip(...)`.
    """
    for action in parser._actions:
        if isinstance(action, SingleUse):
            try:
                delattr(namespace, action.marker_attribute())
            except AttributeError:
                pass
    return namespace


def build_parser():
    """Build the CLI parser.

    Split out of run() so the structural test can inspect the actions
    without parsing anything. Note that parser.parse_args() on its own
    leaves SingleUse's per-parse marker in the namespace; run() passes
    the result through strip_single_use_markers, and any other caller
    who cares should too.
    """
    parser = argparse.ArgumentParser(
        prog="snapdiff.py",
        description="Diff two task-node snapshots and report exactly what changed.",
    )
    parser.add_argument("before", help="Path to the earlier snapshot JSON file.")
    parser.add_argument("after", help="Path to the later snapshot JSON file.")
    parser.add_argument(
        "-o",
        "--output",
        action=SingleUse,
        default=None,
        metavar="OUTPUT",
        help=(
            "Write the canonical JSON report to this file instead of stdout. "
            "May be given only once; a second occurrence is a usage error "
            "(exit 2) rather than a silent overwrite of the first value."
        ),
    )
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="FIELD",
        help=(
            "Exclude FIELD from comparison (repeatable). Recognized special "
            "names: status, reward, evidence, summary disable their entire "
            "change category; any other name is excluded from generic "
            "FIELD_CHANGED comparison. A name that never appears in any "
            "task is accepted as a harmless no-op."
        ),
    )
    return parser


def run(argv):
    parser = build_parser()
    args = strip_single_use_markers(parser, parser.parse_args(argv))

    ignore_set = set(args.ignore)

    try:
        before_text = _read_input_text(args.before)
    except OSError as exc:
        print("snapdiff.py: error: could not read '%s': %s" % (args.before, exc), file=sys.stderr)
        return EXIT_INVALID_INPUT

    try:
        after_text = _read_input_text(args.after)
    except OSError as exc:
        print("snapdiff.py: error: could not read '%s': %s" % (args.after, exc), file=sys.stderr)
        return EXIT_INVALID_INPUT

    try:
        before_doc = parse_json_document(before_text)
    except SnapshotError as exc:
        print("snapdiff.py: error: %s: %s" % (args.before, exc), file=sys.stderr)
        return EXIT_INVALID_INPUT
    except json.JSONDecodeError as exc:
        print("snapdiff.py: error: invalid JSON in '%s': %s" % (args.before, exc), file=sys.stderr)
        return EXIT_INVALID_INPUT

    try:
        after_doc = parse_json_document(after_text)
    except SnapshotError as exc:
        print("snapdiff.py: error: %s: %s" % (args.after, exc), file=sys.stderr)
        return EXIT_INVALID_INPUT
    except json.JSONDecodeError as exc:
        print("snapdiff.py: error: invalid JSON in '%s': %s" % (args.after, exc), file=sys.stderr)
        return EXIT_INVALID_INPUT

    try:
        before_tasks, before_summary = validate_shape(before_doc, "before")
        after_tasks, after_summary = validate_shape(after_doc, "after")
        before_index = validate_and_index_tasks(before_tasks, "before")
        after_index = validate_and_index_tasks(after_tasks, "after")
    except SnapshotError as exc:
        print("snapdiff.py: error: %s" % exc, file=sys.stderr)
        return EXIT_INVALID_INPUT

    changes = diff_documents(before_index, after_index, before_summary, after_summary, ignore_set)
    report = build_report(changes, len(before_tasks), len(after_tasks), ignore_set)
    output_text = canonical_dumps(report)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8", newline="") as fh:
                fh.write(output_text)
        except OSError as exc:
            print("snapdiff.py: error: could not write '%s': %s" % (args.output, exc), file=sys.stderr)
            return EXIT_INVALID_INPUT
    else:
        sys.stdout.write(output_text)

    return EXIT_CHANGES if changes else EXIT_NO_CHANGES


def main():
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
