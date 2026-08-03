#!/usr/bin/env python3
"""tamperrun.py -- prove a verifier catches damaged evidence, not just that
it accepts valid evidence.

tamper-runner is a META-tool. It takes a VALID evidence fixture directory
and a verifier command, applies a fixed set of deterministic tamper cases
to isolated copies of that fixture, runs the verifier command against each
tampered copy, and reports whether the verifier CAUGHT each alteration.

A verifier that accepts everything passes a "does it accept valid
evidence" test perfectly. Only tampering distinguishes a real verifier
from a rubber stamp -- so the headline result of this tool is the list of
ESCAPED tampers (alterations the verifier did NOT catch), never just a
count of successes.

See README.md for full usage, the tamper case catalog, the outcome
vocabulary, exit codes, and documented limitations.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable

# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class ToolError(Exception):
    """Raised for conditions that mean the TOOL could not run at all
    (exit code 2): bad fixture path, unparsable/empty verifier command,
    malformed arguments, unwritable output path."""


class TamperCaseError(Exception):
    """Raised by an individual tamper case's apply function when it
    cannot find a suitable target in the fixture (e.g. no JSON file for
    ALTER_JSON_FIELD). Caught per-case and turned into a CASE_ERROR
    outcome -- never propagates out of run()."""


class PathEscapeError(Exception):
    """Raised when a path would resolve outside the directory it is
    supposed to be confined to. Caught per-case and turned into a
    CASE_ERROR outcome -- never propagates out of run()."""


# --------------------------------------------------------------------------
# Canonical JSON
# --------------------------------------------------------------------------


def canonical_dumps(obj: Any) -> str:
    """The one canonical JSON serialization used everywhere in this tool:
    keys sorted, compact separators, ASCII-only escaping. No trailing
    newline -- callers add exactly one "\\n" when writing to a stream or
    file, per the project convention."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def write_canonical_json(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(canonical_dumps(obj))
        fh.write("\n")


def sorted_with_tiebreak(items, primary_key: Callable[[Any], Any]):
    """Sort items by primary_key(item), then by the canonical JSON dump of
    the item itself as an explicit, documented final tiebreaker. This is
    applied to every list this tool emits, so that output order never
    depends on tamper-case execution order, dict/set iteration order, or
    any other incidental factor -- only on the content of the items."""
    return sorted(items, key=lambda item: (primary_key(item), canonical_dumps(item)))


# --------------------------------------------------------------------------
# Safe filesystem helpers
# --------------------------------------------------------------------------


def safe_join(base: str, relpath: str) -> str:
    """Join relpath onto base, refusing anything that would escape base.

    os.path.join silently discards `base` if `relpath` is absolute
    (os.path.join("/a", "/b") == "/b"), and ".." components can walk back
    out of `base` even when relpath is relative. Both are refused here.

    base is resolved with realpath once; the leaf component of relpath is
    NOT further resolved, so callers can still operate on a symlink itself
    (e.g. os.remove) rather than being silently redirected to whatever it
    points at. Symlink *targets* are still checked for containment: if
    relpath resolves (following any symlinks in its own path) outside of
    base, this raises -- callers get a CASE_ERROR instead of touching
    something outside the isolated copy.
    """
    if os.path.isabs(relpath):
        raise PathEscapeError(f"refusing absolute path component: {relpath!r}")
    parts = relpath.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise PathEscapeError(f"refusing path with '.'/'..'/empty component: {relpath!r}")

    base_real = os.path.realpath(base)
    candidate = os.path.join(base_real, *parts)
    candidate_real = os.path.realpath(candidate)
    if candidate_real != base_real and not candidate_real.startswith(base_real + os.sep):
        raise PathEscapeError(f"path escapes base directory: {relpath!r}")
    return candidate


def list_regular_files(root: str) -> list[str]:
    """Sorted, forward-slash relative paths of every file (including
    broken/dangling symlinks, which count as "files" for our purposes --
    they can still be deleted) under root. Directories are not followed
    through symlinks (followlinks=False) so a symlink loop cannot hang
    the walk."""
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            abs_path = os.path.join(dirpath, name)
            rel = os.path.relpath(abs_path, root).replace(os.sep, "/")
            out.append(rel)
    return sorted(out)


def list_json_files(root: str) -> list[str]:
    return [f for f in list_regular_files(root) if f.endswith(".json")]


def _file_size_or_none(root: str, relpath: str) -> int | None:
    """Size of relpath under root, or None if it cannot be sized -- e.g. a
    dangling symlink (OSError) or a symlink whose target resolves outside
    root (PathEscapeError). Callers use this to filter candidate files, so
    one unusable/unsafe entry must be skipped, not allowed to abort the
    whole scan -- an unrelated broken symlink elsewhere in the fixture
    must never cost a tamper case that has a perfectly good target."""
    try:
        return os.path.getsize(safe_join(root, relpath))
    except (OSError, PathEscapeError):
        return None


# --------------------------------------------------------------------------
# JSON tree helpers (used by ALTER_JSON_FIELD / STALE_HASH)
# --------------------------------------------------------------------------

_HASH_LENGTHS = {32, 40, 56, 64, 96, 128}
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


def find_first_scalar(data: Any, path: list | None = None):
    """Depth-first, sorted-key search for the first scalar (non-list,
    non-dict) value in a decoded JSON document. Returns (path, value)
    where path is a list of dict-keys/list-indices from the root, or None
    if the document contains no scalar at all. Deterministic: dict keys
    are always visited in sorted order."""
    path = path or []
    if isinstance(data, dict):
        for key in sorted(data.keys()):
            value = data[key]
            sub = path + [key]
            if isinstance(value, (dict, list)):
                found = find_first_scalar(value, sub)
                if found is not None:
                    return found
            else:
                return (sub, value)
        return None
    if isinstance(data, list):
        for index, value in enumerate(data):
            sub = path + [index]
            if isinstance(value, (dict, list)):
                found = find_first_scalar(value, sub)
                if found is not None:
                    return found
            else:
                return (sub, value)
        return None
    return (path, data)


def _looks_like_hash(value: str) -> bool:
    return len(value) in _HASH_LENGTHS and bool(_HEX_RE.match(value))


def find_first_hash_like(data: Any, path: list | None = None):
    """Depth-first, sorted-key search for the first string value that
    "looks like a recorded digest": all-hex characters, length equal to
    one of the common digest lengths (32/40/56/64/96/128, i.e. md5 through
    sha512/sha3-512). Unlike find_first_scalar this does NOT stop at the
    first scalar -- it keeps searching past non-hash-like scalars."""
    path = path or []
    if isinstance(data, dict):
        for key in sorted(data.keys()):
            value = data[key]
            sub = path + [key]
            if isinstance(value, (dict, list)):
                found = find_first_hash_like(value, sub)
                if found is not None:
                    return found
            elif isinstance(value, str) and _looks_like_hash(value):
                return (sub, value)
        return None
    if isinstance(data, list):
        for index, value in enumerate(data):
            sub = path + [index]
            if isinstance(value, (dict, list)):
                found = find_first_hash_like(value, sub)
                if found is not None:
                    return found
            elif isinstance(value, str) and _looks_like_hash(value):
                return (sub, value)
        return None
    return None


def _get_at_path(data: Any, path: list) -> Any:
    node = data
    for key in path:
        node = node[key]
    return node


def _set_at_path(data: Any, path: list, value: Any) -> None:
    node = data
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


def _pointer_text(path: list) -> str:
    return "$" + "".join(f"[{key!r}]" for key in path)


def _mutate_scalar(value: Any) -> Any:
    """Deterministically change a JSON scalar to a different value of a
    sensible type-preserving shape."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value + 1.0
    if isinstance(value, str):
        marker = "TAMPERED"
        if value.endswith(marker):
            return value + "_2"
        return f"{value}_{marker}"
    if value is None:
        return "TAMPERED_NULL"
    raise TamperCaseError(f"cannot mutate JSON scalar of type {type(value).__name__}")


def _mutate_hex(value: str) -> str:
    """Deterministically produce a different same-length all-hex string,
    derived from a sha256 of the original value (so the run is
    reproducible without any randomness)."""
    length = len(value)
    digest = __import__("hashlib").sha256(value.encode("utf-8")).hexdigest()
    while len(digest) < length:
        digest += __import__("hashlib").sha256(digest.encode("utf-8")).hexdigest()
    mutated = digest[:length]
    if mutated.lower() == value.lower():
        # Astronomically unlikely, but stay correct rather than merely lucky.
        last = mutated[-1]
        mutated = mutated[:-1] + ("0" if last != "0" else "1")
    return mutated


# --------------------------------------------------------------------------
# Tamper cases
# --------------------------------------------------------------------------
# Each apply function receives the absolute path to an isolated, writable
# copy of the fixture and either:
#   * mutates it and returns (target_relpath, description_text), or
#   * raises TamperCaseError if no suitable target exists in this fixture.
# `target_relpath` and `description_text` must never contain absolute
# paths -- only paths relative to the fixture root -- so the report stays
# byte-identical regardless of where the fixture lives on disk.

CONTROL_CASE_ID = "NO_OP"

DEFAULT_CASE_ORDER = [
    "DELETE_FILE",
    "MUTATE_BYTE",
    "TRUNCATE_FILE",
    "ALTER_JSON_FIELD",
    "STALE_HASH",
    "ADD_UNLISTED_FILE",
]


def apply_no_op(root: str):
    return None, "no changes made (control case, proves the fixture is not already flagged)"


def apply_delete_file(root: str):
    candidates = [f for f in list_regular_files(root) if f != "manifest.json"]
    if not candidates:
        raise TamperCaseError("no non-manifest file available to delete")
    target = "data.txt" if "data.txt" in candidates else candidates[0]
    os.remove(safe_join(root, target))
    return target, f"deleted file '{target}'"


def apply_truncate_file(root: str):
    candidates = [
        f
        for f in list_regular_files(root)
        if f != "manifest.json" and (_file_size_or_none(root, f) or 0) > 1
    ]
    if not candidates:
        raise TamperCaseError("no file with length > 1 byte available to truncate")
    target = "notes.txt" if "notes.txt" in candidates else candidates[0]
    abs_path = safe_join(root, target)
    original_size = os.path.getsize(abs_path)
    new_size = original_size // 2
    with open(abs_path, "r+b") as fh:
        fh.truncate(new_size)
    return target, f"truncated '{target}' from {original_size} bytes to {new_size} bytes"


def apply_mutate_byte(root: str):
    candidates = [
        f
        for f in list_regular_files(root)
        if f != "manifest.json" and (_file_size_or_none(root, f) or 0) >= 2
    ]
    if not candidates:
        raise TamperCaseError("no file with length >= 2 bytes available to mutate")
    target = None
    for preferred in ("binary.dat", "unicode.txt", "data.txt"):
        if preferred in candidates:
            target = preferred
            break
    if target is None:
        target = candidates[0]
    abs_path = safe_join(root, target)
    size = os.path.getsize(abs_path)
    offset = size // 2
    with open(abs_path, "r+b") as fh:
        fh.seek(offset)
        original_byte = fh.read(1)
        fh.seek(offset)
        fh.write(bytes([original_byte[0] ^ 0xFF]))
    return (
        target,
        f"flipped one byte at offset {offset} of '{target}' (size unchanged at {size} bytes)",
    )


def apply_alter_json_field(root: str):
    for relpath in list_json_files(root):
        try:
            abs_path = safe_join(root, relpath)
            with open(abs_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, PathEscapeError):
            # This particular JSON file is unusable (unreadable, invalid,
            # or an escaping symlink) -- skip it, do not abort the search
            # for a usable candidate elsewhere in the fixture.
            continue
        found = find_first_scalar(data)
        if found is None:
            continue
        path, old_value = found
        new_value = _mutate_scalar(old_value)
        _set_at_path(data, path, new_value)
        with open(abs_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            fh.write("\n")
        pointer = _pointer_text(path)
        return (
            relpath,
            f"changed field {pointer} in '{relpath}' from {old_value!r} to {new_value!r}",
        )
    raise TamperCaseError("no JSON file with a scalar field found to alter")


def apply_stale_hash(root: str):
    for relpath in list_json_files(root):
        try:
            abs_path = safe_join(root, relpath)
            with open(abs_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, PathEscapeError):
            continue
        found = find_first_hash_like(data)
        if found is None:
            continue
        path, old_value = found
        new_value = _mutate_hex(old_value)
        _set_at_path(data, path, new_value)
        with open(abs_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            fh.write("\n")
        pointer = _pointer_text(path)
        return (
            relpath,
            f"changed recorded digest at {pointer} in '{relpath}' so it no longer "
            "matches the content it claims to describe",
        )
    raise TamperCaseError("no JSON file with a hash-like field found to make stale")


_UNLISTED_FILE_NAME = "zz_tamper_injected_file.bin"
_UNLISTED_FILE_CONTENT = b"UNLISTED_TAMPER_MARKER: this file is not in any manifest\n"


def apply_add_unlisted_file(root: str):
    target = _UNLISTED_FILE_NAME
    abs_path = safe_join(root, target)
    suffix = 2
    while os.path.exists(abs_path):
        target = f"zz_tamper_injected_file_{suffix}.bin"
        abs_path = safe_join(root, target)
        suffix += 1
    with open(abs_path, "wb") as fh:
        fh.write(_UNLISTED_FILE_CONTENT)
    return target, f"added file '{target}' that is not referenced by any manifest"


APPLY_FUNCS: dict[str, Callable[[str], tuple[str | None, str]]] = {
    CONTROL_CASE_ID: apply_no_op,
    "DELETE_FILE": apply_delete_file,
    "MUTATE_BYTE": apply_mutate_byte,
    "TRUNCATE_FILE": apply_truncate_file,
    "ALTER_JSON_FIELD": apply_alter_json_field,
    "STALE_HASH": apply_stale_hash,
    "ADD_UNLISTED_FILE": apply_add_unlisted_file,
}


# --------------------------------------------------------------------------
# Verifier invocation
# --------------------------------------------------------------------------


def build_argv(template_tokens: list[str], bundle_dir: str) -> list[str]:
    """Substitute {bundle} in the verifier command template with the
    isolated copy's path, or append the path as the final argument if the
    template does not mention {bundle} at all."""
    if any("{bundle}" in tok for tok in template_tokens):
        return [tok.replace("{bundle}", bundle_dir) for tok in template_tokens]
    return list(template_tokens) + [bundle_dir]


def _classify(case_id: str, exit_code: int) -> str:
    """Interpret a verifier's exit code using the documented convention:
    0 = accepted the bundle as valid, 1 = flagged a problem, anything
    else (including negative codes for signal-killed processes) = the
    verifier could not meaningfully run, so we cannot tell whether it
    would have caught the tamper -- CASE_ERROR, not a false CAUGHT."""
    if exit_code == 0:
        return "CONTROL_OK" if case_id == CONTROL_CASE_ID else "ESCAPED"
    if exit_code == 1:
        return "CONTROL_FAILED" if case_id == CONTROL_CASE_ID else "CAUGHT"
    return "CASE_ERROR"


def _make_result(
    case_id: str,
    target: str | None = None,
    description: str = "",
    verifier_exit_code: int | None = None,
    outcome: str = "CASE_ERROR",
    error: str | None = None,
) -> dict:
    return {
        "case_id": case_id,
        "target": target,
        "description": description,
        "verifier_exit_code": verifier_exit_code,
        "outcome": outcome,
        "error": error,
    }


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------


def run(
    fixture_dir: str,
    verifier_template: str,
    timeout: float = 30.0,
    case_order: list[str] | None = None,
) -> tuple[dict, int]:
    """Run every tamper case (plus the NO_OP control) against isolated
    copies of fixture_dir, invoking verifier_template against each copy.
    Returns (report_dict, tool_exit_code). Raises ToolError for anything
    that means the tool itself could not run (exit code 2 territory) --
    callers should catch that and exit 2 without emitting a report.

    case_order lets callers (tests) execute the tamper cases in a
    different order to prove the emitted report is identical regardless
    -- every list in the report is explicitly sorted, so run order must
    not leak into output order.
    """
    fixture_real = os.path.realpath(fixture_dir)
    if not os.path.isdir(fixture_real):
        raise ToolError(f"--fixture is not a directory: {fixture_dir}")

    try:
        template_tokens = shlex.split(verifier_template)
    except ValueError as exc:
        raise ToolError(f"could not parse --verifier command: {exc}") from exc
    if not template_tokens:
        raise ToolError("--verifier command is empty")

    order = list(case_order) if case_order is not None else list(DEFAULT_CASE_ORDER)
    if sorted(order) != sorted(DEFAULT_CASE_ORDER) or len(order) != len(DEFAULT_CASE_ORDER):
        raise ToolError("case_order must be exactly a permutation of the built-in tamper cases")

    results: dict[str, dict] = {}

    with tempfile.TemporaryDirectory(prefix="tamperrun_") as workspace:
        for case_id in [CONTROL_CASE_ID] + order:
            case_dir = os.path.join(workspace, f"case_{case_id.lower()}")

            try:
                shutil.copytree(fixture_real, case_dir, symlinks=True)
            except OSError as exc:
                results[case_id] = _make_result(
                    case_id, error=f"failed to prepare isolated copy: {exc}"
                )
                continue

            try:
                target, description = APPLY_FUNCS[case_id](case_dir)
            except (TamperCaseError, PathEscapeError) as exc:
                results[case_id] = _make_result(case_id, error=str(exc))
                continue

            argv = build_argv(template_tokens, case_dir)
            try:
                proc = subprocess.run(
                    argv,
                    capture_output=True,
                    timeout=timeout,
                    shell=False,
                )
            except FileNotFoundError as exc:
                results[case_id] = _make_result(
                    case_id,
                    target=target,
                    description=description,
                    error=f"verifier command not found or not executable: {exc}",
                )
                continue
            except PermissionError as exc:
                results[case_id] = _make_result(
                    case_id,
                    target=target,
                    description=description,
                    error=f"verifier command could not be executed: {exc}",
                )
                continue
            except subprocess.TimeoutExpired:
                results[case_id] = _make_result(
                    case_id,
                    target=target,
                    description=description,
                    error="verifier timed out (exceeded the configured timeout)",
                )
                continue
            except OSError as exc:
                results[case_id] = _make_result(
                    case_id,
                    target=target,
                    description=description,
                    error=f"verifier invocation failed: {exc}",
                )
                continue

            outcome = _classify(case_id, proc.returncode)
            error = None
            if outcome == "CASE_ERROR":
                error = (
                    f"verifier exited with code {proc.returncode}, which is neither "
                    "0 (accepted) nor 1 (flagged); treated as could-not-run"
                )
            results[case_id] = _make_result(
                case_id,
                target=target,
                description=description,
                verifier_exit_code=proc.returncode,
                outcome=outcome,
                error=error,
            )

    control_result = results[CONTROL_CASE_ID]
    tamper_results = [results[cid] for cid in DEFAULT_CASE_ORDER]

    cases_sorted = sorted_with_tiebreak(tamper_results, primary_key=lambda item: item["case_id"])
    escaped_ids = [item["case_id"] for item in tamper_results if item["outcome"] == "ESCAPED"]
    escaped_sorted = sorted_with_tiebreak(escaped_ids, primary_key=lambda case_id: case_id)

    summary = {
        "caught": sum(1 for item in tamper_results if item["outcome"] == "CAUGHT"),
        "case_errors": sum(1 for item in tamper_results if item["outcome"] == "CASE_ERROR"),
        "control_ok": control_result["outcome"] == "CONTROL_OK",
        "escaped": sum(1 for item in tamper_results if item["outcome"] == "ESCAPED"),
        "total_tamper_cases": len(tamper_results),
    }

    report = {
        "cases": cases_sorted,
        "control": control_result,
        "escaped_cases": escaped_sorted,
        "schema_version": 1,
        "summary": summary,
        "verifier_command_template": verifier_template,
    }

    tool_exit_code = 0
    if control_result["outcome"] != "CONTROL_OK":
        tool_exit_code = 1
    if any(item["outcome"] in ("ESCAPED", "CASE_ERROR") for item in tamper_results):
        tool_exit_code = 1

    return report, tool_exit_code


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tamperrun.py",
        description=(
            "Apply deterministic tamper cases to isolated copies of a valid "
            "evidence fixture and report whether a verifier command catches "
            "each alteration. Escaped tampers -- alterations the verifier did "
            "NOT catch -- are the headline result."
        ),
        epilog=(
            "Example, pointed at the sibling bundle-verifier tool:\n"
            "  python3 tamperrun.py --fixture fixtures/valid_bundle "
            '--verifier "python3 bundleverify.py --bundle {bundle}"\n'
            "If the verifier command does not contain the literal text "
            "{bundle}, the tampered copy's path is appended as the final "
            "argument automatically."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--fixture",
        required=True,
        help="path to a directory containing a VALID evidence fixture (never modified)",
    )
    parser.add_argument(
        "--verifier",
        required=True,
        help=(
            "verifier command to run against each tampered copy; use {bundle} "
            "as a placeholder for the tampered copy's path, or omit it to have "
            "the path appended as the final argument"
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="write the canonical JSON report here instead of stdout",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="seconds to allow each verifier invocation before treating it as CASE_ERROR",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)  # argparse itself exits 2 on malformed args

    try:
        report, exit_code = run(args.fixture, args.verifier, timeout=args.timeout)
    except ToolError as exc:
        print(f"tamperrun: error: {exc}", file=sys.stderr)
        return 2

    report_text = canonical_dumps(report) + "\n"

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(report_text)
        except OSError as exc:
            print(f"tamperrun: error: could not write --output: {exc}", file=sys.stderr)
            return 2
    else:
        sys.stdout.write(report_text)

    summary = report["summary"]
    print(
        "tamperrun: control={control_outcome} caught={caught} escaped={escaped} "
        "case_errors={case_errors} (of {total} tamper cases)".format(
            control_outcome=report["control"]["outcome"],
            caught=summary["caught"],
            escaped=summary["escaped"],
            case_errors=summary["case_errors"],
            total=summary["total_tamper_cases"],
        ),
        file=sys.stderr,
    )
    if report["escaped_cases"]:
        print(
            "tamperrun: ESCAPED (verifier did NOT catch these): "
            + ", ".join(report["escaped_cases"]),
            file=sys.stderr,
        )
    if not summary["control_ok"]:
        print(
            "tamperrun: CONTROL NOT OK -- the verifier did not cleanly accept the "
            "UNMODIFIED fixture, so every other result above is meaningless until "
            "that is fixed",
            file=sys.stderr,
        )

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
