#!/usr/bin/env python3
"""Recreate fixture_repo/ byte-for-byte.

The fixture is a small multi-tool repository carrying deliberate defects - a
tool with no README, one with no test module, one with no description, an
extra non-entrypoint module, and one README stored with CRLF line endings -
so that every finding code has something to fire on.

Contents are stored base64-encoded and written in BINARY mode on purpose. An
earlier version of this generator stored them as text, which silently rewrote
the CRLF fixture to LF and destroyed the line-ending test case while still
producing matching report hashes. Do not "simplify" this back to text mode.

Regenerating the fixture and re-running indexgen reproduces the committed
sample_report.json and sample_INDEX.md hashes exactly; captured_output.txt
shows that round trip.

    python3 make_fixture_repo.py [dest]     # default dest: fixture_repo
"""
import base64
import os
import sys

FILES = {
 "alpha/README.md": "WyFbc3RhdHVzXShodHRwczovL2ltZy5zaGllbGRzLmlvL2JhZGdlL3N0YXR1cy1wYXNzaW5nLWdyZWVuKV0oaHR0cHM6Ly9leGFtcGxlLmNvbSkKCiMgYWxwaGEgLSBwcmludHMgYSBmcmllbmRseSBncmVldGluZwoKQWxwaGEgaXMgYSBtaW5pbWFsIGV4YW1wbGUgdG9vbC4gNCB0ZXN0cywgYWxsIHBhc3NpbmcuCg==",
 "alpha/alpha.py": "ZGVmIG1haW4oKToKICAgIHByaW50KCJhbHBoYSIpCgppZiBfX25hbWVfXyA9PSAiX19tYWluX18iOgogICAgbWFpbigpCg==",
 "alpha/captured_output.txt": "b2sK",
 "alpha/fixtures/sample.txt": "c2FtcGxlIGZpeHR1cmUgZGF0YQo=",
 "alpha/test_alpha.py": "aW1wb3J0IHVuaXR0ZXN0CgpjbGFzcyBBbHBoYVRlc3RzKHVuaXR0ZXN0LlRlc3RDYXNlKToKICAgIGRlZiB0ZXN0X29rKHNlbGYpOgogICAgICAgIHNlbGYuYXNzZXJ0VHJ1ZShUcnVlKQoKaWYgX19uYW1lX18gPT0gIl9fbWFpbl9fIjoKICAgIHVuaXR0ZXN0Lm1haW4oKQo=",
 "beta/README.md": "IyBiZXRhOiBjb21wdXRlcyB0aGUgYW5zd2VyIHRvIGV2ZXJ5dGhpbmcKCkJldGEgcmV0dXJucyBhIGNvbnN0YW50LiBObyB0ZXN0IGNvdW50IHN0YXRlZCBoZXJlIG9uIHB1cnBvc2UuCg==",
 "beta/beta.py": "ZGVmIG1haW4oKToKICAgIHJldHVybiA0Mgo=",
 "beta/test_beta.py": "ZGVmIHRlc3RfbWFpbigpOgogICAgYXNzZXJ0IFRydWUK",
 "delta/captured_output.txt": "b2sK",
 "delta/delta.py": "ZGVmIG1haW4oKToKICAgIHBhc3MK",
 "epsilon/README.md": "IVtiYWRnZV0oaHR0cHM6Ly9pbWcuc2hpZWxkcy5pby9iYWRnZS94LXktZ3JlZW4pCgojIEVwc2lsb24K",
 "epsilon/captured_output.txt": "b2sK",
 "epsilon/epsilon.py": "ZGVmIG1haW4oKToKICAgIHBhc3MK",
 "epsilon/test_epsilon.py": "ZGVmIHRlc3RfeCgpOgogICAgYXNzZXJ0IFRydWUK",
 "gamma/README.md": "IyBnYW1tYQoKR2FtbWEgYnVuZGxlcyBhIGhlbHBlciBtb2R1bGUgYWxvbmdzaWRlIGl0cyBlbnRyeXBvaW50LiBUZXN0czogNgo=",
 "gamma/captured_output.txt": "b2sK",
 "gamma/gamma.py": "ZGVmIG1haW4oKToKICAgIHBhc3MK",
 "gamma/helper.py": "ZGVmIGhlbHBlcigpOgogICAgcGFzcwo=",
 "gamma/test_gamma.py": "ZGVmIHRlc3RfZ2FtbWEoKToKICAgIGFzc2VydCBUcnVlCg==",
 "zeta/README.md": "IyB6ZXRhIOKAlCDml6XmnKzoqp7jg4Tjg7zjg6sNCg0K5pel5pys6Kqe44Gu6Kqs5piO44Gn44GZ44CCIDMgdGVzdHMuDQo=",
 "zeta/captured_output.txt": "b2sK",
 "zeta/test_zeta.py": "ZGVmIHRlc3RfeCgpOgogICAgYXNzZXJ0IFRydWUK",
 "zeta/zeta.py": "ZGVmIG1haW4oKToKICAgIHBhc3MK"
}


def main():
    dest = sys.argv[1] if len(sys.argv) > 1 else "fixture_repo"
    for rel in sorted(FILES):
        path = os.path.join(dest, *rel.split("/"))
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(FILES[rel]))
    print("wrote %d files to %s" % (len(FILES), dest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
