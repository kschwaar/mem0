"""Audit LongMemEval overlap without inference or automatic split assignment."""

import argparse
import hashlib
import json
from pathlib import Path


def plan_history(dataset_path, expected_sha256, question_id, max_chunks=100, max_characters=250000):
    """Verify the original source bytes and plan one untruncated history.

    The expected digest must come from independently reviewed pinned provenance.
    Returning a plan does not verify service readiness or authorize dispatch.
    """
    for cap in (max_chunks, max_characters):
        if isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0:
            raise ValueError("Work caps must be positive integers")
    raw = Path(dataset_path).read_bytes()
    observed = hashlib.sha256(raw).hexdigest()
    if observed != expected_sha256:
        raise ValueError("Dataset bytes do not match pinned provenance")
    records = json.loads(raw)
    matches = [row for row in records if row.get("question_id") == question_id]
    if len(matches) != 1:
        raise ValueError("Require exactly one source record for the selected question")
    question = matches[0]
    sessions = question["haystack_sessions"]
    if (not sessions or len(sessions) != len(question["haystack_session_ids"])
            or len(sessions) != len(question["haystack_dates"])):
        raise ValueError("Source session arrays must be nonempty and aligned")
    chunks = characters = 0
    for session in sessions:
        chunks += (len(session) + 1) // 2
        for turn in session:
            if (turn.get("role") not in {"user", "assistant"}
                    or not isinstance(turn.get("content"), str) or not turn["content"].strip()):
                raise ValueError("Source preprocessing required; no turns silently skipped")
            characters += len(turn["content"])
    if not 1 <= chunks <= max_chunks or characters > max_characters:
        raise ValueError("Complete source history exceeds the declared work budget")
    return {"question_id": question_id, "chunks": chunks, "content_characters": characters,
            "sessions": len(sessions), "dataset_sha256": observed,
            "question_sha256": hashlib.sha256(json.dumps(question, sort_keys=True).encode()).hexdigest(),
            "protocol": "Complete source history; positional two-turn chunks; labels excluded by ingestion adapter",
            "max_chunks": max_chunks, "max_characters": max_characters,
            "quality_acceptance": "unproven"}


def audit(records):
    ids = [record["question_id"] for record in records]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("Require unique nonempty question records")
    parent = list(range(len(records)))

    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    owners = {}
    for index, record in enumerate(records):
        sessions = record["haystack_sessions"]
        session_ids = record["haystack_session_ids"]
        if len(sessions) != len(session_ids):
            raise ValueError("Session arrays must align")
        for session_id, turns in zip(session_ids, sessions):
            # Exclude annotations: equal model-visible conversations must group
            # even if question-specific evidence labels differ.
            content = [{"role": turn["role"], "content": turn["content"]} for turn in turns]
            canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            keys = [("session_id", session_id), ("content", hashlib.sha256(canonical.encode()).hexdigest())]
            for key in keys:
                if key in owners:
                    parent[root(index)] = root(owners[key])
                else:
                    owners[key] = index
    groups = {}
    for index, question_id in enumerate(ids):
        groups.setdefault(root(index), []).append(question_id)
    components = sorted((sorted(group) for group in groups.values()), key=lambda group: (-len(group), group))
    return {
        "questions": len(ids),
        "component_count": len(components),
        "component_sizes": [len(group) for group in components],
        "components": components,
        "protocol": "Connected by shared session IDs or exact ordered role/content SHA-256; labels excluded",
        "limitations": "Does not detect paraphrases, partial-session overlaps or near-duplicates; no split assigned",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.dataset.read_bytes()
    result = audit(json.loads(raw))
    result["dataset_sha256"] = hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({key: value for key, value in result.items() if key != "components"}, indent=2))


if __name__ == "__main__":
    main()
