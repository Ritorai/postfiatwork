"""The input file must survive a run that names it as the output.

Run with:  python3 -m unittest test_output_aliasing -v

Standard library only. The CLI is invoked through sys.executable, so nothing
here needs an executable bit.

WHY THIS EXISTS

`staleness.py INPUT -o OUTPUT` reads INPUT fully into memory, builds a report,
then opens OUTPUT with "w". If the two names identify one file, the open
truncates the input -- and because the read already happened, the run still
prints a correct-looking report and still exits 0 or 1. The user gets a success
code and a destroyed input. Measured against the parent commit:

    cp tasks_stale.json direct.json
    sha256 direct.json -> efd93d30c34eb8ff2e7dee78bef31beefe38ef1e4ffce2a65854a696d298578e
    python3 staleness.py direct.json --now 2026-08-02T00:00:00Z -o direct.json
    exit=1
    sha256 direct.json -> 3651e155d9e4b9e4663a396bfd7564bbd6ca9b2ee9e6c584a0177494732b85cc

The hard-link case is the one a path comparison cannot reach: two genuinely
different names, neither a symlink, one inode. Same result on the parent
commit -- both names ended up holding the report.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "staleness.py")
FIXTURE = os.path.join(HERE, "tasks_stale.json")
NOW = "2026-08-02T00:00:00Z"

USAGE = 2


def sha256(path):
    import hashlib
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class AliasingMixin(object):

    def workdir(self):
        d = tempfile.mkdtemp(prefix="staleness_alias_")
        # Only the directory this call created. Never a parent of it.
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def run_cli(self, *args, **kw):
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.pop("PYTHONUNBUFFERED", None)
        return subprocess.run([sys.executable, SCRIPT] + list(args),
                              capture_output=True, text=True, env=env,
                              cwd=kw.get("cwd"))


class TestRefusesToOverwriteItsInput(AliasingMixin, unittest.TestCase):

    def test_the_same_path_twice_is_refused_before_anything_is_written(self):
        d = self.workdir()
        target = os.path.join(d, "sentinel.json")
        shutil.copy2(FIXTURE, target)
        before = sha256(target)

        proc = self.run_cli(target, "--now", NOW, "-o", target)

        self.assertEqual(proc.returncode, USAGE, proc.stderr)
        self.assertIn("refusing to write", proc.stderr)
        self.assertEqual(sha256(target), before,
                         "the input was modified despite the refusal")

    def test_a_hard_link_alias_is_refused_even_though_the_paths_differ(self):
        """The case a string comparison of the two paths cannot see."""
        d = self.workdir()
        source = os.path.join(d, "source.json")
        alias = os.path.join(d, "alias.json")
        shutil.copy2(FIXTURE, source)
        os.link(source, alias)
        self.assertNotEqual(source, alias)
        self.assertFalse(os.path.islink(alias), "this must be a hard link")
        before = sha256(source)

        proc = self.run_cli(source, "--now", NOW, "-o", alias)

        self.assertEqual(proc.returncode, USAGE, proc.stderr)
        self.assertIn("hard link", proc.stderr)
        self.assertEqual(sha256(source), before)
        self.assertEqual(sha256(alias), before)

    def test_a_relative_spelling_of_the_same_path_is_refused(self):
        d = self.workdir()
        shutil.copy2(FIXTURE, os.path.join(d, "s.json"))
        before = sha256(os.path.join(d, "s.json"))

        proc = self.run_cli("s.json", "--now", NOW, "-o", "./s.json", cwd=d)

        self.assertEqual(proc.returncode, USAGE, proc.stderr)
        self.assertEqual(sha256(os.path.join(d, "s.json")), before)

    def test_the_refusal_names_which_kind_of_alias_it_found(self):
        d = self.workdir()
        same = os.path.join(d, "same.json")
        shutil.copy2(FIXTURE, same)
        self.assertIn("same path", self.run_cli(same, "--now", NOW,
                                                "-o", same).stderr)

        source = os.path.join(d, "src.json")
        alias = os.path.join(d, "ali.json")
        shutil.copy2(FIXTURE, source)
        os.link(source, alias)
        self.assertIn("hard link to the input",
                      self.run_cli(source, "--now", NOW, "-o", alias).stderr)

    def test_nothing_is_printed_to_stdout_when_it_refuses(self):
        d = self.workdir()
        target = os.path.join(d, "s.json")
        shutil.copy2(FIXTURE, target)
        proc = self.run_cli(target, "--now", NOW, "-o", target)
        self.assertEqual(proc.stdout, "")


class TestNormalOutputIsUntouched(AliasingMixin, unittest.TestCase):

    def test_writing_to_a_distinct_path_still_works(self):
        d = self.workdir()
        source = os.path.join(d, "in.json")
        out = os.path.join(d, "out.json")
        shutil.copy2(FIXTURE, source)
        before = sha256(source)

        proc = self.run_cli(source, "--now", NOW, "-o", out)

        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(sha256(source), before, "the input must not change")
        with open(out, encoding="utf-8") as fh:
            self.assertIn("findings", json.load(fh))

    def test_the_written_file_is_what_stdout_would_have_been(self):
        d = self.workdir()
        source = os.path.join(d, "in.json")
        out = os.path.join(d, "out.json")
        shutil.copy2(FIXTURE, source)

        self.run_cli(source, "--now", NOW, "-o", out)
        with open(out, "rb") as fh:
            written = fh.read()
        piped = self.run_cli(source, "--now", NOW).stdout

        self.assertEqual(written.decode("utf-8"), piped)

    def test_overwriting_an_unrelated_existing_file_is_still_allowed(self):
        """The check refuses aliases, not overwrites. Clobbering some other
        file the caller named is their business, and refusing it would be a
        behaviour change nobody asked for."""
        d = self.workdir()
        source = os.path.join(d, "in.json")
        out = os.path.join(d, "out.json")
        shutil.copy2(FIXTURE, source)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("stale contents\n")

        proc = self.run_cli(source, "--now", NOW, "-o", out)

        self.assertEqual(proc.returncode, 1, proc.stderr)
        with open(out, encoding="utf-8") as fh:
            self.assertIn("findings", json.load(fh))

    def test_a_brand_new_output_path_is_not_treated_as_an_alias(self):
        d = self.workdir()
        source = os.path.join(d, "in.json")
        shutil.copy2(FIXTURE, source)
        proc = self.run_cli(source, "--now", NOW,
                            "-o", os.path.join(d, "does_not_exist_yet.json"))
        self.assertEqual(proc.returncode, 1, proc.stderr)

    def test_stdout_mode_is_unaffected(self):
        d = self.workdir()
        source = os.path.join(d, "in.json")
        shutil.copy2(FIXTURE, source)
        before = sha256(source)
        proc = self.run_cli(source, "--now", NOW)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("findings", json.loads(proc.stdout))
        self.assertEqual(sha256(source), before)


class TestTheHelperItself(unittest.TestCase):

    def setUp(self):
        sys.path.insert(0, HERE)
        self.addCleanup(sys.path.remove, HERE)
        import staleness
        self.staleness = staleness
        self.d = tempfile.mkdtemp(prefix="staleness_helper_")
        self.addCleanup(shutil.rmtree, self.d, True)

    def test_a_missing_output_is_not_an_alias(self):
        a = os.path.join(self.d, "a.json")
        shutil.copy2(FIXTURE, a)
        self.assertIsNone(self.staleness.output_aliases_input(
            a, os.path.join(self.d, "nope.json")))

    def test_two_distinct_files_are_not_an_alias(self):
        a, b = os.path.join(self.d, "a"), os.path.join(self.d, "b")
        shutil.copy2(FIXTURE, a)
        shutil.copy2(FIXTURE, b)
        self.assertIsNone(self.staleness.output_aliases_input(a, b))

    def test_identical_content_in_two_files_is_not_an_alias(self):
        """Same bytes, different inodes. Content is not identity."""
        a, b = os.path.join(self.d, "a"), os.path.join(self.d, "b")
        shutil.copy2(FIXTURE, a)
        shutil.copy2(FIXTURE, b)
        self.assertEqual(sha256(a), sha256(b))
        self.assertIsNone(self.staleness.output_aliases_input(a, b))

    def test_a_missing_input_does_not_raise(self):
        self.assertIsNone(self.staleness.output_aliases_input(
            os.path.join(self.d, "gone"), os.path.join(self.d, "also_gone")))


if __name__ == "__main__":
    unittest.main()
