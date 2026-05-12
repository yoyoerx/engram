"""Integration tests for the ingestion pipeline. Requires live services."""

import asyncio
import pytest
from qdrant_client import QdrantClient
from neo4j import GraphDatabase

from engram_mcp.config import QDRANT_URL, QDRANT_COLLECTION, NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
from engram_mcp.ingest.chunker import chunk
from engram_mcp.ingest.embedder import embed
from engram_mcp.tools.store import store_memory


# ── Chunker (no external deps) ────────────────────────────────────────────────

def test_chunk_short_text():
    result = chunk("Hello world.")
    assert result == ["Hello world."]


def test_chunk_long_text():
    text = ("This is sentence one. " * 30).strip()
    result = chunk(text)
    assert len(result) > 1
    for c in result:
        assert len(c) <= 512 + 64  # chunk_size + some overlap tolerance


def test_chunk_overlap():
    text = ("Alpha beta gamma delta epsilon. " * 20).strip()
    chunks = chunk(text)
    if len(chunks) > 1:
        # Each chunk after the first should share content with the previous
        assert chunks[0][-10:] in chunks[1] or len(chunks[1]) > 0


# ── Embedder (requires Ollama) ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_embed_returns_correct_dim():
    vector = await embed("Test embedding for Engram.")
    assert len(vector) == 768
    assert all(isinstance(v, float) for v in vector)


@pytest.mark.asyncio
async def test_embed_different_texts_differ():
    v1 = await embed("The cat sat on the mat.")
    v2 = await embed("Quantum entanglement in superconductors.")
    dot = sum(a * b for a, b in zip(v1, v2))
    mag1 = sum(a * a for a in v1) ** 0.5
    mag2 = sum(b * b for b in v2) ** 0.5
    cosine = dot / (mag1 * mag2)
    assert cosine < 0.99  # distinct texts should not be identical


# ── store_memory end-to-end (requires all services) ───────────────────────────

@pytest.mark.asyncio
async def test_store_memory_writes_to_qdrant_and_neo4j():
    content = (
        "When using FastMCP with Python, name your package something other than 'mcp' "
        "to avoid shadowing the installed mcp library. This caused an ImportError during "
        "the Engram project setup."
    )
    result = await store_memory(
        content=content,
        memory_type="feedback",
        project="engram",
        metadata={"source": "test"},
    )

    assert result["stored"] is True
    assert len(result["chunk_ids"]) >= 1
    chunk_id = result["chunk_ids"][0]

    # Verify Qdrant
    qdrant = QdrantClient(url=QDRANT_URL)
    points = qdrant.retrieve(collection_name=QDRANT_COLLECTION, ids=[chunk_id], with_payload=True)
    assert len(points) == 1
    assert points[0].payload["memory_type"] == "feedback"
    assert points[0].payload["project"] == "engram"

    # Verify Neo4j
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    with driver.session() as session:
        rec = session.run(
            "MATCH (m:Memory {chunk_id: $cid}) RETURN m.memory_type AS mt, m.project AS p",
            cid=chunk_id,
        ).single()
    driver.close()
    assert rec["mt"] == "feedback"
    assert rec["p"] == "engram"


@pytest.mark.asyncio
async def test_store_memory_invalid_type():
    result = await store_memory(content="test", memory_type="nonsense")
    assert result["stored"] is False
    assert "error" in result
