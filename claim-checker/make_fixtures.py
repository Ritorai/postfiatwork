#!/usr/bin/env python3
"""make_fixtures.py -- regenerate claimcheck's fixture bundles byte-for-byte.

Run from inside the claim-checker directory:

    python3 make_fixtures.py

Recreates bundle_truthful/, bundle_false/, and bundle_repro/ exactly as
shipped, from base64-encoded content embedded in this script. Every file
is written in BINARY mode (no newline translation), so the regenerated
bytes -- and therefore their SHA-256 digests, which several of the notes
files claim -- are identical to the originals on every platform.

If a future fixture needs an EMPTY directory, list it in EMPTY_DIRS
below and this script will os.makedirs() it explicitly: os.walk() (used
by claimcheck.py's own discover_files()) silently omits directories that
contain no files, so an empty directory that is only ever created by
checking out a bundle -- never by walking one -- would otherwise
silently vanish, changing the resulting report's byte content the next
time the fixture is regenerated from scratch.
"""
from __future__ import annotations

import base64
import os

FIXTURE_FILES = {
    'bundle_truthful/checker.py': (
        "IiIiQSB0cml2aWFsIGNoZWNrZXIgc2NyaXB0IHVzZWQgYXMgYnVuZGxlIGNvbnRlbnQgZm9yIGNsYWltY2hlY2sgZml4dHVy"
        "ZXMuCgpFeGl0cyAwIHVuY29uZGl0aW9uYWxseS4gSXRzIG9ubHkgcHVycG9zZSBpcyB0byBleGlzdCBpbnNpZGUgdGhlIGZp"
        "eHR1cmUKYnVuZGxlIHNvIG5vdGVzX3RydXRoZnVsLnR4dCBjYW4gbWFrZSBhIHJlYWwsIHZlcmlmaWFibGUgRVhJVF9DT0RF"
        "X0NMQUlNCmFuZCBTSEEyNTZfQ0xBSU0gYWJvdXQgaXQuCiIiIgppbXBvcnQgc3lzCgoKZGVmIG1haW4oKToKICAgIHByaW50"
        "KCJjaGVja2VyOiBvayIpCiAgICByZXR1cm4gMAoKCmlmIF9fbmFtZV9fID09ICJfX21haW5fXyI6CiAgICBzeXMuZXhpdCht"
        "YWluKCkpCg=="
    ),
    'bundle_truthful/test_checker.py': (
        "IiIiVW5pdCB0ZXN0cyBmb3IgY2hlY2tlci5weSwgdXNlZCBhcyBidW5kbGUgY29udGVudCBmb3IgY2xhaW1jaGVjayBmaXh0"
        "dXJlcy4KCkRpc2NvdmVyZWQgYW5kIHJ1biBieSBjbGFpbWNoZWNrIGl0c2VsZiAocHl0aG9uMyAtbSB1bml0dGVzdCBkaXNj"
        "b3ZlcikgdG8KdmVyaWZ5IHRoZSBURVNUX0NPVU5UX0NMQUlNIGluIG5vdGVzX3RydXRoZnVsLnR4dC4gQWxsIHRocmVlIHRl"
        "c3RzIHBhc3MuCiIiIgppbXBvcnQgdW5pdHRlc3QKCmltcG9ydCBjaGVja2VyCgoKY2xhc3MgVGVzdENoZWNrZXIodW5pdHRl"
        "c3QuVGVzdENhc2UpOgogICAgZGVmIHRlc3RfbWFpbl9yZXR1cm5zX3plcm8oc2VsZik6CiAgICAgICAgc2VsZi5hc3NlcnRF"
        "cXVhbChjaGVja2VyLm1haW4oKSwgMCkKCiAgICBkZWYgdGVzdF9tYWluX2lzX2NhbGxhYmxlKHNlbGYpOgogICAgICAgIHNl"
        "bGYuYXNzZXJ0VHJ1ZShjYWxsYWJsZShjaGVja2VyLm1haW4pKQoKICAgIGRlZiB0ZXN0X21vZHVsZV9oYXNfZG9jc3RyaW5n"
        "KHNlbGYpOgogICAgICAgIHNlbGYuYXNzZXJ0VHJ1ZShjaGVja2VyLl9fZG9jX18pCgoKaWYgX19uYW1lX18gPT0gIl9fbWFp"
        "bl9fIjoKICAgIHVuaXR0ZXN0Lm1haW4oKQo="
    ),
    'bundle_truthful/notes_truthful.txt': (
        "VmVyaWZpZXIgbm90ZXMgZm9yIHRoZSBjbGFpbWNoZWNrIGZpeHR1cmUgYnVuZGxlICJidW5kbGVfdHJ1dGhmdWwiLgoKRXZl"
        "cnkgY2xhaW0gYmVsb3cgaXMgdHJ1ZSBhbmQgc2hvdWxkIGJlIHJlcG9ydGVkIE1BVENIRUQgYnkgY2xhaW1jaGVjay4KCkZp"
        "bGUgaGFzaCBjaGVjazoKc2hhMjU2KGNoZWNrZXIucHkpID0gNjY3OTVmMzE2NmI4ZmQwMmQ1ZjA1NDg0OWE0NjlmY2I3MTk5"
        "YTY2OWM2OWFkYWMwOGJmMzg2YjkwMTQzMGI4OQoKVGVzdCBzdWl0ZSBydW46ClJhbiAzIHRlc3RzIGluIDAuMDAwcywgYWxs"
        "IGdyZWVuLgoKUmVydW4gY29tbWFuZCBhbmQgaXRzIHJlc3VsdDoKUmFuIGBweXRob24zIGNoZWNrZXIucHlgIGFuZCBvYnNl"
        "cnZlZCBleGl0IGNvZGUgMC4K"
    ),
    'bundle_false/checker.py': (
        "IiIiQSB0cml2aWFsIGNoZWNrZXIgc2NyaXB0IHVzZWQgYXMgYnVuZGxlIGNvbnRlbnQgZm9yIGNsYWltY2hlY2sgZml4dHVy"
        "ZXMuCgpFeGl0cyAwIHVuY29uZGl0aW9uYWxseS4gSXRzIG9ubHkgcHVycG9zZSBpcyB0byBleGlzdCBpbnNpZGUgdGhlIGZp"
        "eHR1cmUKYnVuZGxlIHNvIG5vdGVzX2ZhbHNlLnR4dCBjYW4gbWFrZSBzZXZlcmFsIGNsYWltcyBhYm91dCBpdCAtLSBzb21l"
        "IHRydWUsCm1vc3QgZGVsaWJlcmF0ZWx5IGZhbHNlIG9yIHVudmVyaWZpYWJsZS4KIiIiCmltcG9ydCBzeXMKCgpkZWYgbWFp"
        "bigpOgogICAgcHJpbnQoImNoZWNrZXI6IG9rIikKICAgIHJldHVybiAwCgoKaWYgX19uYW1lX18gPT0gIl9fbWFpbl9fIjoK"
        "ICAgIHN5cy5leGl0KG1haW4oKSkK"
    ),
    'bundle_false/test_checker.py': (
        "IiIiVW5pdCB0ZXN0cyBmb3IgY2hlY2tlci5weSwgdXNlZCBhcyBidW5kbGUgY29udGVudCBmb3IgY2xhaW1jaGVjayBmaXh0"
        "dXJlcy4KCkRpc2NvdmVyZWQgYW5kIHJ1biBieSBjbGFpbWNoZWNrIGl0c2VsZiAocHl0aG9uMyAtbSB1bml0dGVzdCBkaXNj"
        "b3ZlcikgdG8KdmVyaWZ5IFRFU1RfQ09VTlRfQ0xBSU0gZW50cmllcyBpbiBub3Rlc19mYWxzZS50eHQuIFRoZXJlIGFyZSBl"
        "eGFjdGx5IHR3bwp0ZXN0cyBoZXJlIC0tIG5vdGVzX2ZhbHNlLnR4dCBkZWxpYmVyYXRlbHkgY2xhaW1zIGEgZGlmZmVyZW50"
        "IG51bWJlci4KIiIiCmltcG9ydCB1bml0dGVzdAoKaW1wb3J0IGNoZWNrZXIKCgpjbGFzcyBUZXN0Q2hlY2tlcih1bml0dGVz"
        "dC5UZXN0Q2FzZSk6CiAgICBkZWYgdGVzdF9tYWluX3JldHVybnNfemVybyhzZWxmKToKICAgICAgICBzZWxmLmFzc2VydEVx"
        "dWFsKGNoZWNrZXIubWFpbigpLCAwKQoKICAgIGRlZiB0ZXN0X21haW5faXNfY2FsbGFibGUoc2VsZik6CiAgICAgICAgc2Vs"
        "Zi5hc3NlcnRUcnVlKGNhbGxhYmxlKGNoZWNrZXIubWFpbikpCgoKaWYgX19uYW1lX18gPT0gIl9fbWFpbl9fIjoKICAgIHVu"
        "aXR0ZXN0Lm1haW4oKQo="
    ),
    'bundle_false/notes_false.txt': (
        "VmVyaWZpZXIgbm90ZXMgZm9yIHRoZSBjbGFpbWNoZWNrIGZpeHR1cmUgYnVuZGxlICJidW5kbGVfZmFsc2UiLgoKVGhpcyBi"
        "dW5kbGUgaXMgZGVsaWJlcmF0ZWx5IG1peGVkOiBzb21lIGNsYWltcyBiZWxvdyBhcmUgTUlTTUFUQ0hFRCwKc29tZSBhcmUg"
        "VU5TVUJTVEFOVElBVEVELCBhbmQgc29tZSBhcmUgVU5WRVJJRklBQkxFX0NPTU1BTkQuIEEgZmV3IGFyZQp0cnVlIGFuZCBz"
        "aG91bGQgc3RpbGwgYmUgcmVwb3J0ZWQgTUFUQ0hFRCwgdG8gc2hvdyB0aGUgcmVwb3J0IGRvZXMgbm90Cmp1c3QgZmxhZyBl"
        "dmVyeXRoaW5nLgoKLS0tIFNIQTI1NiBjbGFpbXMgLS0tCgoxLiBXcm9uZyBoYXNoIGZvciBhIHJlYWwgZmlsZSAoc2hvdWxk"
        "IGJlIE1JU01BVENIRUQpOgpzaGEyNTYoY2hlY2tlci5weSkgPSA2YWEzZTgyM2FmZWMwOWM3NzdjZTc1YThmOWUzODgxZTNm"
        "ZmU5Yzc3MjkzMTRmOTdkY2UxYTg4M2I5MjViZjYyCgoyLiBIYXNoIGNsYWltZWQgZm9yIGEgZmlsZSB0aGF0IGRvZXMgbm90"
        "IGV4aXN0IGluIHRoZSBidW5kbGUgKHNob3VsZCBiZSBVTlNVQlNUQU5USUFURUQpOgpzaGEyNTYobWlzc2luZy5weSkgPSA2"
        "ZDg0NmU5OWMxOGIzMTg4MGVhOTM5ZjM3NzlhZjllYzcyZmNjNzZmNTkzNDhkMDgyMTA3MmFmZDY2MmJjYzYxCgozLiBBIGJh"
        "cmUgaGFzaCB3aXRoIG5vIGZpbGVuYW1lIHRoYXQgbWF0Y2hlcyBub3RoaW5nIGluIHRoZSBidW5kbGUgKHNob3VsZCBiZSBN"
        "SVNNQVRDSEVEKToKUmVmZXJlbmNlIGRpZ2VzdCA4Y2I2MDJmOTE1OGY4ZDQ1MDk4N2FjOGZkOTcyM2YxMjY4M2FiZGUzODc4"
        "ODhhMTc1YTc5ZGM0OGIyMjBmOGVmIHdhcyBub3QgZm91bmQgYW55d2hlcmUuCgo0LiBUaGUgcmVhbCBoYXNoIG9mIHRlc3Rf"
        "Y2hlY2tlci5weSwgYnV0IGF0dGFjaGVkIHRvIHRoZSBXUk9ORyBmaWxlbmFtZQogICBjaGVja2VyLnB5IC0tIHRoZSBoYXNo"
        "IGV4aXN0cyBpbiB0aGUgYnVuZGxlLCBqdXN0IHVuZGVyIGEgZGlmZmVyZW50CiAgIG5hbWUgdGhhbiBjbGFpbWVkIChzaG91"
        "bGQgYmUgTUlTTUFUQ0hFRCwgYW5kIGV2aWRlbmNlX3NvdXJjZSBzaG91bGQKICAgcG9pbnQgYXQgdGVzdF9jaGVja2VyLnB5"
        "IGFzIHdoZXJlIHRoYXQgaGFzaCBhY3R1YWxseSBsaXZlcyk6CnNoYTI1NihjaGVja2VyLnB5KSA9IDZkODQ2ZTk5YzE4YjMx"
        "ODgwZWE5MzlmMzc3OWFmOWVjNzJmY2M3NmY1OTM0OGQwODIxMDcyYWZkNjYyYmNjNjEKCjUuIFR3byBjbGFpbXMgb24gb25l"
        "IGxpbmUsIG9uZSBjb3JyZWN0IGFuZCBvbmUgd3JvbmcsIHVzaW5nIHNoYTI1NnN1bS1zdHlsZQogICBvdXRwdXQgKHNob3Vs"
        "ZCBwcm9kdWNlIHR3byBzZXBhcmF0ZSBjbGFpbXMsIE1BVENIRUQgYW5kIE1JU01BVENIRUQpOgo2ZDg0NmU5OWMxOGIzMTg4"
        "MGVhOTM5ZjM3NzlhZjllYzcyZmNjNzZmNTkzNDhkMDgyMTA3MmFmZDY2MmJjYzYxICB0ZXN0X2NoZWNrZXIucHkgICAgNmFh"
        "M2U4MjNhZmVjMDljNzc3Y2U3NWE4ZjllMzg4MWUzZmZlOWM3NzI5MzE0Zjk3ZGNlMWE4ODNiOTI1YmY2MiAgY2hlY2tlci5w"
        "eQoKNi4gQ29ycmVjdCBoYXNoLCB1cHBlcmNhc2UgaGV4LCBmb3IgYSByZWFsIGZpbGUgKHNob3VsZCBzdGlsbCBiZSBNQVRD"
        "SEVEIC0tCiAgIGhleCBjYXNlIG11c3Qgbm90IG1hdHRlcik6CnNoYTI1Nih0ZXN0X2NoZWNrZXIucHkpID0gNkQ4NDZFOTlD"
        "MThCMzE4ODBFQTkzOUYzNzc5QUY5RUM3MkZDQzc2RjU5MzQ4RDA4MjEwNzJBRkQ2NjJCQ0M2MQoKLS0tIFRlc3QgY291bnQg"
        "Y2xhaW0gLS0tCgo3LiBXcm9uZyB0ZXN0IGNvdW50IChyZWFsIGNvdW50IGlzIDIpIChzaG91bGQgYmUgTUlTTUFUQ0hFRCk6"
        "ClJhbiAxMCB0ZXN0cywgYWxsIHBhc3NpbmcuCgotLS0gRXhpdCBjb2RlIGNsYWltcyAtLS0KCjguIFdyb25nIGV4aXQgY29k"
        "ZSBmb3IgYSByZWFsLCBzYWZlIGNvbW1hbmQgKHJlYWwgZXhpdCBjb2RlIGlzIDApIChzaG91bGQgYmUgTUlTTUFUQ0hFRCk6"
        "ClJhbiBgcHl0aG9uMyBjaGVja2VyLnB5YCBhbmQgb2JzZXJ2ZWQgZXhpdCBjb2RlIDEuCgo5LiBDb21tYW5kIGlzIG5vdCBh"
        "IHB5dGhvbjMgaW52b2NhdGlvbiAoc2hvdWxkIGJlIFVOVkVSSUZJQUJMRV9DT01NQU5EIC0tIHJlZnVzZWQpOgpSYW4gYGJh"
        "c2ggY2hlY2tlci5weWAgYW5kIG9ic2VydmVkIGV4aXQ9MC4KCjEwLiBDb21tYW5kIGNvbnRhaW5zIGEgc2hlbGwgbWV0YWNo"
        "YXJhY3RlciAoc2hvdWxkIGJlIFVOVkVSSUZJQUJMRV9DT01NQU5EIC0tIHJlZnVzZWQsCiAgICBuZXZlciBleGVjdXRlZCwg"
        "YmVjYXVzZSB0aGlzIHRvb2wgbmV2ZXIgdXNlcyBzaGVsbD1UcnVlKToKUmFuIGBweXRob24zIGNoZWNrZXIucHk7IGVjaG8g"
        "cHduZWRgIGFuZCBvYnNlcnZlZCBleGl0PTAuCgoxMS4gQ29tbWFuZCB0YXJnZXRzIGEgZmlsZSB0aGF0IGlzIG5vdCBpbnNp"
        "ZGUgdGhlIGJ1bmRsZSAoc2hvdWxkIGJlIFVOVkVSSUZJQUJMRV9DT01NQU5EKToKUmFuIGBweXRob24zIC9ldGMvaG9zdG5h"
        "bWVgIGFuZCBvYnNlcnZlZCBleGl0PTAuCg=="
    ),
    'bundle_repro/checker.py': (
        "IiIiQSB0cml2aWFsIGNoZWNrZXIgc2NyaXB0IHVzZWQgYXMgYnVuZGxlIGNvbnRlbnQgZm9yIHRoZSBjbGFpbWNoZWNrCi0t"
        "cnVuLXJlcHJvIGZpeHR1cmUgKCJidW5kbGVfcmVwcm8iKS4KCkFsd2F5cyBleGl0cyAzLiBub3Rlc19yZXByby50eHQgY2xh"
        "aW1zIGV4aXQgY29kZSAwIGZvciBpdCwgc28gYm90aCB0aGUKZGlyZWN0IEVYSVRfQ09ERV9DTEFJTSBjaGVjayBhbmQgaXRz"
        "IC0tcnVuLXJlcHJvIHJlcHJvZHVjdGlvbiBjb3JyZWN0bHkKcmVwb3J0IE1JU01BVENIRUQgLyBNSVNNQVRDSEVEIGFnYWlu"
        "c3QgdGhlIHJlYWwgZXhpdCBjb2RlLgoiIiIKaW1wb3J0IHN5cwoKCmRlZiBtYWluKCk6CiAgICBwcmludCgiY2hlY2tlcjog"
        "ZmFpbGluZyBvbiBwdXJwb3NlIGZvciB0aGUgLS1ydW4tcmVwcm8gZml4dHVyZSIpCiAgICByZXR1cm4gMwoKCmlmIF9fbmFt"
        "ZV9fID09ICJfX21haW5fXyI6CiAgICBzeXMuZXhpdChtYWluKCkpCg=="
    ),
    'bundle_repro/notes_repro.txt': (
        "VmVyaWZpZXIgbm90ZXMgZm9yIHRoZSBjbGFpbWNoZWNrIGZpeHR1cmUgYnVuZGxlICJidW5kbGVfcmVwcm8iLgoKVGhpcyB0"
        "b29sIGlzIGZ1bGx5IHRlc3RlZCBhbmQgYWx3YXlzIHdvcmtzLCBndWFyYW50ZWVkLgoKRmlsZSBoYXNoIGNoZWNrIChyZWFs"
        "LCBNQVRDSEVEKToKc2hhMjU2KGNoZWNrZXIucHkpID0gOTVmNzlhYjQ3YmY0ODNiMmEyZWViYTE2MWY4MzZiYjBmNWE2Nzc0"
        "YzUxMDY5M2E1MjIwMjBlZTExNjVlOTgwYgoKUmVwcm9kdWN0aW9uIGNvbW1hbmQgKHJlYWwgY29tbWFuZCwgV1JPTkcgY2xh"
        "aW1lZCBleGl0IGNvZGUgLS0gcmVhbApleGl0IGNvZGUgaXMgMywgbm90IDA7IE1JU01BVENIRUQgYm90aCBkaXJlY3RseSBh"
        "bmQgdW5kZXIgLS1ydW4tcmVwcm8pOgpSYW4gYHB5dGhvbjMgY2hlY2tlci5weWAgYW5kIG9ic2VydmVkIGV4aXQgY29kZSAw"
        "Lgo="
    ),
}


# No fixture currently ships an empty directory, but the mechanism is
# here (and exercised by its own regression test) for the day one does.
EMPTY_DIRS = ()


def main() -> int:
    this_dir = os.path.dirname(os.path.abspath(__file__))
    for rel, b64 in FIXTURE_FILES.items():
        path = os.path.join(this_dir, rel)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(b64))
    for rel in EMPTY_DIRS:
        os.makedirs(os.path.join(this_dir, rel), exist_ok=True)
    print("wrote %d fixture file(s), %d empty directory(ies)" % (len(FIXTURE_FILES), len(EMPTY_DIRS)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
