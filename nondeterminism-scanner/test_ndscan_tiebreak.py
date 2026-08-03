"""Focused tests for the canonical-dump total-order tiebreak in Finding.sort_key.

sort_key() is used for BOTH ordering and de-duplication, so the tiebreak had to
be proven not to change dedup. severity is RULE_SEVERITY[rule_id] -- a pure
function of rule_id -- so two findings agreeing on the five leading fields
always serialise identically and still collapse to one.

Run:  python3 -m unittest test_ndscan_tiebreak -v
"""

import itertools
import json
import unittest

import ndscan as N


def _mk(rule_id="ND001_WALL_CLOCK", path="a.py", line=1, col=0, detail="d"):
    return N.Finding(rule_id, path, line, col, detail, N.RULE_SEVERITY[rule_id])


class TestSortKeyTiebreak(unittest.TestCase):

    def test_key_ends_with_canonical_dump_of_to_dict(self):
        f = _mk()
        self.assertEqual(f.sort_key()[-1], N.canonical_json(f.to_dict()))

    def test_leading_five_fields_unchanged(self):
        f = _mk()
        self.assertEqual(f.sort_key()[:-1],
                         (f.rule_id, f.path, f.line, f.col, f.detail))

    def test_severity_is_a_pure_function_of_rule_id(self):
        # This is what makes the tiebreak dedup-neutral.
        for rid in N.RULE_SEVERITY:
            self.assertEqual(_mk(rule_id=rid).severity, N.RULE_SEVERITY[rid])

    def test_dedup_still_collapses_identical_findings(self):
        a, b = _mk(), _mk()
        deduped = list({f.sort_key(): f for f in [a, b]}.values())
        self.assertEqual(len(deduped), 1)

    def test_dedup_keeps_genuinely_distinct_findings(self):
        a, b = _mk(detail="one"), _mk(detail="two")
        deduped = list({f.sort_key(): f for f in [a, b]}.values())
        self.assertEqual(len(deduped), 2)

    def test_permuted_inputs_sort_identically(self):
        recs = [_mk(detail="d%d" % i) for i in range(5)]
        expected = [f.detail for f in sorted(recs, key=lambda f: f.sort_key())]
        for perm in itertools.permutations(recs):
            ordered = sorted(perm, key=lambda f: f.sort_key())
            self.assertEqual([f.detail for f in ordered], expected)

    def test_rule_id_still_dominates(self):
        a = _mk(rule_id="ND001_WALL_CLOCK", detail="zzz")
        b = _mk(rule_id="ND006_FLOAT_IN_MONEY", detail="aaa")
        ordered = sorted([b, a], key=lambda f: f.sort_key())
        self.assertEqual([f.rule_id for f in ordered],
                         ["ND001_WALL_CLOCK", "ND006_FLOAT_IN_MONEY"])

    def test_line_number_sorts_numerically_not_lexically(self):
        a, b = _mk(line=2), _mk(line=10)
        ordered = sorted([b, a], key=lambda f: f.sort_key())
        self.assertEqual([f.line for f in ordered], [2, 10])

    def test_dump_round_trips_to_the_finding_dict(self):
        f = _mk()
        self.assertEqual(json.loads(f.sort_key()[-1]), f.to_dict())


if __name__ == "__main__":
    unittest.main()
