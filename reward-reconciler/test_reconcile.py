#!/usr/bin/env python3
"""Tests for the Deterministic Reward Reconciliation CLI."""
import json, os, shutil, subprocess, sys, tempfile, unittest
from decimal import Decimal
import reconcile

HERE = os.path.dirname(os.path.abspath(__file__))
W1 = "rJ8St5nxoH4DvX"
W2 = "rPJ3VzY3L41jE2"
T1 = "task_b3a3e54adcac730636afd7e9ca80b798"
T2 = "task_2712d3e81d714fc6ab77d237491a58b4"
T3 = "task_a2df370465b16b8923304d8605b7c448"


def r(task_id=T1, wallet=W1, amount="3.5"):
    return {"task_id": task_id, "wallet": wallet, "amount": amount}


def mk(recs):
    """Turn plain dicts into parsed internal records via a temp file."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(recs, fh)
        path = fh.name
    try:
        return reconcile._load_records(path, reconcile.EXPECTED_FIELDS, "expected")
    finally:
        os.unlink(path)


class TestMismatchTypes(unittest.TestCase):
    def test_balanced(self):
        rep = reconcile.reconcile(mk([r()]), mk([r()]))
        self.assertEqual(rep["status"], "balanced")
        self.assertEqual(rep["findings"], [])

    def test_missing_payout(self):
        rep = reconcile.reconcile(mk([r()]), mk([]))
        self.assertEqual(rep["findings"][0]["issue"], reconcile.MISSING_PAYOUT)

    def test_duplicate_payout(self):
        rep = reconcile.reconcile(mk([r()]), mk([r(), r()]))
        f = rep["findings"][0]
        self.assertEqual(f["issue"], reconcile.DUPLICATE_PAYOUT)
        self.assertEqual(f["payout_count"], 2)
        self.assertEqual(f["payout_amount"], "7.000000")

    def test_unexpected_payout(self):
        rep = reconcile.reconcile(mk([]), mk([r()]))
        self.assertEqual(rep["findings"][0]["issue"], reconcile.UNEXPECTED_PAYOUT)

    def test_amount_mismatch_with_delta(self):
        rep = reconcile.reconcile(mk([r(amount="3.5")]), mk([r(amount="3.25")]))
        f = rep["findings"][0]
        self.assertEqual(f["issue"], reconcile.AMOUNT_MISMATCH)
        self.assertEqual(f["delta"], "-0.250000")

    def test_wallet_mismatch(self):
        rep = reconcile.reconcile(mk([r(wallet=W1)]), mk([r(wallet=W2)]))
        codes = [f["issue"] for f in rep["findings"]]
        self.assertIn(reconcile.WALLET_MISMATCH, codes)

    def test_wallet_and_amount_mismatch_both_reported(self):
        rep = reconcile.reconcile(mk([r(wallet=W1, amount="3.5")]), mk([r(wallet=W2, amount="1.0")]))
        codes = sorted(f["issue"] for f in rep["findings"])
        self.assertEqual(codes, [reconcile.AMOUNT_MISMATCH, reconcile.WALLET_MISMATCH])


class TestDecimalPrecision(unittest.TestCase):
    def test_no_float_drift(self):
        exp = mk([r(task_id=T1, amount="0.1"), r(task_id=T2, amount="0.2")])
        pay = mk([r(task_id=T1, amount="0.1"), r(task_id=T2, amount="0.2")])
        rep = reconcile.reconcile(exp, pay)
        self.assertEqual(rep["totals"]["expected_total"], "0.300000")
        self.assertEqual(rep["status"], "balanced")

    def test_sub_micro_difference_detected(self):
        rep = reconcile.reconcile(mk([r(amount="1.000000")]), mk([r(amount="1.000001")]))
        self.assertEqual(rep["findings"][0]["issue"], reconcile.AMOUNT_MISMATCH)

    def test_integer_amount_accepted(self):
        rep = reconcile.reconcile(mk([r(amount=3)]), mk([r(amount="3")]))
        self.assertEqual(rep["status"], "balanced")

    def test_float_amount_rejected(self):
        with self.assertRaises(reconcile.InputError):
            mk([r(amount=3.5)])


class TestOrderingAndCanonical(unittest.TestCase):
    def test_findings_sorted_regardless_of_input_order(self):
        a = reconcile.reconcile(mk([r(T1), r(T2), r(T3)]), mk([]))
        b = reconcile.reconcile(mk([r(T3), r(T1), r(T2)]), mk([]))
        self.assertEqual([f["task_id"] for f in a["findings"]],
                         [f["task_id"] for f in b["findings"]])
        self.assertEqual(reconcile.canonical_json(a), reconcile.canonical_json(b))

    def test_canonical_json_is_byte_identical_across_runs(self):
        rep = reconcile.reconcile(mk([r()]), mk([r(amount="1")]))
        self.assertEqual(reconcile.canonical_json(rep), reconcile.canonical_json(rep))

    def test_canonical_json_sorted_keys_and_newline(self):
        text = reconcile.canonical_json(reconcile.reconcile(mk([]), mk([])))
        self.assertTrue(text.endswith("\n"))
        self.assertLess(text.index('"issue_counts"'), text.index('"totals"'))


class TestMalformedInput(unittest.TestCase):
    def test_missing_field(self):
        with self.assertRaises(reconcile.InputError):
            mk([{"task_id": T1, "wallet": W1}])

    def test_empty_wallet(self):
        with self.assertRaises(reconcile.InputError):
            mk([r(wallet="  ")])

    def test_bad_amount_string(self):
        with self.assertRaises(reconcile.InputError):
            mk([r(amount="abc")])

    def test_duplicate_expected_task_rejected(self):
        with self.assertRaises(reconcile.InputError):
            reconcile.reconcile(mk([r(), r()]), mk([]))


class TestCliExitCodes(unittest.TestCase):
    def _run(self, exp_text, pay_text):
        paths = []
        for text in (exp_text, pay_text):
            fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
            fh.write(text); fh.close(); paths.append(fh.name)
        try:
            return subprocess.run(
                [sys.executable, os.path.join(HERE, "reconcile.py")] + paths,
                capture_output=True, text=True)
        finally:
            for p in paths:
                os.unlink(p)

    def test_exit_zero_balanced(self):
        p = self._run(json.dumps([r()]), json.dumps([r()]))
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["status"], "balanced")

    def test_exit_one_mismatched(self):
        p = self._run(json.dumps([r()]), json.dumps([]))
        self.assertEqual(p.returncode, 1)

    def test_exit_two_invalid_json(self):
        p = self._run("{not json", json.dumps([]))
        self.assertEqual(p.returncode, 2)
        self.assertIn("INVALID_INPUT", p.stderr)

    def test_exit_two_not_array(self):
        p = self._run(json.dumps({"a": 1}), json.dumps([]))
        self.assertEqual(p.returncode, 2)

    def test_cli_output_byte_identical_twice(self):
        e, y = json.dumps([r(T1), r(T2, amount="1.5")]), json.dumps([r(T1, amount="2"), r(T3)])
        a = self._run(e, y).stdout
        b = self._run(e, y).stdout
        self.assertEqual(a, b)


# ---------------------------------------------------------------------------
# The documented exit codes, as a table
#
# README.md's "## Exit codes" section is the contract. Every row of it, and
# every trigger its exit-2 row names, gets a case here, and each case runs the
# real CLI as a subprocess so what is asserted is the process's exit status
# and not a return value that main() might not be the one producing.
#
# Four of these used to fail. An unreadable file, an amount outside the
# representable range, an amount of "Infinity" and a file whose bytes are not
# UTF-8 all escaped as uncaught exceptions, so the process exited 1 -- and 1
# is this tool's code for "mismatched, one or more settlement issues". A
# caller reading exit codes recorded malformed input as a settlement result.
# An unwritable --out did the same thing to a run that was BALANCED.
# ---------------------------------------------------------------------------

SAMPLES = os.path.join(HERE, "samples_invalid")

# PYTHONUNBUFFERED makes stdout writes unbuffered, which hides a whole class
# of failure: a buffered write that fails leaves the buffer full, CPython
# retries the flush during shutdown, and Py_FinalizeEx overrides the exit
# status with 120. Every subprocess below runs with the variable removed, so
# the stdout tests exercise the buffered path this repository's own sandbox
# happens to disable.
CHILD_ENV = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
CHILD_ENV["PYTHONDONTWRITEBYTECODE"] = "1"


def sample(name):
    return os.path.join(SAMPLES, name)


# (id, expected exit code, argv, the README phrase this case is the test for)
EXIT_CODE_CASES = [
    ("balanced", 0,
     [sample("valid_pair_expected.json"), sample("valid_pair_payouts.json")],
     "balanced, no findings"),
    ("mismatched", 1,
     [os.path.join(HERE, "expected_rewards.json"),
      os.path.join(HERE, "recorded_payouts.json")],
     "mismatched, one or more settlement issues"),
    ("bad_json", 2,
     [sample("not_json.json"), sample("valid_pair_payouts.json")],
     "bad JSON"),
    ("wrong_shape", 2,
     [sample("not_an_array.json"), sample("valid_pair_payouts.json")],
     "wrong shape"),
    ("float_amount", 2,
     [sample("float_amount.json"), sample("valid_pair_payouts.json")],
     "float amount"),
    ("missing_field", 2,
     [sample("missing_field.json"), sample("valid_pair_payouts.json")],
     "missing field"),
    ("duplicate_task_id", 2,
     [sample("duplicate_task_id.json"), sample("valid_pair_payouts.json")],
     "duplicate `task_id` in the expected set"),
    ("unreadable_missing", 2,
     [sample("no_such_file_here.json"), sample("valid_pair_payouts.json")],
     "unreadable file"),
    ("unreadable_is_a_directory", 2,
     [SAMPLES, sample("valid_pair_payouts.json")],
     "a directory"),
    ("unreadable_not_utf8", 2,
     [sample("not_utf8.json"), sample("valid_pair_payouts.json")],
     "non-UTF-8 bytes"),
    ("amount_out_of_range", 2,
     [sample("amount_out_of_range.json"), sample("valid_pair_payouts.json")],
     "an amount outside the range `Decimal` can quantize to 6 dp"),
    ("amount_infinity", 2,
     [sample("amount_infinity.json"), sample("valid_pair_payouts.json")],
     "an amount outside the range `Decimal` can quantize to 6 dp"),
    ("argparse_usage_error", 2,
     [sample("valid_pair_expected.json")],
     "a CLI usage error"),
    ("unwritable_out", 2,
     [sample("valid_pair_expected.json"), sample("valid_pair_payouts.json"),
      "-o", os.path.join(os.devnull, "x.json")],
     "an unwritable `--out` (which includes stdout: a closed pipe or a full disk)"),
    ("signalling_nan", 2,
     [sample("amount_signalling_nan.json"), sample("valid_pair_payouts.json")],
     "a signalling NaN amount"),
]

# The exit-2 triggers this README names, as the test table reads them. Kept
# beside EXIT_CODE_CASES rather than inside a test so the two lists are
# visibly one thing; test_every_exit_two_trigger_named_in_the_readme_has_a_case
# asserts in BOTH directions -- every trigger here appears in the README's
# exit-2 row AND is the phrase of a case, and every case phrase for a code-2
# row appears here. A one-directional check would catch a trigger being
# deleted from the README and miss one being added.
EXIT_TWO_TRIGGERS = (
    "bad JSON",
    "wrong shape",
    "float amount",
    "missing field",
    "duplicate `task_id` in the expected set",
    "unreadable file",
    "a directory",
    "non-UTF-8 bytes",
    "an amount outside the range `Decimal` can quantize to 6 dp",
    "a signalling NaN amount",
    "a CLI usage error",
    "an unwritable `--out` (which includes stdout: a closed pipe or a full disk)",
)

# The exit-2 cell, reconstructed from the tuple above. Asserting equality with
# the README's actual cell is what makes the drift check bidirectional: a
# trigger deleted from the row fails it, and so does one ADDED to the row
# without being added here. Checking only `trigger in meaning` catches the
# first and misses the second, which is what an earlier draft did.
EXIT_TWO_ROW = "invalid input / processing error: %s, or %s" % (
    ", ".join(EXIT_TWO_TRIGGERS[:-1]), EXIT_TWO_TRIGGERS[-1])


def readme_exit_table():
    """The exit-code table out of README.md, as {code: meaning}."""
    import re
    with open(os.path.join(HERE, "README.md"), encoding="utf-8") as fh:
        text = fh.read()
    section = re.search(r"\n## Exit codes\n(.*?)(?=\n## |\Z)", text, re.S)
    if section is None:
        raise AssertionError("README.md has no '## Exit codes' section")
    rows = re.findall(r"^\| *`?(\d+)`? *\| *(.+?) *\|$", section.group(1), re.M)
    return {int(code): meaning for code, meaning in rows}


def _close_stdout():
    os.close(1)


def _close_stderr():
    os.close(2)


class TestDocumentedExitCodes(unittest.TestCase):
    def run_cli(self, argv, out=None):
        cmd = [sys.executable, os.path.join(HERE, "reconcile.py")] + list(argv)
        if out is not None:
            cmd += ["-o", out]
        return subprocess.run(cmd, capture_output=True, text=True, cwd=HERE,
                              env=CHILD_ENV)

    def test_every_documented_case_returns_its_code(self):
        for case_id, code, argv, _ in EXIT_CODE_CASES:
            with self.subTest(case=case_id):
                proc = self.run_cli(argv)
                self.assertEqual(
                    proc.returncode, code,
                    "%s: expected exit %d, got %d\nstderr: %s"
                    % (case_id, code, proc.returncode, proc.stderr[:400]))

    def test_no_case_leaves_a_python_traceback(self):
        # The point of the repair. Exit 2 reached by crashing is not exit 2.
        for case_id, code, argv, _ in EXIT_CODE_CASES:
            with self.subTest(case=case_id):
                proc = self.run_cli(argv)
                self.assertNotIn("Traceback", proc.stderr, case_id)

    def test_every_rejection_names_its_reason_as_json(self):
        for case_id, code, argv, _ in EXIT_CODE_CASES:
            if code != 2 or case_id == "argparse_usage_error":
                continue
            with self.subTest(case=case_id):
                proc = self.run_cli(argv)
                doc = json.loads(proc.stderr)
                self.assertIn(doc["error"], ("INVALID_INPUT", "OUTPUT_ERROR"))
                self.assertTrue(doc["detail"].strip(), case_id)

    def test_table_covers_every_code_the_readme_documents(self):
        # Without this the table could quietly stop covering a documented row.
        documented = set(readme_exit_table())
        self.assertEqual(documented, {0, 1, 2})
        self.assertEqual(documented, {code for _, code, _, _ in EXIT_CODE_CASES})

    def test_every_case_quotes_a_phrase_the_readme_actually_contains(self):
        # Ties each case to the sentence it is the test for, so rewording the
        # README without revisiting the table fails here instead of silently
        # leaving the table testing something no longer documented.
        with open(os.path.join(HERE, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        for case_id, _, _, phrase in EXIT_CODE_CASES:
            with self.subTest(case=case_id):
                self.assertIn(phrase, readme)

    def test_every_exit_two_trigger_named_in_the_readme_has_a_case(self):
        # The exit-2 row lists its triggers in prose. Every one of them must
        # be in EXIT_TWO_TRIGGERS, and every one of those must be the phrase
        # of a case.
        meaning = readme_exit_table()[2]
        covered = {phrase for _, code, _, phrase in EXIT_CODE_CASES if code == 2}
        for trigger in EXIT_TWO_TRIGGERS:
            with self.subTest(trigger=trigger):
                self.assertIn(trigger, meaning)
                self.assertIn(trigger, covered)

    def test_the_readme_row_is_exactly_the_trigger_list(self):
        self.assertEqual(readme_exit_table()[2], EXIT_TWO_ROW)

    def test_no_case_is_tied_to_a_phrase_outside_the_trigger_list(self):
        # The other direction. Without this the trigger list could quietly
        # stop covering a case, or a case could be tied to a phrase that is
        # not a trigger at all.
        covered = {phrase for _, code, _, phrase in EXIT_CODE_CASES if code == 2}
        self.assertEqual(covered, set(EXIT_TWO_TRIGGERS) & covered)
        self.assertEqual(set(EXIT_TWO_TRIGGERS) - covered, set())


class TestRepairedExitCodes(unittest.TestCase):
    """The four conditions that used to exit 1 with a traceback.

    Named individually so a regression says which one came back rather than
    only that some row of the table moved.
    """

    def run_cli(self, argv, out=None):
        cmd = [sys.executable, os.path.join(HERE, "reconcile.py")] + list(argv)
        if out is not None:
            cmd += ["-o", out]
        return subprocess.run(cmd, capture_output=True, text=True, cwd=HERE,
                              env=CHILD_ENV)

    def assertRejected(self, proc, needle):
        self.assertEqual(proc.returncode, 2, proc.stderr[:400])
        self.assertNotIn("Traceback", proc.stderr)
        doc = json.loads(proc.stderr)
        self.assertIn(needle, doc["detail"])

    def test_a_missing_file_keeps_its_own_message(self):
        # FileNotFoundError is an OSError, so the broad handler added for
        # "unreadable file" would swallow it and still exit 2 -- the table
        # alone cannot tell the two apart. This is what pins the ordering.
        self.assertRejected(
            self.run_cli([sample("no_such_file_here.json"),
                          sample("valid_pair_payouts.json")]),
            "file not found")

    def test_a_symlink_loop_names_its_errno(self):
        work = tempfile.mkdtemp(prefix="reconcile_loop_")
        try:
            a, b = os.path.join(work, "a"), os.path.join(work, "b")
            os.symlink(b, a)
            os.symlink(a, b)
            proc = self.run_cli([a, sample("valid_pair_payouts.json")])
        finally:
            # Only the directory this test created. Never its parent.
            shutil.rmtree(work, ignore_errors=True)
        self.assertEqual(proc.returncode, 2, proc.stderr[:400])
        self.assertNotIn("Traceback", proc.stderr)
        # The bare class name here is "OSError", which says nothing. The
        # errno symbol is what makes the message worth printing.
        self.assertIn("ELOOP", json.loads(proc.stderr)["detail"])

    def test_directory_instead_of_a_file_is_exit_2(self):
        self.assertRejected(
            self.run_cli([SAMPLES, sample("valid_pair_payouts.json")]),
            "IsADirectoryError")

    def test_non_utf8_bytes_are_exit_2(self):
        self.assertRejected(
            self.run_cli([sample("not_utf8.json"), sample("valid_pair_payouts.json")]),
            "not valid UTF-8")

    def test_amount_outside_the_representable_range_is_exit_2(self):
        # README limitation 2 documented this as exiting 1 with a traceback.
        self.assertRejected(
            self.run_cli([sample("amount_out_of_range.json"),
                          sample("valid_pair_payouts.json")]),
            "not representable")

    def test_a_signalling_nan_amount_is_exit_2(self):
        # Comparing a signalling NaN raises InvalidOperation -- that is what
        # signalling NaNs are for -- so `value != value` crashed here and
        # exited 1. The guard uses is_nan(), which does not signal.
        self.assertRejected(
            self.run_cli([sample("amount_signalling_nan.json"),
                          sample("valid_pair_payouts.json")]),
            "amount is NaN")

    def test_deeply_nested_json_is_exit_2(self):
        # json's scanner recurses per nesting level, so this raises
        # RecursionError rather than JSONDecodeError.
        work = tempfile.mkdtemp(prefix="reconcile_deep_")
        try:
            deep = os.path.join(work, "deep.json")
            with open(deep, "w", encoding="utf-8") as fh:
                fh.write("[" * 20000 + "]" * 20000)
            proc = self.run_cli([deep, sample("valid_pair_payouts.json")])
        finally:
            # Only the directory this test created. Never its parent.
            shutil.rmtree(work, ignore_errors=True)
        self.assertEqual(proc.returncode, 2, proc.stderr[:400])
        self.assertNotIn("Traceback", proc.stderr)
        self.assertIn("nesting too deep", json.loads(proc.stderr)["detail"])

    def test_amount_infinity_is_exit_2(self):
        self.assertRejected(
            self.run_cli([sample("amount_infinity.json"),
                          sample("valid_pair_payouts.json")]),
            "not representable")

    def test_unwritable_out_on_a_balanced_run_is_exit_2_not_1(self):
        # The worst of the four: exit 1 means "mismatched, one or more
        # settlement issues", and this run is balanced. An unrelated I/O
        # failure was being reported as a settlement verdict.
        proc = self.run_cli(
            [sample("valid_pair_expected.json"), sample("valid_pair_payouts.json")],
            out=os.path.join(os.devnull, "x.json"))
        self.assertEqual(proc.returncode, 2, proc.stderr[:400])
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(json.loads(proc.stderr)["error"], "OUTPUT_ERROR")

    def test_unwritable_out_on_a_mismatched_run_is_exit_2_not_1(self):
        proc = self.run_cli(
            [os.path.join(HERE, "expected_rewards.json"),
             os.path.join(HERE, "recorded_payouts.json")],
            out=os.path.join(os.devnull, "x.json"))
        self.assertEqual(proc.returncode, 2, proc.stderr[:400])
        self.assertEqual(json.loads(proc.stderr)["error"], "OUTPUT_ERROR")

    def test_a_failing_stdout_is_exit_2_not_1(self):
        # /dev/full accepts the open and fails the write with ENOSPC. Every
        # documented invocation without -o takes this branch, and the run
        # below is BALANCED -- exiting 1 here would report a clean
        # settlement as "mismatched, one or more settlement issues".
        if not os.path.exists("/dev/full"):
            self.skipTest("no /dev/full on this platform")
        with open("/dev/full", "w") as sink:
            proc = subprocess.run(
                [sys.executable, os.path.join(HERE, "reconcile.py"),
                 sample("valid_pair_expected.json"),
                 sample("valid_pair_payouts.json")],
                stdout=sink, stderr=subprocess.PIPE, text=True, cwd=HERE)
        self.assertEqual(proc.returncode, 2, proc.stderr[:400])
        self.assertNotIn("Traceback", proc.stderr)
        doc = json.loads(proc.stderr)
        self.assertEqual(doc["error"], "OUTPUT_ERROR")
        self.assertIn("stdout", doc["detail"])

    def test_a_closed_stdout_pipe_is_exit_2_not_1(self):
        # The everyday version of the same thing: `reconcile.py ... | head -1`.
        # Driven with a real pipe rather than a shell, so nothing depends on
        # /bin/sh being bash (it is dash here) or on PIPESTATUS.
        read_fd, write_fd = os.pipe()
        os.close(read_fd)
        try:
            proc = subprocess.run(
                [sys.executable, os.path.join(HERE, "reconcile.py"),
                 sample("valid_pair_expected.json"),
                 sample("valid_pair_payouts.json")],
                stdout=write_fd, stderr=subprocess.PIPE, text=True,
                cwd=HERE, env=CHILD_ENV)
        finally:
            os.close(write_fd)
        self.assertEqual(proc.returncode, 2, proc.stderr[:400])
        self.assertNotIn("Exception ignored", proc.stderr)
        self.assertEqual(json.loads(proc.stderr)["error"], "OUTPUT_ERROR")

    def test_a_failing_stdout_leaves_no_ignored_exception_at_shutdown(self):
        # What pins _discard_stdout(). Without it the shutdown flush retries
        # the still-buffered report, prints "Exception ignored in:
        # <_io.TextIOWrapper ...>" and forces the status to 120 -- so the
        # assertion that matters is the exit code, and the stderr assertion
        # says why it moved.
        if not os.path.exists("/dev/full"):
            self.skipTest("no /dev/full on this platform")
        with open("/dev/full", "w") as sink:
            proc = subprocess.run(
                [sys.executable, os.path.join(HERE, "reconcile.py"),
                 sample("valid_pair_expected.json"),
                 sample("valid_pair_payouts.json")],
                stdout=sink, stderr=subprocess.PIPE, text=True, cwd=HERE,
                env=CHILD_ENV)
        self.assertNotEqual(proc.returncode, 120, "shutdown flush overrode the code")
        self.assertEqual(proc.returncode, 2, proc.stderr[:400])
        self.assertNotIn("Exception ignored", proc.stderr)

    def test_a_closed_stdout_descriptor_is_exit_2_not_1(self):
        # `reconcile.py ... >&-`. Python sets sys.stdout to None, so the
        # write raises AttributeError, not OSError.
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "reconcile.py"),
             sample("valid_pair_expected.json"),
             sample("valid_pair_payouts.json")],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            cwd=HERE, env=CHILD_ENV, pass_fds=(),
            preexec_fn=_close_stdout)
        self.assertEqual(proc.returncode, 2, proc.stderr[:400])
        self.assertEqual(json.loads(proc.stderr)["error"], "OUTPUT_ERROR")

    def test_a_closed_stderr_descriptor_does_not_change_the_code(self):
        # `reconcile.py bad.json x.json 2>&-`. The message is a courtesy;
        # the exit code is the contract.
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "reconcile.py"),
             sample("not_json.json"), sample("valid_pair_payouts.json")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=HERE, env=CHILD_ENV, preexec_fn=_close_stderr)
        self.assertEqual(proc.returncode, 2)

    def test_a_write_time_out_failure_is_exit_2(self):
        # The --out cases above fail at open(). This one opens fine and
        # fails during the write, which is a different branch.
        if not os.path.exists("/dev/full"):
            self.skipTest("no /dev/full on this platform")
        proc = self.run_cli(
            [sample("valid_pair_expected.json"), sample("valid_pair_payouts.json")],
            out="/dev/full")
        self.assertEqual(proc.returncode, 2, proc.stderr[:400])
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(json.loads(proc.stderr)["error"], "OUTPUT_ERROR")

    def test_a_writable_out_still_writes_the_report(self):
        # The repair must not turn a working --out into a refusal.
        work = tempfile.mkdtemp(prefix="reconcile_out_")
        try:
            target = os.path.join(work, "report.json")
            proc = self.run_cli(
                [sample("valid_pair_expected.json"),
                 sample("valid_pair_payouts.json")], out=target)
            self.assertEqual(proc.returncode, 0, proc.stderr[:400])
            with open(target, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["status"], "balanced")
        finally:
            # Only the directory this test created. Never its parent -- that
            # is the system temp directory itself.
            shutil.rmtree(work, ignore_errors=True)


class TestValidInputStillWorks(unittest.TestCase):
    """Guards against fixing the error paths by breaking the happy path."""

    def test_committed_fixtures_still_reconcile_to_the_committed_report(self):
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "reconcile.py"),
             os.path.join(HERE, "expected_rewards.json"),
             os.path.join(HERE, "recorded_payouts.json")],
            capture_output=True, text=True, cwd=HERE)
        self.assertEqual(proc.returncode, 1)
        with open(os.path.join(HERE, "expected_report.json"), encoding="utf-8") as fh:
            self.assertEqual(proc.stdout, fh.read())

    def test_integer_and_string_amounts_are_still_accepted(self):
        self.assertEqual(str(reconcile._quantize("1", "x")), "1.000000")
        self.assertEqual(str(reconcile._quantize(2, "x")), "2.000000")
        self.assertEqual(str(reconcile._quantize("1E+2", "x")), "100.000000")


if __name__ == "__main__":
    unittest.main(verbosity=2)
