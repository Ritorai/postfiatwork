#!/usr/bin/env python3
"""Tests for shebangmode.py.

Every test builds a real throwaway Git repository and populates its index
with real modes. Nothing is stubbed, because the whole subject of this
tool is what Git records rather than what a Python object says, and a
stubbed index would test the stub.

The modes are set with ``git update-index --chmod=+x`` rather than
``os.chmod``, for the same reason the tool reads the index rather than
``os.stat``: the index is what a clone reproduces. On a filesystem that
does not carry the executable bit at all, ``os.chmod`` would silently do
nothing and these tests would pass vacuously.
"""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

import shebangmode as S

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "shebangmode.py")


def git(repo, *args):
    proc = subprocess.run(["git", "-C", repo] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise AssertionError("git %s failed in %s: %s"
                             % (" ".join(args), repo,
                                proc.stderr.decode("utf-8", "replace")))
    return proc.stdout


class RepoCase(unittest.TestCase):
    """Base class owning exactly one temporary directory per test."""

    def setUp(self):
        # mkdtemp returns a NEW directory that belongs to this test. Only
        # this exact path is removed in tearDown -- never its parent,
        # which is the shared system temp directory.
        self.repo = tempfile.mkdtemp(prefix="shebangmode_test_")
        self.addCleanup(shutil.rmtree, self.repo)
        git(self.repo, "init", "-q")

    def write(self, relpath, data, executable=False):
        """Create a tracked file with an explicit index mode."""
        full = os.path.join(self.repo, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        mode = "wb" if isinstance(data, bytes) else "w"
        kwargs = {} if isinstance(data, bytes) else {"encoding": "utf-8"}
        with open(full, mode, **kwargs) as fh:
            fh.write(data)
        git(self.repo, "add", "--", relpath)
        if executable:
            git(self.repo, "update-index", "--chmod=+x", "--", relpath)
        return full

    def index_mode(self, relpath):
        out = git(self.repo, "ls-files", "-s", "--", relpath)
        return out.decode("ascii").split()[0]

    def scan(self, **kwargs):
        return S.scan(self.repo, **kwargs)

    def codes(self, report):
        return sorted(f["code"] for f in report["findings"])

    def run_cli(self, *args):
        return subprocess.run([sys.executable, TOOL] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class TestTheTestHarnessItself(RepoCase):
    """If these fail, every other test in this file is vacuous."""

    def test_update_index_chmod_actually_sets_100755(self):
        self.write("s.sh", "#!/bin/sh\necho hi\n", executable=True)
        self.assertEqual(self.index_mode("s.sh"), "100755")

    def test_a_plain_add_leaves_100644(self):
        self.write("s.sh", "#!/bin/sh\necho hi\n")
        self.assertEqual(self.index_mode("s.sh"), "100644")


class TestBothDirections(RepoCase):

    def test_exec_without_shebang_is_sm001(self):
        self.write("tool", "echo not a script\n", executable=True)
        self.assertEqual(self.codes(self.scan()), [S.SM001])

    def test_shebang_without_exec_is_sm002(self):
        self.write("tool.py", "#!/usr/bin/env python3\nprint(1)\n")
        self.assertEqual(self.codes(self.scan()), [S.SM002])

    def test_exec_with_shebang_is_clean(self):
        self.write("tool.py", "#!/usr/bin/env python3\nprint(1)\n",
                   executable=True)
        report = self.scan()
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["status"], "ok")

    def test_plain_without_shebang_is_clean(self):
        self.write("notes.md", "# just a document\n")
        self.assertEqual(self.scan()["findings"], [])

    def test_both_codes_can_appear_in_one_run(self):
        self.write("a", "no shebang\n", executable=True)
        self.write("b.py", "#!/usr/bin/env python3\n")
        self.assertEqual(self.codes(self.scan()), [S.SM001, S.SM002])


class TestWhatCountsAsAShebang(RepoCase):

    def test_a_leading_blank_line_is_not_a_shebang(self):
        """The kernel only honours ``#!`` at byte 0.

        A file that looks shebanged to a reader but has a blank first line
        cannot be executed, so marking it executable is still SM001.
        """
        self.write("tool", "\n#!/bin/sh\necho hi\n", executable=True)
        self.assertEqual(self.codes(self.scan()), [S.SM001])

    def test_leading_whitespace_is_not_a_shebang(self):
        self.write("tool", "  #!/bin/sh\n", executable=True)
        self.assertEqual(self.codes(self.scan()), [S.SM001])

    def test_a_hash_without_the_bang_is_not_a_shebang(self):
        self.write("tool.py", "# !/usr/bin/env python3\n")
        self.assertEqual(self.scan()["findings"], [])

    def test_shebang_on_a_file_with_no_trailing_newline(self):
        self.write("tool", "#!/bin/sh")
        self.assertEqual(self.codes(self.scan()), [S.SM002])

    def test_crlf_line_ending_still_reads_as_a_shebang(self):
        self.write("tool.py", b"#!/usr/bin/env python3\r\nprint(1)\r\n")
        self.assertEqual(self.codes(self.scan()), [S.SM002])


class TestBinaryFilesAreSkippedNotReported(RepoCase):

    def test_a_binary_marked_executable_is_not_sm001(self):
        self.write("blob.bin", b"\x7fELF\x00\x00\x00payload", executable=True)
        report = self.scan()
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["counts"]["skipped_binary"], 1)

    def test_a_binary_starting_with_hash_bang_is_still_binary(self):
        """``#!`` as the first two bytes of a binary is not a script."""
        self.write("blob.bin", b"#!\x00\x00\x00\x01\x02", executable=False)
        report = self.scan()
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["counts"]["skipped_binary"], 1)

    def test_a_skipped_binary_is_listed_with_its_reason(self):
        self.write("blob.bin", b"\x00\x01\x02")
        skipped = self.scan()["skipped"]
        self.assertEqual([(s["path"], s["reason"]) for s in skipped],
                         [("blob.bin", "binary")])

    def test_a_nul_after_the_sniff_window_is_treated_as_text(self):
        """States the limit rather than pretending there isn't one.

        The sniff reads a bounded prefix, exactly as Git does, so a file
        whose first NUL is beyond that window is classified as text. The
        test pins the boundary so a future change to the constant is a
        deliberate one.
        """
        payload = b"#!/bin/sh\n" + b"A" * S.BINARY_SNIFF_BYTES + b"\x00"
        self.write("late.bin", payload)
        report = self.scan()
        self.assertEqual(report["counts"]["skipped_binary"], 0)
        self.assertEqual(self.codes(report), [S.SM002])


class TestExclusions(RepoCase):

    def test_default_is_no_exclusions(self):
        self.write("gen/tool.py", "#!/usr/bin/env python3\n")
        self.assertEqual(self.codes(self.scan()), [S.SM002])
        self.assertEqual(self.scan()["exclude_prefixes"], [])

    def test_a_directory_prefix_excludes_its_contents(self):
        self.write("gen/tool.py", "#!/usr/bin/env python3\n")
        report = self.scan(exclude_prefixes=["gen"])
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["counts"]["skipped_excluded"], 1)

    def test_an_exact_path_can_be_excluded(self):
        self.write("gen/tool.py", "#!/usr/bin/env python3\n")
        self.write("gen/other.py", "#!/usr/bin/env python3\n")
        report = self.scan(exclude_prefixes=["gen/tool.py"])
        self.assertEqual([f["path"] for f in report["findings"]],
                         ["gen/other.py"])

    def test_a_prefix_does_not_match_a_partial_directory_name(self):
        """``doc`` must not exclude ``doc-validator/``.

        Prefix matching on raw strings is the obvious implementation and
        the obvious bug; this pins the boundary.
        """
        self.write("doc-validator/tool.py", "#!/usr/bin/env python3\n")
        report = self.scan(exclude_prefixes=["doc"])
        self.assertEqual(self.codes(report), [S.SM002])
        self.assertEqual(report["counts"]["skipped_excluded"], 0)

    def test_an_excluded_path_is_still_listed_with_the_prefix(self):
        """An exclusion may not hide a file silently."""
        self.write("gen/tool.py", "#!/usr/bin/env python3\n")
        skipped = self.scan(exclude_prefixes=["gen"])["skipped"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["path"], "gen/tool.py")
        self.assertEqual(skipped[0]["reason"], "excluded")
        self.assertEqual(skipped[0]["prefix"], "gen")

    def test_exclusions_are_recorded_in_the_report(self):
        report = self.scan(exclude_prefixes=["b", "a", "a"])
        self.assertEqual(report["exclude_prefixes"], ["a", "b"])


class TestNonRegularFiles(RepoCase):

    def test_a_symlink_is_skipped_with_its_mode(self):
        target = self.write("real.py", "#!/usr/bin/env python3\n",
                            executable=True)
        link = os.path.join(self.repo, "link.py")
        try:
            os.symlink(os.path.basename(target), link)
        except (OSError, NotImplementedError):  # pragma: no cover
            self.skipTest("this filesystem does not support symlinks")
        git(self.repo, "add", "--", "link.py")
        self.assertEqual(self.index_mode("link.py"), "120000")
        report = self.scan()
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["counts"]["skipped_not_a_regular_file"], 1)
        entry = [s for s in report["skipped"]
                 if s["reason"] == "not a regular file"]
        self.assertEqual(entry[0]["mode"], "120000")


class TestTheFixFieldActuallyFixes(RepoCase):
    """The report prints a command; running it must clear the finding."""

    def test_running_the_sm002_fix_makes_the_repo_clean(self):
        self.write("tool.py", "#!/usr/bin/env python3\nprint(1)\n")
        report = self.scan()
        self.assertEqual(self.codes(report), [S.SM002])
        fix = report["findings"][0]["fix"]
        self.assertEqual(fix, "git update-index --chmod=+x -- tool.py")
        git(self.repo, *shlex.split(fix)[1:])
        self.assertEqual(self.index_mode("tool.py"), "100755")
        self.assertEqual(self.scan()["findings"], [])

    def test_running_the_sm001_fix_makes_the_repo_clean(self):
        self.write("data", "not a script\n", executable=True)
        report = self.scan()
        self.assertEqual(self.codes(report), [S.SM001])
        fix = report["findings"][0]["fix"]
        self.assertEqual(fix, "git update-index --chmod=-x -- data")
        git(self.repo, *shlex.split(fix)[1:])
        self.assertEqual(self.index_mode("data"), "100644")
        self.assertEqual(self.scan()["findings"], [])


class TestDeterminism(RepoCase):

    def test_two_scans_serialise_byte_identically(self):
        self.write("b.py", "#!/usr/bin/env python3\n")
        self.write("a", "no shebang\n", executable=True)
        self.write("z/c.sh", "#!/bin/sh\n")
        first = S.canonical_dumps(self.scan())
        second = S.canonical_dumps(self.scan())
        self.assertEqual(first, second)

    def test_findings_are_sorted_by_code_then_path(self):
        self.write("z.py", "#!/usr/bin/env python3\n")
        self.write("a.py", "#!/usr/bin/env python3\n")
        self.write("m", "no shebang\n", executable=True)
        keys = [(f["code"], f["path"]) for f in self.scan()["findings"]]
        self.assertEqual(keys, sorted(keys))

    def test_the_report_carries_no_absolute_path(self):
        self.write("tool.py", "#!/usr/bin/env python3\n")
        blob = S.canonical_dumps(self.scan())
        self.assertNotIn(self.repo, blob)
        self.assertNotIn(tempfile.gettempdir(), blob)


class TestCli(RepoCase):

    def test_clean_repo_exits_zero(self):
        self.write("tool.py", "#!/usr/bin/env python3\n", executable=True)
        proc = self.run_cli("--root", self.repo)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout.decode("utf-8"))["status"],
                         "ok")

    def test_mismatch_exits_one_and_names_the_file(self):
        self.write("tool.py", "#!/usr/bin/env python3\n")
        proc = self.run_cli("--root", self.repo)
        self.assertEqual(proc.returncode, 1)
        report = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual([f["path"] for f in report["findings"]],
                         ["tool.py"])
        self.assertIn("inert", report["findings"][0]["detail"])

    def test_a_directory_that_is_not_a_git_checkout_exits_two(self):
        plain = tempfile.mkdtemp(prefix="shebangmode_notgit_")
        self.addCleanup(shutil.rmtree, plain)   # created on the line above
        proc = self.run_cli("--root", plain)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not inside a Git checkout",
                      proc.stderr.decode("utf-8", "replace"))

    def test_a_missing_root_exits_two(self):
        proc = self.run_cli("--root", os.path.join(self.repo, "nope"))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("is not a directory",
                      proc.stderr.decode("utf-8", "replace"))

    def test_an_unwritable_output_exits_two_not_one(self):
        """Exit 2 and exit 1 mean different things and must not blur."""
        self.write("tool.py", "#!/usr/bin/env python3\n")
        bad = os.path.join(self.repo, "no_such_dir", "out.json")
        proc = self.run_cli("--root", self.repo, "-o", bad)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("cannot write --output",
                      proc.stderr.decode("utf-8", "replace"))

    def test_output_flag_writes_the_file_and_prints_a_summary(self):
        self.write("tool.py", "#!/usr/bin/env python3\n")
        out = os.path.join(self.repo, "report.json")
        proc = self.run_cli("--root", self.repo, "-o", out)
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(os.path.isfile(out))
        self.assertIn(b"status=mismatches findings=1", proc.stdout)

    def test_quiet_suppresses_stdout_but_not_the_exit_code(self):
        self.write("tool.py", "#!/usr/bin/env python3\n")
        proc = self.run_cli("--root", self.repo, "--quiet")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, b"")

    def test_exclude_flag_is_repeatable(self):
        self.write("g1/tool.py", "#!/usr/bin/env python3\n")
        self.write("g2/tool.py", "#!/usr/bin/env python3\n")
        proc = self.run_cli("--root", self.repo,
                            "--exclude", "g1", "--exclude", "g2")
        self.assertEqual(proc.returncode, 0)
        report = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(report["exclude_prefixes"], ["g1", "g2"])

    def test_two_cli_runs_produce_identical_bytes(self):
        self.write("tool.py", "#!/usr/bin/env python3\n")
        a = self.run_cli("--root", self.repo).stdout
        b = self.run_cli("--root", self.repo).stdout
        self.assertEqual(a, b)


class TestASubdirectoryIsNotAScannableRoot(RepoCase):
    """``git -C DIR ls-files`` walks UP to the enclosing repository.

    A plain directory nested inside a checkout therefore does not make git
    fail: it returns the tracked files at or below DIR, which for a fresh
    subdirectory is none. The first version of this tool printed
    ``"status": "ok"`` and exited 0 for exactly that input -- a silent pass
    on a repository it had not looked at.

    The transcript found it, not these tests: capture.sh pointed --root at
    a scratch directory created inside this checkout, and the CLI-contract
    record that was supposed to show exit 2 showed exit 0 instead. The
    original unit test used a mkdtemp OUTSIDE any repository, where git
    really does fail, so it passed throughout.
    """

    def test_a_nested_plain_directory_is_a_setup_error_not_ok(self):
        nested = os.path.join(self.repo, "scratch")
        os.makedirs(nested)
        with self.assertRaises(S.SetupError) as ctx:
            S.scan(nested)
        self.assertIn("is not its root", str(ctx.exception))

    def test_that_nested_directory_exits_two_through_the_cli(self):
        nested = os.path.join(self.repo, "scratch")
        os.makedirs(nested)
        self.write("tool.py", "#!/usr/bin/env python3\n")
        proc = self.run_cli("--root", nested)
        self.assertEqual(proc.returncode, 2, "a nested directory reported ok")
        self.assertIn("is not its root",
                      proc.stderr.decode("utf-8", "replace"))

    def test_a_tracked_subdirectory_is_refused_too(self):
        """Even a subdirectory that DOES contain tracked files.

        It would scan a real subset and could report ok for a repository
        that fails the rule elsewhere, which is the same silent pass in a
        less obvious costume.
        """
        self.write("sub/tool.py", "#!/usr/bin/env python3\n",
                   executable=True)
        self.write("bad.py", "#!/usr/bin/env python3\n")
        with self.assertRaises(S.SetupError):
            S.scan(os.path.join(self.repo, "sub"))

    def test_the_checkout_root_is_named_relatively(self):
        """The message lands in a committed transcript.

        Naming the checkout root by its absolute path would bake the
        scanning machine's directory layout into that file. ``..`` is
        just as actionable and reproduces anywhere. The --root the caller
        supplied is echoed back as given -- if that was absolute, that is
        the caller's string, not one this tool went and derived.
        """
        nested = os.path.join(self.repo, "scratch")
        os.makedirs(nested)
        with self.assertRaises(S.SetupError) as ctx:
            S.scan(nested)
        self.assertIn("'..'", str(ctx.exception))

    def test_a_relative_root_produces_a_wholly_relative_message(self):
        """The property the transcript actually depends on.

        Run the CLI with the cwd inside the checkout and a relative
        --root, exactly as capture.sh does, and require that no absolute
        path reaches stderr at all.
        """
        nested = os.path.join(self.repo, "scratch")
        os.makedirs(nested)
        proc = subprocess.run(
            [sys.executable, TOOL, "--root", "scratch"],
            cwd=self.repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        message = proc.stderr.decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("'scratch'", message)
        self.assertIn("'..'", message)
        self.assertNotIn(os.path.realpath(self.repo), message)
        self.assertNotIn(tempfile.gettempdir(), message)

    def test_the_relative_root_is_correct_two_levels_down(self):
        nested = os.path.join(self.repo, "a", "b")
        os.makedirs(nested)
        with self.assertRaises(S.SetupError) as ctx:
            S.scan(nested)
        self.assertIn(os.path.join("..", ".."), str(ctx.exception))

    def test_the_checkout_root_itself_is_still_accepted(self):
        """Control: the guard must not refuse the legitimate case."""
        self.write("tool.py", "#!/usr/bin/env python3\n", executable=True)
        self.assertEqual(S.scan(self.repo)["findings"], [])


class TestSetupErrorsAreNotFindings(RepoCase):

    def test_a_file_deleted_from_the_worktree_is_still_checked(self):
        """The index still has the blob, so the question is still answerable.

        Content comes from ``git cat-file``, not from the working tree, so
        a half-finished ``git rm`` or an interrupted checkout does not stop
        the scan or change its verdict.
        """
        path = self.write("tool.py", "#!/usr/bin/env python3\n")
        os.remove(path)
        self.assertEqual(self.codes(self.scan()), [S.SM002])

    def test_a_missing_git_binary_raises_setup_error(self):
        empty = tempfile.mkdtemp(prefix="shebangmode_nopath_")
        self.addCleanup(shutil.rmtree, empty)   # created on the line above
        original = os.environ.get("PATH", "")
        os.environ["PATH"] = empty
        self.addCleanup(os.environ.__setitem__, "PATH", original)
        with self.assertRaises(S.SetupError) as ctx:
            self.scan()
        self.assertIn("git executable not found", str(ctx.exception))


class TestContentComesFromTheIndexNotTheWorktree(RepoCase):
    """Both halves of the predicate must come from the same place.

    The first version took the mode from ``git ls-files -s`` and the first
    line from ``open(os.path.join(root, path))``. That is a silent pass:
    the committed blob can start with ``#!`` while the unstaged working
    copy does not, and the tool then reported ``"status": "ok"`` and exit 0
    for an index that really does violate the rule. The mirror case was
    worse -- it printed a ``fix`` that would have turned a conforming index
    into an SM001 violation.
    """

    def _diverge(self, relpath, worktree_text):
        """Rewrite the working copy WITHOUT staging it."""
        with open(os.path.join(self.repo, relpath), "w",
                  encoding="utf-8") as fh:
            fh.write(worktree_text)

    def test_a_shebang_only_in_the_index_is_still_reported(self):
        self.write("bad.py", "#!/usr/bin/env python3\nprint(1)\n")
        self._diverge("bad.py", "print(2)\n")      # unstaged: no shebang
        report = self.scan()
        self.assertEqual(self.codes(report), [S.SM002],
                         "the working copy hid an index violation")
        self.assertEqual(report["counts"]["with_shebang"], 1)

    def test_a_shebang_only_in_the_worktree_is_not_reported(self):
        self.write("ok.py", "print(1)\n")           # index: no shebang
        self._diverge("ok.py", "#!/usr/bin/env python3\nprint(1)\n")
        report = self.scan()
        self.assertEqual(report["findings"], [],
                         "an unstaged edit invented a finding, and its fix "
                         "would have created a real SM001 violation")

    def test_an_unstaged_edit_cannot_flip_the_exit_code(self):
        self.write("bad.py", "#!/usr/bin/env python3\n")
        first = self.run_cli("--root", self.repo).returncode
        self._diverge("bad.py", "print(2)\n")
        second = self.run_cli("--root", self.repo).returncode
        self.assertEqual((first, second), (1, 1))

    def test_binary_detection_also_reads_the_index(self):
        self.write("blob.bin", b"\x00\x01\x02", executable=True)
        self._diverge("blob.bin", "#!/bin/sh\n")
        report = self.scan()
        self.assertEqual(report["counts"]["skipped_binary"], 1)
        self.assertEqual(report["findings"], [])


class TestUnmergedPathsAreSkippedOnce(RepoCase):
    """A conflicted path is listed once per stage by ``ls-files -s``.

    Reporting it three times would triple every count and print the same
    finding three times; picking one stage would invent an answer about a
    file that currently has none.
    """

    def _conflict(self):
        self.write("f.py", "#!/usr/bin/env python3\nbase\n")
        git(self.repo, "-c", "user.email=t@e", "-c", "user.name=t",
            "commit", "-q", "-m", "base")
        git(self.repo, "checkout", "-q", "-b", "other")
        self.write("f.py", "#!/usr/bin/env python3\nother\n")
        git(self.repo, "-c", "user.email=t@e", "-c", "user.name=t",
            "commit", "-q", "-m", "other")
        git(self.repo, "checkout", "-q", "-")
        self.write("f.py", "#!/usr/bin/env python3\nmine\n")
        git(self.repo, "-c", "user.email=t@e", "-c", "user.name=t",
            "commit", "-q", "-m", "mine")
        merge = subprocess.run(["git", "-C", self.repo, "merge", "other"],
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
        self.assertNotEqual(merge.returncode, 0, "the merge did not conflict")

    def test_a_conflicted_path_appears_once_as_skipped(self):
        self._conflict()
        report = self.scan()
        unmerged = [s for s in report["skipped"] if s["reason"] == "unmerged"]
        self.assertEqual([s["path"] for s in unmerged], ["f.py"])
        self.assertEqual(report["counts"]["skipped_unmerged"], 1)
        self.assertEqual(report["counts"]["tracked"], 1)
        self.assertEqual(report["findings"], [])


class TestFixCommandsAreShellSafe(RepoCase):
    """The README tells a reader to extract and run these strings."""

    def test_a_path_with_a_space_is_quoted(self):
        self.write("has space.py", "#!/usr/bin/env python3\n")
        fix = self.scan()["findings"][0]["fix"]
        self.assertEqual(fix, "git update-index --chmod=+x -- 'has space.py'")

    def test_a_path_with_a_semicolon_cannot_start_a_second_command(self):
        self.write("semi;rm -rf x.py", "#!/usr/bin/env python3\n")
        fix = self.scan()["findings"][0]["fix"]
        self.assertEqual(shlex.split(fix)[-1], "semi;rm -rf x.py")
        self.assertNotIn("; rm", fix.replace("'", ""))

    def test_a_quoted_fix_still_works_when_run(self):
        self.write("has space.py", "#!/usr/bin/env python3\n")
        fix = self.scan()["findings"][0]["fix"]
        git(self.repo, *shlex.split(fix)[1:])
        self.assertEqual(self.index_mode("has space.py"), "100755")
        self.assertEqual(self.scan()["findings"], [])

    def test_every_fix_round_trips_through_shlex(self):
        for name in ("plain.py", "has space.py", "semi;x.py", "quote'.py",
                     "dollar$.py", "star*.py"):
            self.write(name, "#!/usr/bin/env python3\n")
        for f in self.scan()["findings"]:
            self.assertEqual(shlex.split(f["fix"])[-1], f["path"])


class TestCountsAddUp(RepoCase):

    def test_every_tracked_file_is_either_checked_or_skipped(self):
        self.write("a.py", "#!/usr/bin/env python3\n")
        self.write("bin/blob", b"\x00\x01")
        self.write("gen/x.py", "#!/usr/bin/env python3\n")
        c = self.scan(exclude_prefixes=["gen"])["counts"]
        accounted = (c["checked"] + c["skipped_binary"]
                     + c["skipped_excluded"] + c["skipped_not_a_regular_file"]
                     + c["skipped_unmerged"])
        self.assertEqual(accounted, c["tracked"])

    def test_executable_and_shebang_counts_are_measured_not_assumed(self):
        self.write("a.py", "#!/usr/bin/env python3\n", executable=True)
        self.write("b.py", "#!/usr/bin/env python3\n")
        self.write("c.md", "# doc\n")
        c = self.scan()["counts"]
        self.assertEqual(c["with_shebang"], 2)
        self.assertEqual(c["executable"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
