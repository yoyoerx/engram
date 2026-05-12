"""store_memory tool — full implementation."""

import uuid
from datetime import datetime, timezone
from typing import Annotated

from pydantic import Field
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from engram_mcp.config import (
    MEMORY_TYPES, QDRANT_URL, QDRANT_COLLECTION,
    NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
)
from engram_mcp.retry import neo4j_driver as _neo4j_driver
from engram_mcp.logger import get_logger
from engram_mcp.ingest.chunker import chunk
from engram_mcp.ingest.embedder import embed
from engram_mcp.ingest.extractor import extract

_log = get_logger("store")


def _upsert_graph(driver, chunk_id: str, content: str, memory_type: str,
                  project: str | None, timestamp: str, extracted: dict) -> str:
    """Write Memory node + extracted entities/relationships to Neo4j. Returns element ID."""
    with driver.session() as session:
        result = session.run(
            """
            MERGE (m:Memory {chunk_id: $chunk_id})
            SET m.content = $content,
                m.memory_type = $memory_type,
                m.project = $project,
                m.timestamp = $timestamp,
                m.tombstone = false
            RETURN elementId(m) AS eid
            """,
            chunk_id=chunk_id, content=content, memory_type=memory_type,
            project=project, timestamp=timestamp,
        )
        neo4j_id = result.single()["eid"]

        entity_names = set()
        for entity in extracted.get("entities", []):
            name = entity.get("name", "").strip()
            label = entity.get("label", "Concept").strip()
            if not name:
                continue
            entity_names.add(name)
            props = entity.get("properties", {})
            session.run(
                f"MERGE (e:{label} {{name: $name}}) SET e += $props",
                name=name, props=props,
            )
            session.run(
                """
                MATCH (m:Memory {chunk_id: $chunk_id})
                MATCH (e {name: $name})
                MERGE (m)-[:ABOUT]->(e)
                """,
                chunk_id=chunk_id, name=name,
            )

        valid_rel_types = {
            "APPLIES_TO", "PREVENTS", "CAUSED_BY", "USES", "INVOLVES",
            "SUPERSEDES", "SIMILAR_TO", "LINKED_TO", "ABOUT",
        }
        for rel in extracted.get("relationships", []):
            frm = rel.get("from", "").strip()
            rel_type = rel.get("type", "LINKED_TO").strip().upper()
            to = rel.get("to", "").strip()
            if not frm or not to or frm not in entity_names or to not in entity_names:
                continue
            if rel_type not in valid_rel_types:
                rel_type = "LINKED_TO"
            session.run(
                f"MATCH (a {{name: $f}}) MATCH (b {{name: $t}}) MERGE (a)-[:{rel_type}]->(b)",
                f=frm, t=to,
            )

        if project:
            session.run(
                """
                MERGE (p:Project {name: $project})
                WITH p
                MATCH (m:Memory {chunk_id: $chunk_id})
                MERGE (m)-[:INVOLVES]->(p)
                """,
                project=project, chunk_id=chunk_id,
            )

    return neo4j_id


async def store_memory(
    content: Annotated[str, Field(description="The memory text to store.")],
    memory_type: Annotated[
        str,
        Field(description=f"Memory category. One of: {', '.join(sorted(MEMORY_TYPES))}."),
    ],
    project: Annotated[
        str | None,
        Field(description="Project name to scope this memory (optional)."),
    ] = None,
    metadata: Annotated[
        dict | None,
        Field(description="Additional key-value metadata (optional)."),
    ] = None,
) -> dict:
    """Store a memory chunk in the vector store and knowledge graph."""
    if memory_type not in MEMORY_TYPES:
        return {
            "stored": False,
            "error": f"Invalid memory_type '{memory_type}'. Must be one of: {sorted(MEMORY_TYPES)}",
        }

    qdrant = QdrantClient(url=QDRANT_URL)
    timestamp = datetime.now(timezone.utc).isoformat()
    stored_ids: list[str] = []
    total_entities = 0

    with _neo4j_driver(NEO4J_URI, (NEO4J_USER, NEO4J_PASSWORD)) as driver:
        for chunk_text in chunk(content):
            chunk_id = str(uuid.uuid4())
            extracted = extract(chunk_text)
            total_entities += len(extracted.get("entities", []))
            vector = await embed(chunk_text)

            neo4j_id = _upsert_graph(
                driver, chunk_id, chunk_text,
                memory_type, project, timestamp, extracted,
            )

            payload = {
                "chunk_id": chunk_id,
                "content": chunk_text,
                "memory_type": memory_type,
                "project": project,
                "timestamp": timestamp,
                "neo4j_node_id": neo4j_id,
                **(metadata or {}),
            }
            try:
                qdrant.upsert(
                    collection_name=QDRANT_COLLECTION,
                    points=[PointStruct(id=chunk_id, vector=vector, payload=payload)],
                )
            except Exception as qdrant_exc:
                # Compensating delete: remove the Neo4j node we just wrote.
                try:
                    with driver.session() as session:
                        session.run(
                            "MATCH (m:Memory {chunk_id: $cid}) DETACH DELETE m",
                            cid=chunk_id,
                        )
                except Exception as rollback_exc:
                    _log.error(
                        "rollback failed — orphaned Neo4j node",
                        extra={"chunk_id": chunk_id, "exc": str(rollback_exc)},
                    )
                raise qdrant_exc

            with driver.session() as session:
                session.run(
                    "MATCH (m:Memory {chunk_id: $cid}) SET m.vector_id = $cid",
                    cid=chunk_id,
                )

            stored_ids.append(chunk_id)

    _log.info(
        "memory stored",
        extra={
            "chunks": len(stored_ids),
            "entities": total_entities,
            "memory_type": memory_type,
            "project": project,
        },
    )
    return {
        "stored": True,
        "chunk_ids": stored_ids,
        "chunks_written": len(stored_ids),
        "entities_extracted": total_entities,
    }
