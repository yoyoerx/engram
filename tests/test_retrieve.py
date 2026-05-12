"""Integration tests for the retrieval engine. Requires live services + seeded data."""

import pytest
from engram_mcp.tools.store import store_memory
from engram_mcp.tools.retrieve import retrieve_context
from engram_mcp.search.merger import merge


# ── Merger unit tests (no external deps) ─────────────────────────────────────

def test_merge_vector_only():
    vector = [{"chunk_id": "a", "content": "alpha", "score": 0.9,
               "memory_type": "feedback", "project": None, "timestamp": None,
               "neo4j_node_id": None, "_source": "vector"}]
    result = merge(vector, [], limit=5)
    assert len(result) == 1
    assert result[0]["chunk_id"] == "a"
    assert result[0]["score"] == pytest.approx(0.9 * 0.6, abs=1e-4)


def test_merge_graph_only():
    graph = [{"chunk_id": "b", "content": "beta", "score": 1.0,
              "memory_type": "project", "project": "engram", "timestamp": None,
              "neo4j_node_id": None, "_source": "graph"}]
    result = merge([], graph, limit=5)
    assert result[0]["score"] == pytest.approx(1.0 * 0.4, abs=1e-4)


def test_merge_both_sources_combines_scores():
    shared_id = "shared"
    vector = [{"chunk_id": shared_id, "content": "gamma", "score": 0.8,
               "memory_type": "feedback", "project": None, "timestamp": None,
               "neo4j_node_id": None, "_source": "vector"}]
    graph = [{"chunk_id": shared_id, "content": "gamma", "score": 1.0,
              "memory_type": "feedback", "project": None, "timestamp": None,
              "neo4j_node_id": None, "_source": "graph"}]
    result = merge(vector, graph, limit=5)
    assert len(result) == 1
    assert result[0]["source"] == "both"
    expected = 0.6 * 0.8 + 0.4 * 1.0
    assert result[0]["score"] == pytest.approx(expected, abs=1e-4)


def test_merge_respects_limit():
    vector = [
        {"chunk_id": str(i), "content": f"item {i}", "score": 1.0 - i * 0.1,
         "memory_type": "feedback", "project": None, "timestamp": None,
         "neo4j_node_id": None, "_source": "vector"}
        for i in range(10)
    ]
    result = merge(vector, [], limit=3)
    assert len(result) == 3


def test_merge_sorted_descending():
    vector = [
        {"chunk_id": "low", "content": "low", "score": 0.3,
         "memory_type": "feedback", "project": None, "timestamp": None,
         "neo4j_node_id": None, "_source": "vector"},
        {"chunk_id": "high", "content": "high", "score": 0.95,
         "memory_type": "feedback", "project": None, "timestamp": None,
         "neo4j_node_id": None, "_source": "vector"},
    ]
    result = merge(vector, [], limit=5)
    assert result[0]["chunk_id"] == "high"


# ── retrieve_context end-to-end (requires all services) ──────────────────────

@pytest.fixture(scope="module")
def event_loop_policy():
    import asyncio
    return asyncio.DefaultEventLoopPolicy()


@pytest.mark.asyncio
async def test_retrieve_context_returns_results():
    # Seed a known memory first
    await store_memory(
        content=(
            "The Neo4j knowledge graph stores entity relationships for Engram. "
            "Nodes include Memory, Project, Feedback, and Tool."
        ),
        memory_type="project",
        project="engram",
    )

    results = await retrieve_context(
        query="How does Engram store knowledge graph entities?",
        limit=5,
        project="engram",
    )

    assert isinstance(results, list)
    assert len(results) >= 1
    assert all("chunk_id" in r for r in results)
    assert all("content" in r for r in results)
    assert all("score" in r for r in results)
    # Scores should be in descending order
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_retrieve_context_filter_by_type():
    await store_memory(
        content="Always use async/await when calling the Ollama embedding endpoint.",
        memory_type="feedback",
        project="engram",
    )

    results = await retrieve_context(
        query="Ollama embedding async",
        limit=5,
        memory_types=["feedback"],
    )

    assert all(r["memory_type"] == "feedback" for r in results)


@pytest.mark.asyncio
async def test_retrieve_context_empty_query_returns_list():
    results = await retrieve_context(query="zzz_unlikely_match_xqz", limit=3)
    assert isinstance(results, list)
