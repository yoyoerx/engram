"""update_memory, forget, list_memories tools."""

import uuid
from datetime import datetime, timezone
from typing import Annotated

from pydantic import Field
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, FieldCondition, Filter, MatchValue, MatchAny
from engram_mcp.config import (
    MEMORY_TYPES, QDRANT_URL, QDRANT_COLLECTION,
    NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
)
from engram_mcp.retry import neo4j_driver as _neo4j_driver
from engram_mcp.ingest.chunker import chunk
from engram_mcp.ingest.embedder import embed
from engram_mcp.ingest.extractor import extract


async def update_memory(
    chunk_id: Annotated[str, Field(description="ID of the chunk to update.")],
    content: Annotated[str, Field(description="New memory content.")],
    metadata: Annotated[
        dict | None,
        Field(description="Updated metadata (optional)."),
    ] = None,
) -> dict:
    """Update an existing memory chunk. Tombstones the old chunk and creates a SUPERSEDES edge."""
    qdrant = QdrantClient(url=QDRANT_URL)

    # Fetch old chunk's metadata from Qdrant
    old_points = qdrant.retrieve(
        collection_name=QDRANT_COLLECTION, ids=[chunk_id], with_payload=True
    )
    if not old_points:
        return {"updated": False, "error": f"chunk_id '{chunk_id}' not found."}

    old_payload = old_points[0].payload or {}
    memory_type = old_payload.get("memory_type", "feedback")
    project = old_payload.get("project")

    new_chunk_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    extracted = extract(content)
    vector = await embed(content)

    new_payload = {
        "chunk_id": new_chunk_id,
        "content": content,
        "memory_type": memory_type,
        "project": project,
        "timestamp": timestamp,
        **(metadata or {}),
    }
    qdrant.upsert(
        collection_name=QDRANT_COLLECTION,
        points=[PointStruct(id=new_chunk_id, vector=vector, payload=new_payload)],
    )

    with _neo4j_driver(NEO4J_URI, (NEO4J_USER, NEO4J_PASSWORD)) as driver:
        with driver.session() as session:
            session.run(
                "MATCH (m:Memory {chunk_id: $cid}) SET m.tombstone = true",
                cid=chunk_id,
            )
        with driver.session() as session:
            session.run(
                """
                MERGE (m:Memory {chunk_id: $cid})
                SET m.content = $content, m.memory_type = $type,
                    m.project = $project, m.timestamp = $ts, m.tombstone = false
                """,
                cid=new_chunk_id, content=content, type=memory_type,
                project=project, ts=timestamp,
            )
            session.run(
                """
                MATCH (new:Memory {chunk_id: $new_id})
                MATCH (old:Memory {chunk_id: $old_id})
                MERGE (new)-[:SUPERSEDES]->(old)
                """,
                new_id=new_chunk_id, old_id=chunk_id,
            )

    return {"updated": True, "old_chunk_id": chunk_id, "new_chunk_id": new_chunk_id}


async def forget(
    chunk_id: Annotated[str, Field(description="ID of the chunk to forget.")],
    hard: Annotated[
        bool,
        Field(description="Permanently delete if true; tombstone only if false (default)."),
    ] = False,
) -> dict:
    """Soft-delete (tombstone) or hard-delete a memory chunk."""
    qdrant = QdrantClient(url=QDRANT_URL)

    if hard:
        qdrant.delete(
            collection_name=QDRANT_COLLECTION,
            points_selector=[chunk_id],
        )
        with _neo4j_driver(NEO4J_URI, (NEO4J_USER, NEO4J_PASSWORD)) as driver:
            with driver.session() as session:
                session.run(
                    "MATCH (m:Memory {chunk_id: $cid}) DETACH DELETE m",
                    cid=chunk_id,
                )
        return {"forgotten": True, "hard": True, "chunk_id": chunk_id}
    else:
        with _neo4j_driver(NEO4J_URI, (NEO4J_USER, NEO4J_PASSWORD)) as driver:
            with driver.session() as session:
                session.run(
                    "MATCH (m:Memory {chunk_id: $cid}) SET m.tombstone = true",
                    cid=chunk_id,
                )
        return {"forgotten": True, "hard": False, "chunk_id": chunk_id}


async def list_memories(
    memory_type: Annotated[
        str | None,
        Field(description=f"Filter by type. Options: {', '.join(sorted(MEMORY_TYPES))}."),
    ] = None,
    project: Annotated[
        str | None,
        Field(description="Filter by project name (optional)."),
    ] = None,
    limit: Annotated[
        int,
        Field(description="Maximum results to return.", ge=1, le=200),
    ] = 50,
) -> list[dict]:
    """List stored memories with optional filters."""
    must = [FieldCondition(key="tombstone", match=MatchValue(value=False))]

    if memory_type:
        must.append(FieldCondition(key="memory_type", match=MatchValue(value=memory_type)))
    if project:
        must.append(FieldCondition(key="project", match=MatchValue(value=project)))

    qdrant = QdrantClient(url=QDRANT_URL)
    results, _ = qdrant.scroll(
        collection_name=QDRANT_COLLECTION,
        scroll_filter=Filter(must=must) if must else None,
        limit=limit,
        with_payload=True,
        with_vectors=False,
    )

    return [
        {
            "chunk_id": p.payload.get("chunk_id", p.id),
            "content_preview": (p.payload.get("content", "")[:120] + "…")
                if len(p.payload.get("content", "")) > 120
                else p.payload.get("content", ""),
            "memory_type": p.payload.get("memory_type"),
            "project": p.payload.get("project"),
            "timestamp": p.payload.get("timestamp"),
        }
        for p in results
    ]
