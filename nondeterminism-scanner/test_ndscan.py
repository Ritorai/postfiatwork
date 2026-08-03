"""Focused tests for ndscan.py. Stdlib-only (unittest). Run with:

    python3 -m unittest test_ndscan -v

Organized as:
  - Per-rule AST-pattern unit tests (call check_ndXXX directly on ast.parse()
    output) -- the bulk of the suite, covering true positives, documented
    false positives, documented false negatives, and things that must never
    fire (patterns inside strings/comments).
  - Alias-resolution unit tests.
  - Driver-level tests (scan_source/scan_file/scan_root, canonical_json,
    sorting, dedup).
  - CLI/subprocess black-box tests (exit codes, -o/--output, --root,
    --rule, --min-severity, usage errors, byte-identical repeat runs,
    relocation determinism, no-absolute-paths contract).
"""
import ast
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import ndscan


NDSCAN_PATH = os.path.abspath(ndscan.__file__)


def parse(src):
    return ast.parse(textwrap.dedent(src))


def nd001(src):
    tree = parse(src)
    return ndscan.check_nd001(tree, ndscan.build_aliases(tree))


def nd002(src):
    tree = parse(src)
    aliases = ndscan.build_aliases(tree)
    parents = ndscan.build_parent_map(tree)
    return ndscan.check_nd002(tree, aliases, parents)


def nd003(src):
    tree = parse(src)
    return ndscan.check_nd003(tree)


def nd004(src):
    tree = parse(src)
    return ndscan.check_nd004(tree)


def nd005(src):
    tree = parse(src)
    return ndscan.check_nd005(tree, ndscan.build_aliases(tree))


def nd006(src):
    tree = parse(src)
    return ndscan.check_nd006(tree)


def run_cli(args, cwd=None):
    proc = subprocess.run(
        [sys.executable, NDSCAN_PATH] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


# ===========================================================================
# ND001_WALL_CLOCK
# ===========================================================================

class TestND001WallClock(unittest.TestCase):
    def test_datetime_datetime_now_plain_import(self):
        out = nd001("import datetime\nx = datetime.datetime.now()\n")
        self.assertEqual(len(out), 1)
        self.assertIn("datetime.datetime.now", out[0][2])

    def test_datetime_datetime_utcnow(self):
        out = nd001("import datetime\nx = datetime.datetime.utcnow()\n")
        self.assertEqual(len(out), 1)

    def test_datetime_date_today(self):
        out = nd001("import datetime\nx = datetime.date.today()\n")
        self.assertEqual(len(out), 1)

    def test_time_time_plain_import(self):
        out = nd001("import time\nx = time.time()\n")
        self.assertEqual(len(out), 1)

    def test_time_monotonic(self):
        out = nd001("import time\nx = time.monotonic()\n")
        self.assertEqual(len(out), 1)

    def test_from_datetime_import_datetime_bare_now(self):
        out = nd001("from datetime import datetime\nx = datetime.now()\n")
        self.assertEqual(len(out), 1)

    def test_from_datetime_import_datetime_as_alias(self):
        out = nd001("from datetime import datetime as dt\nx = dt.now()\n")
        self.assertEqual(len(out), 1)

    def test_from_datetime_import_date_bare_today(self):
        out = nd001("from datetime import date\nx = date.today()\n")
        self.assertEqual(len(out), 1)

    def test_import_time_as_alias(self):
        out = nd001("import time as t\nx = t.time()\n")
        self.assertEqual(len(out), 1)

    def test_from_time_import_time_bare_call(self):
        out = nd001("from time import time\nx = time()\n")
        self.assertEqual(len(out), 1)

    def test_from_time_import_time_as_alias_bare_call(self):
        out = nd001("from time import time as t2\nx = t2()\n")
        self.assertEqual(len(out), 1)

    def test_multiple_calls_all_flagged(self):
        out = nd001(
            "import datetime, time\n"
            "a = datetime.datetime.now()\n"
            "b = time.time()\n"
            "c = time.monotonic()\n"
        )
        self.assertEqual(len(out), 3)

    def test_pattern_inside_string_not_flagged(self):
        out = nd001('s = "datetime.datetime.now() and time.time()"\n')
        self.assertEqual(out, [])

    def test_pattern_inside_comment_not_flagged(self):
        out = nd001("# datetime.datetime.now() time.time()\nx = 1\n")
        self.assertEqual(out, [])

    def test_os_path_join_not_flagged(self):
        out = nd001("import os\nx = os.path.join('a', 'b')\n")
        self.assertEqual(out, [])

    def test_datetime_now_without_call_not_flagged(self):
        # Referencing the bound method without calling it should not flag.
        out = nd001("import datetime\nx = datetime.datetime.now\n")
        self.assertEqual(out, [])

    def test_time_perf_counter_not_a_target(self):
        # Documents scope: perf_counter is not one of the six documented
        # ND001 targets, so it is intentionally never flagged.
        out = nd001("import time\nx = time.perf_counter()\n")
        self.assertEqual(out, [])

    def test_datetime_strptime_not_flagged(self):
        out = nd001("import datetime\nx = datetime.datetime.strptime('2020', '%Y')\n")
        self.assertEqual(out, [])

    def test_unresolved_base_not_imported_not_flagged(self):
        # datetime is used but never imported anywhere ndscan can see ->
        # documented false negative: alias map has no entry, base token used
        # literally, "datetime.now" != "datetime.datetime.now".
        out = nd001("x = datetime.now()\n")
        self.assertEqual(out, [])

    def test_shadowed_name_is_a_documented_false_positive(self):
        # `time` parameter shadows the `import time` module; ndscan cannot
        # see the shadowing (file-wide alias map, no scope analysis) so this
        # is flagged even though at runtime `time` here is not the module.
        out = nd001(
            "import time\n"
            "def f(time):\n"
            "    return time.time()\n"
        )
        self.assertEqual(len(out), 1)

    def test_import_datetime_module_then_class_attr_chain(self):
        out = nd001("import datetime as dtmod\nx = dtmod.datetime.now()\n")
        self.assertEqual(len(out), 1)

    def test_call_result_attr_not_a_chain_not_flagged(self):
        out = nd001("x = get_module().now()\n")
        self.assertEqual(out, [])

    def test_line_col_reported(self):
        out = nd001("import time\nx = time.time()\n")
        line, col, detail = out[0]
        self.assertEqual(line, 2)
        self.assertEqual(col, 4)


# ===========================================================================
# ND002_UNSORTED_LISTDIR
# ===========================================================================

class TestND002UnsortedListdir(unittest.TestCase):
    def test_os_listdir_unwrapped_flags(self):
        out = nd002("import os\nx = os.listdir('.')\n")
        self.assertEqual(len(out), 1)

    def test_os_scandir_unwrapped_flags(self):
        out = nd002("import os\nx = os.scandir('.')\n")
        self.assertEqual(len(out), 1)

    def test_os_walk_unwrapped_flags(self):
        out = nd002("import os\nx = os.walk('.')\n")
        self.assertEqual(len(out), 1)

    def test_glob_glob_unwrapped_flags(self):
        out = nd002("import glob\nx = glob.glob('*.py')\n")
        self.assertEqual(len(out), 1)

    def test_sorted_os_listdir_not_flagged(self):
        out = nd002("import os\nx = sorted(os.listdir('.'))\n")
        self.assertEqual(out, [])

    def test_sorted_os_scandir_not_flagged(self):
        out = nd002("import os\nx = sorted(os.scandir('.'), key=lambda e: e.name)\n")
        self.assertEqual(out, [])

    def test_sorted_glob_glob_not_flagged(self):
        out = nd002("import glob\nx = sorted(glob.glob('*.py'))\n")
        self.assertEqual(out, [])

    def test_for_loop_over_sorted_listdir_not_flagged(self):
        out = nd002("import os\nfor f in sorted(os.listdir('.')):\n    pass\n")
        self.assertEqual(out, [])

    def test_aliased_from_import_listdir_unwrapped_flags(self):
        out = nd002("from os import listdir\nx = listdir('.')\n")
        self.assertEqual(len(out), 1)

    def test_aliased_from_import_listdir_sorted_not_flagged(self):
        out = nd002("from os import listdir\nx = sorted(listdir('.'))\n")
        self.assertEqual(out, [])

    def test_aliased_glob_as_alias_unwrapped_flags(self):
        out = nd002("from glob import glob as g\nx = g('*.py')\n")
        self.assertEqual(len(out), 1)

    def test_pattern_inside_string_not_flagged(self):
        out = nd002('s = "os.listdir(\'.\')"\n')
        self.assertEqual(out, [])

    def test_false_positive_sorted_applied_later(self):
        # Documented FP: sorted() is applied one statement later, not
        # immediately -- still flags even though the final value is sorted.
        out = nd002("import os\nitems = os.listdir('.')\nitems = sorted(items)\n")
        self.assertEqual(len(out), 1)

    def test_false_positive_sorted_wraps_list_not_listdir(self):
        # Documented FP: sorted(list(os.listdir(d))) -- sorted wraps list(),
        # not the listdir() call directly.
        out = nd002("import os\nx = sorted(list(os.listdir('.')))\n")
        self.assertEqual(len(out), 1)

    def test_os_path_isdir_not_a_target(self):
        out = nd002("import os\nx = os.path.isdir('.')\n")
        self.assertEqual(out, [])

    def test_os_mkdir_not_a_target(self):
        out = nd002("import os\nos.mkdir('newdir')\n")
        self.assertEqual(out, [])

    def test_multiple_targets_in_one_function_all_flagged(self):
        out = nd002(
            "import os, glob\n"
            "def f(d):\n"
            "    a = os.listdir(d)\n"
            "    b = glob.glob(d)\n"
            "    return a, b\n"
        )
        self.assertEqual(len(out), 2)

    def test_sorted_with_reversed_first_arg_still_ok(self):
        out = nd002("import os\nx = sorted(os.listdir('.'), reverse=True)\n")
        self.assertEqual(out, [])

    def test_sorted_second_positional_arg_not_recognized_as_wrap(self):
        # sorted(other, os.listdir(d)) is nonsensical Python but exercises
        # the "must be args[0]" requirement.
        out = nd002("import os\nx = sorted([], os.listdir('.'))\n")
        self.assertEqual(len(out), 1)

    def test_list_call_wrapping_walk_flags(self):
        out = nd002("import os\nx = list(os.walk('.'))\n")
        self.assertEqual(len(out), 1)

    def test_line_col_reported(self):
        out = nd002("import os\nx = os.listdir('.')\n")
        line, col, detail = out[0]
        self.assertEqual(line, 2)
        self.assertIn("os.listdir", detail)


# ===========================================================================
# ND003_UNORDERED_ITERATION
# ===========================================================================

class TestND003UnorderedIteration(unittest.TestCase):
    def test_set_literal_with_append_flags(self):
        out = nd003("out = []\nfor x in {1, 2, 3}:\n    out.append(x)\n")
        self.assertEqual(len(out), 1)

    def test_set_call_with_append_flags(self):
        out = nd003("out = []\nfor x in set([1, 2, 3]):\n    out.append(x)\n")
        self.assertEqual(len(out), 1)

    def test_frozenset_call_with_append_flags(self):
        out = nd003("out = []\nfor x in frozenset([1, 2, 3]):\n    out.append(x)\n")
        self.assertEqual(len(out), 1)

    def test_dict_literal_with_append_flags(self):
        out = nd003("out = []\nfor k in {'a': 1}:\n    out.append(k)\n")
        self.assertEqual(len(out), 1)

    def test_dict_keys_call_with_append_flags(self):
        out = nd003("d = {}\nout = []\nfor k in d.keys():\n    out.append(k)\n")
        self.assertEqual(len(out), 1)

    def test_dict_values_call_with_append_flags(self):
        out = nd003("d = {}\nout = []\nfor v in d.values():\n    out.append(v)\n")
        self.assertEqual(len(out), 1)

    def test_dict_items_call_with_extend_flags(self):
        out = nd003("d = {}\nout = []\nfor kv in d.items():\n    out.extend(kv)\n")
        self.assertEqual(len(out), 1)

    def test_set_with_add_to_other_set_flags(self):
        out = nd003("other = set()\nfor x in {1, 2}:\n    other.add(x)\n")
        self.assertEqual(len(out), 1)

    def test_dict_items_with_update_flags(self):
        out = nd003("acc = {}\nfor k in {}.items():\n    acc.update({k: 1})\n")
        self.assertEqual(len(out), 1)

    def test_set_with_list_augassign_flags(self):
        out = nd003("out = []\nfor x in {1, 2}:\n    out += [x]\n")
        self.assertEqual(len(out), 1)

    def test_set_with_tuple_augassign_flags(self):
        out = nd003("out = ()\nfor x in {1, 2}:\n    out += (x,)\n")
        self.assertEqual(len(out), 1)

    def test_sorted_wrapped_set_not_flagged(self):
        out = nd003("out = []\nfor x in sorted({1, 2, 3}):\n    out.append(x)\n")
        self.assertEqual(out, [])

    def test_sorted_wrapped_dict_items_not_flagged(self):
        out = nd003("d = {}\nout = []\nfor k, v in sorted(d.items()):\n    out.append(k)\n")
        self.assertEqual(out, [])

    def test_set_iterated_no_accum_not_flagged(self):
        out = nd003("total = 0\nfor x in {1, 2, 3}:\n    total += x\n")
        self.assertEqual(out, [])

    def test_set_iterated_only_print_not_flagged(self):
        out = nd003("for x in {1, 2, 3}:\n    print(x)\n")
        self.assertEqual(out, [])

    def test_list_literal_iteration_never_flagged(self):
        out = nd003("out = []\nfor x in [1, 2, 3]:\n    out.append(x)\n")
        self.assertEqual(out, [])

    def test_tuple_literal_iteration_never_flagged(self):
        out = nd003("out = []\nfor x in (1, 2, 3):\n    out.append(x)\n")
        self.assertEqual(out, [])

    def test_plain_name_dict_reference_is_documented_false_negative(self):
        # ndscan has no type inference, so `for k in some_dict` (a bare
        # Name, not a dict literal or .keys()/.values()/.items() call) is
        # invisible to this rule even though some_dict really is a dict.
        out = nd003("out = []\nfor k in some_dict:\n    out.append(k)\n")
        self.assertEqual(out, [])

    def test_dict_item_assignment_accum_is_documented_false_negative(self):
        out = nd003("acc = {}\nfor k in {1, 2}:\n    acc[k] = True\n")
        self.assertEqual(out, [])

    def test_helper_function_accum_is_documented_false_negative(self):
        out = nd003("def collect(x):\n    pass\nfor x in {1, 2}:\n    collect(x)\n")
        self.assertEqual(out, [])

    def test_nested_function_def_inside_body_not_counted(self):
        # An append() inside a nested function definition in the loop body
        # is not "the loop body accumulating" on each iteration.
        out = nd003(
            "for x in {1, 2}:\n"
            "    def helper():\n"
            "        out = []\n"
            "        out.append(x)\n"
            "        return out\n"
        )
        self.assertEqual(out, [])

    def test_scalar_augassign_not_counted_even_with_other_append(self):
        out = nd003(
            "out = []\n"
            "total = 0\n"
            "for x in {1, 2}:\n"
            "    total += x\n"
        )
        self.assertEqual(out, [])

    def test_pattern_inside_string_not_flagged(self):
        out = nd003('s = "for x in {1, 2, 3}: out.append(x)"\n')
        self.assertEqual(out, [])

    def test_if_inside_loop_body_still_detected(self):
        out = nd003(
            "out = []\n"
            "for x in {1, 2, 3}:\n"
            "    if x > 1:\n"
            "        out.append(x)\n"
        )
        self.assertEqual(len(out), 1)

    def test_line_col_reported(self):
        out = nd003("out = []\nfor x in {1, 2}:\n    out.append(x)\n")
        line, col, detail = out[0]
        self.assertEqual(line, 2)
        self.assertIn("set literal", detail)


# ===========================================================================
# ND004_UNSAFE_REPR
# ===========================================================================

class TestND004UnsafeRepr(unittest.TestCase):
    def test_repr_of_name_flags(self):
        out = nd004("x = 1\nrepr(x)\n")
        self.assertEqual(len(out), 1)

    def test_repr_of_attribute_flags(self):
        out = nd004("repr(obj.field)\n")
        self.assertEqual(len(out), 1)

    def test_repr_of_subscript_flags(self):
        out = nd004("repr(d['key'])\n")
        self.assertEqual(len(out), 1)

    def test_repr_of_user_function_call_flags(self):
        out = nd004("repr(make_widget())\n")
        self.assertEqual(len(out), 1)

    def test_repr_of_int_literal_not_flagged(self):
        out = nd004("repr(42)\n")
        self.assertEqual(out, [])

    def test_repr_of_string_literal_not_flagged(self):
        out = nd004("repr('hello')\n")
        self.assertEqual(out, [])

    def test_repr_of_none_not_flagged(self):
        out = nd004("repr(None)\n")
        self.assertEqual(out, [])

    def test_repr_of_list_of_literals_not_flagged(self):
        out = nd004("repr([1, 2, 3])\n")
        self.assertEqual(out, [])

    def test_repr_of_dict_of_literals_not_flagged(self):
        out = nd004("repr({'a': 1, 'b': 2})\n")
        self.assertEqual(out, [])

    def test_repr_of_list_containing_name_flags(self):
        # One non-literal element makes the whole container unsafe.
        out = nd004("x = 1\nrepr([1, x])\n")
        self.assertEqual(len(out), 1)

    def test_repr_of_str_call_not_flagged(self):
        out = nd004("repr(str(x))\n")
        self.assertEqual(out, [])

    def test_repr_of_int_call_not_flagged(self):
        out = nd004("repr(int(x))\n")
        self.assertEqual(out, [])

    def test_repr_of_comparison_not_flagged(self):
        out = nd004("repr(x == y)\n")
        self.assertEqual(out, [])

    def test_repr_of_boolop_not_flagged(self):
        out = nd004("repr(x and y)\n")
        self.assertEqual(out, [])

    def test_repr_of_binop_over_literals_not_flagged(self):
        out = nd004("repr(1 + 2)\n")
        self.assertEqual(out, [])

    def test_repr_of_binop_over_name_flags(self):
        out = nd004("repr(1 + x)\n")
        self.assertEqual(len(out), 1)

    def test_repr_of_unaryop_over_literal_not_flagged(self):
        out = nd004("repr(-1)\n")
        self.assertEqual(out, [])

    def test_fstring_bang_r_of_name_flags(self):
        out = nd004("f'{x!r}'\n")
        self.assertEqual(len(out), 1)

    def test_fstring_bang_r_of_literal_not_flagged(self):
        out = nd004("f'{1!r}'\n")
        self.assertEqual(out, [])

    def test_fstring_no_conversion_not_flagged(self):
        out = nd004("f'{x}'\n")
        self.assertEqual(out, [])

    def test_fstring_bang_s_conversion_not_flagged(self):
        out = nd004("f'{x!s}'\n")
        self.assertEqual(out, [])

    def test_percent_r_of_name_flags(self):
        out = nd004("'%r' % x\n")
        self.assertEqual(len(out), 1)

    def test_percent_s_not_flagged(self):
        out = nd004("'%s' % x\n")
        self.assertEqual(out, [])

    def test_percent_r_literal_string_not_flagged_operand_ignored(self):
        # Coarse heuristic: presence of %r in the format string is enough
        # to flag, regardless of what the operand actually is.
        out = nd004("'%r' % (42,)\n")
        self.assertEqual(len(out), 1)

    def test_pattern_inside_string_not_flagged(self):
        out = nd004('s = "repr(x) and f\'{x!r}\'"\n')
        self.assertEqual(out, [])

    def test_repr_builtin_shadowed_by_user_def_is_documented_false_negative(self):
        # ndscan trusts any Name('repr') Call -- if the user shadows the
        # builtin, ndscan cannot tell.
        out = nd004("def repr(x):\n    return 'safe'\nrepr(some_obj)\n")
        self.assertEqual(len(out), 1)  # still flags: this documents the FN in check_nd004's own logic (it can't know 'repr' isn't the builtin either way, so it always treats bare repr() calls as the rule target)

    def test_repr_with_no_args_not_flagged(self):
        out = nd004("repr()\n")
        self.assertEqual(out, [])

    def test_line_col_reported(self):
        out = nd004("x = 1\nrepr(x)\n")
        line, col, detail = out[0]
        self.assertEqual(line, 2)


# ===========================================================================
# ND005_UNSEEDED_RANDOM
# ===========================================================================

class TestND005UnseededRandom(unittest.TestCase):
    def test_random_random_no_seed_flags(self):
        out = nd005("import random\nx = random.random()\n")
        self.assertEqual(len(out), 1)

    def test_random_choice_no_seed_flags(self):
        out = nd005("import random\nx = random.choice([1, 2])\n")
        self.assertEqual(len(out), 1)

    def test_random_randint_no_seed_flags(self):
        out = nd005("import random\nx = random.randint(1, 10)\n")
        self.assertEqual(len(out), 1)

    def test_random_with_seed_anywhere_in_module_not_flagged(self):
        out = nd005("import random\nrandom.seed(1)\nx = random.random()\n")
        self.assertEqual(out, [])

    def test_seed_after_usage_still_suppresses_documented_false_negative(self):
        # Documented FN: no execution-order analysis, seed() anywhere in
        # the module (even textually after use) suppresses all findings.
        out = nd005("import random\nx = random.random()\nrandom.seed(1)\n")
        self.assertEqual(out, [])

    def test_seed_in_unreachable_branch_still_suppresses_documented_false_negative(self):
        out = nd005(
            "import random\n"
            "if False:\n"
            "    random.seed(1)\n"
            "x = random.random()\n"
        )
        self.assertEqual(out, [])

    def test_secrets_token_hex_always_flagged(self):
        out = nd005("import secrets\nx = secrets.token_hex(8)\n")
        self.assertEqual(len(out), 1)

    def test_secrets_flagged_even_with_random_seed_present(self):
        out = nd005("import random, secrets\nrandom.seed(1)\nx = secrets.token_hex(8)\n")
        self.assertEqual(len(out), 1)

    def test_secrets_choice_always_flagged(self):
        out = nd005("import secrets\nx = secrets.choice([1, 2])\n")
        self.assertEqual(len(out), 1)

    def test_aliased_import_random_as(self):
        out = nd005("import random as r\nx = r.random()\n")
        self.assertEqual(len(out), 1)

    def test_aliased_import_random_as_with_seed(self):
        out = nd005("import random as r\nr.seed(1)\nx = r.random()\n")
        self.assertEqual(out, [])

    def test_from_random_import_random_bare_call(self):
        out = nd005("from random import random\nx = random()\n")
        self.assertEqual(len(out), 1)

    def test_from_random_import_seed_and_random_bare_calls(self):
        out = nd005("from random import seed, random\nseed(1)\nx = random()\n")
        self.assertEqual(out, [])

    def test_from_random_import_randint_as_alias(self):
        out = nd005("from random import randint as ri\nx = ri(1, 10)\n")
        self.assertEqual(len(out), 1)

    def test_random_seed_call_itself_not_flagged(self):
        out = nd005("import random\nrandom.seed(1)\n")
        self.assertEqual(out, [])

    def test_no_random_or_secrets_usage_clean(self):
        out = nd005("import os\nx = os.getcwd()\n")
        self.assertEqual(out, [])

    def test_pattern_inside_string_not_flagged(self):
        out = nd005('s = "random.random() secrets.token_hex(8)"\n')
        self.assertEqual(out, [])

    def test_multiple_random_calls_all_flagged_without_seed(self):
        out = nd005("import random\na = random.random()\nb = random.random()\n")
        self.assertEqual(len(out), 2)

    def test_random_seed_with_different_alias_still_recognized(self):
        out = nd005("from random import seed as sd, random as rnd\nsd(42)\nx = rnd()\n")
        self.assertEqual(out, [])

    def test_line_col_reported(self):
        out = nd005("import random\nx = random.random()\n")
        line, col, detail = out[0]
        self.assertEqual(line, 2)
        self.assertIn("random.random", detail)


# ===========================================================================
# ND006_FLOAT_IN_MONEY
# ===========================================================================

class TestND006FloatInMoney(unittest.TestCase):
    def test_float_literal_assigned_to_amount_flags(self):
        out = nd006("amount = 10.5\n")
        self.assertEqual(len(out), 1)

    def test_float_literal_assigned_to_price_flags(self):
        out = nd006("price = 9.99\n")
        self.assertEqual(len(out), 1)

    def test_float_literal_assigned_to_reward_flags(self):
        out = nd006("reward = 1.0\n")
        self.assertEqual(len(out), 1)

    def test_float_literal_assigned_to_balance_flags(self):
        out = nd006("balance = 100.0\n")
        self.assertEqual(len(out), 1)

    def test_float_literal_assigned_to_total_flags(self):
        out = nd006("total = 5.5\n")
        self.assertEqual(len(out), 1)

    def test_float_literal_assigned_to_payout_flags(self):
        out = nd006("payout = 3.25\n")
        self.assertEqual(len(out), 1)

    def test_float_literal_assigned_to_fee_flags(self):
        out = nd006("fee = 0.5\n")
        self.assertEqual(len(out), 1)

    def test_float_literal_assigned_to_drops_flags(self):
        out = nd006("drops = 12.5\n")
        self.assertEqual(len(out), 1)

    def test_float_literal_case_insensitive(self):
        out = nd006("Amount = 10.5\n")
        self.assertEqual(len(out), 1)

    def test_int_literal_assigned_to_amount_not_flagged(self):
        out = nd006("amount = 10\n")
        self.assertEqual(out, [])

    def test_string_literal_assigned_to_amount_not_flagged(self):
        out = nd006("amount = '10.5'\n")
        self.assertEqual(out, [])

    def test_float_literal_assigned_to_unrelated_name_not_flagged(self):
        out = nd006("ratio = 0.5\n")
        self.assertEqual(out, [])

    def test_float_call_assigned_to_amount_flags(self):
        out = nd006("amount = float(x)\n")
        self.assertEqual(len(out), 1)

    def test_float_call_on_amount_arg_flags(self):
        out = nd006("y = float(amount)\n")
        self.assertEqual(len(out), 1)

    def test_float_call_on_unrelated_arg_not_flagged(self):
        out = nd006("y = float(count)\n")
        self.assertEqual(out, [])

    def test_float_call_arg_and_target_both_match_two_findings(self):
        out = nd006("amount = float(amount)\n")
        self.assertEqual(len(out), 2)

    def test_augassign_float_literal_flags(self):
        out = nd006("total = 0.0\ntotal += 1.5\n")
        # first line: literal assign flags; second line is AugAssign whose
        # value is a float Constant -> also flags (target matches 'total').
        self.assertEqual(len(out), 2)

    def test_annassign_float_literal_flags(self):
        out = nd006("amount: float = 10.5\n")
        self.assertEqual(len(out), 1)

    def test_attribute_target_matching_money_flags(self):
        out = nd006("self.amount = 10.5\n")
        self.assertEqual(len(out), 1)

    def test_substring_match_false_positive_toffee(self):
        # Documented FP: unanchored regex, "toffee" contains "fee".
        out = nd006("toffee = 3.5\n")
        self.assertEqual(len(out), 1)

    def test_substring_match_false_positive_coffee_price(self):
        out = nd006("coffee_price = 3.5\n")
        self.assertEqual(len(out), 1)

    def test_division_producing_float_is_documented_false_negative(self):
        out = nd006("amount = total_cents / 100\n")
        self.assertEqual(out, [])

    def test_subscript_target_is_documented_false_negative(self):
        out = nd006("data['price'] = 9.99\n")
        self.assertEqual(out, [])

    def test_float_arg_to_unrelated_call_not_flagged(self):
        out = nd006("charge(9.99)\n")
        self.assertEqual(out, [])

    def test_pattern_inside_string_not_flagged(self):
        out = nd006('s = "amount = 10.5"\n')
        self.assertEqual(out, [])

    def test_non_money_float_literal_not_flagged(self):
        out = nd006("pi = 3.14159\n")
        self.assertEqual(out, [])

    def test_line_col_reported(self):
        out = nd006("amount = 10.5\n")
        line, col, detail = out[0]
        self.assertEqual(line, 1)
        self.assertIn("amount", detail)


# ===========================================================================
# Alias resolution
# ===========================================================================

class TestBuildAliases(unittest.TestCase):
    def test_plain_import(self):
        aliases = ndscan.build_aliases(parse("import os\n"))
        self.assertEqual(aliases.get("os"), "os")

    def test_import_as(self):
        aliases = ndscan.build_aliases(parse("import time as t\n"))
        self.assertEqual(aliases.get("t"), "time")

    def test_from_import(self):
        aliases = ndscan.build_aliases(parse("from os import listdir\n"))
        self.assertEqual(aliases.get("listdir"), "os.listdir")

    def test_from_import_as(self):
        aliases = ndscan.build_aliases(parse("from os import listdir as ld\n"))
        self.assertEqual(aliases.get("ld"), "os.listdir")

    def test_relative_import_skipped(self):
        aliases = ndscan.build_aliases(parse("from . import foo\n"))
        self.assertNotIn("foo", aliases)

    def test_star_import_skipped(self):
        aliases = ndscan.build_aliases(parse("from os import *\n"))
        self.assertEqual(aliases, {})

    def test_import_inside_function_still_captured_file_wide(self):
        aliases = ndscan.build_aliases(parse("def f():\n    import time\n    return time\n"))
        self.assertEqual(aliases.get("time"), "time")

    def test_multiple_imports_all_captured(self):
        aliases = ndscan.build_aliases(parse("import os\nimport time as t\nfrom glob import glob\n"))
        self.assertEqual(aliases.get("os"), "os")
        self.assertEqual(aliases.get("t"), "time")
        self.assertEqual(aliases.get("glob"), "glob.glob")

    def test_dotted_import_binds_top_name(self):
        aliases = ndscan.build_aliases(parse("import os.path\n"))
        self.assertEqual(aliases.get("os"), "os")


class TestResolveCallTarget(unittest.TestCase):
    def _call_node(self, src):
        tree = parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                return node
        raise AssertionError("no call found")

    def test_simple_attribute_chain(self):
        node = self._call_node("time.time()\n")
        self.assertEqual(ndscan.resolve_call_target(node, {"time": "time"}), "time.time")

    def test_bare_name_call(self):
        node = self._call_node("f()\n")
        self.assertEqual(ndscan.resolve_call_target(node, {"f": "random.random"}), "random.random")

    def test_unresolvable_call_target(self):
        node = self._call_node("get()().attr()\n")
        self.assertIsNone(ndscan.resolve_call_target(node, {}))

    def test_unaliased_head_falls_back_to_literal(self):
        node = self._call_node("foo.bar()\n")
        self.assertEqual(ndscan.resolve_call_target(node, {}), "foo.bar")

    def test_deep_attribute_chain(self):
        node = self._call_node("a.b.c.d()\n")
        self.assertEqual(ndscan.resolve_call_target(node, {"a": "pkg"}), "pkg.b.c.d")


# ===========================================================================
# Driver-level: scan_source / scan_file / canonical_json / sorting / dedup
# ===========================================================================

class TestScanSource(unittest.TestCase):
    def test_syntax_error_raises(self):
        with self.assertRaises(SyntaxError):
            ndscan.scan_source("def f(:\n", "bad.py", list(ndscan.RULE_IDS))

    def test_empty_source_no_findings(self):
        findings = ndscan.scan_source("", "empty.py", list(ndscan.RULE_IDS))
        self.assertEqual(findings, [])

    def test_restricting_rules_limits_findings(self):
        src = "import random\nrandom.random()\namount = 10.5\n"
        all_findings = ndscan.scan_source(src, "x.py", list(ndscan.RULE_IDS))
        only_nd006 = ndscan.scan_source(src, "x.py", ["ND006_FLOAT_IN_MONEY"])
        self.assertTrue(len(all_findings) > len(only_nd006))
        self.assertTrue(all(f.rule_id == "ND006_FLOAT_IN_MONEY" for f in only_nd006))


class TestScanFile(unittest.TestCase):
    def test_syntax_error_becomes_error_entry(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.py"
            p.write_text("def f(:\n", encoding="utf-8")
            findings, err = ndscan.scan_file(str(p), "bad.py", list(ndscan.RULE_IDS))
            self.assertEqual(findings, [])
            self.assertIsNotNone(err)
            self.assertIn("SyntaxError", err["message"])
            self.assertEqual(err["path"], "bad.py")

    def test_non_utf8_file_becomes_error_entry(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bin.py"
            p.write_bytes(b"x = '\xff\xfe invalid utf8'\n")
            findings, err = ndscan.scan_file(str(p), "bin.py", list(ndscan.RULE_IDS))
            self.assertEqual(findings, [])
            self.assertIsNotNone(err)
            self.assertIn("not valid UTF-8", err["message"])

    def test_empty_file_clean(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "empty.py"
            p.write_text("", encoding="utf-8")
            findings, err = ndscan.scan_file(str(p), "empty.py", list(ndscan.RULE_IDS))
            self.assertEqual(findings, [])
            self.assertIsNone(err)

    def test_error_message_never_contains_absolute_path(self):
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d) / "does_not_exist.py"
            findings, err = ndscan.scan_file(str(missing), "does_not_exist.py", list(ndscan.RULE_IDS))
            self.assertIsNotNone(err)
            self.assertNotIn(str(d), err["message"])
            self.assertNotIn(d, err["message"])


class TestCanonicalJson(unittest.TestCase):
    def test_sorted_keys(self):
        text = ndscan.canonical_json({"b": 1, "a": 2})
        self.assertTrue(text.startswith('{"a":2,"b":1}'))

    def test_compact_separators(self):
        text = ndscan.canonical_json({"a": [1, 2], "b": {"c": 1}})
        self.assertNotIn(", ", text)
        self.assertNotIn(": ", text)

    def test_trailing_newline(self):
        text = ndscan.canonical_json({"a": 1})
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))

    def test_ascii_only(self):
        text = ndscan.canonical_json({"a": "café"})
        self.assertNotIn("é", text)
        self.assertIn("\\u00e9", text)

    def test_deterministic_for_same_input(self):
        obj = {"z": 1, "a": [3, 1, 2], "m": {"y": 1, "x": 2}}
        self.assertEqual(ndscan.canonical_json(obj), ndscan.canonical_json(dict(obj)))


class TestFindingOrdering(unittest.TestCase):
    def test_sort_key_order(self):
        f1 = ndscan.Finding("ND001_WALL_CLOCK", "a.py", 5, 1, "z", "high")
        f2 = ndscan.Finding("ND001_WALL_CLOCK", "a.py", 3, 1, "z", "high")
        self.assertLess(f2.sort_key(), f1.sort_key())

    def test_scan_root_output_is_sorted(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "z.py").write_text("import random\nrandom.random()\n", encoding="utf-8")
            (Path(d) / "a.py").write_text("import random\nrandom.random()\n", encoding="utf-8")
            findings, errors, _ = ndscan.scan_root(d, list(ndscan.RULE_IDS), "low")
            paths = [f.path for f in findings]
            self.assertEqual(paths, sorted(paths))

    def test_scan_root_dedups_identical_findings(self):
        # A hand-built list with a literal duplicate collapses to one entry
        # (defensive dedup; verified indirectly via scan_root's dict-based
        # dedup on sort_key()).
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text("amount = float(amount)\n", encoding="utf-8")
            findings, errors, _ = ndscan.scan_root(d, ["ND006_FLOAT_IN_MONEY"], "low")
            keys = [f.sort_key() for f in findings]
            self.assertEqual(len(keys), len(set(keys)))


class TestMinSeverityFilter(unittest.TestCase):
    def test_min_severity_low_includes_all(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text("repr(x)\n", encoding="utf-8")
            findings, _, _ = ndscan.scan_root(d, ["ND004_UNSAFE_REPR"], "low")
            self.assertEqual(len(findings), 1)

    def test_min_severity_medium_excludes_low(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text("repr(x)\n", encoding="utf-8")
            findings, _, _ = ndscan.scan_root(d, ["ND004_UNSAFE_REPR"], "medium")
            self.assertEqual(len(findings), 0)

    def test_min_severity_high_excludes_medium_and_low(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text("amount = 10.5\n", encoding="utf-8")
            findings, _, _ = ndscan.scan_root(d, ["ND006_FLOAT_IN_MONEY"], "high")
            self.assertEqual(len(findings), 0)

    def test_min_severity_high_includes_high(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text("import time\ntime.time()\n", encoding="utf-8")
            findings, _, _ = ndscan.scan_root(d, ["ND001_WALL_CLOCK"], "high")
            self.assertEqual(len(findings), 1)


class TestScanRootWalking(unittest.TestCase):
    def test_ignores_non_py_files(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "notes.txt").write_text("amount = 10.5\n", encoding="utf-8")
            findings, errors, scanned = ndscan.scan_root(d, list(ndscan.RULE_IDS), "low")
            self.assertEqual(scanned, 0)
            self.assertEqual(findings, [])

    def test_recurses_into_subdirectories(self):
        with tempfile.TemporaryDirectory() as d:
            sub = Path(d) / "pkg" / "subpkg"
            sub.mkdir(parents=True)
            (sub / "m.py").write_text("amount = 10.5\n", encoding="utf-8")
            findings, errors, scanned = ndscan.scan_root(d, list(ndscan.RULE_IDS), "low")
            self.assertEqual(scanned, 1)
            self.assertEqual(findings[0].path, "pkg/subpkg/m.py")

    def test_ignores_pycache_directory(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "__pycache__"
            cache.mkdir()
            (cache / "m.cpython-310.py").write_text("amount = 10.5\n", encoding="utf-8")
            findings, errors, scanned = ndscan.scan_root(d, list(ndscan.RULE_IDS), "low")
            self.assertEqual(scanned, 0)

    def test_relative_paths_use_forward_slash(self):
        with tempfile.TemporaryDirectory() as d:
            sub = Path(d) / "a" / "b"
            sub.mkdir(parents=True)
            (sub / "m.py").write_text("amount = 10.5\n", encoding="utf-8")
            findings, _, _ = ndscan.scan_root(d, list(ndscan.RULE_IDS), "low")
            self.assertIn("/", findings[0].path)
            self.assertNotIn("\\", findings[0].path)

    def test_paths_are_relative_not_absolute(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text("amount = 10.5\n", encoding="utf-8")
            findings, _, _ = ndscan.scan_root(d, list(ndscan.RULE_IDS), "low")
            self.assertEqual(findings[0].path, "m.py")
            self.assertFalse(os.path.isabs(findings[0].path))

    def test_multiple_files_error_and_findings_coexist(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "good.py").write_text("amount = 10.5\n", encoding="utf-8")
            (Path(d) / "bad.py").write_text("def f(:\n", encoding="utf-8")
            findings, errors, scanned = ndscan.scan_root(d, list(ndscan.RULE_IDS), "low")
            self.assertEqual(scanned, 2)
            self.assertEqual(len(findings), 1)
            self.assertEqual(len(errors), 1)


class TestBuildReport(unittest.TestCase):
    def test_report_has_expected_top_level_keys(self):
        report = ndscan.build_report([], [], list(ndscan.RULE_IDS), "low", 0)
        for key in ("schema_version", "tool", "rules_run", "min_severity", "summary", "findings", "errors"):
            self.assertIn(key, report)

    def test_by_rule_counts_zero_for_rules_with_no_findings(self):
        report = ndscan.build_report([], [], list(ndscan.RULE_IDS), "low", 0)
        for rule_id in ndscan.RULE_IDS:
            self.assertEqual(report["summary"]["by_rule"][rule_id], 0)

    def test_by_severity_counts(self):
        f = ndscan.Finding("ND001_WALL_CLOCK", "a.py", 1, 0, "d", "high")
        report = ndscan.build_report([f], [], list(ndscan.RULE_IDS), "low", 1)
        self.assertEqual(report["summary"]["by_severity"]["high"], 1)
        self.assertEqual(report["summary"]["by_severity"]["low"], 0)

    def test_report_contains_no_wall_clock_or_duration_fields(self):
        report = ndscan.build_report([], [], list(ndscan.RULE_IDS), "low", 0)
        text = ndscan.canonical_json(report)
        for forbidden in ("duration", "timestamp", "elapsed", "started_at", "finished_at"):
            self.assertNotIn(forbidden, text)


# ===========================================================================
# CLI / subprocess black-box tests
# ===========================================================================

class TestCli(unittest.TestCase):
    def test_exit_zero_on_clean_root(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text("x = 1\n", encoding="utf-8")
            code, out, err = run_cli(["--root", d])
            self.assertEqual(code, 0)
            self.assertEqual(err, "")
            report = json.loads(out)
            self.assertEqual(report["findings"], [])

    def test_exit_one_on_findings(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text("amount = 10.5\n", encoding="utf-8")
            code, out, err = run_cli(["--root", d])
            self.assertEqual(code, 1)
            report = json.loads(out)
            self.assertEqual(len(report["findings"]), 1)

    def test_exit_one_on_scan_error_with_no_findings(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "bad.py").write_text("def f(:\n", encoding="utf-8")
            code, out, err = run_cli(["--root", d])
            self.assertEqual(code, 1)
            report = json.loads(out)
            self.assertEqual(report["findings"], [])
            self.assertEqual(len(report["errors"]), 1)

    def test_exit_two_on_missing_root(self):
        code, out, err = run_cli(["--root", "/definitely/does/not/exist/anywhere"])
        self.assertEqual(code, 2)
        self.assertNotEqual(err, "")

    def test_exit_two_on_missing_required_arg(self):
        code, out, err = run_cli([])
        self.assertEqual(code, 2)

    def test_exit_two_on_invalid_rule_id(self):
        with tempfile.TemporaryDirectory() as d:
            code, out, err = run_cli(["--root", d, "--rule", "ND999_NOPE"])
            self.assertEqual(code, 2)

    def test_exit_two_on_invalid_min_severity(self):
        with tempfile.TemporaryDirectory() as d:
            code, out, err = run_cli(["--root", d, "--min-severity", "extreme"])
            self.assertEqual(code, 2)

    def test_exit_two_root_is_a_file_not_a_directory(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "notadir.py"
            f.write_text("x = 1\n", encoding="utf-8")
            code, out, err = run_cli(["--root", str(f)])
            self.assertEqual(code, 2)

    def test_output_flag_short(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text("amount = 10.5\n", encoding="utf-8")
            out_path = Path(d) / "report.json"
            code, out, err = run_cli(["--root", d, "-o", str(out_path)])
            self.assertEqual(code, 1)
            self.assertEqual(out, "")
            self.assertTrue(out_path.exists())
            report = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(len(report["findings"]), 1)

    def test_output_flag_long(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text("x = 1\n", encoding="utf-8")
            out_path = Path(d) / "report.json"
            code, out, err = run_cli(["--root", d, "--output", str(out_path)])
            self.assertEqual(code, 0)
            self.assertTrue(out_path.exists())

    def test_stdout_used_when_no_output_flag(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text("x = 1\n", encoding="utf-8")
            code, out, err = run_cli(["--root", d])
            self.assertTrue(len(out) > 0)

    def test_output_file_ends_with_single_newline(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text("x = 1\n", encoding="utf-8")
            out_path = Path(d) / "report.json"
            run_cli(["--root", d, "-o", str(out_path)])
            raw = out_path.read_bytes()
            self.assertTrue(raw.endswith(b"\n"))
            self.assertFalse(raw.endswith(b"\n\n"))

    def test_repeat_runs_byte_identical(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text("amount = 10.5\nimport random\nrandom.random()\n", encoding="utf-8")
            out1 = Path(d) / "r1.json"
            out2 = Path(d) / "r2.json"
            run_cli(["--root", d, "-o", str(out1)])
            run_cli(["--root", d, "-o", str(out2)])
            self.assertEqual(out1.read_bytes(), out2.read_bytes())

    def test_rule_filter_changes_finding_count(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text(
                "import time\ntime.time()\namount = 10.5\n", encoding="utf-8"
            )
            code_all, out_all, _ = run_cli(["--root", d])
            code_one, out_one, _ = run_cli(["--root", d, "--rule", "ND001_WALL_CLOCK"])
            report_all = json.loads(out_all)
            report_one = json.loads(out_one)
            self.assertGreater(len(report_all["findings"]), len(report_one["findings"]))
            self.assertEqual(report_one["rules_run"], ["ND001_WALL_CLOCK"])
            self.assertTrue(all(f["rule_id"] == "ND001_WALL_CLOCK" for f in report_one["findings"]))

    def test_rule_filter_can_change_verdict_to_clean(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text("amount = 10.5\n", encoding="utf-8")
            code_all, _, _ = run_cli(["--root", d])
            code_one, _, _ = run_cli(["--root", d, "--rule", "ND001_WALL_CLOCK"])
            self.assertEqual(code_all, 1)
            self.assertEqual(code_one, 0)

    def test_repeated_rule_flag_is_union_deduped(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text("amount = 10.5\n", encoding="utf-8")
            code, out, err = run_cli(["--root", d, "--rule", "ND006_FLOAT_IN_MONEY", "--rule", "ND006_FLOAT_IN_MONEY"])
            report = json.loads(out)
            self.assertEqual(report["rules_run"], ["ND006_FLOAT_IN_MONEY"])

    def test_multiple_distinct_rule_flags(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text("amount = 10.5\nimport time\ntime.time()\n", encoding="utf-8")
            code, out, err = run_cli(["--root", d, "--rule", "ND001_WALL_CLOCK", "--rule", "ND006_FLOAT_IN_MONEY"])
            report = json.loads(out)
            self.assertEqual(report["rules_run"], ["ND001_WALL_CLOCK", "ND006_FLOAT_IN_MONEY"])

    def test_min_severity_flag_via_cli(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text("repr(x)\n", encoding="utf-8")
            code, out, err = run_cli(["--root", d, "--min-severity", "medium"])
            self.assertEqual(code, 0)

    def test_no_absolute_paths_in_report(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text("amount = 10.5\nimport time\ntime.time()\n", encoding="utf-8")
            code, out, err = run_cli(["--root", d])
            self.assertNotIn(d, out)
            self.assertNotIn(str(Path(d).resolve()), out)

    def test_no_root_value_embedded_in_report(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text("x = 1\n", encoding="utf-8")
            code, out, err = run_cli(["--root", d])
            report = json.loads(out)
            self.assertNotIn("root", report)

    def test_relocation_produces_identical_report(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            content = "amount = 10.5\nimport random\nrandom.random()\n"
            (Path(d1) / "m.py").write_text(content, encoding="utf-8")
            (Path(d2) / "m.py").write_text(content, encoding="utf-8")
            _, out1, _ = run_cli(["--root", d1])
            _, out2, _ = run_cli(["--root", d2])
            self.assertEqual(out1, out2)

    def test_help_flag_exits_zero(self):
        code, out, err = run_cli(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("ndscan", out)

    def test_scan_directory_with_only_subdirs_is_clean(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "empty_subdir").mkdir()
            code, out, err = run_cli(["--root", d])
            self.assertEqual(code, 0)
            report = json.loads(out)
            self.assertEqual(report["summary"]["files_scanned"], 0)

    def test_findings_sorted_in_json_output(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "z.py").write_text("import time\ntime.time()\n", encoding="utf-8")
            (Path(d) / "a.py").write_text("import time\ntime.time()\n", encoding="utf-8")
            code, out, err = run_cli(["--root", d])
            report = json.loads(out)
            paths = [f["path"] for f in report["findings"]]
            self.assertEqual(paths, sorted(paths))

    def test_non_utf8_file_reported_as_error_exit_one(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "bin.py").write_bytes(b"x = '\xff\xfe'\n")
            code, out, err = run_cli(["--root", d])
            self.assertEqual(code, 1)
            report = json.loads(out)
            self.assertEqual(len(report["errors"]), 1)

    def test_stderr_empty_on_clean_run(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text("x = 1\n", encoding="utf-8")
            code, out, err = run_cli(["--root", d])
            self.assertEqual(err, "")

    def test_stderr_empty_on_findings_run(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text("amount = 10.5\n", encoding="utf-8")
            code, out, err = run_cli(["--root", d])
            self.assertEqual(err, "")

    def test_output_write_failure_exits_two(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text("x = 1\n", encoding="utf-8")
            bad_out = Path(d) / "no_such_subdir" / "report.json"
            code, out, err = run_cli(["--root", d, "-o", str(bad_out)])
            self.assertEqual(code, 2)

    def test_files_scanned_count_in_summary(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.py").write_text("x = 1\n", encoding="utf-8")
            (Path(d) / "b.py").write_text("y = 2\n", encoding="utf-8")
            code, out, err = run_cli(["--root", d])
            report = json.loads(out)
            self.assertEqual(report["summary"]["files_scanned"], 2)


class TestSamplesDirectories(unittest.TestCase):
    """Exercise the committed samples_clean/ and samples_risky/ trees exactly
    as the mandatory VERIFICATION commands do."""

    REPO_DIR = Path(__file__).resolve().parent

    def test_samples_clean_exits_zero(self):
        code, out, err = run_cli(["--root", str(self.REPO_DIR / "samples_clean")])
        self.assertEqual(code, 0, msg=out)

    def test_samples_risky_exits_one(self):
        code, out, err = run_cli(["--root", str(self.REPO_DIR / "samples_risky")])
        self.assertEqual(code, 1)

    def test_samples_risky_trips_all_six_rules(self):
        code, out, err = run_cli(["--root", str(self.REPO_DIR / "samples_risky")])
        report = json.loads(out)
        rule_ids_found = {f["rule_id"] for f in report["findings"]}
        self.assertEqual(rule_ids_found, set(ndscan.RULE_IDS))

    def test_samples_clean_has_zero_findings_for_every_rule(self):
        code, out, err = run_cli(["--root", str(self.REPO_DIR / "samples_clean")])
        report = json.loads(out)
        self.assertEqual(report["findings"], [])

    def test_samples_risky_restricted_to_nd001_only_still_exit_one(self):
        code, out, err = run_cli(
            ["--root", str(self.REPO_DIR / "samples_risky"), "--rule", "ND001_WALL_CLOCK"]
        )
        self.assertEqual(code, 1)
        report = json.loads(out)
        self.assertTrue(all(f["rule_id"] == "ND001_WALL_CLOCK" for f in report["findings"]))


if __name__ == "__main__":
    unittest.main()
