#!/usr/bin/env python3
"""schema_check.py -- validate a batch of evidence payloads against a declarative schema.

Standard library only. See README.md for the schema DSL specification.

Usage:
    python3 schema_check.py <schema.json> <payloads.json> [-o REPORT.json]

Exit codes:
    0  every payload conforms
    1  one or more violations were found
    2  input/schema could not be read, parsed, or is not a well-formed schema
"""

from __future__ import annotations

import argparse
import json
import re
import sys

TOOL_VERSION = "1.0.0"

# --------------------------------------------------------------------------
# canonical JSON
# --------------------------------------------------------------------------

def canonical(obj) -> str:
    """Canonical, deterministic JSON encoding used for output and for value equality."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


# --------------------------------------------------------------------------
# RFC 6901 JSON pointers
# --------------------------------------------------------------------------

def escape_token(token) -> str:
    """Escape a single reference token per RFC 6901 (~ -> ~0, / -> ~1)."""
    return str(token).replace("~", "~0").replace("/", "~1")


def child_pointer(parent: str, token) -> str:
    """Append one reference token to a pointer. Root pointer is the empty string."""
    return parent + "/" + escape_token(token)


# --------------------------------------------------------------------------
# JSON type model
# --------------------------------------------------------------------------

PRIMITIVE_TYPES = (
    "any",
    "array",
    "boolean",
    "integer",
    "null",
    "number",
    "object",
    "string",
)


def json_type(value) -> str:
    """Return the JSON type name of a decoded Python value.

    bool is checked before int because bool is a subclass of int in Python.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def type_matches(value, declared: str) -> bool:
    """True when *value* satisfies the declared type name."""
    if declared == "any":
        return True
    actual = json_type(value)
    if declared == "number":
        return actual in ("integer", "number")
    return actual == declared


def type_list(node) -> list:
    """Normalise a node's ``type`` keyword to a list of type names."""
    declared = node.get("type")
    if isinstance(declared, list):
        return list(declared)
    return [declared]


# --------------------------------------------------------------------------
# schema DSL keyword tables
# --------------------------------------------------------------------------

COMMON_KEYWORDS = {"type", "enum", "description"}

TYPE_KEYWORDS = {
    "object": {"properties", "required", "additional_properties"},
    "array": {"items", "min_items", "max_items", "unique_items"},
    "string": {"pattern", "min_length", "max_length"},
    "number": {"minimum", "maximum"},
    "integer": {"minimum", "maximum"},
    "boolean": set(),
    "null": set(),
    "any": (
        {"properties", "required", "additional_properties"}
        | {"items", "min_items", "max_items", "unique_items"}
        | {"pattern", "min_length", "max_length"}
        | {"minimum", "maximum"}
    ),
}

TOP_LEVEL_KEYWORDS = {"root", "name", "version", "description"}

NON_NEGATIVE_INT_KEYWORDS = ("min_length", "max_length", "min_items", "max_items")
NUMERIC_KEYWORDS = ("minimum", "maximum")
BOOLEAN_KEYWORDS = ("additional_properties", "unique_items")


# --------------------------------------------------------------------------
# record helpers
# --------------------------------------------------------------------------

def make_entry(code: str, pointer: str, message: str) -> dict:
    return {"code": code, "message": message, "pointer": pointer}


def sort_entries(entries: list) -> list:
    """Deterministic ordering: (pointer, code, message, canonical(entry)).

    The trailing canonical(e) is a total-order tiebreak. Two entries agreeing
    on pointer, code and message would otherwise compare equal and fall back to
    input order, which is not guaranteed stable. It cannot reorder entries that
    already differ on an earlier field.
    """
    return sorted(
        entries,
        key=lambda e: (e["pointer"], e["code"], e["message"], canonical(e)),
    )


def tally(entries: list) -> dict:
    counts: dict = {}
    for entry in entries:
        counts[entry["code"]] = counts.get(entry["code"], 0) + 1
    return counts


# --------------------------------------------------------------------------
# schema validation (the meta level)
# --------------------------------------------------------------------------

def validate_schema_document(schema) -> list:
    """Return every structural problem found in a schema document."""
    errors: list = []
    if not isinstance(schema, dict):
        errors.append(make_entry(
            "SCHEMA_NOT_OBJECT", "",
            "schema document must be a JSON object, got %s" % json_type(schema)))
        return sort_entries(errors)

    for key in sorted(schema):
        if key not in TOP_LEVEL_KEYWORDS:
            errors.append(make_entry(
                "SCHEMA_UNKNOWN_KEYWORD", child_pointer("", key),
                "unknown top-level schema keyword %r" % key))

    if "root" not in schema:
        errors.append(make_entry(
            "SCHEMA_MISSING_ROOT", "",
            "schema document must define a 'root' node"))
    else:
        validate_schema_node(schema["root"], "/root", errors)
    return sort_entries(errors)


def validate_schema_node(node, pointer: str, errors: list) -> None:
    """Recursively check one schema node, appending problems to *errors*."""
    if not isinstance(node, dict):
        errors.append(make_entry(
            "SCHEMA_NODE_NOT_OBJECT", pointer,
            "schema node must be a JSON object, got %s" % json_type(node)))
        return

    # --- type -------------------------------------------------------------
    if "type" not in node:
        errors.append(make_entry(
            "SCHEMA_MISSING_TYPE", pointer, "schema node must declare a 'type'"))
        declared = []
    else:
        raw = node["type"]
        if isinstance(raw, str):
            declared = [raw]
        elif isinstance(raw, list):
            declared = raw
            if not raw:
                errors.append(make_entry(
                    "SCHEMA_BAD_KEYWORD_TYPE", child_pointer(pointer, "type"),
                    "'type' list must not be empty"))
        else:
            errors.append(make_entry(
                "SCHEMA_BAD_KEYWORD_TYPE", child_pointer(pointer, "type"),
                "'type' must be a string or a list of strings, got %s" % json_type(raw)))
            declared = []

        for name in declared:
            if not isinstance(name, str) or name not in PRIMITIVE_TYPES:
                errors.append(make_entry(
                    "SCHEMA_UNKNOWN_TYPE", child_pointer(pointer, "type"),
                    "unknown type %s (allowed: %s)"
                    % (canonical(name), ", ".join(PRIMITIVE_TYPES))))

    valid_types = [t for t in declared if isinstance(t, str) and t in PRIMITIVE_TYPES]
    allowed = set(COMMON_KEYWORDS)
    for name in valid_types:
        allowed |= TYPE_KEYWORDS[name]

    # --- unknown / inapplicable keywords ---------------------------------
    known_anywhere = set(COMMON_KEYWORDS)
    for group in TYPE_KEYWORDS.values():
        known_anywhere |= group
    for key in sorted(node):
        if key not in known_anywhere:
            errors.append(make_entry(
                "SCHEMA_UNKNOWN_KEYWORD", child_pointer(pointer, key),
                "unknown schema keyword %r" % key))
        elif valid_types and key not in allowed:
            errors.append(make_entry(
                "SCHEMA_KEYWORD_NOT_APPLICABLE", child_pointer(pointer, key),
                "keyword %r is not applicable to type %s"
                % (key, "|".join(valid_types))))

    # --- enum -------------------------------------------------------------
    if "enum" in node:
        if not isinstance(node["enum"], list):
            errors.append(make_entry(
                "SCHEMA_BAD_KEYWORD_TYPE", child_pointer(pointer, "enum"),
                "'enum' must be a list, got %s" % json_type(node["enum"])))
        elif not node["enum"]:
            errors.append(make_entry(
                "SCHEMA_EMPTY_ENUM", child_pointer(pointer, "enum"),
                "'enum' must contain at least one value"))

    if "description" in node and not isinstance(node["description"], str):
        errors.append(make_entry(
            "SCHEMA_BAD_KEYWORD_TYPE", child_pointer(pointer, "description"),
            "'description' must be a string, got %s" % json_type(node["description"])))

    # --- scalar keyword types --------------------------------------------
    for key in NON_NEGATIVE_INT_KEYWORDS:
        if key in node:
            value = node[key]
            if json_type(value) != "integer" or value < 0:
                errors.append(make_entry(
                    "SCHEMA_BAD_KEYWORD_TYPE", child_pointer(pointer, key),
                    "%r must be a non-negative integer, got %s" % (key, canonical(value))))

    for key in NUMERIC_KEYWORDS:
        if key in node and json_type(node[key]) not in ("integer", "number"):
            errors.append(make_entry(
                "SCHEMA_BAD_KEYWORD_TYPE", child_pointer(pointer, key),
                "%r must be a number, got %s" % (key, json_type(node[key]))))

    for key in BOOLEAN_KEYWORDS:
        if key in node and json_type(node[key]) != "boolean":
            errors.append(make_entry(
                "SCHEMA_BAD_KEYWORD_TYPE", child_pointer(pointer, key),
                "%r must be a boolean, got %s" % (key, json_type(node[key]))))

    # --- bound sanity -----------------------------------------------------
    for low, high in (("min_length", "max_length"), ("min_items", "max_items"),
                      ("minimum", "maximum")):
        if low in node and high in node:
            lo, hi = node[low], node[high]
            if json_type(lo) in ("integer", "number") and json_type(hi) in ("integer", "number"):
                if lo > hi:
                    errors.append(make_entry(
                        "SCHEMA_BAD_BOUNDS", child_pointer(pointer, high),
                        "%r (%s) must not be less than %r (%s)"
                        % (high, canonical(hi), low, canonical(lo))))

    # --- pattern ----------------------------------------------------------
    if "pattern" in node:
        if not isinstance(node["pattern"], str):
            errors.append(make_entry(
                "SCHEMA_BAD_KEYWORD_TYPE", child_pointer(pointer, "pattern"),
                "'pattern' must be a string, got %s" % json_type(node["pattern"])))
        else:
            try:
                re.compile(node["pattern"])
            except re.error as exc:
                errors.append(make_entry(
                    "SCHEMA_BAD_REGEX", child_pointer(pointer, "pattern"),
                    "'pattern' is not a valid regular expression: %s" % exc))

    # --- required ---------------------------------------------------------
    if "required" in node:
        req = node["required"]
        if not isinstance(req, list):
            errors.append(make_entry(
                "SCHEMA_BAD_KEYWORD_TYPE", child_pointer(pointer, "required"),
                "'required' must be a list of strings, got %s" % json_type(req)))
        else:
            for index, name in enumerate(req):
                if not isinstance(name, str):
                    errors.append(make_entry(
                        "SCHEMA_BAD_KEYWORD_TYPE",
                        child_pointer(child_pointer(pointer, "required"), index),
                        "'required' entries must be strings, got %s" % json_type(name)))

    # --- properties -------------------------------------------------------
    if "properties" in node:
        props = node["properties"]
        if not isinstance(props, dict):
            errors.append(make_entry(
                "SCHEMA_BAD_KEYWORD_TYPE", child_pointer(pointer, "properties"),
                "'properties' must be a JSON object, got %s" % json_type(props)))
        else:
            base = child_pointer(pointer, "properties")
            for name in sorted(props):
                validate_schema_node(props[name], child_pointer(base, name), errors)

    # --- items ------------------------------------------------------------
    if "items" in node:
        validate_schema_node(node["items"], child_pointer(pointer, "items"), errors)


# --------------------------------------------------------------------------
# payload validation
# --------------------------------------------------------------------------

def validate_value(value, node: dict, pointer: str, out: list) -> None:
    """Validate *value* against schema *node*, appending every violation to *out*."""
    declared = type_list(node)
    if not any(type_matches(value, name) for name in declared):
        out.append(make_entry(
            "TYPE_MISMATCH", pointer,
            "expected type %s, got %s" % ("|".join(str(d) for d in declared), json_type(value))))
        # Deeper constraints are meaningless once the type is wrong.
        return

    if "enum" in node:
        allowed = [canonical(item) for item in node["enum"]]
        if canonical(value) not in allowed:
            out.append(make_entry(
                "ENUM_MISMATCH", pointer,
                "value %s is not one of [%s]" % (canonical(value), ", ".join(allowed))))

    actual = json_type(value)

    if actual == "string":
        _check_string(value, node, pointer, out)
    elif actual in ("integer", "number"):
        _check_number(value, node, pointer, out)
    elif actual == "array":
        _check_array(value, node, pointer, out)
    elif actual == "object":
        _check_object(value, node, pointer, out)


def _check_string(value: str, node: dict, pointer: str, out: list) -> None:
    if "min_length" in node and len(value) < node["min_length"]:
        out.append(make_entry(
            "MIN_LENGTH", pointer,
            "string length %d is below minimum %d" % (len(value), node["min_length"])))
    if "max_length" in node and len(value) > node["max_length"]:
        out.append(make_entry(
            "MAX_LENGTH", pointer,
            "string length %d exceeds maximum %d" % (len(value), node["max_length"])))
    if "pattern" in node and re.search(node["pattern"], value) is None:
        out.append(make_entry(
            "PATTERN_MISMATCH", pointer,
            "value %s does not match pattern %s"
            % (canonical(value), canonical(node["pattern"]))))


def _check_number(value, node: dict, pointer: str, out: list) -> None:
    if "minimum" in node and value < node["minimum"]:
        out.append(make_entry(
            "MINIMUM", pointer,
            "value %s is below minimum %s" % (canonical(value), canonical(node["minimum"]))))
    if "maximum" in node and value > node["maximum"]:
        out.append(make_entry(
            "MAXIMUM", pointer,
            "value %s exceeds maximum %s" % (canonical(value), canonical(node["maximum"]))))


def _check_array(value: list, node: dict, pointer: str, out: list) -> None:
    if "min_items" in node and len(value) < node["min_items"]:
        out.append(make_entry(
            "MIN_ITEMS", pointer,
            "array has %d items, minimum is %d" % (len(value), node["min_items"])))
    if "max_items" in node and len(value) > node["max_items"]:
        out.append(make_entry(
            "MAX_ITEMS", pointer,
            "array has %d items, maximum is %d" % (len(value), node["max_items"])))
    if node.get("unique_items") is True:
        seen: dict = {}
        for index, item in enumerate(value):
            key = canonical(item)
            if key in seen:
                out.append(make_entry(
                    "DUPLICATE_ITEMS", child_pointer(pointer, index),
                    "duplicate of item at index %d (value %s)" % (seen[key], key)))
            else:
                seen[key] = index
    if "items" in node:
        for index, item in enumerate(value):
            validate_value(item, node["items"], child_pointer(pointer, index), out)


def _check_object(value: dict, node: dict, pointer: str, out: list) -> None:
    props = node.get("properties", {})
    for name in node.get("required", []):
        if name not in value:
            out.append(make_entry(
                "MISSING_REQUIRED", child_pointer(pointer, name),
                "required key %r is missing" % name))
    if node.get("additional_properties") is False:
        for name in sorted(value):
            if name not in props:
                out.append(make_entry(
                    "UNEXPECTED_KEY", child_pointer(pointer, name),
                    "key %r is not declared and additional_properties is false" % name))
    for name in sorted(props):
        if name in value:
            validate_value(value[name], props[name], child_pointer(pointer, name), out)


def validate_payload(payload, schema: dict) -> list:
    """Validate a decoded payload document against a decoded, already-checked schema."""
    out: list = []
    validate_value(payload, schema["root"], "", out)
    return sort_entries(out)


# --------------------------------------------------------------------------
# I/O + report assembly
# --------------------------------------------------------------------------

def load_json_file(path: str, kind: str):
    """Return (value, error_entry). Exactly one of the two is None."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except OSError as exc:
        return None, make_entry(
            "IO_ERROR", "",
            "cannot read %s file %s: %s" % (kind, canonical(path), exc.strerror or str(exc)))
    except UnicodeDecodeError as exc:
        return None, make_entry(
            "ENCODING_ERROR", "",
            "%s file %s is not valid UTF-8: %s" % (kind, canonical(path), exc.reason))
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        return None, make_entry(
            "JSON_PARSE_ERROR", "",
            "%s file %s is not valid JSON: %s (line %d column %d)"
            % (kind, canonical(path), exc.msg, exc.lineno, exc.colno))


def empty_report(schema_path: str, payload_path: str) -> dict:
    return {
        "exit_code": 0,
        "io_errors": [],
        "ok": True,
        "payload_source": payload_path,
        "schema_errors": [],
        "schema_source": schema_path,
        "status": "conform",
        "summary": {},
        "tool_version": TOOL_VERSION,
        "violation_count": 0,
        "violations": [],
    }


def build_report(schema_path: str, payload_path: str) -> dict:
    report = empty_report(schema_path, payload_path)

    schema, schema_err = load_json_file(schema_path, "schema")
    payload, payload_err = load_json_file(payload_path, "payload")

    io_errors = [entry for entry in (schema_err, payload_err) if entry is not None]
    if io_errors:
        report["io_errors"] = sort_entries(io_errors)
        report["status"] = "error"
        report["ok"] = False
        report["exit_code"] = 2
        report["summary"] = tally(report["io_errors"])
        return report

    schema_errors = validate_schema_document(schema)
    if schema_errors:
        report["schema_errors"] = schema_errors
        report["status"] = "error"
        report["ok"] = False
        report["exit_code"] = 2
        report["summary"] = tally(schema_errors)
        return report

    violations = validate_payload(payload, schema)
    report["violations"] = violations
    report["violation_count"] = len(violations)
    report["summary"] = tally(violations)
    if violations:
        report["status"] = "violations"
        report["ok"] = False
        report["exit_code"] = 1
    return report


def render(report: dict) -> str:
    """Canonical JSON text plus a trailing newline."""
    return canonical(report) + "\n"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="schema_check.py",
        description="Validate a batch of evidence payloads against a declarative schema file.")
    parser.add_argument("schema", help="path to the declarative schema JSON file")
    parser.add_argument("payloads", help="path to the payload batch JSON file")
    parser.add_argument("-o", "--out", default=None,
                        help="write the canonical JSON report to this file instead of stdout")
    parser.add_argument("--version", action="version",
                        version="schema_check.py %s" % TOOL_VERSION)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report(args.schema, args.payloads)
    text = render(report)

    if args.out:
        try:
            with open(args.out, "w", encoding="ascii", newline="\n") as handle:
                handle.write(text)
        except OSError as exc:
            sys.stderr.write("schema_check.py: cannot write report to %s: %s\n"
                             % (args.out, exc.strerror or str(exc)))
            return 2
        sys.stderr.write("schema_check.py: status=%s violations=%d report=%s\n"
                         % (report["status"], report["violation_count"], args.out))
    else:
        sys.stdout.write(text)

    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
