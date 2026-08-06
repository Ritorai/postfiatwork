#!/usr/bin/env python3
"""sortdetect.py -- sort-key-tiebreak instability detector.

Runs a target tool against multiple DETERMINISTIC permutations of the same
input record set, canonicalizes each resulting output, and flags whether the
ORDER of records in the output changed between permutations. A tool whose
sort key is a total order (i.e. every group of records that ties on the
tool's "meaningful" fields is broken by a final canonical-dump tiebreak)
must produce byte-identical, order-stable output regardless of what order
the records were supplied in. A tool that is missing that tiebreak will,
for records that tie, fall back to Python's *stable* sort behaviour and
simply preserve whatever order the records happened to arrive in -- so its
output order becomes a function of input order, which is exactly the
"non-total-order" signature this tool is built to catch.

CRITICAL DESIGN RULE: this tool never inspects or reasons about the target's
source code. It only ever subprocesses the target tool for real, on real
materialised fixture copies, and diffs the real bytes that come back.

Exit codes (this detector's own exit code, not the target tool's):
  0 -- all permutations produced identical output order (STABLE)
  1 -- output order changed across permutations (UNSTABLE -- the finding)
  2 -- the detector could not complete the run (bad args, missing target,
       target crashed, target produced unparsable output, fixture malformed)

Canonical JSON: json.dumps(obj, sort_keys=True, separators=(",", ":"),
ensure_ascii=True) + a single trailing "\\n", written with newline="\\n".
Every list this tool itself emits is explicitly sorted with the canonical
dump of each item as the FINAL tiebreak element of its own sort key -- see
`_report_sort_key` below. We practise what we preach.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

DETECTOR_VERSION = "1.0"

EXIT_STABLE = 0
EXIT_UNSTABLE = 1
EXIT_ERROR = 2

DEFAULT_PERMUTATIONS = 6


# ---------------------------------------------------------------------------
# canonical JSON
# ---------------------------------------------------------------------------

def canonical_dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def canonical_text(obj) -> str:
    """Same as canonical_dumps but without the trailing newline, for
    embedding one canonical value inside another data structure (e.g. as a
    tiebreak key or inside a diff entry)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def write_canonical(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(canonical_dumps(obj))


# ---------------------------------------------------------------------------
# tiny JSON Pointer subset (RFC 6901 "/"-separated tokens; "" = root)
# ---------------------------------------------------------------------------

def _unescape_token(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def pointer_tokens(pointer: str):
    if pointer in ("", None):
        return []
    if not pointer.startswith("/"):
        raise ValueError("JSON pointer must start with '/' or be empty: %r" % pointer)
    return [_unescape_token(t) for t in pointer.split("/")[1:]]


def pointer_get(doc, pointer: str):
    node = doc
    for token in pointer_tokens(pointer):
        if isinstance(node, list):
            node = node[int(token)]
        elif isinstance(node, dict):
            node = node[token]
        else:
            raise KeyError("cannot descend into %r with token %r" % (type(node).__name__, token))
    return node


def pointer_set(doc, pointer: str, value):
    tokens = pointer_tokens(pointer)
    if not tokens:
        raise ValueError("cannot pointer_set the document root")
    node = doc
    for token in tokens[:-1]:
        node = node[int(token)] if isinstance(node, list) else node[token]
    last = tokens[-1]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value


# ---------------------------------------------------------------------------
# deterministic permutation generation (no RNG, no seeds, no clock)
# ---------------------------------------------------------------------------

def generate_index_permutations(k: int, n: int):
    """Return up to n distinct deterministic permutations of range(k), each
    a full permutation covering every one of the k records. Built from
    rotations and reversed-rotations, which are fully determined by k and
    n alone -- no randomness. If fewer than n distinct permutations exist
    (small k) the sequence is padded by cycling back through the distinct
    ones already produced, still deterministically."""
    if k <= 0:
        return [[] for _ in range(max(n, 0))]
    if k == 1:
        return [[0] for _ in range(max(n, 0))]

    candidates = [list(range(k))]  # identity
    candidates.append(list(reversed(range(k))))  # full reversal
    for shift in range(1, k):
        rot = list(range(shift, k)) + list(range(0, shift))
        candidates.append(rot)
    for shift in range(1, k):
        rot = list(range(shift, k)) + list(range(0, shift))
        candidates.append(list(reversed(rot)))

    seen = set()
    distinct = []
    for c in candidates:
        key = tuple(c)
        if key not in seen:
            seen.add(key)
            distinct.append(c)

    result = []
    i = 0
    while len(result) < n:
        result.append(list(distinct[i % len(distinct)]))
        i += 1
    return result[:n]


def apply_index_permutation(items, perm):
    return [items[i] for i in perm]


# ---------------------------------------------------------------------------
# fixture materialisation
# ---------------------------------------------------------------------------

def materialise_fixture_copy(fixture_dir: str, dest_dir: str) -> None:
    """Copy the entire fixture tree into dest_dir (which must already exist
    and be empty). Uses shutil.copytree with dirs_exist_ok so empty
    directories in the fixture are preserved."""
    for name in sorted(os.listdir(fixture_dir)):
        src = os.path.join(fixture_dir, name)
        dst = os.path.join(dest_dir, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def apply_list_reorder(dest_dir: str, record_file: str, record_pointer: str, perm):
    path = os.path.join(dest_dir, record_file)
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    current = pointer_get(doc, record_pointer)
    if not isinstance(current, list):
        raise ValueError("record_pointer %r in %s is not a list" % (record_pointer, record_file))
    if len(perm) != len(current):
        raise ValueError("permutation length does not match record list length")
    reordered = apply_index_permutation(current, perm)
    if record_pointer in ("", None):
        doc = reordered
    else:
        pointer_set(doc, record_pointer, reordered)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(canonical_dumps(doc))
    return len(current)


def apply_dict_key_reorder(dest_dir: str, record_file: str, perm):
    path = os.path.join(dest_dir, record_file)
    with open(path, "r", encoding="utf-8") as fh:
        pairs = json.load(fh, object_pairs_hook=lambda p: p)
    if not isinstance(pairs, list):
        raise ValueError("%s top level is not a JSON object" % record_file)
    if len(perm) != len(pairs):
        raise ValueError("permutation length does not match key count")
    reordered_pairs = apply_index_permutation(pairs, perm)
    reordered = dict(reordered_pairs)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(reordered, ensure_ascii=True, indent=None))
        fh.write("\n")
    return len(pairs)


def _list_empty_dirs(root):
    """Relative paths (forward-slash) of every directory under root that
    contains no files anywhere in its subtree (i.e. would be silently
    dropped by any rebuild logic that only re-creates parents of files).

    A directory counts as "empty" here if it has no files in its own
    listing AND every subdirectory it contains is itself empty
    (recursively) -- i.e. the whole subtree has zero files."""
    def subtree_has_file(path):
        for entry in sorted(os.listdir(path)):
            full = os.path.join(path, entry)
            if os.path.isdir(full):
                if subtree_has_file(full):
                    return True
            else:
                return True
        return False

    empty = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        if not subtree_has_file(dirpath):
            rel = os.path.relpath(dirpath, root)
            if rel != ".":
                empty.append(rel.replace(os.sep, "/"))
    empty.sort()
    return empty


def apply_file_creation_order(dest_dir: str, record_dir: str, filenames, perm):
    """filenames is the fixed, sorted list of relative filenames living
    under record_dir in the *source* fixture (already copied by
    materialise_fixture_copy). This re-creates that directory from scratch,
    writing the same files with the same content but in the physical order
    given by perm, to probe whether output depends on directory-entry
    creation order rather than the tool's own explicit sort.

    Empty subdirectories are explicitly re-created too -- a naive rebuild
    that only recreates parents of tracked files silently drops them, which
    has previously changed report hashes for reasons unrelated to the thing
    actually being tested (see make_fixtures.py and README "Limitations")."""
    target = os.path.join(dest_dir, record_dir)
    contents = {}
    for name in filenames:
        with open(os.path.join(target, name), "rb") as fh:
            contents[name] = fh.read()
    empty_dirs = _list_empty_dirs(target)
    shutil.rmtree(target)
    os.makedirs(target, exist_ok=True)
    for reldir in empty_dirs:
        os.makedirs(os.path.join(target, reldir), exist_ok=True)
    ordered_names = apply_index_permutation(filenames, perm)
    for name in ordered_names:
        full = os.path.join(target, name)
        if os.path.dirname(name):
            os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(contents[name])
    return len(filenames)


# ---------------------------------------------------------------------------
# built-in tool adapters
# ---------------------------------------------------------------------------

BUILTIN_TOOLS = {
    "consolidate": {
        "argv_template": ["{tool_path}", ".", "-o", "{output}"],
        "permute_mode": "list-reorder",
        "record_file": "reports/tied.json",
        "record_pointer": "/issues",
        "output_file": "out.json",
        "output_list_pointer": "/ungrouped_findings",
        "tool_ok_exit_codes": (0, 1),
    },
    "schema_check": {
        "argv_template": ["{tool_path}", "schema.json", "payload.json", "-o", "{output}"],
        "permute_mode": "list-reorder",
        "record_file": "payload.json",
        "record_pointer": "",
        "output_file": "out.json",
        "output_list_pointer": "/violations",
        "tool_ok_exit_codes": (0, 1),
    },
    "ndscan": {
        "argv_template": ["{tool_path}", "--root", "src", "-o", "{output}"],
        "permute_mode": "file-creation-order",
        "record_file": "src",
        "record_pointer": None,
        "output_file": "out.json",
        "output_list_pointer": "/findings",
        "tool_ok_exit_codes": (0, 1),
    },
}


# ---------------------------------------------------------------------------
# core run
# ---------------------------------------------------------------------------

class DetectorError(Exception):
    """Raised for any condition that should make the detector exit 2."""


def _run_one_permutation(python_exe, argv_template, tool_path, work_dir, output_file,
                          output_list_pointer, tool_ok_exit_codes, timeout_s):
    output_path = os.path.join(work_dir, output_file)
    argv = [
        tool_path if a == "{tool_path}" else (output_file if a == "{output}" else a)
        for a in argv_template
    ]
    full_cmd = [python_exe] + argv
    try:
        proc = subprocess.run(
            full_cmd,
            cwd=work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        raise DetectorError("target executable not found: %s" % exc.__class__.__name__)
    except subprocess.TimeoutExpired:
        raise DetectorError("target timed out")

    if proc.returncode not in tool_ok_exit_codes:
        raise DetectorError("target exited with unexpected code %d" % proc.returncode)

    if not os.path.isfile(output_path):
        raise DetectorError("target did not produce the expected output file")

    with open(output_path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise DetectorError("target output was not valid JSON: %s" % exc.__class__.__name__)

    try:
        observed_list = pointer_get(doc, output_list_pointer)
    except (KeyError, IndexError, ValueError) as exc:
        raise DetectorError("output_list_pointer %r not found in target output" % output_list_pointer)
    if not isinstance(observed_list, list):
        raise DetectorError("output_list_pointer %r did not resolve to a list" % output_list_pointer)

    order = [canonical_text(item) for item in observed_list]
    return order, canonical_dumps(doc)


def run_detector(python_exe, tool_path, fixture_dir, adapter, permutations, timeout_s=60):
    """Run `permutations` deterministic permutations of the fixture's record
    set against `tool_path`, and determine whether output order is stable.

    Returns a fully-populated, canonical-JSON-serialisable result dict.
    Raises DetectorError for exit-2 conditions.
    """
    if permutations < 1:
        raise DetectorError("--permutations must be >= 1, got %r" % (permutations,))
    if not os.path.isdir(fixture_dir):
        raise DetectorError("fixture directory does not exist: not a directory")
    if not os.path.isfile(tool_path):
        raise DetectorError("tool_path does not exist: not a file")
    # Subprocesses run with cwd set to a temp permutation directory elsewhere
    # on disk, so any relative tool_path/fixture_dir given by the caller must
    # be resolved against *our* cwd now, before that cwd changes meaning.
    tool_path = os.path.abspath(tool_path)
    fixture_dir = os.path.abspath(fixture_dir)

    permute_mode = adapter["permute_mode"]
    record_file = adapter["record_file"]
    record_pointer = adapter.get("record_pointer")

    fixed_filenames = None
    if permute_mode == "file-creation-order":
        src_dir = os.path.join(fixture_dir, record_file)
        if not os.path.isdir(src_dir):
            raise DetectorError("record_file directory not found in fixture")
        fixed_filenames = []
        for dirpath, dirnames, filenames in os.walk(src_dir):
            dirnames.sort()
            for fname in sorted(filenames):
                rel = os.path.relpath(os.path.join(dirpath, fname), src_dir)
                fixed_filenames.append(rel.replace(os.sep, "/"))
        fixed_filenames.sort()
        k = len(fixed_filenames)
    elif permute_mode == "list-reorder":
        rf_path = os.path.join(fixture_dir, record_file)
        if not os.path.isfile(rf_path):
            raise DetectorError("record_file not found in fixture")
        with open(rf_path, "r", encoding="utf-8") as fh:
            raw = fh.read()
        try:
            doc = json.loads(raw)
        except ValueError as exc:
            raise DetectorError("record_file is not valid JSON: %s" % exc.__class__.__name__)
        try:
            base_list = pointer_get(doc, record_pointer)
        except (KeyError, IndexError, ValueError):
            raise DetectorError("record_pointer not found in record_file")
        if not isinstance(base_list, list):
            raise DetectorError("record_pointer did not resolve to a list")
        k = len(base_list)
    elif permute_mode == "dict-key-reorder":
        rf_path = os.path.join(fixture_dir, record_file)
        if not os.path.isfile(rf_path):
            raise DetectorError("record_file not found in fixture")
        with open(rf_path, "r", encoding="utf-8") as fh:
            raw = fh.read()
        try:
            pairs = json.loads(raw, object_pairs_hook=lambda p: p)
        except ValueError as exc:
            raise DetectorError("record_file is not valid JSON: %s" % exc.__class__.__name__)
        if not isinstance(pairs, list):
            raise DetectorError("record_file top level is not a JSON object")
        k = len(pairs)
    else:
        raise DetectorError("unknown permute_mode: %r" % permute_mode)

    if k < 2:
        raise DetectorError("fixture record set has fewer than 2 records; cannot test order stability")

    index_perms = generate_index_permutations(k, permutations)

    orders = []
    outputs = []
    with tempfile.TemporaryDirectory(prefix="sortdetect-") as tmp_root:
        for p_index, perm in enumerate(index_perms):
            work_dir = os.path.join(tmp_root, "perm_%d" % p_index)
            os.makedirs(work_dir)
            try:
                materialise_fixture_copy(fixture_dir, work_dir)

                if permute_mode == "list-reorder":
                    apply_list_reorder(work_dir, record_file, record_pointer, perm)
                elif permute_mode == "dict-key-reorder":
                    apply_dict_key_reorder(work_dir, record_file, perm)
                elif permute_mode == "file-creation-order":
                    apply_file_creation_order(work_dir, record_file, fixed_filenames, perm)

                order, full_output = _run_one_permutation(
                    python_exe,
                    adapter["argv_template"],
                    tool_path,
                    work_dir,
                    adapter["output_file"],
                    adapter["output_list_pointer"],
                    adapter["tool_ok_exit_codes"],
                    timeout_s,
                )
            except DetectorError:
                raise
            except Exception as exc:
                # Never let an unanticipated exception escape as a raw
                # traceback / exit 1 -- any failure to complete a permutation
                # run is an exit-2 "could not run" condition, not a finding.
                raise DetectorError("permutation %d failed: %s: %s" % (
                    p_index, exc.__class__.__name__, exc))
            orders.append(order)
            outputs.append(full_output)

    baseline = orders[0]
    diffs = []
    stable = True
    for p_index in range(1, len(orders)):
        if orders[p_index] != baseline:
            stable = False
            diffs.append(_describe_order_diff(p_index, baseline, orders[p_index]))

    result = {
        "detector_version": DETECTOR_VERSION,
        "permute_mode": permute_mode,
        "record_count": k,
        "permutations_requested": permutations,
        "permutations_run": len(index_perms),
        "permutation_index_orders": index_perms,
        "stable": stable,
        "baseline_order": baseline,
        "diffs": sorted(diffs, key=_diff_sort_key),
        "distinct_record_moves": summarise_record_moves(diffs),
    }
    return result


def summarise_record_moves(diffs):
    """Deduplicated, sorted (baseline_record, permutation_record) pairs seen
    across every diff. Multiple diffs commonly report the SAME baseline
    record paired with several different permutation_records (one baseline
    record can end up swapped with different other records depending on
    which permutation produced the divergence) -- i.e. genuine ties on the
    leading "baseline_record" field are the normal case here, not an edge
    case. The trailing canonical_text(pair) tiebreak is what makes this
    list's order a function of *content* rather than of which diff we
    happened to build the pair from first -- see
    test_distinct_record_moves_tiebreak_is_order_independent in the test
    suite, which proves this directly. We practise what we preach."""
    pairs = {}
    for d in diffs:
        for m in d["changed_positions"]:
            pair = {"baseline_record": m["baseline_record"], "permutation_record": m["permutation_record"]}
            pairs[(pair["baseline_record"], pair["permutation_record"])] = pair
    return sorted(pairs.values(), key=lambda p: (p["baseline_record"], canonical_text(p)))


def _diff_sort_key(d):
    return (d["permutation_index"], canonical_text(d))


def _describe_order_diff(p_index, baseline, other):
    """Describe concretely which records changed position, not just 'differs'."""
    baseline_pos = {}
    for i, v in enumerate(baseline):
        baseline_pos.setdefault(v, []).append(i)
    other_pos = {}
    for i, v in enumerate(other):
        other_pos.setdefault(v, []).append(i)

    moved = []
    for i, (b_val, o_val) in enumerate(zip(baseline, other)):
        if b_val != o_val:
            moved.append({
                "position": i,
                "baseline_record": b_val,
                "permutation_record": o_val,
            })
    return sorted_result_entry({
        "permutation_index": p_index,
        "baseline_order_length": len(baseline),
        "changed_positions": sorted(moved, key=lambda m: (m["position"], canonical_text(m))),
    })


def sorted_result_entry(d):
    return d


# ---------------------------------------------------------------------------
# generic --cmd mode
# ---------------------------------------------------------------------------

def build_generic_adapter(args):
    if not args.cmd:
        raise DetectorError("--cmd requires --record-file, --output-file and --output-list-pointer")
    argv_template = args.cmd.split()
    return {
        "argv_template": argv_template,
        "permute_mode": args.permute_mode or "list-reorder",
        "record_file": args.record_file,
        "record_pointer": args.record_pointer if args.record_pointer is not None else "",
        "output_file": args.output_file,
        "output_list_pointer": args.output_list_pointer,
        "tool_ok_exit_codes": tuple(int(x) for x in args.tool_ok_exit_codes.split(",")) if args.tool_ok_exit_codes else (0, 1),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="sortdetect.py",
        description=(
            "Run a target tool against deterministic permutations of the same "
            "record set and flag output-order instability (a missing total-order "
            "sort-key tiebreak)."
        ),
    )
    parser.add_argument("--tool", choices=sorted(BUILTIN_TOOLS.keys()),
                         help="built-in target tool adapter to use")
    parser.add_argument("--tool-path", required=True,
                         help="path to the target tool's .py script")
    parser.add_argument("--fixture", required=True, metavar="DIR",
                         help="fixture directory to permute and feed to the target")
    parser.add_argument("--permutations", type=int, default=DEFAULT_PERMUTATIONS,
                         help="number of deterministic permutations to run (default: %d)" % DEFAULT_PERMUTATIONS)
    parser.add_argument("--python", default=sys.executable,
                         help="python interpreter to invoke the target with (default: this interpreter)")
    parser.add_argument("-o", "--output", metavar="FILE",
                         help="write the canonical JSON proof report to FILE instead of stdout")
    parser.add_argument("--timeout", type=float, default=60.0,
                         help="per-permutation subprocess timeout in seconds (default: 60)")

    # generic --cmd mode
    parser.add_argument("--cmd", metavar="TEMPLATE",
                         help="generic command template; use {tool_path} and {output} placeholders "
                              "(space-split, no shell). Requires --record-file/--output-file/"
                              "--output-list-pointer.")
    parser.add_argument("--permute-mode", choices=["list-reorder", "dict-key-reorder", "file-creation-order"],
                         help="how to permute the fixture's record set (generic mode)")
    parser.add_argument("--record-file", help="fixture-relative file (or dir, for file-creation-order) holding the record set")
    parser.add_argument("--record-pointer", help="JSON pointer within --record-file to the record list (list-reorder mode)")
    parser.add_argument("--output-file", help="fixture-relative path the target writes its output to")
    parser.add_argument("--output-list-pointer", help="JSON pointer within the target's output to the list to compare")
    parser.add_argument("--tool-ok-exit-codes", help="comma-separated list of exit codes from the target "
                                                       "that mean 'ran to completion' (default: 0,1)")

    return parser


def resolve_adapter(args):
    if args.tool:
        adapter = dict(BUILTIN_TOOLS[args.tool])
        # allow overrides even in built-in mode
        if args.record_file:
            adapter["record_file"] = args.record_file
        if args.record_pointer is not None:
            adapter["record_pointer"] = args.record_pointer
        if args.output_file:
            adapter["output_file"] = args.output_file
        if args.output_list_pointer:
            adapter["output_list_pointer"] = args.output_list_pointer
        if args.permute_mode:
            adapter["permute_mode"] = args.permute_mode
        return adapter
    if args.cmd:
        return build_generic_adapter(args)
    raise DetectorError("either --tool or --cmd must be given")


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    error = None
    result = None
    try:
        adapter = resolve_adapter(args)
        result = run_detector(
            args.python,
            args.tool_path,
            args.fixture,
            adapter,
            args.permutations,
            timeout_s=args.timeout,
        )
    except DetectorError as exc:
        error = str(exc)
    except Exception as exc:  # pragma: no cover - safety net, see README "Bug hunt"
        error = "unexpected internal error: %s: %s" % (exc.__class__.__name__, exc)

    if error is not None:
        error_report = {
            "detector_version": DETECTOR_VERSION,
            "stable": None,
            "error": error,
        }
        text = canonical_dumps(error_report)
        if args.output:
            with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        else:
            sys.stdout.write(text)
        return EXIT_ERROR

    text = canonical_dumps(result)
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)

    return EXIT_STABLE if result["stable"] else EXIT_UNSTABLE


if __name__ == "__main__":
    sys.exit(main())
