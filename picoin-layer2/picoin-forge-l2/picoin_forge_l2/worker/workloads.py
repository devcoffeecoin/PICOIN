from __future__ import annotations

import re

from picoin_forge_l2.common.hashing import hash_json, sha256_text
from picoin_forge_l2.common.models import WorkloadTask, WorkloadType

STOP_WORDS = {
    "about",
    "after",
    "also",
    "and",
    "because",
    "from",
    "have",
    "into",
    "that",
    "the",
    "their",
    "this",
    "with",
    "will",
}


def compute_workload_result_hash(task_type: WorkloadType, payload: dict) -> str:
    if task_type == WorkloadType.HASH_TEXT:
        return sha256_text(str(payload.get("text", "")))
    if task_type == WorkloadType.TEXT_CLASSIFY:
        return hash_json({"task_type": task_type.value, "result": classify_text_payload(payload)})
    if task_type == WorkloadType.BATCH_SUMMARIZE:
        return hash_json({"task_type": task_type.value, "result": summarize_text_payload(payload)})
    if task_type == WorkloadType.TEXT_EMBED:
        return hash_json({"task_type": task_type.value, "result": embed_text_payload(payload)})
    raise ValueError(f"unsupported workload type: {task_type}")


def solve_workload(task: WorkloadTask) -> str:
    return compute_workload_result_hash(task.task_type, task.payload)


def workload_task_id(task_type: WorkloadType, payload: dict, created_at: str) -> str:
    return "workload_" + hash_json(
        {
            "task_type": task_type.value,
            "payload": payload,
            "created_at": created_at,
        }
    )[:16]


def classify_text_payload(payload: dict) -> dict:
    text = str(payload.get("text", "")).lower()
    labels = payload.get("labels") or {}
    default_label = str(payload.get("default_label", "unknown"))
    scores: dict[str, int] = {}
    if not isinstance(labels, dict):
        return {"label": default_label, "scores": scores}
    for label, keywords in labels.items():
        if not isinstance(keywords, list):
            scores[str(label)] = 0
            continue
        scores[str(label)] = sum(text.count(str(keyword).lower()) for keyword in keywords if str(keyword))
    positive_scores = [(label, score) for label, score in scores.items() if score > 0]
    if not positive_scores:
        return {"label": default_label, "scores": scores}
    best_score = max(score for _, score in positive_scores)
    winners = sorted(label for label, score in positive_scores if score == best_score)
    if len(winners) > 1:
        return {"label": default_label, "scores": scores}
    return {"label": winners[0], "scores": scores}


def summarize_text_payload(payload: dict) -> dict:
    documents = payload.get("documents")
    if isinstance(documents, list):
        text = "\n".join(str(document) for document in documents)
    else:
        text = str(payload.get("text", ""))
    max_sentences = _safe_int(payload.get("max_sentences", 3), default=3, minimum=1, maximum=20)
    sentences = _split_sentences(text)
    if not sentences:
        return {"summary": "", "sentences": [], "sentence_count": 0}

    frequencies: dict[str, int] = {}
    for sentence in sentences:
        for word in _words(sentence):
            frequencies[word] = frequencies.get(word, 0) + 1

    scored = []
    for index, sentence in enumerate(sentences):
        score = sum(frequencies.get(word, 0) for word in _words(sentence))
        scored.append((index, score, sentence))
    selected = sorted(sorted(scored, key=lambda item: (-item[1], item[0]))[:max_sentences], key=lambda item: item[0])
    selected_sentences = [sentence for _, _, sentence in selected]
    return {
        "summary": " ".join(selected_sentences),
        "sentences": selected_sentences,
        "sentence_count": len(sentences),
    }


def embed_text_payload(payload: dict) -> dict:
    documents = payload.get("documents")
    if isinstance(documents, list):
        text = "\n".join(str(document) for document in documents)
    else:
        text = str(payload.get("text", ""))
    dimensions = _safe_int(payload.get("dimensions", 16), default=16, minimum=4, maximum=256)
    tokens = _words(text)
    vector = [0.0 for _ in range(dimensions)]
    for token in tokens:
        digest = sha256_text(token)
        bucket = int(digest[:8], 16) % dimensions
        sign = 1.0 if int(digest[8:10], 16) % 2 == 0 else -1.0
        weight = 1.0 + (int(digest[10:12], 16) % 7) / 10.0
        vector[bucket] += sign * weight
    norm = sum(abs(value) for value in vector) or 1.0
    normalized = [round(value / norm, 8) for value in vector]
    return {
        "dimensions": dimensions,
        "token_count": len(tokens),
        "vector": normalized,
        "vector_hash": hash_json(normalized),
    }


def _split_sentences(text: str) -> list[str]:
    compact = re.sub(r"\s+", " ", text.strip())
    if not compact:
        return []
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", compact) if part.strip()]


def _words(text: str) -> list[str]:
    return [
        word
        for word in re.findall(r"[a-z0-9]+", text.lower())
        if len(word) >= 3 and word not in STOP_WORDS
    ]


def _safe_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(number, maximum))
