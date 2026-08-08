# samples_invalid

One fixture per README-documented exit-2 trigger, so
`TestDocumentedExitCodes` invokes the CLI on a committed file rather than on
something a test generated. Each file is the smallest input that triggers
exactly one condition; all of them are valid targets for the `expected`
positional argument.

| file | documented trigger |
|---|---|
| `not_json.json` | bad JSON |
| `not_an_array.json` | wrong shape (top-level JSON value is not an array) |
| `float_amount.json` | float amount |
| `missing_field.json` | missing field |
| `duplicate_task_id.json` | duplicate `task_id` in the expected set |
| `amount_out_of_range.json` | an amount no `Decimal` can quantize to 6 dp |
| `amount_infinity.json` | the same failure by a different route |
| `amount_signalling_nan.json` | a signalling NaN amount, which raises on comparison rather than comparing unequal |
| `not_utf8.json` | unreadable file: the bytes are not UTF-8 |

`not_utf8.json` is committed as bytes on purpose. Generating it in the test
would let a later edit quietly change what "not UTF-8" means; the byte
`0xFF` at offset 0 is never valid UTF-8 and never will be.

`valid_pair_expected.json` and `valid_pair_payouts.json` are the balanced
control: the same eight cases are worthless without a case that must still
succeed.
