"""Qdrant semantic search."""

import json
from datetime import datetime, timezone
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny, SetPayload

from engram_mcp.config import QDRANT_URL, QDRANT_COLLECTION, ENGRAM_STATS_PATH


def _current_session() -> int:
    """Read the current session count from stats.json. Returns 0 if unavailable."""
    try:
        stats = json.loads(Path(ENGRAM_STATS_PATH).read_text(encoding="utf-8"))
        return int(stats.get("session_count", 0))
    except Exception:
        return 0


def _update_retrieval_stats(client: QdrantClient, hits: list) -> None:
    """Fire-and-forget: increment retrieval_count and set last_retrieved_session on returned hits."""
    session = _current_session()
    now = datetime.now(timezone.utc).isoformat()

    for hit in hits:
        p = hit.payload or {}
        chunk_id = p.get("chunk_id", str(hit.id))
        new_count = p.get("retrieval_count", 0) + 1
        try:
            client.set_payload(
                collection_name=QDRANT_COLLECTION,
                payload={
                    "retrieval_count": new_count,
                    "last_retrieved_session": session,
                    "last_retrieved": now,
                },
                points=[hit.id],
            )
        except Exception:
            pass


def search(
    query_vector: list[float],
    limit: int = 20,
    memory_types: list[str] | None = None,
    project: str | None = None,
) -> list[dict]:
    """
    Return up to `limit` nearest neighbours from Qdrant, excluding tombstoned memories.
    Each result: {chunk_id, content, score, memory_type, project, timestamp, neo4j_node_id}.
    """
    must = []

    if memory_types:
        must.append(FieldCondition(key="memory_type", match=MatchAny(any=memory_types)))

    if project:
        must.append(FieldCondition(key="project", match=MatchValue(value=project)))

    qdrant_filter = Filter(must=must) if must else None

    client = QdrantClient(url=QDRANT_URL)
    response = client.query_points(
        collection_name=QDRANT_COLLECTION,
        query=query_vector,
        limit=limit,
        query_filter=qdrant_filter,
        with_payload=True,
    )
    hits = response.points

    results = []
    for hit in hits:
        p = hit.payload or {}
        results.append({
            "chunk_id": p.get("chunk_id", str(hit.id)),
            "content": p.get("content", ""),
            "score": hit.score,
            "memory_type": p.get("memory_type"),
            "project": p.get("project"),
            "timestamp": p.get("timestamp"),
            "neo4j_node_id": p.get("neo4j_node_id"),
            "retrieval_count": p.get("retrieval_count", 0),
            "last_retrieved_session": p.get("last_retrieved_session"),
            "_source": "vector",
        })

    _update_retrieval_stats(client, hits)

    return results
