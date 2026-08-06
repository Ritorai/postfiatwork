#!/usr/bin/env python3
"""Run regenpre end-to-end over the fixture repository test_regenpre builds.

A separate file rather than an inline `python3 -c` because this repository's
transcript grammar requires a command to be a single line: a multi-line
`-c` payload produces a header that no `=== $ ... ===` parser can read.
Prints only stable fields, so the transcript record is reproducible.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_regenpre import FixtureRepo  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

for good in (True, False):
    tmp = tempfile.mkdtemp(prefix="regenpre_demo_")
    try:
        fx = FixtureRepo(tmp, good=good)
        p = subprocess.run(
            [sys.executable, os.path.join(HERE, "regenpre.py"),
             "--root", fx.root],
            stdout=subprocess.PIPE)
        r = json.loads(p.stdout.decode())
        print("good_fixture=%s cli_exit=%s failing=%s counts=%s"
              % (good, p.returncode, r["failing"],
                 json.dumps(r["counts"], sort_keys=True)))
        for i in r["items"]:
            print("   %-12s %-22s %s" % (i["phase"], i["name"], i["state"]))
    finally:
        shutil.rmtree(tmp)
