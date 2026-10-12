"""Exact turn-text overlap audit; no inference or acceptance assignment."""

import argparse
import hashlib
import json
import re
from pathlib import Path


def fingerprint(text):
    if not isinstance(text, str):
        raise ValueError("Turn text must be a string")
    if not text.strip():
        return None
    # Conservative normalization: remove only whitespace differences.
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode()).hexdigest()


def compare(longmem, locomo):
    left = {}
    blank_left = 0
    for item in longmem:
        for session in item["haystack_sessions"]:
            for turn in session:
                digest = fingerprint(turn["content"])
                if digest is None:
                    blank_left += 1
                    continue
                left.setdefault(digest, set()).add(item["question_id"])
    matches = []
    turns = 0
    blank_right = 0
    for item in locomo:
        for key, session in item["conversation"].items():
            if not re.fullmatch(r"session_\d+", key):
                continue
            for turn in session:
                turns += 1
                digest = fingerprint(turn["text"])
                if digest is None:
                    blank_right += 1
                    continue
                if digest in left:
                    matches.append({
                        "sample_id": item["sample_id"], "session": key,
                        "dialog_id": turn["dia_id"], "text_sha256": digest,
                        "longmemeval_question_ids": sorted(left[digest]),
                    })
    return {
        "locomo_turns_checked": turns, "matched_turns": len(matches), "matches": matches,
        "blank_turns_excluded": {"longmemeval": blank_left, "locomo": blank_right},
        "protocol": "Case-sensitive whole-turn text equality after whitespace normalization; annotations excluded",
        "limitations": "No paraphrase, substring, semantic or model-training contamination detection",
        "acceptance_independence": "unproven",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--longmemeval", type=Path, required=True)
    parser.add_argument("--locomo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    left, right = args.longmemeval.read_bytes(), args.locomo.read_bytes()
    result = compare(json.loads(left), json.loads(right))
    result["source_sha256"] = {
        "longmemeval": hashlib.sha256(left).hexdigest(), "locomo": hashlib.sha256(right).hexdigest(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({key: value for key, value in result.items() if key != "matches"}, indent=2))


if __name__ == "__main__":
    main()
