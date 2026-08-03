"""Focused tests for the canonical-dump total-order tiebreak in sort_entries.

Before the change the key was (pointer, code, message). Two entries agreeing on
all three compared equal and fell back to input order. Appending canonical(e)
makes the ordering total.

Run:  python3 -m unittest test_schema_check_tiebreak -v
"""

import itertools
import json
import unittest

import schema_check as S


def _e(pointer="/a/0", code="TYPE", message="m", extra="x"):
    return {"pointer": pointer, "code": code, "message": message, "extra": extra}


class TestSortEntriesTiebreak(unittest.TestCase):

    def test_entries_tying_on_all_three_fields_order_deterministically(self):
        a, b, c = _e(extra="aaa"), _e(extra="bbb"), _e(extra="ccc")
        results = set()
        for perm in itertools.permutations([a, b, c]):
            ordered = S.sort_entries(list(perm))
            results.add(tuple(x["extra"] for x in ordered))
        self.assertEqual(len(results), 1)
        self.assertEqual(results.pop(), ("aaa", "bbb", "ccc"))

    def test_all_permutations_of_four_tying_entries_agree(self):
        recs = [_e(extra="e%d" % i) for i in range(4)]
        expected = [x["extra"] for x in S.sort_entries(list(recs))]
        for perm in itertools.permutations(recs):
            self.assertEqual([x["extra"] for x in S.sort_entries(list(perm))], expected)

    def test_pointer_still_dominates(self):
        first = _e(pointer="/a", extra="zzz")
        second = _e(pointer="/b", extra="aaa")
        ordered = S.sort_entries([second, first])
        self.assertEqual([x["pointer"] for x in ordered], ["/a", "/b"])

    def test_code_still_dominates_extra(self):
        first = _e(code="AAA", extra="zzz")
        second = _e(code="ZZZ", extra="aaa")
        ordered = S.sort_entries([second, first])
        self.assertEqual([x["code"] for x in ordered], ["AAA", "ZZZ"])

    def test_message_still_dominates_extra(self):
        first = _e(message="aaa", extra="zzz")
        second = _e(message="zzz", extra="aaa")
        ordered = S.sort_entries([second, first])
        self.assertEqual([x["message"] for x in ordered], ["aaa", "zzz"])

    def test_returns_a_new_list_and_preserves_contents(self):
        recs = [_e(extra="b"), _e(extra="a")]
        out = S.sort_entries(recs)
        self.assertIsNot(out, recs)
        self.assertCountEqual(out, recs)

    def test_empty_and_single(self):
        self.assertEqual(S.sort_entries([]), [])
        one = [_e()]
        self.assertEqual(S.sort_entries(one), one)

    def test_canonical_is_deterministic_regardless_of_key_insertion_order(self):
        a = {"pointer": "/p", "code": "C", "message": "m", "extra": "x"}
        b = {"extra": "x", "message": "m", "code": "C", "pointer": "/p"}
        self.assertEqual(S.canonical(a), S.canonical(b))


if __name__ == "__main__":
    unittest.main()
