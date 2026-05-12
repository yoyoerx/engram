"""Qdrant semantic search."""

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny

from engram_mcp.config import QDRANT_URL, QDRANT_COLLECTION


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
            "chunk_id": p.get("chunk_id", hit.id),
            "content": p.get("content", ""),
            "score": hit.score,
            "memory_type": p.get("memory_type"),
            "project": p.get("project"),
            "timestamp": p.get("timestamp"),
            "neo4j_node_id": p.get("neo4j_node_id"),
            "_source": "vector",
        })

    return results
