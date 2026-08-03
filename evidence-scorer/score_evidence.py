#!/usr/bin/env python3
"""
Objective Evidence Quality Scorer.

Scores submitted evidence on computable signals only. No opinion, no model, no
network: every component is a countable property of the text, so the same input
always yields the same score and a reviewer can recompute it by hand.

SIGNALS (each contributes 0.0-1.0, then weighted)
  artifacts    density of concrete artifacts: fenced code blocks, shell prompts,
               "exit=N", 64-hex hashes, CIDs, file paths, URLs
  specificity  ratio of specific tokens (digits, snake/camel identifiers, paths,
               hex) to total tokens
  length       length credit, saturating at --target-length characters
  originality  1 minus the fraction of the record's sentences that appear
               verbatim in OTHER records (boilerplate detection)

Score = weighted sum. Records scoring below --threshold fail.

Exit codes: 0 all records pass | 1 one or more below threshold | 2 invalid input.
"""
import argparse
import json
import re
import sys

REQUIRED = ("submission_id", "text")

ARTIFACTS = "artifacts"
SPECIFICITY = "specificity"
LENGTH = "length"
ORIGINALITY = "originality"

DEFAULTS = {
    "weights": {ARTIFACTS: 0.35, SPECIFICITY: 0.25, LENGTH: 0.15, ORIGINALITY: 0.25},
    "target_length": 800,
    "artifact_target": 6,
    "threshold": 0.5,
}

_CODE_FENCE = re.compile(r"```")
_SHELL = re.compile(r"(?m)^\s*[\$>]\s+\S")
_EXIT = re.compile(r"\bexit(?:\s*code)?\s*[=:]\s*\d+", re.I)
_HEX64 = re.compile(r"\b[0-9a-fA-F]{64}\b")
_CID = re.compile(r"\b(?:Qm[1-9A-HJ-NP-Za-km-z]{44}|bafy[a-z2-7]{20,})\b")
_PATH = re.compile(r"(?:^|\s)(?:/|\./|[A-Za-z]:\\)[\w./\\-]+")
_URL = re.compile(r"https?://\S+")

_TOKEN = re.compile(r"[A-Za-z0-9_./:-]+")
_HAS_DIGIT = re.compile(r"\d")
_IDENTIFIER = re.compile(r"^[a-z]+(?:_[a-z0-9]+)+$|^[a-z]+(?:[A-Z][a-z0-9]*)+$")
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


class InputError(Exception):
    pass


def _clamp(x):
    return 0.0 if x < 0 else (1.0 if x > 1 else x)


def count_artifacts(text):
    counts = {
        "code_fences": len(_CODE_FENCE.findall(text)) // 2,
        "shell_lines": len(_SHELL.findall(text)),
        "exit_codes": len(_EXIT.findall(text)),
        "hashes": len(_HEX64.findall(text)),
        "cids": len(_CID.findall(text)),
        "paths": len(_PATH.findall(text)),
        "urls": len(_URL.findall(text)),
    }
    return counts, sum(counts.values())


MIN_TOKENS_FOR_CONFIDENCE = 20


def specificity_ratio(text):
    """Ratio of specific tokens to total, damped when the sample is tiny.

    Two guards matter here:
      1. Tokens are stripped of surrounding punctuation before classification.
         Without this, "Done." counts as specific purely because of the full
         stop, which is meaningless.
      2. The raw ratio is scaled by min(1, tokens/MIN_TOKENS_FOR_CONFIDENCE).
         A one-token submission is not 100% specific, it is unmeasurable, and
         leaving it undamped let a five-character record clear the threshold.
    """
    raw = _TOKEN.findall(text)
    tokens = [t.strip(".:/-_") for t in raw]
    tokens = [t for t in tokens if t]
    if not tokens:
        return 0.0, 0, 0
    specific = 0
    for t in tokens:
        if _HAS_DIGIT.search(t) or "/" in t or "." in t or _IDENTIFIER.match(t):
            specific += 1
    ratio = specific / len(tokens)
    confidence = min(1.0, len(tokens) / MIN_TOKENS_FOR_CONFIDENCE)
    return ratio * confidence, specific, len(tokens)


def sentences(text):
    out = []
    for s in _SENTENCE.findall(text):
        s = " ".join(s.split()).strip().rstrip(".!?").lower()
        if len(s) >= 20:
            out.append(s)
    return out


def load_records(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise InputError(f"file not found: {path}")
    except json.JSONDecodeError as exc:
        raise InputError(f"invalid JSON: {exc}")
    except UnicodeDecodeError as exc:
        raise InputError(f"not valid UTF-8: {exc}")
    if not isinstance(data, list):
        raise InputError(f"expected a JSON array, got {type(data).__name__}")
    out = []
    for i, r in enumerate(data):
        where = f"records[{i}]"
        if not isinstance(r, dict):
            raise InputError(f"{where}: record must be an object")
        for f in REQUIRED:
            if f not in r:
                raise InputError(f"{where}: missing required field '{f}'")
        if not isinstance(r["submission_id"], str) or not r["submission_id"].strip():
            raise InputError(f"{where}: 'submission_id' must be a non-empty string")
        if not isinstance(r["text"], str):
            raise InputError(f"{where}: 'text' must be a string")
        out.append({"submission_id": r["submission_id"], "text": r["text"]})
    return out


def load_config(path, overrides):
    cfg = json.loads(json.dumps(DEFAULTS))
    if path:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                user = json.load(fh)
        except FileNotFoundError:
            raise InputError(f"config file not found: {path}")
        except json.JSONDecodeError as exc:
            raise InputError(f"config invalid JSON: {exc}")
        if not isinstance(user, dict):
            raise InputError("config must be a JSON object")
        for k, v in user.items():
            if k == "weights":
                if not isinstance(v, dict):
                    raise InputError("config.weights must be an object")
                for sk, sv in v.items():
                    if sk not in DEFAULTS["weights"]:
                        raise InputError(f"config.weights: unknown signal {sk!r}")
                    cfg["weights"][sk] = float(sv)
            elif k in DEFAULTS:
                cfg[k] = float(v)
            else:
                raise InputError(f"config: unknown key {k!r}")
    for k, v in overrides.items():
        if v is not None:
            cfg[k] = float(v)
    return cfg


def score_all(records, cfg):
    sent_owners = {}
    for r in records:
        for s in set(sentences(r["text"])):
            sent_owners.setdefault(s, set()).add(r["submission_id"])

    results = []
    for r in records:
        text = r["text"]
        counts, total_art = count_artifacts(text)
        art = _clamp(total_art / cfg["artifact_target"]) if cfg["artifact_target"] else 0.0
        spec, spec_n, tok_n = specificity_ratio(text)
        spec = _clamp(spec)
        ln = _clamp(len(text) / cfg["target_length"]) if cfg["target_length"] else 0.0

        mine = sentences(text)
        shared = [s for s in mine if len(sent_owners.get(s, set())) > 1]
        orig = 1.0 if not mine else _clamp(1.0 - len(shared) / len(mine))

        comps = {ARTIFACTS: round(art, 6), SPECIFICITY: round(spec, 6),
                 LENGTH: round(ln, 6), ORIGINALITY: round(orig, 6)}
        score = round(sum(comps[k] * cfg["weights"][k] for k in comps), 6)
        results.append({
            "submission_id": r["submission_id"],
            "score": score,
            "passed": score >= cfg["threshold"],
            "components": comps,
            "evidence": {
                "characters": len(text),
                "tokens": tok_n,
                "specific_tokens": spec_n,
                "artifact_counts": dict(sorted(counts.items())),
                "artifact_total": total_art,
                "sentences": len(mine),
                "boilerplate_sentences": len(shared),
            },
        })

    results.sort(key=lambda x: (x["score"], x["submission_id"]))
    failed = [r for r in results if not r["passed"]]
    return {
        "report_version": "1.0",
        "config": {
            "weights": {k: cfg["weights"][k] for k in sorted(cfg["weights"])},
            "target_length": cfg["target_length"],
            "artifact_target": cfg["artifact_target"],
            "threshold": cfg["threshold"],
        },
        "totals": {"records": len(results), "passed": len(results) - len(failed),
                   "failed": len(failed)},
        "records": results,
        "status": "pass" if not failed else "fail",
    }


def serialize(report):
    return json.dumps(report, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Score evidence records on objective signals.")
    ap.add_argument("records")
    ap.add_argument("-c", "--config")
    ap.add_argument("-o", "--out")
    ap.add_argument("--threshold", type=float)
    ap.add_argument("--target-length", type=float)
    ap.add_argument("--artifact-target", type=float)
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config, {
            "threshold": args.threshold,
            "target_length": args.target_length,
            "artifact_target": args.artifact_target,
        })
        report = score_all(load_records(args.records), cfg)
    except InputError as exc:
        sys.stderr.write(f"INVALID_INPUT: {exc}\n")
        return 2
    text = serialize(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        sys.stdout.write(f"status={report['status']} passed={report['totals']['passed']} "
                         f"failed={report['totals']['failed']}\n")
    else:
        sys.stdout.write(text)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
