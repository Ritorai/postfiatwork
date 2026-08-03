"""Focused tests for the canonical-dump total-order tiebreak in _finding_sort_key.

Before the change the key ended at `detail`. Two findings agreeing on every
listed field compared equal, so their relative order fell back to whatever order
they arrived in -- which depends on filesystem walk order and is not guaranteed
stable. Appending canonical_dumps(f) makes the ordering total by construction.

Run:  python3 -m unittest test_consolidate_tiebreak -v
"""

import itertools
import json
import unittest

import consolidate as C


def _f(detail="d", extra="x", tool="toolA", report="r.json",
       task_id="task-1", code="C001", severity="warning"):
    """A finding that ties on every field of the pre-change sort key."""
    return {
        "source_tool": tool,
        "source_report": report,
        "task_id": task_id,
        "code": code,
        "severity": severity,
        "detail": detail,
        "extra": extra,
    }


class TestTiebreakIsTotalOrder(unittest.TestCase):

    def test_key_ends_with_canonical_dump(self):
        f = _f()
        self.assertEqual(C._finding_sort_key(f)[-1], C.canonical_dumps(f))

    def test_records_tying_on_every_leading_field_still_order_deterministically(self):
        a, b, c = _f(extra="aaa"), _f(extra="bbb"), _f(extra="ccc")
        # All three are identical on source_tool/source_report/task_id/code/
        # severity/detail -- the entire pre-change key.
        self.assertEqual(C._finding_sort_key(a)[:-1], C._finding_sort_key(b)[:-1])
        self.assertEqual(C._finding_sort_key(b)[:-1], C._finding_sort_key(c)[:-1])
        results = set()
        for perm in itertools.permutations([a, b, c]):
            ordered = sorted(perm, key=C._finding_sort_key)
            results.add(tuple(x["extra"] for x in ordered))
        self.assertEqual(len(results), 1, "all 6 permutations must agree")
        self.assertEqual(results.pop(), ("aaa", "bbb", "ccc"))

    def test_all_permutations_of_six_tying_records_agree(self):
        recs = [_f(extra="e%d" % i) for i in range(6)]
        expected = [x["extra"] for x in sorted(recs, key=C._finding_sort_key)]
        for perm in itertools.permutations(recs[:5]):
            ordered = sorted(list(perm) + [recs[5]], key=C._finding_sort_key)
            self.assertEqual([x["extra"] for x in ordered], expected)

    def test_tiebreak_never_reorders_records_differing_earlier(self):
        # `detail` differs, so the dump must not override it. "z" sorts after
        # "a" on detail even though its extra field sorts before.
        early = _f(detail="a", extra="zzz")
        late = _f(detail="z", extra="aaa")
        ordered = sorted([late, early], key=C._finding_sort_key)
        self.assertEqual([x["detail"] for x in ordered], ["a", "z"])

    def test_leading_fields_unchanged_by_the_change(self):
        f = _f()
        self.assertEqual(
            C._finding_sort_key(f)[:-1],
            (f["source_tool"], f["source_report"], f["task_id"],
             f["code"], f["severity"], f["detail"]),
        )

    def test_null_task_id_still_maps_to_empty_string(self):
        f = _f()
        f["task_id"] = None
        self.assertEqual(C._finding_sort_key(f)[2], "")

    def test_identical_records_produce_identical_keys(self):
        self.assertEqual(C._finding_sort_key(_f()), C._finding_sort_key(_f()))

    def test_key_is_comparable_and_sortable(self):
        recs = [_f(extra="b"), _f(detail="a"), _f(code="C000")]
        sorted(recs, key=C._finding_sort_key)  # must not raise

    def test_dump_is_canonical_json(self):
        f = _f()
        dump = C._finding_sort_key(f)[-1]
        self.assertEqual(json.loads(dump), f)
        self.assertNotIn(", ", dump)   # tight separators
        self.assertTrue(dump.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
