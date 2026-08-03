#!/usr/bin/env python3
"""thread_check.py -- stdlib-only Thread Check CLI for reviewer/contributor
message threads.

Reads an array of per-task reviewer/contributor message threads (JSON) and
reports, per thread:

  * UNANSWERED_QUESTION    -- a reviewer message that contains a question
    has no contributor response anywhere after it in the thread's
    chronological order.
  * UNANSWERED_OVERDUE     -- an UNANSWERED_QUESTION whose age against the
    injected --now exceeds --unanswered-max-hours.
  * NO_ARTIFACT_REFERENCE  -- a contributor message that resolves an open
    reviewer question cites no concrete artifact (see README.md "What
    counts as a concrete artifact reference").
  * RESTATEMENT_ONLY       -- a resolving contributor response that is
    substantially just a restatement of the question rather than an
    answer (see README.md "RESTATEMENT_ONLY -- read this before trusting
    it"; this check is deliberately conservative and is a candidate for
    human review, never a verdict on its own).
  * OUT_OF_ORDER_MESSAGE   -- a message whose timestamp is earlier than an
    earlier-positioned message in the raw input array (a watermark
    violation; see README.md).
  * DANGLING_REPLY         -- a message's in_reply_to references a
    message_id that does not exist anywhere in that thread.
  * MALFORMED_RECORD       -- a thread record or one of its messages fails
    the structural shape contract (see README.md "Input shape").
  * INVALID_TIMESTAMP      -- a message's "at" value is present as a
    string but fails to parse as a UTC ISO-8601 timestamp.
  * EMPTY_THREAD           -- a thread record's "messages" array is
    present, is a JSON array, and has zero elements.

Output is emitted as canonical JSON:

    json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

plus a single trailing newline.

Scope versus loop-health
-------------------------
loop-health (loop_health.py, the sibling tool this module was built next
to) measures the verification LOOP at the task level: resubmission
rounds, overdue reviews, and refusal-reason distribution, derived from a
flat lifecycle-event history per task. This module inspects the CONTENT
of an individual reviewer/contributor message thread: whether questions
got answered, whether answers cite anything concrete, whether an answer
is substantively different from the question it restates, and whether
the thread's own bookkeeping (message ordering, reply references) is
internally consistent. The two tools are complementary, not overlapping:
loop-health could report a task as "not overdue" while this tool reports
its most recent reviewer question as unanswered for days, because
loop-health has no visibility into message content at all.

Reproducibility contract
-------------------------
The wall clock is never consulted anywhere in the report path. The UTC
reference moment used for every age / overdue computation is supplied
exclusively via the required --now command-line argument and threaded
explicitly through every function that needs it, as an ordinary
parameter. See README.md / captured_output.txt for a grep proof over this
file's source.

parse_utc_timestamp, iso_z, and format_age below are reused verbatim (by
design, per the brief) from loop_health.py, which established this
"injected --now, never read the wall clock" pattern for this tool family
(itself matched from staleness-monitor). See README.md, "What we reused
from loop-health".

Exit codes
----------
  0  -- input parsed successfully and no findings were produced.
  1  -- input parsed successfully and at least one finding was produced.
  2  -- invalid input or usage error (missing/unparseable --now, unreadable
        or malformed input file, input JSON whose root is not a list,
        a negative --unanswered-max-hours, etc). A malformed *thread* or
        *message* inside an otherwise-valid array is NOT a usage error --
        it is reported as a MALFORMED_RECORD finding (exit 1).
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone

__all__ = [
    "parse_utc_timestamp",
    "iso_z",
    "format_age",
    "has_question",
    "has_artifact_reference",
    "artifact_kinds",
    "process_thread",
    "build_report",
    "canonical_json",
    "InputError",
]

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

ALLOWED_ROLES = ("reviewer", "contributor")

CODE_UNANSWERED_QUESTION = "UNANSWERED_QUESTION"
CODE_UNANSWERED_OVERDUE = "UNANSWERED_OVERDUE"
CODE_NO_ARTIFACT_REFERENCE = "NO_ARTIFACT_REFERENCE"
CODE_RESTATEMENT_ONLY = "RESTATEMENT_ONLY"
CODE_OUT_OF_ORDER_MESSAGE = "OUT_OF_ORDER_MESSAGE"
CODE_DANGLING_REPLY = "DANGLING_REPLY"
CODE_MALFORMED_RECORD = "MALFORMED_RECORD"
CODE_INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
CODE_EMPTY_THREAD = "EMPTY_THREAD"

ALL_CODES = (
    CODE_UNANSWERED_QUESTION,
    CODE_UNANSWERED_OVERDUE,
    CODE_NO_ARTIFACT_REFERENCE,
    CODE_RESTATEMENT_ONLY,
    CODE_OUT_OF_ORDER_MESSAGE,
    CODE_DANGLING_REPLY,
    CODE_MALFORMED_RECORD,
    CODE_INVALID_TIMESTAMP,
    CODE_EMPTY_THREAD,
)

DEFAULT_UNANSWERED_MAX_HOURS = 48

# Sentinel distinguishing "key absent" from "key present with value None".
_MISSING = object()


class InputError(Exception):
    """Raised for invalid input / usage problems. Maps to exit code 2."""


# --------------------------------------------------------------------------
# Timestamp parsing -- reused verbatim from loop_health.py (see README.md,
# "What we reused from loop-health"). No wall-clock reads in this module.
# --------------------------------------------------------------------------


def parse_utc_timestamp(raw):
    """Parse an ISO-8601 UTC timestamp string into an aware UTC datetime.

    Accepted forms:
      * a trailing 'Z' or 'z' with no embedded offset, e.g. "2026-08-02T00:00:00Z"
      * an explicit zero UTC offset, e.g. "2026-08-02T00:00:00+00:00" or
        "...-00:00"

    Rejected (raises ValueError):
      * any non-string value
      * an empty / whitespace-only string
      * a string that datetime.fromisoformat cannot parse
      * a timezone-naive string (no offset and no 'Z')
      * a string with a non-zero UTC offset (e.g. "+05:30")
      * a string that combines a 'Z' suffix with an embedded offset
    """
    if not isinstance(raw, str):
        raise ValueError("timestamp must be a JSON string, got %s" % type(raw).__name__)
    s = raw.strip()
    if not s:
        raise ValueError("timestamp must be a non-empty string")

    if s[-1] in ("Z", "z"):
        core = s[:-1]
        try:
            dt = datetime.fromisoformat(core)
        except ValueError:
            raise ValueError("unparseable ISO-8601 timestamp: %r" % raw)
        if dt.tzinfo is not None:
            raise ValueError(
                "timestamp combines a 'Z' suffix with an embedded UTC offset: %r" % raw
            )
        return dt.replace(tzinfo=timezone.utc)

    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise ValueError("unparseable ISO-8601 timestamp: %r" % raw)
    if dt.tzinfo is None:
        raise ValueError("timestamp is missing a UTC offset / timezone-naive: %r" % raw)
    if dt.utcoffset() != timedelta(0):
        raise ValueError("timestamp is not expressed in UTC (non-zero offset): %r" % raw)
    return dt.astimezone(timezone.utc)


def iso_z(dt):
    """Render an aware UTC datetime as ISO-8601 with a 'Z' suffix."""
    s = dt.isoformat()
    if s.endswith("+00:00"):
        s = s[: -len("+00:00")] + "Z"
    return s


def format_age(total_seconds):
    """Render an age (in seconds, possibly negative or fractional) as a
    human string of the form "<sign><d>d <h>h <m>m". Sub-minute remainders
    are truncated (the exact value belongs in the accompanying age_seconds
    field, not in this rounded human string)."""
    seconds = int(total_seconds)
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    minutes_total = seconds // 60
    days, rem_minutes = divmod(minutes_total, 24 * 60)
    hours, minutes = divmod(rem_minutes, 60)
    return f"{sign}{days}d {hours}h {minutes}m"


# --------------------------------------------------------------------------
# Question detection
# --------------------------------------------------------------------------

# A reviewer message "contains a question" if it has a literal '?', OR if
# any of its sentences (split on '.', '!', '?', newline) begins with one of
# these fixed interrogative lead phrases. This is a deliberately small,
# fixed list -- see README.md "What counts as a question" for the
# rationale and the false-negative risk it accepts (a genuinely
# interrogative sentence with none of these leads and no '?' is missed,
# on purpose, rather than guessed at with a fuzzier heuristic).
INTERROGATIVE_LEADS = (
    "what",
    "why",
    "how",
    "when",
    "where",
    "who",
    "which",
    "can you",
    "could you",
    "would you",
    "will you",
    "do you",
    "does this",
    "is this",
    "are these",
    "please clarify",
    "please explain",
    "please confirm",
    "please provide",
    "please share",
    "please describe",
)

_SENTENCE_SPLIT_RE = re.compile(r"[.!?\n]+")
_WORD_RE = re.compile(r"\w+", re.UNICODE)


# Both the ASCII '?' and the CJK/full-width '\uff1f' ideographic question
# mark count as a literal question mark. Treating only the ASCII form as a
# question mark would silently miss questions written in Chinese/Japanese
# punctuation conventions, where '\uff1f' is the normal sentence-final mark.
_QUESTION_MARK_CHARS = ("?", "\uff1f")


def has_question(text):
    """Return True if ``text`` (a plain string) is judged to contain a
    question. See INTERROGATIVE_LEADS above for the exact rule."""
    if not isinstance(text, str) or not text:
        return False
    if any(ch in text for ch in _QUESTION_MARK_CHARS):
        return True
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        s = sentence.strip().lower()
        if not s:
            continue
        for lead in INTERROGATIVE_LEADS:
            if s == lead or s.startswith(lead + " "):
                return True
    return False


# --------------------------------------------------------------------------
# Concrete artifact reference detection
# --------------------------------------------------------------------------

# Each pattern below is intentionally narrow. Matching ANY one of them
# marks the text as having a "concrete artifact reference"; this measures
# FORM, not substance -- see README.md "What counts as a concrete artifact
# reference" for the exact false-positive risk this accepts (e.g. a URL
# that is present but irrelevant to the question still counts).
_ARTIFACT_PATTERNS = (
    ("url", re.compile(r"https?://\S+")),
    # 7-40 char hex token that contains at least one a-f letter, so a
    # plain decimal number (which is also technically all-hex-digits)
    # does not count as a "commit sha" on its own.
    ("commit_sha", re.compile(r"\b(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")),
    ("sha256", re.compile(r"\b[0-9a-fA-F]{64}\b")),
    ("code_span", re.compile(r"`[^`\n]+`")),
    ("test_method", re.compile(r"\btest_[a-z_]+\b")),
    (
        "file_path",
        re.compile(
            r"(?:^|[\s(\"'`])"
            r"(?:[\w-]+/)*[\w-]{2,}\.[A-Za-z]{1,5}"
            r"(?=[\s).,;:\"'`]|$)"
        ),
    ),
    (
        "shell_command",
        re.compile(
            r"(?:^|\n)\s*\$\s+\S+"
            r"|\b(?:git|python3?|pip3?|npm|yarn|curl|wget|make|pytest|docker|kubectl|bash|sh)\s+\S+"
        ),
    ),
)


def artifact_kinds(text):
    """Return the sorted list of distinct artifact-pattern names matched
    in ``text`` (may be empty)."""
    if not isinstance(text, str) or not text:
        return []
    kinds = [name for name, pat in _ARTIFACT_PATTERNS if pat.search(text)]
    return sorted(kinds)


def has_artifact_reference(text):
    """Return True if ``text`` contains at least one concrete artifact
    reference per _ARTIFACT_PATTERNS."""
    if not isinstance(text, str) or not text:
        return False
    return any(pat.search(text) for _name, pat in _ARTIFACT_PATTERNS)


# --------------------------------------------------------------------------
# RESTATEMENT_ONLY thresholds -- deliberately conservative, see README.md
# "RESTATEMENT_ONLY -- read this before trusting it".
# --------------------------------------------------------------------------

RESTATEMENT_OVERLAP_THRESHOLD = 0.70
RESTATEMENT_NEW_TOKEN_MAX_RATIO = 0.30
RESTATEMENT_MIN_QUESTION_TOKENS = 4


def _tokens(text):
    if not isinstance(text, str) or not text:
        return set()
    return {w.lower() for w in _WORD_RE.findall(text)}


def _restatement_metrics(question_text, response_text):
    """Return (overlap_ratio, new_token_ratio, q_token_count) for a
    question/response pair. overlap_ratio is the fraction of the
    QUESTION's distinct tokens that reappear in the response (1.0 if the
    question has no tokens). new_token_ratio is the fraction of the
    RESPONSE's distinct tokens that are NOT in the question (0.0 if the
    response has no tokens)."""
    q_tokens = _tokens(question_text)
    r_tokens = _tokens(response_text)
    if not q_tokens:
        overlap_ratio = 0.0
    else:
        overlap_ratio = len(q_tokens & r_tokens) / len(q_tokens)
    if not r_tokens:
        new_token_ratio = 0.0
    else:
        new_token_ratio = len(r_tokens - q_tokens) / len(r_tokens)
    return overlap_ratio, new_token_ratio, len(q_tokens)


def is_restatement_only(question_text, response_text):
    """Return (bool, overlap_ratio, new_token_ratio) applying the three
    required conditions documented in README.md: high overlap with the
    question, no artifact reference in the response, and few new content
    tokens contributed by the response."""
    overlap_ratio, new_token_ratio, q_token_count = _restatement_metrics(
        question_text, response_text
    )
    if q_token_count < RESTATEMENT_MIN_QUESTION_TOKENS:
        return False, overlap_ratio, new_token_ratio
    if has_artifact_reference(response_text):
        return False, overlap_ratio, new_token_ratio
    if overlap_ratio < RESTATEMENT_OVERLAP_THRESHOLD:
        return False, overlap_ratio, new_token_ratio
    if new_token_ratio > RESTATEMENT_NEW_TOKEN_MAX_RATIO:
        return False, overlap_ratio, new_token_ratio
    return True, overlap_ratio, new_token_ratio


# --------------------------------------------------------------------------
# Finding construction
# --------------------------------------------------------------------------


def _finding(task_id, code, message, extra=None):
    f = {"task_id": task_id, "code": code, "message": message}
    if extra:
        f.update(extra)
    return f


def _index_ref(idx):
    return f"<index:{idx}>"


def _round3(x):
    return round(x, 3)


# --------------------------------------------------------------------------
# Per-message record: a lightweight namespace used only internally.
# --------------------------------------------------------------------------


class _Msg:
    __slots__ = ("j", "mid", "role", "text", "irt", "dt")

    def __init__(self, j, mid, role, text, irt, dt):
        self.j = j
        self.mid = mid
        self.role = role
        self.text = text
        self.irt = irt
        self.dt = dt


# --------------------------------------------------------------------------
# Per-thread processing
# --------------------------------------------------------------------------


def process_thread(idx, record, now, unanswered_max_hours):
    """Process a single top-level array element (one thread).

    Returns (findings, summary_or_None). ``summary_or_None`` is None only
    when the thread record itself is unusable at the structural level (not
    an object, missing/invalid task_id, missing/non-array messages) -- in
    that case it contributes no thread_summaries entry, only the
    MALFORMED_RECORD finding(s) already appended.
    """
    findings = []

    if not isinstance(record, dict):
        findings.append(
            _finding(
                _index_ref(idx),
                CODE_MALFORMED_RECORD,
                f"record at index {idx} is not a JSON object",
                extra={"record_index": idx},
            )
        )
        return findings, None

    if "task_id" not in record:
        findings.append(
            _finding(
                _index_ref(idx),
                CODE_MALFORMED_RECORD,
                f"record at index {idx} is missing required key: task_id",
                extra={"record_index": idx},
            )
        )
        return findings, None

    task_id = record["task_id"]
    if not isinstance(task_id, str) or task_id == "":
        findings.append(
            _finding(
                _index_ref(idx),
                CODE_MALFORMED_RECORD,
                f"record at index {idx} has an invalid task_id (must be a non-empty "
                f"JSON string): {task_id!r}",
                extra={"record_index": idx},
            )
        )
        return findings, None

    if "messages" not in record:
        findings.append(
            _finding(
                task_id,
                CODE_MALFORMED_RECORD,
                f"thread {task_id!r} is missing required key: messages",
            )
        )
        return findings, None

    messages = record["messages"]
    if not isinstance(messages, list):
        findings.append(
            _finding(
                task_id,
                CODE_MALFORMED_RECORD,
                f"thread {task_id!r} 'messages' must be a JSON array",
            )
        )
        return findings, None

    if len(messages) == 0:
        findings.append(
            _finding(task_id, CODE_EMPTY_THREAD, f"thread {task_id!r} has zero messages")
        )
        return findings, {
            "task_id": task_id,
            "message_count": 0,
            "question_count": 0,
            "answered_count": 0,
            "unanswered_count": 0,
        }

    # ---- PASS 1: collect every syntactically identifiable message_id ----
    known_ids = set()
    for m in messages:
        if isinstance(m, dict):
            mid = m.get("message_id", _MISSING)
            if isinstance(mid, str) and mid != "":
                known_ids.add(mid)

    # ---- PASS 2: full per-message validation ----
    typed_in_array_order = []  # list of _Msg, usable ones only, in array order

    for j, m in enumerate(messages):
        if not isinstance(m, dict):
            findings.append(
                _finding(
                    task_id,
                    CODE_MALFORMED_RECORD,
                    f"thread {task_id!r} message at index {j} is not a JSON object",
                    extra={"message_index": j},
                )
            )
            continue

        mid = m.get("message_id", _MISSING)
        if mid is _MISSING or not isinstance(mid, str) or mid == "":
            findings.append(
                _finding(
                    task_id,
                    CODE_MALFORMED_RECORD,
                    f"thread {task_id!r} message at index {j} has a missing or invalid "
                    f"'message_id' (must be a non-empty string)",
                    extra={"message_index": j},
                )
            )
            continue

        role = m.get("role", _MISSING)
        role_valid = role in ALLOWED_ROLES
        if not role_valid:
            findings.append(
                _finding(
                    task_id,
                    CODE_MALFORMED_RECORD,
                    f"thread {task_id!r} message {mid!r} (index {j}) has a missing or "
                    f"invalid 'role' (must be 'reviewer' or 'contributor'): {role!r}",
                    extra={"message_index": j, "message_id": mid},
                )
            )

        text_raw = m.get("text", _MISSING)
        text_valid = isinstance(text_raw, str)
        if not text_valid:
            findings.append(
                _finding(
                    task_id,
                    CODE_MALFORMED_RECORD,
                    f"thread {task_id!r} message {mid!r} (index {j}) has a missing or "
                    f"non-string 'text'",
                    extra={"message_index": j, "message_id": mid},
                )
            )

        at_raw = m.get("at", _MISSING)
        dt = None
        if at_raw is _MISSING or not isinstance(at_raw, str):
            findings.append(
                _finding(
                    task_id,
                    CODE_MALFORMED_RECORD,
                    f"thread {task_id!r} message {mid!r} (index {j}) is missing "
                    f"required key: at",
                    extra={"message_index": j, "message_id": mid},
                )
            )
        else:
            try:
                dt = parse_utc_timestamp(at_raw)
            except ValueError as exc:
                findings.append(
                    _finding(
                        task_id,
                        CODE_INVALID_TIMESTAMP,
                        f"thread {task_id!r} message {mid!r} (index {j}): {exc}",
                        extra={"message_index": j, "message_id": mid, "at_raw": at_raw},
                    )
                )

        irt_raw = m.get("in_reply_to", _MISSING)
        irt = None
        if irt_raw is _MISSING or irt_raw is None:
            irt = None
        elif isinstance(irt_raw, str) and irt_raw != "":
            irt = irt_raw
        else:
            findings.append(
                _finding(
                    task_id,
                    CODE_MALFORMED_RECORD,
                    f"thread {task_id!r} message {mid!r} (index {j}) has an invalid "
                    f"'in_reply_to' (must be a non-empty string, or absent/null): "
                    f"{irt_raw!r}",
                    extra={"message_index": j, "message_id": mid},
                )
            )
            irt = None

        if irt is not None and irt not in known_ids:
            findings.append(
                _finding(
                    task_id,
                    CODE_DANGLING_REPLY,
                    f"thread {task_id!r} message {mid!r} (index {j}) has in_reply_to "
                    f"{irt!r}, which does not match any message_id in this thread",
                    extra={"message_index": j, "message_id": mid, "in_reply_to": irt},
                )
            )

        if role_valid and text_valid and dt is not None:
            typed_in_array_order.append(_Msg(j, mid, role, text_raw, irt, dt))

    # ---- OUT_OF_ORDER_MESSAGE: watermark check over raw array order ----
    running_max_dt = None
    running_max_mid = None
    for tm in typed_in_array_order:
        if running_max_dt is not None and tm.dt < running_max_dt:
            findings.append(
                _finding(
                    task_id,
                    CODE_OUT_OF_ORDER_MESSAGE,
                    f"thread {task_id!r} message {tm.mid!r} (index {tm.j}) has "
                    f"timestamp {iso_z(tm.dt)}, which is earlier than "
                    f"{iso_z(running_max_dt)} on message {running_max_mid!r}, which "
                    f"appears earlier in the input array",
                    extra={
                        "message_index": tm.j,
                        "message_id": tm.mid,
                        "at": iso_z(tm.dt),
                        "conflicts_with_message_id": running_max_mid,
                        "conflicts_with_at": iso_z(running_max_dt),
                    },
                )
            )
        else:
            running_max_dt = tm.dt
            running_max_mid = tm.mid

    # ---- Chronological order for question/answer matching ----
    typed_chrono = sorted(typed_in_array_order, key=lambda tm: (tm.dt, tm.j))

    open_stack = []  # list of _Msg (open reviewer questions), oldest first
    responses = {}  # reviewer message_id -> resolving _Msg (contributor)
    question_count = 0

    for tm in typed_chrono:
        if tm.role == "reviewer":
            if has_question(tm.text):
                question_count += 1
                open_stack.append(tm)
            continue

        # tm.role == "contributor"
        target = None
        if tm.irt is not None:
            for k in range(len(open_stack) - 1, -1, -1):
                if open_stack[k].mid == tm.irt:
                    target = open_stack.pop(k)
                    break
            # An explicit in_reply_to that does not name an open question
            # (dangling, already-answered, or not a question at all) does
            # NOT fall back to implicit binding -- explicit intent is not
            # silently reassigned. See README.md.
        else:
            if open_stack:
                target = open_stack.pop()  # nearest preceding open question

        if target is not None:
            responses[target.mid] = tm

    for q in open_stack:
        findings.append(
            _finding(
                task_id,
                CODE_UNANSWERED_QUESTION,
                f"thread {task_id!r} reviewer message {q.mid!r} (index {q.j}) contains "
                f"a question with no contributor response after it",
                extra={"message_index": q.j, "message_id": q.mid},
            )
        )
        age = (now - q.dt).total_seconds()
        threshold_seconds = unanswered_max_hours * 3600.0
        if age > threshold_seconds:
            findings.append(
                _finding(
                    task_id,
                    CODE_UNANSWERED_OVERDUE,
                    f"thread {task_id!r} reviewer message {q.mid!r} (index {q.j}) has "
                    f"been unanswered for {format_age(age)}, exceeding the "
                    f"{unanswered_max_hours}h unanswered-max threshold",
                    extra={
                        "message_index": q.j,
                        "message_id": q.mid,
                        "since": iso_z(q.dt),
                        "age_seconds": int(round(age)),
                        "age_human": format_age(age),
                        "unanswered_max_hours": unanswered_max_hours,
                    },
                )
            )

    for q_mid, resp in responses.items():
        q_msg = next(m for m in typed_chrono if m.mid == q_mid)
        if not has_artifact_reference(resp.text):
            findings.append(
                _finding(
                    task_id,
                    CODE_NO_ARTIFACT_REFERENCE,
                    f"thread {task_id!r} contributor message {resp.mid!r} (index "
                    f"{resp.j}), answering {q_mid!r}, cites no concrete artifact "
                    f"reference",
                    extra={
                        "message_index": resp.j,
                        "message_id": resp.mid,
                        "in_response_to": q_mid,
                    },
                )
            )
        restated, overlap_ratio, new_token_ratio = is_restatement_only(q_msg.text, resp.text)
        if restated:
            findings.append(
                _finding(
                    task_id,
                    CODE_RESTATEMENT_ONLY,
                    f"thread {task_id!r} contributor message {resp.mid!r} (index "
                    f"{resp.j}), answering {q_mid!r}, is substantially a restatement "
                    f"of the question rather than an answer -- candidate for human "
                    f"review, not a verdict",
                    extra={
                        "message_index": resp.j,
                        "message_id": resp.mid,
                        "in_response_to": q_mid,
                        "overlap_ratio": _round3(overlap_ratio),
                        "new_token_ratio": _round3(new_token_ratio),
                    },
                )
            )

    summary = {
        "task_id": task_id,
        "message_count": len(messages),
        "question_count": question_count,
        "answered_count": len(responses),
        "unanswered_count": len(open_stack),
    }
    return findings, summary


# --------------------------------------------------------------------------
# Whole-input report assembly
# --------------------------------------------------------------------------


def build_report(data, now, unanswered_max_hours):
    """Build the full report dict for ``data`` at reference time ``now``.

    Returns (report_dict, total_finding_count). Raises InputError only if
    ``data`` itself is not a JSON array.
    """
    if not isinstance(data, list):
        raise InputError("input JSON must be an array of thread records")

    all_findings = []
    thread_summaries = []

    for idx, record in enumerate(data):
        findings, summary = process_thread(idx, record, now, unanswered_max_hours)
        all_findings.extend(findings)
        if summary is not None:
            summary["_record_index"] = idx
            thread_summaries.append(summary)

    thread_summaries.sort(key=lambda e: (str(e["task_id"]), e["_record_index"]))
    for e in thread_summaries:
        del e["_record_index"]

    all_findings.sort(
        key=lambda f: (
            str(f["task_id"]),
            f["code"],
            f.get("message_index", f.get("record_index", -1)),
            json.dumps(f, sort_keys=True, ensure_ascii=True),
        )
    )

    total = len(all_findings)

    counts_by_code = {code: 0 for code in ALL_CODES}
    for f in all_findings:
        counts_by_code[f["code"]] += 1

    summary = {
        "total_threads": len(data),
        "total_findings": total,
        "counts_by_code": counts_by_code,
    }

    report = {
        "generated_at": iso_z(now),
        "options": {"unanswered_max_hours": unanswered_max_hours},
        "summary": summary,
        "thread_summaries": thread_summaries,
        "findings": all_findings,
    }
    return report, total


def canonical_json(report):
    """Serialize ``report`` as canonical JSON (sorted keys, compact
    separators, ASCII-only) plus exactly one trailing newline."""
    return json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="thread_check.py",
        description=(
            "Check a JSON reviewer/contributor message thread for unanswered "
            "questions, responses without concrete artifact references, and "
            "overdue unanswered requests; emit canonical JSON."
        ),
    )
    parser.add_argument(
        "input_file", help="Path to a JSON file containing an array of thread records."
    )
    parser.add_argument(
        "--now",
        required=True,
        help=(
            "UTC reference moment in ISO-8601 (e.g. 2026-08-03T00:00:00Z). Required; "
            "this value is never defaulted and the wall clock is never consulted."
        ),
    )
    parser.add_argument(
        "--unanswered-max-hours",
        type=float,
        default=DEFAULT_UNANSWERED_MAX_HOURS,
        help=(
            "Hours after an unanswered reviewer question's timestamp before "
            "UNANSWERED_OVERDUE fires (default: %(default)s)."
        ),
    )
    parser.add_argument(
        "-o", "--output", help="Write the JSON report to this path instead of stdout."
    )
    return parser


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)  # argparse itself exits(2) on usage errors

    try:
        now = parse_utc_timestamp(args.now)
    except ValueError as exc:
        print(f"thread_check.py: error: invalid --now value: {exc}", file=sys.stderr)
        return 2

    if args.unanswered_max_hours < 0:
        print(
            "thread_check.py: error: --unanswered-max-hours must be >= 0", file=sys.stderr
        )
        return 2

    try:
        with open(args.input_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        print(
            f"thread_check.py: error: input file not found: {args.input_file}",
            file=sys.stderr,
        )
        return 2
    except OSError as exc:
        print(f"thread_check.py: error: could not read input file: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"thread_check.py: error: input file is not valid JSON: {exc}", file=sys.stderr)
        return 2

    try:
        report, total_findings = build_report(data, now, args.unanswered_max_hours)
    except InputError as exc:
        print(f"thread_check.py: error: {exc}", file=sys.stderr)
        return 2

    out = canonical_json(report)
    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(out)
        except OSError as exc:
            print(
                f"thread_check.py: error: could not write output file: {exc}",
                file=sys.stderr,
            )
            return 2
    else:
        sys.stdout.write(out)

    return 1 if total_findings > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
