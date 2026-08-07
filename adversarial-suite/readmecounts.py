#!/usr/bin/env python3
"""readmecounts.py -- check this README's counted claims against the tree.

WHAT IT ANSWERS

`adversarial-suite/README.md` states five distinct integers and one hash
about this directory (a seventh claim, `grep_test_count`, states no numeral
of its own -- it asserts that a grep returns the same number as one of the
others, and is checked by doing that grep):
how many tests the suite has, how many fixture files and empty directories
the generator produces, how many cases `expected_results.json` records, and
the `RESULTS_DIGEST` hash three separate runs agreed on. Every one of them
was prose. Nothing compared any of them to the tree, and the README said so
in as many words: "It is **not** hardcoded anywhere as an assertion;
nothing in this suite fails if a future edit adds or removes a test."

That sentence was right about what it was defending and wrong about the
consequence. What it was defending against is real -- this repository has a
recorded incident where a test hardcoded a repository-wide directory count
and had to be corrected. The lesson from that incident is "do not assert a
number the tree owns", not "do not check a number the README claims". Those
are different things, and this script is the second one.

THE RULE IT ENFORCES, STATED PRECISELY

  For every claim below, the number printed in README.md must equal the
  number measured from the tree right now.

Nothing here asserts how many tests the suite has. Adding a test does not
fail this check on its own -- it fails only if the README still states the
old number afterwards. The number lives in exactly one place, the README,
and this script is the thing that notices when reality moves away from it.

WHY NO EXPECTED VALUE APPEARS IN THIS FILE

A checker that hardcodes the numbers it checks is the same unchecked
hardcode moved one file to the left. So every expected value here is
PARSED OUT OF README.md at run time; this module contains locator patterns
and measurement functions, and no claim's value -- not in the code, and
not in this docstring either. `test_readmecounts.py` pins that from both
directions: one test rewrites a claim in a throwaway copy of the README
and requires the reported expectation to move with it, another appends a
real test method to a throwaway copy of the suite and requires the
measured value to move with it.

THE OTHER HALF OF THE PROBLEM: OCCURRENCES THE LOCATORS DO NOT SEE

Locators are hand-written patterns, and a hand-written pattern set is
exactly the thing that quietly stops covering its subject. A claim's
number can appear in a sentence no locator matches, go stale there, and
be reported clean. So after matching, every claim ALSO scans the whole
README for its measured value as a standalone number and subtracts the
character spans the locators already covered. Anything left over is an
`unlocated_occurrences` entry with its line number, and the claim's state
becomes `unlocated_occurrence`, which fails the run.

The sweep applies to the NUMERIC claims. `results_digest` is a 64-character
hash and is not swept: it is not going to appear by coincidence, and
sweeping it would flag its own three table rows. That is a real gap and it
is named here rather than left implied -- a stale copy of the digest in
prose would not be caught.

This is deliberately strict: a number that merely coincides with a
claim's value (a version, an unrelated count) will be reported too. The
fix in that case is a locator or a rewording, not a suppression list --
a suppression list would be the same unchecked hardcode again.

THE CLAIMS

  test_count          "Ran <N> tests in ..." and the Layout table's
                      "<N> `unittest` tests across ..."
                      measured: `def test_*` methods in test_adversarial.py,
                      counted from the syntax tree, not by grepping
  grep_test_count     the README's own claim that
                      `grep -c "def test_" test_adversarial.py` returns the
                      same number -- measured by actually doing that
  fixture_file_count  "<N> generated fixture files"
                      measured: len(make_fixtures.FIXTURES_B64)
  empty_dir_count     "+ <N> empty directories"
                      measured: len(make_fixtures.EMPTY_DIRS)
  case_count          "case count (<N>," and "`cases=<N>` in all"
                      measured: len(expected_results.json["cases"])
  own_test_count      the Layout table's "`test_readmecounts.py` | <N>
                      `unittest` tests" -- this checker's own suite, held
                      to the same rule as everything else it checks
  results_digest      the three-row relocation table's sha256
                      measured: only with --with-digest, by running
                      `test_adversarial.py --digest` for real (~10s). It
                      needs `fixtures/`, which is generator output and not
                      committed. An existing fixtures/ is moved aside and
                      moved back; either way the tree is left as it was
                      found. This claim is not swept for stale duplicates
                      the way the numeric claims are -- see below.

A claim that appears in more than one place in the README is checked in
every place it appears, and the places must agree with each other as well
as with the tree. That is deliberate: the failure this repository keeps
hitting is not a wrong number, it is two numbers for one fact.

Usage:
    python3 readmecounts.py
    python3 readmecounts.py --readme README.md --root .
    python3 readmecounts.py --with-digest
    python3 readmecounts.py -o counts_report.json

Exit codes:
    0  every claim found and matching
    1  at least one mismatch, or a claim whose text could not be found
    2  setup error (missing README, unreadable tree, bad usage)
"""
import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

PROG = "readmecounts.py"
EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2

HERE = os.path.dirname(os.path.abspath(__file__))

MATCH = "match"
MISMATCH = "mismatch"
DISAGREEMENT = "internal_disagreement"
NOT_FOUND = "claim_text_not_found"
UNLOCATED = "unlocated_occurrence"
SKIPPED = "skipped"

#: States that do not fail the run.
OK_STATES = frozenset({MATCH, SKIPPED})


class SetupError(Exception):
    pass


# --------------------------------------------------------------------------
# Locators. Each is a regex with exactly one capturing group: the claimed
# value as it is written in the README. No expected value appears here.
# --------------------------------------------------------------------------

LOCATORS = {
    "test_count": [
        re.compile(r"^Ran (\d+) tests in ", re.M),
        re.compile(r"\|\s*`test_adversarial\.py`\s*\|\s*(\d+) `unittest` tests"),
        re.compile(r"^(\d+) is the number of `def test_` methods", re.M),
        re.compile(r"`Ran (\d+) tests in [0-9.]+s`"),
        re.compile(r"smaller than the test count \((\d+)\)"),
    ],
    "grep_test_count": [
        # The README claims grep returns "the same number" rather than
        # printing a second numeral, so the claimed value for this one is
        # the test_count claim itself. Resolved in build_report.
    ],
    "fixture_file_count": [
        re.compile(r"(\d+) generated fixture files"),
        re.compile(r"VERIFY OK: (\d+) files \+ \d+\s*\n?\s*empty dirs"),
        re.compile(r"(\d+)\s*\n?\s*fixtures against three tools"),
    ],
    "empty_dir_count": [
        re.compile(r"generated fixture files \+ (\d+) empty director"),
        re.compile(r"VERIFY OK: \d+ files \+ (\d+)\s*\n?\s*empty dirs"),
    ],
    "case_count": [
        re.compile(r"case count \((\d+),"),
        re.compile(r"`cases=(\d+)` in all"),
    ],
    # Dog-fooding: this checker's own suite size is a counted claim in the
    # same README, so it is checked the same way. Only one locator, and
    # deliberately not a "Ran N tests in ..." line -- that shape belongs to
    # test_count above, and a second one in this README would read as an
    # internal disagreement about a different fact.
    "own_test_count": [
        re.compile(r"\|\s*`test_readmecounts\.py`\s*\|\s*(\d+) `unittest` tests"),
    ],
    "results_digest": [
        re.compile(r"\|\s*`([0-9a-f]{64})`\s*\|"),
        re.compile(r"same 64-character hex string"),  # presence only, no group
    ],
}

#: Claims whose measurement needs a real suite run.
EXPENSIVE = frozenset({"results_digest"})

#: How wide a window around a bare number counts as "about this claim".
CONTEXT_CHARS = 200

#: What a number has to be near to be treated as an occurrence of a claim.
#:
#: Without this, sweeping the README for a small value flags a Python
#: version, a finding id like "EM-n", and a table's row number. The
#: number alone does not identify the fact; the noun beside it does. The window is generous
#: (CONTEXT_CHARS either side of the digits, not the same line) because
#: markdown wraps, and the word that gives a number its meaning is often
#: on the line above -- which is exactly the case that made a line-scoped
#: version of this miss a real stale occurrence.
#:
#: The cost is stated rather than hidden: a genuinely stale occurrence
#: with no nearby keyword is not swept up. That is a smaller failure than
#: a wall of false positives, which would train a reader to ignore the
#: whole section.
CONTEXTS = {
    "test_count": re.compile(r"tests?\b", re.I),
    "grep_test_count": re.compile(r"tests?\b", re.I),
    "fixture_file_count": re.compile(r"fixture", re.I),
    "empty_dir_count": re.compile(r"empty\s+(?:director|dir)", re.I),
    "case_count": re.compile(r"\bcases?\b", re.I),
    "own_test_count": re.compile(r"test_readmecounts", re.I),
    "results_digest": re.compile(r"digest", re.I),
}


def claimed_values(text, patterns):
    """-> list of (pattern_index, value, span) for every occurrence found.

    `span` is the (start, end) of the CAPTURED value, not of the whole
    match, so coverage below is measured against the number itself.
    """
    out = []
    for i, pat in enumerate(patterns):
        if pat.groups == 0:
            continue
        for m in pat.finditer(text):
            out.append((i, m.group(1), m.span(1)))
    return out


def _line_of(text, offset):
    return text.count("\n", 0, offset) + 1


def _standalone_number_pattern(value):
    r"""A number on its own, including at the end of a sentence.

    The first version used `(?<![\w.])N(?![\w.])`, which refuses to match
    when the next character is a full stop -- so a value written at the end
    of an ordinary English sentence was invisible to the sweep, and to the
    control that checks this file for hardcoded values. One full stop was
    the whole bypass. A sentence ending is not a reason to stop looking; a
    decimal point followed by a digit is.

    Writing N for the value, the intended behaviour is:

      "has N tests"    match      "exactly N."     match
      "N.5"            no match   "Nth"            no match
      "v1.N"           no match

    (No literal example is spelled out, because a literal here would be a
    number in this file -- which is the thing the control forbids. That is
    not a coincidence; it is the control working, and it caught an earlier
    draft of this very docstring.)
    """
    return r"(?<![\w.])" + re.escape(value) + r"(?!\.?\d)(?!\w)"


def unlocated_occurrences(text, value, covered_spans, context):
    """Every standalone occurrence of `value` near `context` that no
    locator captured. See CONTEXTS for why the context is required."""
    if not value.isdigit():
        return []
    covered = {(s, e) for s, e in covered_spans}
    lines = text.splitlines()
    out = []
    pat = re.compile(_standalone_number_pattern(value))
    for m in pat.finditer(text):
        if m.span() in covered:
            continue
        window = text[max(0, m.start() - CONTEXT_CHARS):
                      m.end() + CONTEXT_CHARS]
        if context is not None and not context.search(window):
            continue
        line_no = _line_of(text, m.start())
        out.append({"line": line_no, "text": lines[line_no - 1].strip()[:160]})
    return out


# --------------------------------------------------------------------------
# Measurements. Each returns the value the tree actually has.
# --------------------------------------------------------------------------

def _count_test_methods(path):
    """`def test_*` methods in one file, from the syntax tree."""
    if not os.path.isfile(path):
        raise SetupError("missing %s" % path)
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                n += 1
    return n


def measure_own_test_count(root):
    """This checker's own suite size, measured the same way."""
    return _count_test_methods(os.path.join(root, "test_readmecounts.py"))


def measure_test_count(root):
    """`def test_*` methods in test_adversarial.py, from the syntax tree.

    AST rather than grep on purpose: grep also counts the string
    "def test_" inside a docstring or a fixture, and this number is meant
    to be the number of tests that actually run. The README separately
    claims grep agrees; measure_grep_test_count checks that claim on its
    own terms rather than conflating the two.
    """
    return _count_test_methods(os.path.join(root, "test_adversarial.py"))


def measure_grep_test_count(root):
    """Literally `grep -c "def test_" test_adversarial.py`, in Python.

    grep -c counts matching LINES, not matches, so this counts lines
    containing the substring -- the same thing the README's command does.
    """
    path = os.path.join(root, "test_adversarial.py")
    if not os.path.isfile(path):
        raise SetupError("missing %s" % path)
    n = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if "def test_" in line:
                n += 1
    return n


def _load_make_fixtures(root):
    """Read FIXTURES_B64 / EMPTY_DIRS without importing the module.

    Importing would execute the generator's module body, which is avoidable
    and is the kind of thing this repository's doc-validator already
    refuses to do. The two names are plain literals, so the syntax tree is
    enough.
    """
    path = os.path.join(root, "make_fixtures.py")
    if not os.path.isfile(path):
        raise SetupError("missing %s" % path)
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    found = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id not in ("FIXTURES_B64", "EMPTY_DIRS"):
                continue
            try:
                found[target.id] = ast.literal_eval(node.value)
            except ValueError:
                raise SetupError(
                    "%s is not a literal in %s" % (target.id, path))
    for name in ("FIXTURES_B64", "EMPTY_DIRS"):
        if name not in found:
            raise SetupError("could not find %s in %s" % (name, path))
    return found


def measure_fixture_file_count(root):
    return len(_load_make_fixtures(root)["FIXTURES_B64"])


def measure_empty_dir_count(root):
    return len(_load_make_fixtures(root)["EMPTY_DIRS"])


def measure_case_count(root):
    path = os.path.join(root, "expected_results.json")
    if not os.path.isfile(path):
        raise SetupError("missing %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SetupError("could not read %s: %s" % (path, exc))
    if not isinstance(data, dict) or "cases" not in data:
        raise SetupError("%s has no 'cases' object" % path)
    return len(data["cases"])


DIGEST_RE = re.compile(r"RESULTS_DIGEST sha256=([0-9a-f]{64}) cases=(\d+)")


def measure_results_digest(root, case_count=None, timeout=600):
    """Run the suite for real and read its RESULTS_DIGEST line.

    The suite has to run in `root` itself, not in a copy of it: every
    recorded case's argv is relative to the repository root
    (`../adversarial-suite/fixtures/...`, `../evidence-manifest/...`), so
    a copy of this one directory produces zero cases and a digest of the
    empty string. That was tried and is why this note exists.

    Running in place means generating `fixtures/`, which is generator
    output and is not committed. A check that damages the tree it is
    checking is its own kind of defect, and the first version of this
    function had exactly that defect: it guarded its own `rmtree` but then
    shelled out to `make_fixtures.py`, whose `generate()` begins by
    removing the destination. An existing `fixtures/` -- including
    anything a person had put in it -- was destroyed. Guarding only the
    lines you wrote is not the same as not destroying anything.

    So the directory is moved aside into a scratch parent this function
    creates, and moved back in a `finally`. If it was not there to begin
    with, the generated one is removed. Either way the tree is left as it
    was found.

    `case_count`, when given, is cross-checked against the `cases=N` the
    suite prints. Without that, a generator that fails leaves zero
    recorded cases and the run reports the digest of the empty object as
    a perfectly good hash -- a vacuous pass in the middle of a tool built
    to catch vacuous passes.
    """
    for name in ("make_fixtures.py", "test_adversarial.py"):
        if not os.path.isfile(os.path.join(root, name)):
            raise SetupError("missing %s" % os.path.join(root, name))

    fixtures = os.path.join(root, "fixtures")
    preexisting = os.path.exists(fixtures)
    stash_parent = tempfile.mkdtemp(prefix="readmecounts_stash_")
    stashed = os.path.join(stash_parent, "fixtures")
    if preexisting:
        shutil.move(fixtures, stashed)
    try:
        gen = subprocess.run([sys.executable, "make_fixtures.py"], cwd=root,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=timeout)
        if gen.returncode != 0:
            raise SetupError(
                "make_fixtures.py exited %d: %s"
                % (gen.returncode,
                   gen.stderr.decode("utf-8", "replace").strip()[-300:]))
        proc = subprocess.run(
            [sys.executable, "test_adversarial.py", "--digest"], cwd=root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        blob = (proc.stdout + proc.stderr).decode("utf-8", "replace")
    finally:
        if os.path.isdir(fixtures):
            shutil.rmtree(fixtures)       # generated above, by this call
        if preexisting:
            shutil.move(stashed, fixtures)
        shutil.rmtree(stash_parent)       # created above, by this call

    m = DIGEST_RE.search(blob)
    if not m:
        raise SetupError("no RESULTS_DIGEST line in the suite's output "
                         "(exit %s)" % proc.returncode)
    reported_cases = int(m.group(2))
    if reported_cases <= 0:
        raise SetupError("the suite recorded 0 cases, so its digest is the "
                         "hash of an empty result set")
    if case_count is not None and reported_cases != int(case_count):
        raise SetupError(
            "the suite recorded %d cases but expected_results.json has %d; "
            "the digest is not over the case set this README describes"
            % (reported_cases, int(case_count)))
    return m.group(1)


MEASURERS = {
    "test_count": measure_test_count,
    "grep_test_count": measure_grep_test_count,
    "fixture_file_count": measure_fixture_file_count,
    "empty_dir_count": measure_empty_dir_count,
    "case_count": measure_case_count,
    "own_test_count": measure_own_test_count,
    "results_digest": measure_results_digest,
}


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def _normalise(value):
    """Claims are compared as strings; numeric ones as ints as well."""
    s = str(value).strip()
    return s


def bump_claim(text, name, delta=1, occurrence=0):
    """Make one occurrence of a claim stale, without naming any number.

    Rewrites the `occurrence`-th value captured by `name`'s locators to
    value+delta and returns (new_text, old_value, new_value).

    This exists so that the tests and stale_demo.py do not have to
    hardcode either the number or the sentence it lives in. A mutation
    test has to name the mutation somehow, and naming it as "whatever
    locator N captures, plus one" survives a legitimate future edit that
    changes the number -- a literal `"Ran <the old count> tests in 8.9s"`
    does not, and would turn this suite red on exactly the day the README
    was correctly updated. LOCATORS is the single place a spelling lives.

    Raises ValueError when the claim has no numeric locator match, which
    is a real failure worth surfacing rather than skipping.
    """
    pats = LOCATORS["test_count"] if name == "grep_test_count" \
        else LOCATORS[name]
    found = claimed_values(text, pats)
    numeric = [(i, v, sp) for i, v, sp in found if v.isdigit()]
    if len(numeric) <= occurrence:
        raise ValueError("claim %r has no locator match #%d to bump"
                         % (name, occurrence))
    _i, value, (start, end) = numeric[occurrence]
    new_value = str(int(value) + delta)
    return text[:start] + new_value + text[end:], value, new_value


def build_report(readme_path, root, with_digest=False):
    try:
        with open(readme_path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise SetupError("could not read %s: %s" % (readme_path, exc))

    claims = []
    for name in sorted(LOCATORS):
        if name in EXPENSIVE and not with_digest:
            claims.append({
                "claim": name,
                "state": SKIPPED,
                "reason": "needs a real suite run; pass --with-digest",
                "claimed_occurrences": [],
                "measured": None,
            })
            continue

        if name == "grep_test_count":
            # The README does not print a second numeral for this; it says
            # grep "returns the same number". So the claimed value IS the
            # test_count claim, and this row checks the tree against it.
            found = claimed_values(text, LOCATORS["test_count"])
        else:
            found = claimed_values(text, LOCATORS[name])

        occurrences = [{"pattern_index": i, "value": _normalise(v),
                        "line": _line_of(text, sp[0])}
                       for i, v, sp in found]
        spans = [sp for _i, _v, sp in found]
        distinct = sorted({o["value"] for o in occurrences})

        if not occurrences:
            claims.append({
                "claim": name,
                "state": NOT_FOUND,
                "reason": "no locator matched README text",
                "claimed_occurrences": [],
                "measured": None,
            })
            continue

        try:
            if name == "results_digest":
                measured = _normalise(measure_results_digest(
                    root, case_count=measure_case_count(root)))
            else:
                measured = _normalise(MEASURERS[name](root))
        except SetupError:
            raise
        except (OSError, ValueError) as exc:            # pragma: no cover
            raise SetupError("measuring %s: %s" % (name, exc))

        # grep_test_count reuses test_count's locators, so reporting its
        # unlocated occurrences too would duplicate them.
        stray = ([] if name == "grep_test_count"
                 else unlocated_occurrences(text, measured, spans,
                                            CONTEXTS.get(name)))

        if len(distinct) > 1:
            state = DISAGREEMENT
        elif distinct[0] != measured:
            state = MISMATCH
        elif stray:
            state = UNLOCATED
        else:
            state = MATCH

        claims.append({
            "claim": name,
            "state": state,
            "claimed_occurrences": occurrences,
            "claimed_distinct": distinct,
            "measured": measured,
            "unlocated_occurrences": stray,
        })

    counts = {}
    for c in claims:
        counts[c["state"]] = counts.get(c["state"], 0) + 1

    return {
        "schema_version": 1,
        "tool": "readmecounts",
        "readme": os.path.basename(readme_path),
        "with_digest": bool(with_digest),
        "counts": counts,
        "failing": sorted(c["claim"] for c in claims
                          if c["state"] not in OK_STATES),
        "claims": claims,
    }


def canonical_dumps(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True) + "\n"


def human_lines(report):
    out = []
    for c in report["claims"]:
        if c["state"] == SKIPPED:
            out.append("%-19s %-22s %s" % (c["claim"], c["state"],
                                           c.get("reason", "")))
            continue
        if c["state"] == NOT_FOUND:
            out.append("%-19s %-22s %s" % (c["claim"], c["state"],
                                           c.get("reason", "")))
            continue
        line = "%-19s %-22s readme=%s tree=%s" % (
            c["claim"], c["state"],
            ",".join(c.get("claimed_distinct") or []), c["measured"])
        out.append(line)
        for u in c.get("unlocated_occurrences") or []:
            out.append("%-19s   line %d not covered by any locator: %s"
                       % ("", u["line"], u["text"][:90]))
    return out


def build_arg_parser():
    ap = argparse.ArgumentParser(prog=PROG)
    ap.add_argument("--readme", default=os.path.join(HERE, "README.md"),
                    help="README to read the claims from (default: this "
                         "directory's README.md)")
    ap.add_argument("--root", default=HERE,
                    help="directory to measure (default: this directory)")
    ap.add_argument("--with-digest", action="store_true",
                    help="also run the suite and check the RESULTS_DIGEST "
                         "claim (slow)")
    ap.add_argument("-o", "--output", help="write the JSON report here")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress the human-readable summary on stdout")
    return ap


def run(argv=None):
    args = build_arg_parser().parse_args(argv)
    if not os.path.isdir(args.root):
        sys.stderr.write("%s: --root is not a directory: %s\n"
                         % (PROG, args.root))
        return EXIT_ERROR
    try:
        report = build_report(args.readme, args.root, args.with_digest)
    except SetupError as exc:
        sys.stderr.write("%s: %s\n" % (PROG, exc))
        return EXIT_ERROR

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(canonical_dumps(report))
        except OSError as exc:
            sys.stderr.write("%s: could not write --output: %s\n"
                             % (PROG, exc))
            return EXIT_ERROR
    if not args.quiet:
        for line in human_lines(report):
            sys.stdout.write(line + "\n")
        sys.stdout.write("failing=%d\n" % len(report["failing"]))
    return EXIT_FINDINGS if report["failing"] else EXIT_OK


def main(argv=None):
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
