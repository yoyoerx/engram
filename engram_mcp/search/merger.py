"""Result fusion: deduplicate, weight, and rank vector + graph results."""

from engram_mcp.config import VECTOR_WEIGHT, GRAPH_WEIGHT


def merge(
    vector_results: list[dict],
    graph_results: list[dict],
    limit: int = 10,
) -> list[dict]:
    """
    Merge vector and graph results into a single ranked list.

    Scoring: combined = (VECTOR_WEIGHT * vector_score) + (GRAPH_WEIGHT * graph_score)
    When a chunk appears in both, scores are combined. When only one source has it,
    the missing score is 0.
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

    for item in merged.values():
        item["score"] = (
            VECTOR_WEIGHT * item["_vector_score"]
            + GRAPH_WEIGHT * item["_graph_score"]
        )

    ranked = sorted(merged.values(), key=lambda x: x["score"], reverse=True)

    # Clean up internal scoring fields before returning
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
