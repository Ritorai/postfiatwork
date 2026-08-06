#!/usr/bin/env python3
"""make_fixtures.py -- regenerate claim-crosscheck's test fixture tree.

The fixtures under fixtures/ are small, synthetic (tool-dir README.md +
report.json) pairs used by test_crosscheck.py's integration tests: a clean
match, a deliberate contradiction across every supported claim kind, a
missing report, an invalid-JSON report, a non-object-JSON report, a Unicode
file, a CRLF-line-ending README, an empty README, a README with zero
checkable claims, a genuinely empty directory, an ambiguous multi-candidate
discovery case, a tool with no committed JSON at all, a bare directory-name
"accounts for" claim, and a count-shaped number sitting inside a fenced code
block.

Every fixture byte is embedded below as base64 and written with 'wb' in
BINARY mode -- not 'w' text mode -- specifically so CRLF line endings in
crlf_tool/README.md and the exact UTF-8 bytes in unicode_tool/ survive
untouched. A text-mode writer on a CRLF payload silently rewrites it to LF
before the first test ever runs, which is exactly the kind of drift this
tool exists to catch in other people's reports, so it must not happen here.

fixtures/empty_dir_tool/scratch_empty/ is a directory with nothing in it.
os.walk() (and therefore anything built on it, including any report
discovery that scans a tree) never visits an empty directory on its own --
there is no file event to trigger a visit -- so a generator that only
recreates directories implied by file paths would silently drop it. It is
recreated explicitly via EMPTY_DIRS below.

Usage:
    python3 make_fixtures.py                 # (re)write fixtures/ here
    python3 make_fixtures.py --verify         # write to a temp dir, diff -r
                                               # it against the committed
                                               # fixtures/, print the result
"""

import base64
import filecmp
import os
import shutil
import sys
import tempfile


FIXTURE_FILES = {
    'absent_tool/README.md': (
        'IyBhYnNlbnQtdG9vbAoKTm8gSlNPTiByZXBvcnQgaXMgY29tbWl0dGVkIGZvciB0aGlzIHRvb2wg'
        'eWV0LgoKYG0vYS5weWAgY2FycmllcyBhIGZpbmRpbmcuCg=='
    ),
    'ambiguous_tool/README.md': (
        'IyBhbWJpZ3VvdXMtdG9vbAoKU2VlIGByZXBvcnRfYS5qc29uYCBhbmQgYHJlcG9ydF9iLmpzb25g'
        'IGZvciBkZXRhaWxzOyBuZWl0aGVyIGlzIHNpbmdsZWQgb3V0CmFzICJ0aGUgY29tbWl0dGVkIHJl'
        'cG9ydCIsIHNvIGEgcmVhZGVyIGhhcyB0byBndWVzcy4KCmBtL2EucHlgIGNhcnJpZXMgYSBmaW5k'
        'aW5nLgo='
    ),
    'ambiguous_tool/report_a.json': (
        'ewogICJjb25maXJtZWRfbGVha3MiOiBbCiAgICB7CiAgICAgICJjYXRlZ29yeSI6ICJob3N0bmFt'
        'ZSIsCiAgICAgICJmaWxlIjogIm0vYS5weSIsCiAgICAgICJsaW5lIjogMQogICAgfQogIF0KfQo='
    ),
    'ambiguous_tool/report_b.json': (
        'ewogICJjb25maXJtZWRfbGVha3MiOiBbCiAgICB7CiAgICAgICJjYXRlZ29yeSI6ICJob3N0bmFt'
        'ZSIsCiAgICAgICJmaWxlIjogIm0vYi5weSIsCiAgICAgICJsaW5lIjogMgogICAgfQogIF0KfQo='
    ),
    'contradiction_tool/README.md': (
        'IyBjb250cmFkaWN0aW9uLXRvb2wKClRoZSBjb21taXR0ZWQgcmVwb3J0IGlzIGByZXBvcnQuanNv'
        'bmAuCgp8IENhdGVnb3J5IHwgQ29uZmlybWVkIHwKfC0tLXwtLS18CnwgYHRlbXBfZGlyZWN0b3J5'
        'YCB8IDUgfAoKKio5IGNvbmZpcm1lZCBsZWFrcyoqLCA0IG1hdGNoZXMgcmV2aWV3ZWQgYW5kIGRp'
        'c21pc3NlZCBhcyBiZW5pZ24sIGFjcm9zcyA5IGZpbGVzLgoKYGxpYi95LnB5YCBjYXJyaWVzIGEg'
        'ZmluZGluZywgYXQgYGxpYi95LnB5YCBsaW5lIDM6CgpgYGAKc29tZSB0ZXh0CmBgYAoKYGxpYi94'
        'LnB5YCBpcyBhIGBob3N0bmFtZWAgbGVhay4KCmBsaWIvYCBhbG9uZSBhY2NvdW50cyBmb3IgOSBv'
        'ZiB0aGVtLgo='
    ),
    'contradiction_tool/report.json': (
        'ewogICJjb25maXJtZWRfbGVha3MiOiBbCiAgICB7CiAgICAgICJjYXRlZ29yeSI6ICJ0ZW1wX2Rp'
        'cmVjdG9yeSIsCiAgICAgICJmaWxlIjogImxpYi94LnB5IiwKICAgICAgImxpbmUiOiAyMCwKICAg'
        'ICAgIm1hdGNoZWQiOiAicCIKICAgIH0KICBdLAogICJjb3VudHMiOiB7CiAgICAiYmVuaWduIjog'
        'MSwKICAgICJieV9jYXRlZ29yeSI6IHsKICAgICAgImhvc3RuYW1lIjogMCwKICAgICAgInRlbXBf'
        'ZGlyZWN0b3J5IjogMQogICAgfSwKICAgICJjb25maXJtZWQiOiAxCiAgfSwKICAicmV2aWV3ZWRf'
        'YmVuaWduIjogWwogICAgewogICAgICAiY2F0ZWdvcnkiOiAiaG9zdG5hbWUiLAogICAgICAiZmls'
        'ZSI6ICJsaWIveS5weSIsCiAgICAgICJsaW5lIjogMywKICAgICAgIm1hdGNoZWQiOiAicSIsCiAg'
        'ICAgICJ2ZXJkaWN0IjogImJlbmlnbiIKICAgIH0KICBdCn0K'
    ),
    'crlf_tool/README.md': (
        'IyBjcmxmLXRvb2wNCg0KVGhlIGNvbW1pdHRlZCByZXBvcnQgaXMgYHJlcG9ydC5qc29uYC4NCg0K'
        'fCBDYXRlZ29yeSB8IENvbmZpcm1lZCB8DQp8LS0tfC0tLXwNCnwgYGhvc3RuYW1lYCB8IDEgfA0K'
        'DQpgd2luL2EucHlgIGNhcnJpZXMgYSBmaW5kaW5nLCBhdCBgd2luL2EucHlgIGxpbmUgNy4NCg=='
    ),
    'crlf_tool/report.json': (
        'ewogICJjb25maXJtZWRfbGVha3MiOiBbCiAgICB7CiAgICAgICJjYXRlZ29yeSI6ICJob3N0bmFt'
        'ZSIsCiAgICAgICJmaWxlIjogIndpbi9hLnB5IiwKICAgICAgImxpbmUiOiA3LAogICAgICAibWF0'
        'Y2hlZCI6ICJjIgogICAgfQogIF0sCiAgImNvdW50cyI6IHsKICAgICJiZW5pZ24iOiAwLAogICAg'
        'ImJ5X2NhdGVnb3J5IjogewogICAgICAiaG9zdG5hbWUiOiAxCiAgICB9LAogICAgImNvbmZpcm1l'
        'ZCI6IDEKICB9LAogICJyZXZpZXdlZF9iZW5pZ24iOiBbXQp9Cg=='
    ),
    'empty_dir_tool/README.md': (
        'IyBlbXB0eS1kaXItdG9vbAoKVGhlIGNvbW1pdHRlZCByZXBvcnQgaXMgYHJlcG9ydC5qc29uYC4K'
        'CmBvdXQvYCBhbG9uZSBhY2NvdW50cyBmb3IgMCBvZiB0aGVtLgo='
    ),
    'empty_dir_tool/report.json': (
        'ewogICJjb25maXJtZWRfbGVha3MiOiBbXSwKICAicmV2aWV3ZWRfYmVuaWduIjogW10KfQo='
    ),
    'empty_readme_tool/README.md': (
        ''
    ),
    'empty_readme_tool/report.json': (
        'ewogICJjb25maXJtZWRfbGVha3MiOiBbXSwKICAicmV2aWV3ZWRfYmVuaWduIjogW10KfQo='
    ),
    'fence_tool/README.md': (
        'IyBmZW5jZS10b29sCgpUaGUgY29tbWl0dGVkIHJlcG9ydCBpcyBgcmVwb3J0Lmpzb25gLgoKYGBg'
        'ClRoaXMgYmxvY2sgY2xhaW1zIDk5OSBjb25maXJtZWQgbGVha3MgYnV0IGl0IGlzIGV4YW1wbGUg'
        'c2hlbGwgb3V0cHV0LCBub3QKcHJvc2UsIGFuZCBzaG91bGQgbm90IGJlIHJlYWQgYXMgYSByZWFs'
        'IGNsYWltIGJ5IGEgY2FyZWxlc3MgZXh0cmFjdG9yLgpgYGAKCioqMSBjb25maXJtZWQgbGVha3Mq'
        'KiwgMCBtYXRjaGVzIHJldmlld2VkIGFuZCBkaXNtaXNzZWQgYXMgYmVuaWduLCBhY3Jvc3MgMSBm'
        'aWxlcy4K'
    ),
    'fence_tool/report.json': (
        'ewogICJjb25maXJtZWRfbGVha3MiOiBbCiAgICB7CiAgICAgICJjYXRlZ29yeSI6ICJob3N0bmFt'
        'ZSIsCiAgICAgICJmaWxlIjogInEvb25lLnB5IiwKICAgICAgImxpbmUiOiAxCiAgICB9CiAgXSwK'
        'ICAiY291bnRzIjogewogICAgImJlbmlnbiI6IDAsCiAgICAiY29uZmlybWVkIjogMQogIH0sCiAg'
        'InJldmlld2VkX2JlbmlnbiI6IFtdCn0K'
    ),
    'invalid_json_tool/README.md': (
        'IyBpbnZhbGlkLWpzb24tdG9vbAoKVGhlIGNvbW1pdHRlZCByZXBvcnQgaXMgYHJlcG9ydC5qc29u'
        'YC4KCmBzcmMvei5weWAgY2FycmllcyBhIGZpbmRpbmcuCg=='
    ),
    'invalid_json_tool/report.json': (
        'e25vdCB2YWxpZCBqc29uLCws'
    ),
    'match_tool/README.md': (
        'IyBtYXRjaC10b29sCgpUaGUgY29tbWl0dGVkIHJlcG9ydCBpcyBgcmVwb3J0Lmpzb25gLgoKfCBD'
        'YXRlZ29yeSB8IENvbmZpcm1lZCB8CnwtLS18LS0tfAp8IGBob3N0bmFtZWAgfCAxIHwKfCBgdGVt'
        'cF9kaXJlY3RvcnlgIHwgMSB8CgoqKjIgY29uZmlybWVkIGxlYWtzKiosIDEgbWF0Y2hlcyByZXZp'
        'ZXdlZCBhbmQgZGlzbWlzc2VkIGFzIGJlbmlnbiwgYWNyb3NzIDIgZmlsZXMuCgpgc3JjL2EucHlg'
        'IGNhcnJpZXMgYSBmaW5kaW5nLCBhdCBgc3JjL2EucHlgIGxpbmUgMTA6CgpgYGAKZXhhbXBsZSBt'
        'YXRjaGVkIHRleHQKYGBgCgpgc3JjL2EucHlgIGlzIGEgYGhvc3RuYW1lYCBsZWFrLgoKYHNyYy9g'
        'IGFsb25lIGFjY291bnRzIGZvciAyIG9mIHRoZW0uCg=='
    ),
    'match_tool/report.json': (
        'ewogICJjb25maXJtZWRfbGVha3MiOiBbCiAgICB7CiAgICAgICJjYXRlZ29yeSI6ICJob3N0bmFt'
        'ZSIsCiAgICAgICJmaWxlIjogInNyYy9hLnB5IiwKICAgICAgImxpbmUiOiAxMCwKICAgICAgIm1h'
        'dGNoZWQiOiAieCIKICAgIH0sCiAgICB7CiAgICAgICJjYXRlZ29yeSI6ICJ0ZW1wX2RpcmVjdG9y'
        'eSIsCiAgICAgICJmaWxlIjogInNyYy9iLnB5IiwKICAgICAgImxpbmUiOiA1LAogICAgICAibWF0'
        'Y2hlZCI6ICJ5IgogICAgfQogIF0sCiAgImNvdW50cyI6IHsKICAgICJiZW5pZ24iOiAxLAogICAg'
        'ImJ5X2NhdGVnb3J5IjogewogICAgICAiaG9zdG5hbWUiOiAxLAogICAgICAidGVtcF9kaXJlY3Rv'
        'cnkiOiAxCiAgICB9LAogICAgImNvbmZpcm1lZCI6IDIKICB9LAogICJyZXZpZXdlZF9iZW5pZ24i'
        'OiBbCiAgICB7CiAgICAgICJjYXRlZ29yeSI6ICJob3N0bmFtZSIsCiAgICAgICJmaWxlIjogInNy'
        'Yy9jLnB5IiwKICAgICAgImxpbmUiOiAxLAogICAgICAibWF0Y2hlZCI6ICJ6IiwKICAgICAgInZl'
        'cmRpY3QiOiAiYmVuaWduIgogICAgfQogIF0KfQo='
    ),
    'missing_report_tool/README.md': (
        'IyBtaXNzaW5nLXJlcG9ydC10b29sCgpUaGUgY29tbWl0dGVkIHJlcG9ydCBpcyBgcmVwb3J0Lmpz'
        'b25gLgoKYHNyYy96LnB5YCBjYXJyaWVzIGEgZmluZGluZy4K'
    ),
    'no_claims_tool/README.md': (
        'IyBuby1jbGFpbXMtdG9vbAoKVGhpcyB0b29sIGRvZXMgaW50ZXJlc3RpbmcgdGhpbmdzIGJ1dCB0'
        'aGlzIHBhcmFncmFwaCBuYW1lcyBubyBmaWxlcywgbm8KY2F0ZWdvcmllcyBhbmQgbm8gY291bnRz'
        'LiBJdCBqdXN0IHRhbGtzIGFib3V0IGRlc2lnbiBwaGlsb3NvcGh5IGluIHRoZQphYnN0cmFjdCwg'
        'dGhlIHdheSBhIG1pc3Npb24gc3RhdGVtZW50IHdvdWxkLCB3aXRob3V0IGV2ZXIgcXVvdGluZyBh'
        'IHBhdGggaW4KYmFja3RpY2tzIG9yIGNpdGluZyBhIHNwZWNpZmljIG51bWJlciB0aWVkIHRvIGEg'
        'cmVwb3J0IGZpZWxkLgo='
    ),
    'no_claims_tool/report.json': (
        'ewogICJjb25maXJtZWRfbGVha3MiOiBbXSwKICAicmV2aWV3ZWRfYmVuaWduIjogW10KfQo='
    ),
    'not_object_tool/README.md': (
        'IyBub3Qtb2JqZWN0LXRvb2wKClRoZSBjb21taXR0ZWQgcmVwb3J0IGlzIGByZXBvcnQuanNvbmAu'
        'Cgpgc3JjL3oucHlgIGNhcnJpZXMgYSBmaW5kaW5nLgo='
    ),
    'not_object_tool/report.json': (
        'WzEsIDIsIDNdCg=='
    ),
    'prefix_tool/README.md': (
        'IyBwcmVmaXgtdG9vbAoKVGhlIGNvbW1pdHRlZCByZXBvcnQgaXMgYHJlcG9ydC5qc29uYC4KCmB3'
        'ZWFrLXRoaW5nYCBhbG9uZSBhY2NvdW50cyBmb3IgMTIgb2YgdGhlbS4K'
    ),
    'prefix_tool/report.json': (
        'ewogICJjb25maXJtZWRfbGVha3MiOiBbCiAgICB7CiAgICAgICJjYXRlZ29yeSI6ICJob3N0bmFt'
        'ZSIsCiAgICAgICJmaWxlIjogIndlYWstdGhpbmcvb25lLnR4dCIsCiAgICAgICJsaW5lIjogMQog'
        'ICAgfSwKICAgIHsKICAgICAgImNhdGVnb3J5IjogImhvc3RuYW1lIiwKICAgICAgImZpbGUiOiAi'
        'd2Vhay10aGluZy90d28udHh0IiwKICAgICAgImxpbmUiOiAyCiAgICB9LAogICAgewogICAgICAi'
        'Y2F0ZWdvcnkiOiAidGVtcF9kaXJlY3RvcnkiLAogICAgICAiZmlsZSI6ICJ3ZWFrLXRoaW5nL3Ro'
        'cmVlLnR4dCIsCiAgICAgICJsaW5lIjogMwogICAgfQogIF0sCiAgInJldmlld2VkX2JlbmlnbiI6'
        'IFtdCn0K'
    ),
    'unicode_tool/README.md': (
        'IyB1bmljb2RlLXRvb2wg4piDCgpUaGUgY29tbWl0dGVkIHJlcG9ydCBpcyBgcmVwb3J0Lmpzb25g'
        'LgoKfCBDYXRlZ29yeSB8IENvbmZpcm1lZCB8CnwtLS18LS0tfAp8IGBob3N0bmFtZWAgfCAxIHwK'
        'CioqMSBjb25maXJtZWQgbGVha3MqKiwgMCBtYXRjaGVzIHJldmlld2VkIGFuZCBkaXNtaXNzZWQg'
        'YXMgYmVuaWduLCBhY3Jvc3MgMSBmaWxlcy4KCmBkb2NzL8OpdMOpLm1kYCBjYXJyaWVzIGEgZmlu'
        'ZGluZywgYXQgYGRvY3Mvw6l0w6kubWRgIGxpbmUgMy4KCmBkb2NzL8OpdMOpLm1kYCBpcyBhIGBo'
        'b3N0bmFtZWAgbGVhay4K'
    ),
    'unicode_tool/report.json': (
        'ewogICJjb25maXJtZWRfbGVha3MiOiBbCiAgICB7CiAgICAgICJjYXRlZ29yeSI6ICJob3N0bmFt'
        'ZSIsCiAgICAgICJmaWxlIjogImRvY3MvXHUwMGU5dFx1MDBlOS5tZCIsCiAgICAgICJsaW5lIjog'
        'MywKICAgICAgIm1hdGNoZWQiOiAiXHUyNjAzIHNub3dtYW4iCiAgICB9CiAgXSwKICAiY291bnRz'
        'IjogewogICAgImJlbmlnbiI6IDAsCiAgICAiYnlfY2F0ZWdvcnkiOiB7CiAgICAgICJob3N0bmFt'
        'ZSI6IDEKICAgIH0sCiAgICAiY29uZmlybWVkIjogMQogIH0sCiAgInJldmlld2VkX2JlbmlnbiI6'
        'IFtdCn0K'
    ),
}

EMPTY_DIRS = ['empty_dir_tool/scratch_empty']


def write_all(dest_root):
    """Write every fixture file (binary mode) and recreate every empty
    directory under dest_root. Returns the number of files written."""
    for rel in sorted(FIXTURE_FILES):
        full = os.path.join(dest_root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        data = base64.b64decode(FIXTURE_FILES[rel])
        with open(full, "wb") as fh:
            fh.write(data)
    for rel in EMPTY_DIRS:
        os.makedirs(os.path.join(dest_root, rel), exist_ok=True)
    return len(FIXTURE_FILES)


def _dir_diff(a, b, path=""):
    cmp = filecmp.dircmp(a, b)
    problems = []
    problems += ["only in %s: %s/%s" % (a, path, n) for n in cmp.left_only]
    problems += ["only in %s: %s/%s" % (b, path, n) for n in cmp.right_only]
    problems += ["differs: %s/%s" % (path, n) for n in cmp.diff_files]
    for sub in cmp.common_dirs:
        problems += _dir_diff(os.path.join(a, sub), os.path.join(b, sub),
                               path + "/" + sub)
    return problems


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    here = os.path.dirname(os.path.abspath(__file__))
    if "--verify" in argv:
        committed = os.path.join(here, "fixtures")
        tmp = tempfile.mkdtemp(prefix="make_fixtures_verify_")
        try:
            n = write_all(tmp)
            problems = _dir_diff(committed, tmp)
            if problems:
                print("VERIFY FAILED: regenerated tree differs from "
                      "committed fixtures/:")
                for p in problems:
                    print("  " + p)
                return 1
            print("VERIFY OK: %d fixture files, %d empty dir(s), "
                  "byte-identical to committed fixtures/." % (n,
                                                                len(EMPTY_DIRS)))
            return 0
        finally:
            shutil.rmtree(tmp)
    else:
        dest = os.path.join(here, "fixtures")
        n = write_all(dest)
        print("wrote %d fixture files and %d empty dir(s) under %s" % (
            n, len(EMPTY_DIRS), dest))
        return 0


if __name__ == "__main__":
    sys.exit(main())
