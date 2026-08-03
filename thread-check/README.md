# thread-check

A stdlib-only Python 3 CLI that reads exported reviewer/contributor
message threads and reports unanswered questions, responses without
concrete artifact references, restatement-only responses, and structural
integrity problems, as canonical, reproducible JSON.

```
python3 thread_check.py THREADS.json --now 2026-08-03T00:00:00Z [options]
```

No third-party packages, no network access, and **no reads of the system
clock anywhere in the report path**. Every age / overdue computation is
driven exclusively by the UTC reference time you pass via the required
`--now` argument. Run the tool twice with the same input file and the same
`--now` and you get byte-identical output, forever, regardless of when you
actually run it.

## Scope versus loop-health

This tool was built directly alongside `loop-health` (`loop_health.py`),
the existing verification-loop tool in this repo, per an explicit
instruction to inspect it and reuse its conventions rather than build
overlapping tooling. The two tools are deliberately scoped at different
levels and are meant to be run together, not as alternatives to each
other:

- **loop-health** measures the verification **loop at the task level**:
  resubmission rounds (`verification_requested -> submitted` adjacency),
  overdue reviews (age of a task's *latest lifecycle state*), and a
  refusal-reason distribution, derived from a flat lifecycle-event history
  per task. It has no visibility into message content at all -- it cannot
  tell you whether a review was ever actually answered, only how long a
  task has sat in a given state.
- **thread-check** (this tool) inspects the **content of an individual
  reviewer/contributor message thread**: whether a reviewer's questions
  got answered at all, whether the answers cite anything concrete, whether
  an answer is substantively different from the question it quotes, and
  whether the thread's own bookkeeping (message ordering, `in_reply_to`
  references) is internally consistent. It has no visibility into
  task-level lifecycle state at all.

A task could be `REVIEW_OVERDUE`-clean under loop-health (its lifecycle
state hasn't sat still long enough to breach the threshold) while
thread-check simultaneously reports its most recent reviewer question as
`UNANSWERED_OVERDUE` for days -- or vice versa, a task could have a healthy
thread (every question answered, with artifacts) while loop-health flags
it `EXCESSIVE_RESUBMISSIONS` because of how many rounds it took to get
there. Run both; they answer different questions.

## What we reused from loop-health

Per the brief, the following were matched deliberately, not reinvented:

- **The reproducibility contract itself**: `--now` is a required CLI
  argument, parsed once in `main()`, and threaded explicitly as an
  ordinary function parameter (`now`) into every function that needs it
  (`process_thread`, `build_report`). No wall-clock read appears anywhere
  in the report path -- see "Why no wall-clock reads" below.
- **`parse_utc_timestamp`**, **`iso_z`**, and **`format_age`**: copied
  verbatim from `loop_health.py` (same accepted/rejected timestamp forms,
  the same `"<sign><d>d <h>h <m>m"` age rendering, the same "sub-minute
  remainders truncated, exact value in `*_seconds`" convention).
- **Canonical JSON**: identical `json.dumps(obj, sort_keys=True,
  separators=(",", ":"), ensure_ascii=True)` plus one trailing `\n`.
- **Exit code scheme**: `0` clean / `1` findings / `2` usage error, and
  the same `-o`/`--output` flag semantics.
- **Strict-inequality boundary semantics**: a value exactly equal to a
  configured threshold does **not** breach; one unit past it does. Applied
  here to `--unanswered-max-hours`.
- **The `"<index:N>"` synthetic task identifier** for structurally
  unusable top-level records, plus the identical two-tier "record-level
  vs. field-level" `MALFORMED_RECORD` policy: a broken identity field
  (`task_id` / `message_id`) causes the record to be skipped entirely;
  broken non-identity fields are each reported independently without
  skipping the rest of the record's checks.
- **The finding shape** (`task_id`, `code`, `message`, plus code-specific
  extra fields) and the **deterministic total-order sort** on findings:
  `(task_id, code, index-or-(-1), the finding's own canonical JSON dump)`
  as a last-resort tiebreak.
- **Doc style**: a "Known limitations" section that names real,
  unresolved risk areas, and a test suite structured as one
  `unittest.TestCase` subclass per concept with dynamically-generated,
  table-driven test methods (`setattr(TestClass, "test_x", ...)`).

What is genuinely new here (not matched, because the domain differs):
loop-health evaluates a single flat *state* per event; thread-check
evaluates *pairs* of messages (a reviewer question and the contributor
message that may or may not resolve it), which required a whole new
question-detection heuristic (`has_question`), artifact-reference
detection (`has_artifact_reference` / `artifact_kinds`), a
restatement-similarity check (`is_restatement_only`), and a
question-to-response matching algorithm (the open-question stack
described below) that has no analogue in loop-health at all.

## Installation

None. It's one file (`thread_check.py`) that only imports from the Python
3 standard library (`argparse`, `json`, `re`, `sys`, `datetime`). Requires
Python 3.9+ (uses `datetime.fromisoformat`; tested on 3.10).

## Usage

```
python3 thread_check.py INPUT_FILE --now ISO8601_UTC
                         [--unanswered-max-hours HOURS]
                         [-o OUTPUT_FILE | --output OUTPUT_FILE]
```

| Flag | Required | Default | Meaning |
|---|---|---|---|
| `INPUT_FILE` | yes | -- | Path to a JSON file containing an array of thread records. |
| `--now` | yes | -- | UTC reference time, ISO-8601 (`Z` or `+00:00` suffix). Never defaulted, never read from the OS clock. |
| `--unanswered-max-hours` | no | `48` | Hours after an unanswered reviewer question's timestamp before `UNANSWERED_OVERDUE` fires. |
| `-o`, `--output` | no | stdout | Write the JSON report to a file instead of stdout. |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Input parsed successfully; zero findings. |
| `1` | Input parsed successfully; one or more findings were produced. |
| `2` | Invalid input or usage error: missing/unparseable `--now`, missing/unreadable input file, input file is not valid JSON, the JSON root is not an array, or a negative `--unanswered-max-hours`. |

As with loop-health: a structurally malformed **thread record** or
**message** inside an otherwise-valid top-level array is **not** a usage
error. It produces a `MALFORMED_RECORD` finding (exit `1`), and the rest
of the array is still processed. Only a malformed *root* (file unreadable,
invalid JSON syntax, or the root value isn't a JSON array) is a usage
error (exit `2`).

## Input shape

The input file must be a JSON array of thread records:

```json
[
  {
    "task_id": "T-100",
    "messages": [
      {"message_id": "m1", "role": "reviewer", "at": "2026-08-01T09:00:00Z",
       "text": "Why did the retry logic change in the client?"},
      {"message_id": "m2", "role": "contributor", "at": "2026-08-01T10:00:00Z",
       "text": "Capped backoff at 30s and added jitter; see commit a1b2c3d and `retry_client.py`.",
       "in_reply_to": "m1"}
    ]
  }
]
```

- `task_id` -- **required**, non-empty JSON string. A record missing this,
  or with a `task_id` that is `null`/a number/empty/any other non-string,
  is unusable: exactly one `MALFORMED_RECORD` finding, and it contributes
  nothing else (no `thread_summaries` entry). The finding's own `task_id`
  is the synthetic placeholder `"<index:N>"` (N = its position in the
  top-level array), with `record_index` also present -- see "Known
  limitations" for why this placeholder isn't bulletproof (an unrestricted
  string namespace means a real `task_id` could collide with it).
- `messages` -- **required**, JSON array (may be empty). Missing or
  non-array is likewise fully unusable (`MALFORMED_RECORD`, no further
  processing). An empty array (`[]`) *is* usable -- it produces
  `EMPTY_THREAD` and a `thread_summaries` entry with all counts at `0`.
- Each element of `messages` must be a JSON object with:
  - `message_id` -- **required**, non-empty string. This is the message's
    *identity* field (like `task_id` above): if it's missing, empty, or
    non-string, that single message is skipped entirely (one
    `MALFORMED_RECORD` finding, `message_index` only -- no `message_id` to
    report since none was usable). Every OTHER field-level problem on a
    message with a *valid* `message_id` is checked and reported
    independently (does not skip the rest of that message's checks).
  - `role` -- **required**, must be exactly `"reviewer"` or
    `"contributor"` (case-sensitive). Missing or any other value ->
    `MALFORMED_RECORD`.
  - `at` -- **required**, an ISO-8601 UTC timestamp string (see "Timestamp
    format" below). Present-but-unparseable -> `INVALID_TIMESTAMP`.
    Missing entirely, or not a string -> `MALFORMED_RECORD` instead (a
    *structural* problem, not a parse failure).
  - `text` -- **required**, must be a JSON string. **The empty string
    (`""`) is legal** -- it is simply treated as containing no question and
    no artifact reference. Missing or non-string -> `MALFORMED_RECORD`.
  - `in_reply_to` -- **optional**. Absent, or explicit JSON `null`, means
    "no reply target" (not an error). Present as a non-empty string means
    "this message replies to the message with that `message_id`". Any
    other value (number, empty string, list, object, boolean) ->
    `MALFORMED_RECORD`.

A message that is **not usable** (identity field bad, or any of `role` /
`text` / `at` invalid) is excluded from all question/answer analysis and
from `OUT_OF_ORDER_MESSAGE` -- it simply cannot be time-ordered or
role-typed reliably. It is, however, still checked independently for
`DANGLING_REPLY` as long as it has a valid `message_id` and a present,
non-empty `in_reply_to` (referential integrity does not require the rest
of the message to be well-formed) -- see
`test_message_id_valid_but_role_bad_still_known_for_dangling_reply`.

### Lifecycle of a message_id (for DANGLING_REPLY)

`known_ids` for a thread is built in a first pass over the raw `messages`
array: any element that is a JSON object with a non-empty string
`message_id` contributes that id, **regardless of whether its other
fields are valid**. A second pass then checks every message's
`in_reply_to` (if present and non-empty) against that set; a value not
present in it produces `DANGLING_REPLY`. A message whose `in_reply_to`
equals its own `message_id` (a self-reply) is **not** dangling (the id
does exist -- itself) but also cannot resolve any open question (see
"How questions get matched to responses" below); it produces no finding
either way.

### Timestamp format

Identical rule set to `loop_health.py`'s `parse_utc_timestamp` (itself
matched from `staleness.py`):

Accepted:
- A trailing `Z`/`z` with no embedded offset: `2026-08-02T00:00:00Z`
- An explicit zero UTC offset: `2026-08-02T00:00:00+00:00` or `...-00:00`
- Optional fractional seconds: `2026-08-02T00:00:00.500000Z`

Rejected (-> `INVALID_TIMESTAMP` when on a message's `at`; -> exit-2 usage
error when on `--now`):
- Any non-zero offset, e.g. `2026-08-02T00:00:00+05:30`
- A timezone-naive string with no offset at all
- Anything `datetime.fromisoformat` cannot parse
- Any non-string JSON value where a *value* was actually supplied (an
  absent `at` key is `MALFORMED_RECORD`, not `INVALID_TIMESTAMP`)

## What gets computed

### Chronological ordering vs. raw array order

Two different orderings are used deliberately for two different purposes:

1. **Raw array order** (the literal order of `messages` in the input) is
   used for `OUT_OF_ORDER_MESSAGE`: a simple running-watermark scan. A
   message's `at` that is strictly earlier than the highest `at` seen so
   far among earlier-positioned, usable messages is flagged, carrying
   `conflicts_with_message_id` / `conflicts_with_at` naming the earlier
   message that set the watermark it violates. The watermark itself is
   never lowered by a flagged message -- it always tracks the true running
   maximum, not the most recently seen value (see
   `test_watermark_tracks_true_max_not_last_flagged_value`). Equal
   timestamps never trigger this (strict `<`).
2. **Chronological order** (`sorted by (parsed at, original array index)`,
   the original-index tiebreak matched from loop-health) is used for
   *everything else*: question detection, unanswered/overdue evaluation,
   and question-to-response matching. This means a thread whose raw array
   is out of order still gets a semantically correct "was this question
   ever answered" analysis (it just also gets an `OUT_OF_ORDER_MESSAGE`
   finding for the disorder itself).

Only *usable* messages (valid `message_id`, `role`, `text`, and a
parseable `at`) participate in either ordering.

### What counts as a "question"

A reviewer message is judged to contain a question (`has_question`) if
**either**:

1. It contains a literal question mark -- either the ASCII `?` or the
   full-width/CJK `?` (U+FF1F). Both count; see "Bug found and fixed"
   below for why the CJK form matters.
2. Splitting the text on `.`, `!`, `?`, or newline into sentences, **any**
   sentence, lowercased and stripped, starts with one of a small fixed
   list of interrogative lead phrases (case-insensitive, must be followed
   by a space or be the entire sentence): `what`, `why`, `how`, `when`,
   `where`, `who`, `which`, `can you`, `could you`, `would you`,
   `will you`, `do you`, `does this`, `is this`, `are these`,
   `please clarify`, `please explain`, `please confirm`,
   `please provide`, `please share`, `please describe`.

This is a **boolean per message**, not a count -- two questions in one
message are treated identically to a single question for matching
purposes (see "Known limitations"). It is deliberately a small, fixed
list, not a general NLP classifier: a genuinely interrogative sentence
with neither a question mark nor a recognized lead at its start (e.g. "I
wonder what happened here.") is missed on purpose, rather than guessed at
with a fuzzier heuristic that would risk false positives elsewhere (see
`TestHasQuestionDocumentedLimitation`).

### How questions get matched to responses

Reviewer questions and contributor messages are walked once, in
chronological order, maintaining a stack (`open_stack`) of not-yet-resolved
reviewer questions, oldest first:

- A **reviewer** message that `has_question` is pushed onto `open_stack`.
  A reviewer message without a question is not tracked at all.
- A **contributor** message:
  - If it has an explicit `in_reply_to`, `open_stack` is searched (from
    most-recently-pushed backward) for an entry whose `message_id` equals
    it. If found, that entry is popped and resolved by this message. **If
    not found** (the target isn't currently an open question -- it's
    dangling, already resolved, or not a question at all), this message
    resolves **nothing**. It does **not** fall back to implicit matching;
    explicit intent is never silently reassigned (see
    `test_explicit_reply_to_non_open_target_does_not_fall_back_implicit`).
  - If it has **no** `in_reply_to`, and `open_stack` is non-empty, the
    **most recently asked** still-open question (top of the stack, LIFO)
    is popped and resolved by this message. If `open_stack` is empty
    (e.g. a contributor message with no preceding open question, or one
    appearing before any reviewer message at all), this message resolves
    nothing and is not evaluated further.

Any question remaining in `open_stack` once the whole thread has been
walked triggers `UNANSWERED_QUESTION`. Every resolved (question,
response) pair is evaluated for `NO_ARTIFACT_REFERENCE` and
`RESTATEMENT_ONLY` (below). A contributor message with no open question
to resolve (small talk, status updates) is **not** checked for artifact
references or restatement at all; those checks are scoped to messages
actually serving as an answer.

**Why LIFO for implicit binding, not FIFO**: with two open questions
Q1 (asked first) and Q2 (asked second), an untagged contributor reply is
assumed to answer the *most recently asked* one (Q2), on the theory that
it's freshest in the conversation. This is a real, documented judgment
call, not an obvious fact about how people actually converse -- a FIFO
("answer the oldest open question first") interpretation is equally
defensible. See "Known limitations".

### UNANSWERED_QUESTION / UNANSWERED_OVERDUE

`UNANSWERED_QUESTION` fires for every reviewer message left in
`open_stack` at the end of the walk above -- always, regardless of age.
`UNANSWERED_OVERDUE` fires **additionally**, on the same message, when its
age against `--now` (i.e. `now - message.at`) **strictly exceeds**
`--unanswered-max-hours * 3600` seconds (default 48h). Exactly at the
threshold does not breach; the boundary is strict `>`, matched from
loop-health's `REVIEW_OVERDUE`.

The `UNANSWERED_OVERDUE` finding carries `since` (the question's
timestamp), `age_seconds` (`int(round(...))` of the exact
`timedelta.total_seconds()`), `age_human` (e.g. `"4d 4h 0m"`), and
`unanswered_max_hours` (the effective threshold used for this run).

### What counts as a "concrete artifact reference"

`has_artifact_reference(text)` is `True` if **any** of the following
patterns matches (`artifact_kinds(text)` returns the sorted list of which
ones matched):

| Kind | Pattern (Python `re`) | Notes |
|---|---|---|
| `url` | `https?://\S+` | Any `http(s)://` token. |
| `commit_sha` | `\b(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b` | 7-40 lowercase-hex-char token, requiring **at least one `a`-`f` letter** so a plain decimal number (e.g. a phone number or ticket ID) is not treated as a SHA. |
| `sha256` | `\b[0-9a-fA-F]{64}\b` | Exactly 64 hex chars (case-insensitive). |
| `code_span` | `` `[^`\n]+` `` | Markdown backtick code span. |
| `test_method` | `\btest_[a-z_]+\b` | Deliberately the literal pattern from the task spec -- lowercase letters and underscores only. A test name containing digits (`test_page2_loads`) is **not** matched; see "Known limitations". |
| `file_path` | a word/path of 2+ chars, optionally `/`-segmented, followed by a `.` and a 1-5 letter extension, bounded by whitespace/`(`/quotes/backtick or string edges | e.g. `src/foo/bar.py`, `README.md`, `config.yaml`. |
| `shell_command` | a line starting with `$ ` (prompt style), OR one of `git`/`python3?`/`pip3?`/`npm`/`yarn`/`curl`/`wget`/`make`/`pytest`/`docker`/`kubectl`/`bash`/`sh` followed by an argument | e.g. `$ python3 thread_check.py x.json`, `run pytest test_thread_check.py`. |

**This measures form, not substance, and that is a deliberate,
documented limitation, not an oversight.** A response that contains
`https://example.com/completely-unrelated-cat-pictures` still counts as
"has a concrete artifact reference" even though that URL has nothing to
do with the reviewer's question -- see
`test_url_present_but_irrelevant_still_counts`. `NO_ARTIFACT_REFERENCE`
therefore tells a reviewer "this response cites nothing at all" reliably,
but its *absence* (no finding) is not proof the cited artifact is
relevant, current, or correct. A human still has to check that.

### RESTATEMENT_ONLY -- read this before trusting it

This is the most dangerous check in this tool, and it is written to be
**deliberately conservative**: it is a candidate for a human to read, not
a verdict, and it requires **all three** of the following simultaneously
(`is_restatement_only`, called only on a resolved (question, response)
pair):

1. **High token overlap with the question.** Tokens are lowercase
   `\w+` matches (Unicode-aware) with no stopword removal, compared as
   sets. `overlap_ratio = |question_tokens intersect response_tokens| /
   |question_tokens|` must be `>= RESTATEMENT_OVERLAP_THRESHOLD = 0.70`
   (70% of the question's distinct tokens reappear in the response).
2. **The response contains no concrete artifact reference at all**
   (`has_artifact_reference(response_text)` is `False`). This is why a
   response that quotes the question **verbatim** but also supplies a
   commit SHA, file path, or other artifact is **never** flagged --
   citing something concrete is definitionally not "restatement only",
   regardless of how much of the question it also echoes. This is a
   required test case (`test_verbatim_quote_plus_commit_sha_not_flagged`).
3. **The response adds few new content tokens beyond the question.**
   `new_token_ratio = |response_tokens - question_tokens| /
   |response_tokens|` must be `<= RESTATEMENT_NEW_TOKEN_MAX_RATIO = 0.30`
   (at most 30% of the response's distinct tokens are new relative to the
   question). This is what protects a legitimate answer that opens by
   quoting the question for clarity ("Why did you change the retry logic
   in the client? I rewrote the exponential backoff to cap at 30s and
   added jitter...") -- once the substantive continuation exceeds 30% of
   the response's own distinct vocabulary, it is no longer flagged, even
   though `overlap_ratio` is still `1.0`. See
   `test_quote_plus_substantial_new_content_not_flagged`.

A fourth guard, `RESTATEMENT_MIN_QUESTION_TOKENS = 4`, skips the check
entirely for very short questions (fewer than 4 distinct tokens, e.g.
`"Is this correct?"`) -- with so few tokens, high overlap is nearly
unavoidable for *any* short reply and would not be meaningful signal.

All three conditions (and the length guard) are strict, hand-picked
constants, not learned or tuned against a labeled corpus -- see
"Known limitations", limitation #1, the most important limitation in
this delivery.

`RESTATEMENT_ONLY` firing on a message **always** implies
`NO_ARTIFACT_REFERENCE` also fires on that same message, since condition
2 above (no artifact reference) is one of `RESTATEMENT_ONLY`'s own
requirements; the two are not independent findings by coincidence, they
are related by construction (see
`test_restatement_only_implies_no_artifact_reference_too`).

The finding carries `overlap_ratio` and `new_token_ratio` (each rounded
to 3 decimal places) so a human reviewing the output can see exactly how
close to the boundary the flagged response was, and `in_response_to`
naming the question it was matched against.

### MALFORMED_RECORD / INVALID_TIMESTAMP / EMPTY_THREAD / DANGLING_REPLY / OUT_OF_ORDER_MESSAGE

All described inline above (per-field / per-check). Every finding carries
at minimum `task_id`, `code`, `message`; specific codes add
`record_index` and/or `message_index`, `message_id`, and code-specific
fields as documented above.

## Output shape

Canonical JSON (`json.dumps(obj, sort_keys=True, separators=(",", ":"),
ensure_ascii=True)` plus one trailing `\n`):

```json
{"findings":[...],"generated_at":"2026-08-03T00:00:00Z","options":{"unanswered_max_hours":48},"summary":{...},"thread_summaries":[...]}
```

Top-level keys (alphabetized by `sort_keys=True`): `findings`,
`generated_at`, `options`, `summary`, `thread_summaries`.

- `generated_at` -- the injected `--now`, normalized and echoed back.
- `options` -- the effective `unanswered_max_hours` used.
- `summary` -- `total_threads` (length of the input array, including
  unusable records), `total_findings`, and `counts_by_code` (a key for
  every one of the nine finding codes, always present, `0` when absent).
- `thread_summaries` -- `[{"task_id", "message_count", "question_count",
  "answered_count", "unanswered_count"}, ...]` for every record that was
  structurally usable (valid `task_id` and a `messages` array, even an
  empty one), sorted by `(str(task_id), the record's position in the
  input array)`. Structurally unusable records do **not** get an entry.
- `findings` -- a single flat array. Sorted by `(task_id, code,
  message_index-or-record_index-or-(-1), the finding's own canonical
  JSON dump)` -- the last component is a deterministic tiebreak of last
  resort, matched from loop-health.

## Why no wall-clock reads

The only place "now" enters the program is in `main()`, where it is
parsed from `args.now` via `parse_utc_timestamp` and then threaded
explicitly as an ordinary parameter named `now` into `process_thread` and
`build_report`. Verified two ways:

1. `TestNoWallClockRead.test_source_has_no_forbidden_wall_clock_calls` in
   `test_thread_check.py` scans this script's own source for the three
   forbidden substrings at test time (built via string concatenation in
   the test so the test file itself doesn't trip the same grep).
2. `captured_output.txt` includes the verbatim result of running
   `grep -n "now()\|utcnow\|time.time" thread_check.py` against the
   shipped source -- it returns nothing.

Because `--now` is declared with `required=True`, omitting it is an
`argparse` usage error and exits `2` automatically -- there is no
fallback path to the system clock to fall back *to*.

## Bug found and fixed during development

While building `threads_complete.json` (intended to be a zero-finding
fixture), a Chinese-language thread (`T-CLEAN-UNICODE`) whose reviewer
message ended in the full-width CJK question mark (U+FF1F) --
`"..."` -- was **not** detected as containing a question at all
(`question_count` came back `0`), even though it is obviously and
unambiguously a question. The bug was in `has_question`: it checked only
for the literal ASCII `"?"` character (`if "?" in text`). U+FF1F is a
distinct Unicode code point from U+003F (ASCII `?`) and is the normal
sentence-final question punctuation in Chinese/Japanese text -- `"?" in
text` is simply `False` for a string that only contains the full-width
form.

**Fixed in the tool** (not the test): `has_question` now checks for
either character via `_QUESTION_MARK_CHARS = ("?", "？")`, and
`test_unicode_question_and_response` /
`TestHasQuestionPositive.test_unicode_with_question_mark` /
`TestRestatementOnly.test_unicode_restatement` exercise this. This is a
real, user-facing bug (any non-ASCII-punctuation reviewer question would
have silently never been flagged as unanswered, no matter how long it
sat, since it would never even be recognized as a question in the first
place) and not a test-authoring mistake, so per the "fix the tool, not
the test" rule this was fixed in `thread_check.py` itself.

Note this fix is necessarily incomplete: it covers the one CJK
punctuation mark that came up in testing, not every locale's
sentence-final question convention (e.g. it does not special-case the
Japanese question particle, Spanish's leading inverted question mark, or
other scripts entirely). See "Known limitations" below.

## Known limitations (read before relying on this in production)

1. **`RESTATEMENT_ONLY`'s thresholds are hand-picked, not learned, and
   the whole "question" concept it depends on is a small fixed heuristic,
   not NLP.** `RESTATEMENT_OVERLAP_THRESHOLD = 0.70`,
   `RESTATEMENT_NEW_TOKEN_MAX_RATIO = 0.30`, and
   `RESTATEMENT_MIN_QUESTION_TOKENS = 4` were chosen to satisfy the
   specific required test cases in the brief (verbatim quote + SHA must
   not flag; a legitimate answer that quotes the question before adding
   substance must not flag) and to behave sensibly on hand-constructed
   examples -- they were not tuned against any labeled corpus of real
   reviewer threads. A real thread with unusual phrasing, heavy
   boilerplate greetings, or a question that is long relative to its
   answer's substantive content could still produce a false positive or
   false negative at these exact boundaries. Likewise, `has_question`'s
   fixed lead-phrase list and question-mark check will both under- and
   over-detect on real-world text the fixtures never exercised (sarcastic
   rhetorical questions, questions phrased as imperatives with no lead
   word, other locales' question punctuation). **`RESTATEMENT_ONLY`
   findings are candidates for a human to read, never a verdict on their
   own** -- this is stated here and in the finding's own message
   deliberately, not as boilerplate.
2. **Implicit (no `in_reply_to`) contributor replies bind LIFO (most
   recently asked open question), which is a judgment call, not an
   established fact about how threads work.** A thread with two
   simultaneously open questions where an untagged reply is actually
   answering the *older* one, not the newer one, will have that binding
   assigned to the wrong question -- so `NO_ARTIFACT_REFERENCE` /
   `RESTATEMENT_ONLY` would be evaluated against the wrong question's
   text, and the truly-answered question would be left showing
   `UNANSWERED_QUESTION` instead. Explicit `in_reply_to` avoids this
   entirely and is the reliable path; untagged multi-question threads are
   inherently ambiguous to any automated matcher, this one included.
3. **Artifact-reference and question-detection patterns are precision
   trade-offs, and both directions of error are real.** `commit_sha`
   requires an `a`-`f` letter specifically to avoid flagging plain
   numbers, which means an all-numeric abbreviated SHA (rare in practice,
   but not impossible for a short numeric-only hex prefix) would be
   missed. `test_method` uses the literal `test_[a-z_]+` pattern from the
   task spec, which does not include digits, so a real test name like
   `test_page2_loads` or `test_retry_backoff_caps_at_30s` is **not**
   recognized as an artifact reference even though it obviously is one.
   `file_path` requires a 1-5 letter extension and a 2+ character
   filename stem to avoid matching sentence-final abbreviations like
   "e.g." -- an intentionally narrow single-letter extension or a
   one-character filename would be missed. None of these patterns
   attempt to verify that a referenced URL, path, or SHA actually exists
   or is reachable; see "What counts as a concrete artifact reference"
   above for the broader form-not-substance caveat, which applies to
   every one of these seven patterns, not just URLs.

## Files in this delivery

- `thread_check.py` -- the CLI (stdlib only).
- `test_thread_check.py` -- unittest suite (229 tests; run with
  `python3 -m unittest test_thread_check -v`).
- `threads_complete.json` -- fixture with zero findings (exit code 0).
- `threads_incomplete.json` -- fixture engineered to trigger all nine
  finding codes (exit code 1).
- `README.md` -- this file.
- `captured_output.txt` -- real captured output of the verification
  commands, including the wall-clock grep proof.

## Reproducible commands

```
python3 -m unittest test_thread_check -v
python3 thread_check.py threads_complete.json --now 2026-08-03T00:00:00Z ; echo "exit=$?"
python3 thread_check.py threads_incomplete.json --now 2026-08-03T00:00:00Z -o r1.json ; echo "exit=$?"
python3 thread_check.py threads_incomplete.json --now 2026-08-03T00:00:00Z -o r2.json ; echo "exit=$?"
sha256sum r1.json r2.json
cmp r1.json r2.json && echo BYTE-IDENTICAL
python3 thread_check.py threads_incomplete.json --now 2026-08-03T00:00:00Z --unanswered-max-hours 99999 ; echo "exit=$?"
python3 thread_check.py threads_incomplete.json ; echo "exit=$?"
python3 thread_check.py /nonexistent.json --now 2026-08-03T00:00:00Z ; echo "exit=$?"
grep -n "now()\|utcnow\|time.time" thread_check.py
```

`r1.json` / `r2.json` are throwaway scratch files used only to prove
byte-for-byte reproducibility; they are not part of this delivery. See
`captured_output.txt` for the real, captured output of every command
above.
