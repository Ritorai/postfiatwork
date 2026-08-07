"""Tests for leakscan.py."""

import json
import os
import string
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import leakscan as L  # noqa: E402

LEAKSCAN_PY = os.path.join(HERE, "leakscan.py")


def rules(line):
    return {f["rule"] for f in L.scan_line("f.md", 1, line)}


def cats(line):
    return {f["category"] for f in L.scan_line("f.md", 1, line)}


class TreeMixin:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def tmp(self):
        return self._tmp.name

    def tree(self, files):
        root = tempfile.mkdtemp(dir=self.tmp())
        for rel, text in files.items():
            path = os.path.join(root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        return root

    def run_cli(self, args):
        proc = subprocess.run([sys.executable, LEAKSCAN_PY] + args,
                              capture_output=True, text=True)
        return proc.returncode, proc.stdout


# --------------------------------------------------------------------------
# Positive cases: one per category, at least two per rule family
# --------------------------------------------------------------------------

class TestAbsolutePath(unittest.TestCase):
    def test_posix_absolute_path(self):
        self.assertIn("EL-ABS-POSIX", rules("output went to /opt/build/out.json"))

    def test_posix_path_at_line_start(self):
        self.assertIn("EL-ABS-POSIX", rules("/opt/build/out.json exists"))

    def test_windows_drive_path(self):
        self.assertIn("EL-ABS-WIN", rules("wrote D:\\builds\\out.json"))

    def test_unc_path(self):
        self.assertIn("EL-ABS-UNC", rules("copied from \\\\fileserv\\share\\x"))

    def test_category_is_absolute_path(self):
        self.assertEqual(cats("see /opt/build/out.json"), {L.C_ABS})


class TestHomeDirectory(unittest.TestCase):
    def test_tilde_slash(self):
        self.assertIn("EL-HOME-TILDE", rules("run ~/bin/tool.py"))

    def test_home_env_var(self):
        self.assertIn("EL-HOME-ENV", rules("cd $HOME/work"))

    def test_braced_home_env_var(self):
        self.assertIn("EL-HOME-ENV", rules("cd ${HOME}/work"))

    def test_userprofile(self):
        self.assertIn("EL-HOME-ENV", rules("set X=%USERPROFILE%\\work"))

    def test_posix_home_path(self):
        self.assertIn("EL-HOME-ABS", rules("saved to /home/rito/out.json"))

    def test_macos_users_path(self):
        self.assertIn("EL-HOME-ABS", rules("saved to /Users/rito/out.json"))

    def test_windows_users_path(self):
        self.assertIn("EL-HOME-WIN", rules("saved to C:\\Users\\Rito\\out.json"))

    def test_home_wins_over_generic_absolute(self):
        # /home/rito/x must not ALSO be reported as a bare absolute_path.
        self.assertNotIn("EL-ABS-POSIX", rules("saved to /home/rito/out.json"))


class TestTempDirectory(unittest.TestCase):
    def test_tmp(self):
        self.assertIn("EL-TEMP-POSIX", rules("wrote /tmp/a.json"))

    def test_var_folders(self):
        self.assertIn("EL-TEMP-POSIX", rules("wrote /var/folders/xy/z/T/out"))

    def test_tmpdir_env(self):
        self.assertIn("EL-TEMP-ENV", rules("cd $TMPDIR"))

    def test_windows_temp_env(self):
        self.assertIn("EL-TEMP-ENV", rules("set OUT=%TEMP%\\x"))

    def test_appdata_temp(self):
        self.assertIn("EL-TEMP-ENV", rules("C:\\Users\\x\\AppData\\Local\\Temp\\y"))

    def test_temp_wins_over_generic_absolute(self):
        self.assertNotIn("EL-ABS-POSIX", rules("wrote /tmp/a.json"))


class TestHostname(unittest.TestCase):
    def test_session_root(self):
        self.assertIn("EL-HOST-SESSION",
                      rules("cd /sessions/sharp-stoic-knuth/mnt/outputs"))

    def test_localhost(self):
        self.assertIn("EL-HOST-LOCAL", rules("curl http://localhost:8080/x"))

    def test_loopback_ip(self):
        self.assertIn("EL-HOST-LOCAL", rules("bound to 127.0.0.1:5000"))

    def test_mdns_local(self):
        self.assertIn("EL-HOST-LOCAL", rules("ssh buildbox.local"))

    def test_ec2_private_name(self):
        self.assertIn("EL-HOST-CLOUD", rules("on ip-10-0-3-17 the run failed"))

    def test_compute_internal(self):
        self.assertIn("EL-HOST-CLOUD", rules("host node-7.compute.internal"))


class TestUsername(unittest.TestCase):
    def test_username_from_posix_home(self):
        found = [f for f in L.scan_line("f.md", 1, "in /home/rito/x")
                 if f["rule"] == "EL-USER-PATH"]
        self.assertEqual([f["matched"] for f in found], ["rito"])

    def test_username_from_users_path(self):
        found = [f for f in L.scan_line("f.md", 1, "in /Users/rito/x")
                 if f["rule"] == "EL-USER-PATH"]
        self.assertEqual([f["matched"] for f in found], ["rito"])

    def test_username_from_windows_path(self):
        found = [f for f in L.scan_line("f.md", 1, "in C:\\Users\\Rito\\x")
                 if f["rule"] == "EL-USER-WINPATH"]
        self.assertEqual([f["matched"] for f in found], ["Rito"])

    def test_username_from_shell_prompt(self):
        found = [f for f in L.scan_line("f.md", 1, "rito@buildbox:~$ ls")
                 if f["rule"] == "EL-USER-PROMPT"]
        self.assertEqual([f["matched"] for f in found], ["rito"])


# --------------------------------------------------------------------------
# Negative cases: things that must NOT be reported
# --------------------------------------------------------------------------

class TestNegatives(unittest.TestCase):
    def test_https_url_is_not_an_absolute_path(self):
        self.assertEqual(rules("see https://github.com/Ritorai/postfiatwork"), set())

    def test_http_url_is_not_an_absolute_path(self):
        self.assertEqual(rules("see http://example.com/a/b/c"), set())

    def test_relative_repo_path_is_not_absolute(self):
        self.assertEqual(rules("edit transcript-drift/driftcheck.py now"), set())

    def test_nested_relative_path_is_not_absolute(self):
        self.assertEqual(rules("see a/b/c/d.py"), set())

    def test_markdown_table_row_is_clean(self):
        self.assertEqual(rules("| `0` | clean |"), set())

    def test_plain_prose_is_clean(self):
        self.assertEqual(rules("The tool exits 1 when drift is found."), set())

    def test_division_in_prose_is_clean(self):
        self.assertEqual(rules("a 3/4 majority of reviewers"), set())

    def test_command_without_paths_is_clean(self):
        self.assertEqual(rules("python3 -m unittest test_x"), set())

    def test_email_like_text_is_not_a_prompt(self):
        # A prompt rule is anchored at line start; mid-line @ is not a prompt.
        self.assertNotIn("EL-USER-PROMPT", rules("contact me at rito@example.com"))


# --------------------------------------------------------------------------
# The prefilter must be a superset of every rule
# --------------------------------------------------------------------------

class TestPrefilterIsSuperset(unittest.TestCase):
    """The candidate pipeline only transfers lines matching PREFILTER_RE.
    If any rule could match a line the prefilter rejects, the repository-wide
    scan would silently miss it.

    THIS CLASS USED TO PROVE LESS THAN ITS NAME AND THE README CLAIMED.

    `README.md` says: "The soundness of this rests on one claim: the
    prefilter can never drop a line a rule would match." `leakscan.py`
    says the same thing in a comment above PREFILTER_RE, and points here:
    "Tested both ways -- see test_leakscan.py TestPrefilterIsSuperset."

    What was actually tested was a hand-written list of 22 example lines.
    A list cannot establish a claim of the form "no line, ever" -- it can
    only fail to find one -- and the invariant was in fact FALSE. Every
    entry in POSITIVES happened to put the interesting text at a start of
    line or after a space, which is precisely the shape the prefilter's
    POSIX branch requires; EL-USER-PATH has no such anchor. So a real
    finding like `ls foo/home/alice/bin` was dropped before any rule ran,
    and this class passed.

    The list is kept (it is a fine smoke test and it pins the intent of
    each rule), and a GENERATIVE check is added beside it:
    test_prefilter_covers_every_placement_of_every_core wraps each core
    string in every printable single-character prefix AND every printable
    single-character suffix, keeps the combinations that genuinely fire a
    rule, and requires the prefilter to match all of them.

    Two limits of that, stated rather than glossed:

      * It is not a proof over all strings. It is a check whose passing
        depends on the invariant rather than on the author's imagination,
        and it fails on the parent commit. That is the whole claim.
      * `EL-USER-PROMPT` is `^`-anchored, so ANY non-empty prefix kills
        it and no prefix matrix can cover it. It is covered by the
        suffix-only half and by an explicit case below instead.
    """

    #: Cores that between them exercise every rule in RULES. The
    #: PLACEMENT matrix below is what is generated; these are minimal
    #: representatives, and every one is asserted to fire a rule.
    #: test_every_rule_is_exercised_by_some_core pins the coverage, so
    #: adding a rule without adding a core fails loudly instead of
    #: silently shrinking what this class proves.
    CORES = [
        "/sessions/sharp-stoic-knuth/mnt/outputs",
        "rito@buildbox:~$ ls",
        "/home/rito/out.json",
        "/Users/rito/out.json",
        "/opt/build/out.json",
        "/tmp/a.json",
        "~/bin/tool.py",
        "$HOME/work",
        "${HOME}/work",
        "$TMPDIR",
        "%USERPROFILE%\\work",
        "%TEMP%\\x",
        "D:\\builds\\out.json",
        "C:\\Users\\Rito\\out.json",
        "C:\\Users\\x\\AppData\\Local\\Temp\\y",
        "\\\\fileserv\\share\\x",
        "\\\\_fileserv\\share\\x",
        "\\\\.fileserv\\share\\x",
        "\\\\-fileserv\\share\\x",
        "localhost:8080",
        "127.0.0.1:5000",
        "buildbox.local",
        "ip-10-0-3-17",
        "node-7.compute.internal",
    ]

    #: Every printable character except the line terminators, which cannot
    #: appear inside a line by construction.
    AFFIXES = [""] + [c for c in string.printable
                      if c not in "\n\r\x0b\x0c"]

    def test_every_core_fires_at_least_one_rule(self):
        """Guards the generative test from passing vacuously."""
        dead = [c for c in self.CORES if not L.scan_line("f.md", 1, c)]
        self.assertEqual(dead, [])

    def test_every_rule_is_exercised_by_some_core(self):
        """CORES must reach every rule, or the matrix silently shrinks."""
        reached = set()
        for core in self.CORES:
            for hit in L.scan_line("f.md", 1, core):
                reached.add(hit["rule"])
        all_rules = {r["id"] for r in L.RULES}
        self.assertEqual(sorted(all_rules - reached), [],
                         "no core exercises these rules")

    def test_prefilter_covers_every_placement_of_every_core(self):
        """The real invariant: rule matched => prefilter matched.

        Only combinations that actually fire a rule are checked; the
        prefilter is allowed to be over-broad, never under-broad.
        """
        checked = 0
        missed = []
        for core in self.CORES:
            for affix in self.AFFIXES:
                for line in (affix + core, core + affix):
                    if not L.scan_line("f.md", 1, line):
                        continue
                    checked += 1
                    if not L.PREFILTER_RE.search(line):
                        missed.append(line)
        self.assertEqual(missed, [], "prefilter dropped %d line(s) a rule "
                                     "matches, e.g. %r"
                                     % (len(missed), missed[:3]))
        self.assertGreater(checked, 3000,
                           "the matrix collapsed; it is no longer checking "
                           "what it claims to check")

    def test_prefilter_covers_cores_embedded_mid_line(self):
        """The specific shape the old list never produced."""
        missed = []
        for core in self.CORES:
            line = "the build wrote its log to ." + core + " last night."
            if not L.scan_line("f.md", 1, line):
                continue
            if not L.PREFILTER_RE.search(line):
                missed.append(line)
        self.assertEqual(missed, [])

    def test_the_specific_counterexamples_that_were_being_dropped(self):
        """Named, so a future prefilter edit cannot quietly undo this."""
        for line in ("ls foo/home/alice/bin",
                     "see ../Users/rito/x",
                     "The build wrote its log to ../home/rito/out.json.",
                     "copy from \\\\_fileserv\\share\\x"):
            with self.subTest(line=line):
                self.assertTrue(L.scan_line("f.md", 1, line),
                                "this line must fire a rule for the test to "
                                "mean anything")
                self.assertTrue(L.PREFILTER_RE.search(line))

    POSITIVES = [
        "output went to /opt/build/out.json",
        "wrote D:\\builds\\out.json",
        "copied from \\\\fileserv\\share\\x",
        "run ~/bin/tool.py",
        "cd $HOME/work",
        "cd ${HOME}/work",
        "set X=%USERPROFILE%\\work",
        "saved to /home/rito/out.json",
        "saved to /Users/rito/out.json",
        "saved to C:\\Users\\Rito\\out.json",
        "wrote /tmp/a.json",
        "wrote /var/folders/xy/z/T/out",
        "cd $TMPDIR",
        "set OUT=%TEMP%\\x",
        "C:\\Users\\x\\AppData\\Local\\Temp\\y",
        "cd /sessions/sharp-stoic-knuth/mnt/outputs",
        "curl http://localhost:8080/x",
        "bound to 127.0.0.1:5000",
        "ssh buildbox.local",
        "on ip-10-0-3-17 the run failed",
        "host node-7.compute.internal",
        "rito@buildbox:~$ ls",
    ]

    def test_every_positive_line_matches_the_prefilter(self):
        missed = [s for s in self.POSITIVES if not L.PREFILTER_RE.search(s)]
        self.assertEqual(missed, [])

    def test_every_positive_line_actually_fires_a_rule(self):
        # Guards the test above from passing vacuously.
        dead = [s for s in self.POSITIVES if not L.scan_line("f.md", 1, s)]
        self.assertEqual(dead, [])

    def test_prefilter_rejects_ordinary_prose(self):
        self.assertIsNone(L.PREFILTER_RE.search(
            "The tool exits 1 when drift is found."))

    def test_prefilter_rejects_a_plain_url(self):
        self.assertIsNone(L.PREFILTER_RE.search(
            "see https://github.com/Ritorai/postfiatwork"))


class TestFullScanEqualsCandidateScan(TreeMixin, unittest.TestCase):
    """The claim the repository-wide report rests on: scanning full file text
    and scanning only the prefiltered candidate lines give identical findings."""

    DOC = (
        "# Title\n"
        "Ordinary prose with no paths at all.\n"
        "See https://github.com/Ritorai/postfiatwork for source.\n"
        "cd /sessions/sharp-stoic-knuth/mnt/outputs\n"
        "wrote /tmp/relocated_report.json\n"
        "relative/path/file.py is fine\n"
        "saved to /home/rito/out.json\n"
        "rito@buildbox:~$ ls\n"
        "curl http://localhost:8080/x\n"
    )

    def test_identical_findings(self):
        root = self.tree({"a/README.md": self.DOC, "b/captured_output.txt": self.DOC})
        full = []
        for rel, path in L.discover(root):
            with open(path, encoding="utf-8") as fh:
                full.extend(L.scan_text(rel, fh.read()))
        cand = L.emit_candidates(root)
        via_cand = []
        for rel in sorted(cand):
            for lineno, line in cand[rel]:
                via_cand.extend(L.scan_line(rel, lineno, line))
        key = (lambda f: (f["file"], f["line"], f["column"], f["rule"], f["matched"]))
        self.assertEqual(sorted(map(key, full)), sorted(map(key, via_cand)))
        self.assertTrue(full, "fixture produced no findings; test is vacuous")

    def test_candidates_drop_the_clean_lines(self):
        root = self.tree({"a/README.md": self.DOC})
        cand = L.emit_candidates(root)
        self.assertEqual(len(cand), 1)
        self.assertLess(len(cand["a/README.md"]), len(self.DOC.splitlines()))


# --------------------------------------------------------------------------
# Review is fail-closed
# --------------------------------------------------------------------------

class TestReview(TreeMixin, unittest.TestCase):
    LEAKY = "cd /sessions/sharp-stoic-knuth/mnt/outputs\n"

    def scan(self, review=None):
        findings = L.scan_text("a/README.md", self.LEAKY)
        return L.build_report(findings, review or {}, {"mode": "test"})

    def test_unreviewed_match_is_a_leak(self):
        r = self.scan()
        self.assertEqual(r["counts"]["confirmed"], 1)
        self.assertEqual(r["counts"]["benign"], 0)

    def test_benign_verdict_moves_it_out_of_confirmed(self):
        key = "a/README.md:1:/sessions/sharp-stoic-knuth/mnt/outputs"
        r = self.scan({key: {"verdict": "benign", "reason": "example only"}})
        self.assertEqual(r["counts"]["confirmed"], 0)
        self.assertEqual(r["counts"]["benign"], 1)
        self.assertEqual(r["reviewed_benign"][0]["review_reason"], "example only")

    def test_leak_verdict_keeps_it_confirmed_and_keeps_the_reason(self):
        key = "a/README.md:1:/sessions/sharp-stoic-knuth/mnt/outputs"
        r = self.scan({key: {"verdict": "leak", "reason": "checked, real"}})
        self.assertEqual(r["counts"]["confirmed"], 1)
        self.assertEqual(r["confirmed_leaks"][0]["review_reason"], "checked, real")

    def test_a_review_key_that_matches_nothing_is_reported_stale(self):
        r = self.scan({"gone.md:9:/tmp/x": {"verdict": "benign", "reason": "r"}})
        self.assertEqual(r["stale_review_entries"], ["gone.md:9:/tmp/x"])

    def test_review_entry_without_reason_is_a_setup_error(self):
        p = os.path.join(self.tmp(), "r.json")
        with open(p, "w") as fh:
            json.dump({"a:1:b": {"verdict": "benign"}}, fh)
        with self.assertRaises(L.SetupError):
            L.load_review(p)

    def test_review_entry_with_empty_reason_is_a_setup_error(self):
        p = os.path.join(self.tmp(), "r.json")
        with open(p, "w") as fh:
            json.dump({"a:1:b": {"verdict": "benign", "reason": "  "}}, fh)
        with self.assertRaises(L.SetupError):
            L.load_review(p)

    def test_unknown_verdict_is_a_setup_error(self):
        p = os.path.join(self.tmp(), "r.json")
        with open(p, "w") as fh:
            json.dump({"a:1:b": {"verdict": "ignore", "reason": "r"}}, fh)
        with self.assertRaises(L.SetupError):
            L.load_review(p)


# --------------------------------------------------------------------------
# CLI behaviour
# --------------------------------------------------------------------------

class TestCli(TreeMixin, unittest.TestCase):
    def test_clean_tree_exits_zero(self):
        root = self.tree({"a/README.md": "Nothing to see here.\n"})
        rc, out = self.run_cli(["--root", root])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["status"], "clean")

    def test_leaky_tree_exits_one(self):
        root = self.tree({"a/README.md": "cd /sessions/abc/mnt/outputs\n"})
        rc, out = self.run_cli(["--root", root])
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out)["status"], "leaks")

    def test_missing_root_exits_two(self):
        rc, out = self.run_cli(["--root", os.path.join(self.tmp(), "nope")])
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(out)["status"], "error")

    def test_missing_review_exits_two(self):
        root = self.tree({"a/README.md": "x\n"})
        rc, out = self.run_cli(["--root", root, "--review",
                                os.path.join(self.tmp(), "nope.json")])
        self.assertEqual(rc, 2)

    def test_malformed_review_exits_two(self):
        root = self.tree({"a/README.md": "x\n"})
        p = os.path.join(self.tmp(), "bad.json")
        with open(p, "w") as fh:
            fh.write("{not json")
        rc, _ = self.run_cli(["--root", root, "--review", p])
        self.assertEqual(rc, 2)

    def test_only_md_and_txt_are_scanned(self):
        root = self.tree({"a/code.py": "P = '/sessions/abc/mnt/outputs'\n",
                          "a/README.md": "clean\n"})
        rc, out = self.run_cli(["--root", root])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["coverage"]["files"], ["a/README.md"])

    def test_coverage_names_every_scanned_file(self):
        root = self.tree({"a/README.md": "x\n", "b/notes.txt": "y\n"})
        _, out = self.run_cli(["--root", root])
        self.assertEqual(json.loads(out)["coverage"]["files"],
                         ["a/README.md", "b/notes.txt"])

    def test_candidates_mode_exits_zero_even_with_leaks(self):
        root = self.tree({"a/README.md": "cd /sessions/abc/mnt/outputs\n"})
        rc, out = self.run_cli(["--root", root, "--candidates"])
        self.assertEqual(rc, 0)
        self.assertIn("a/README.md", json.loads(out))

    def test_scan_candidates_round_trip(self):
        root = self.tree({"a/README.md": "cd /sessions/abc/mnt/outputs\n"})
        cand_path = os.path.join(self.tmp(), "c.json")
        self.run_cli(["--root", root, "--candidates", "-o", cand_path])
        rc, out = self.run_cli(["--scan-candidates", cand_path])
        self.assertEqual(rc, 1)
        report = json.loads(out)
        self.assertEqual(report["counts"]["confirmed"], 1)
        self.assertEqual(report["coverage"]["mode"], "candidates")

    def test_report_is_deterministic(self):
        root = self.tree({"a/README.md": "cd /tmp/x and /sessions/abc/y\n"})
        _, a = self.run_cli(["--root", root])
        _, b = self.run_cli(["--root", root])
        self.assertEqual(a, b)

    def test_every_finding_states_its_reproducibility_harm(self):
        root = self.tree({"a/README.md": "cd /sessions/abc/mnt/outputs\n"})
        _, out = self.run_cli(["--root", root])
        for f in json.loads(out)["confirmed_leaks"]:
            self.assertTrue(f["harms_reproducibility"].strip())
            self.assertIn(f["category"], L.CATEGORIES)
            self.assertGreaterEqual(f["column"], 1)

    def test_by_category_lists_all_five_including_zeroes(self):
        root = self.tree({"a/README.md": "cd /tmp/x\n"})
        _, out = self.run_cli(["--root", root])
        self.assertEqual(set(json.loads(out)["counts"]["by_category"]),
                         set(L.CATEGORIES))

    def test_output_file_has_one_trailing_newline(self):
        root = self.tree({"a/README.md": "x\n"})
        p = os.path.join(self.tmp(), "r.json")
        self.run_cli(["--root", root, "-o", p])
        with open(p, "rb") as fh:
            data = fh.read()
        self.assertTrue(data.endswith(b"\n"))
        self.assertFalse(data.endswith(b"\n\n"))


class TestStdlibOnlyImports(unittest.TestCase):
    ALLOWED = {"argparse", "json", "os", "re", "sys"}

    def test_only_allow_listed_imports(self):
        import ast
        with open(LEAKSCAN_PY, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module.split(".")[0])
        self.assertEqual(found - self.ALLOWED, set())


if __name__ == "__main__":
    unittest.main()
