"""Result fusion: deduplicate, weight, rank, and apply session-based decay."""

import json
import math
from pathlib import Path

from engram_mcp.config import VECTOR_WEIGHT, GRAPH_WEIGHT, DECAY_LAMBDA, USAGE_BOOST_MAX, ENGRAM_STATS_PATH


def _current_session() -> int:
    try:
        stats = json.loads(Path(ENGRAM_STATS_PATH).read_text(encoding="utf-8"))
        return int(stats.get("session_count", 0))
    except Exception:
        return 0


def _adjusted_score(base: float, item: dict, current_session: int) -> float:
    """Apply session-count decay and usage boost to a combined base score."""
    retrieval_count = item.get("retrieval_count", 0)
    last_retrieved_session = item.get("last_retrieved_session")

    if last_retrieved_session is None or retrieval_count == 0:
        return base  # never retrieved — no decay, no boost

    sessions_since = max(0, current_session - int(last_retrieved_session))
    freshness = math.exp(-DECAY_LAMBDA * sessions_since)
    usage_boost = min(USAGE_BOOST_MAX, 0.1 * retrieval_count)
    return base * (1.0 + usage_boost) * freshness


def merge(
    vector_results: list[dict],
    graph_results: list[dict],
    limit: int = 10,
) -> list[dict]:
    """
    Merge vector and graph results into a single ranked list.

    Scoring: combined = (VECTOR_WEIGHT * vector_score) + (GRAPH_WEIGHT * graph_score)
    Then decay-adjusted by session-count: recently retrieved memories rank higher,
    stale memories (many sessions without retrieval) rank lower.
    """
    merged: dict[str, dict] = {}

    for r in vector_results:
        cid = r["chunk_id"]
        merged[cid] = {**r, "_vector_score": r["score"], "_graph_score": 0.0}

    for r in graph_results:
        cid = r["chunk_id"]
        if cid in merged:
            merged[cid]["_graph_score"] = r["score"]
            merged[cid]["_source"] = "both"
        else:
            merged[cid] = {**r, "_vector_score": 0.0, "_graph_score": r["score"]}

    current_session = _current_session()

    for item in merged.values():
        base = (
            VECTOR_WEIGHT * item["_vector_score"]
            + GRAPH_WEIGHT * item["_graph_score"]
        )
        item["score"] = _adjusted_score(base, item, current_session)

    ranked = sorted(merged.values(), key=lambda x: x["score"], reverse=True)

    clean = []
    for item in ranked[:limit]:
        clean.append({
            "chunk_id": item["chunk_id"],
            "content": item["content"],
            "score": round(item["score"], 4),
            "memory_type": item.get("memory_type"),
            "project": item.get("project"),
            "timestamp": item.get("timestamp"),
            "source": item.get("_source", "unknown"),
        })

    return clean
