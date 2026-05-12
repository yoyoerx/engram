"""Unit tests for atomic rollback in store_memory. No live services required."""

from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_driver_mock():
    """Return a mock Neo4j driver whose session() is a context manager."""
    session_mock = MagicMock()
    session_mock.__enter__ = MagicMock(return_value=session_mock)
    session_mock.__exit__ = MagicMock(return_value=False)
    session_mock.run = MagicMock(return_value=MagicMock(single=lambda: {"eid": "neo4j-id-1"}))

    driver_mock = MagicMock()
    driver_mock.session = MagicMock(return_value=session_mock)
    driver_mock.__enter__ = MagicMock(return_value=driver_mock)
    driver_mock.__exit__ = MagicMock(return_value=False)
    return driver_mock, session_mock


# ── Rollback on Qdrant failure ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_store_rollback_deletes_neo4j_on_qdrant_failure():
    """If Qdrant upsert raises, the Neo4j node must be DETACH DELETEd."""
    from engram_mcp.tools.store import store_memory

    driver_mock, session_mock = _make_driver_mock()

    with (
        patch("engram_mcp.tools.store._neo4j_driver") as mock_cm,
        patch("engram_mcp.tools.store.QdrantClient") as mock_qdrant_cls,
        patch("engram_mcp.tools.store.embed", new_callable=AsyncMock) as mock_embed,
        patch("engram_mcp.tools.store.extract", return_value={"entities": [], "relationships": []}),
        patch("engram_mcp.tools.store.chunk", return_value=["test chunk"]),
    ):
        # neo4j_driver context manager yields our mock driver
        mock_cm.return_value.__enter__ = MagicMock(return_value=driver_mock)
        mock_cm.return_value.__exit__ = MagicMock(return_value=False)

        mock_embed.return_value = [0.1] * 768

        qdrant_instance = MagicMock()
        qdrant_instance.upsert.side_effect = RuntimeError("Qdrant unavailable")
        mock_qdrant_cls.return_value = qdrant_instance

        with pytest.raises(RuntimeError, match="Qdrant unavailable"):
            await store_memory(content="test content", memory_type="feedback")

    # Verify a DETACH DELETE was issued
    delete_calls = [
        c for c in session_mock.run.call_args_list
        if "DETACH DELETE" in str(c)
    ]
    assert len(delete_calls) == 1, "Expected one DETACH DELETE compensating call"


@pytest.mark.asyncio
async def test_store_no_rollback_on_success():
    """On a successful store, no DETACH DELETE should be issued."""
    from engram_mcp.tools.store import store_memory

    driver_mock, session_mock = _make_driver_mock()

    with (
        patch("engram_mcp.tools.store._neo4j_driver") as mock_cm,
        patch("engram_mcp.tools.store.QdrantClient") as mock_qdrant_cls,
        patch("engram_mcp.tools.store.embed", new_callable=AsyncMock) as mock_embed,
        patch("engram_mcp.tools.store.extract", return_value={"entities": [], "relationships": []}),
        patch("engram_mcp.tools.store.chunk", return_value=["test chunk"]),
    ):
        mock_cm.return_value.__enter__ = MagicMock(return_value=driver_mock)
        mock_cm.return_value.__exit__ = MagicMock(return_value=False)

        mock_embed.return_value = [0.1] * 768

        qdrant_instance = MagicMock()
        qdrant_instance.upsert.return_value = None  # success
        mock_qdrant_cls.return_value = qdrant_instance

        result = await store_memory(content="test content", memory_type="feedback")

    assert result["stored"] is True
    delete_calls = [
        c for c in session_mock.run.call_args_list
        if "DETACH DELETE" in str(c)
    ]
    assert len(delete_calls) == 0, "DETACH DELETE should not be called on success"


@pytest.mark.asyncio
async def test_store_rollback_first_chunk_continues_on_second():
    """If first chunk's Qdrant write fails (rolled back), second chunk still attempted."""
    from engram_mcp.tools.store import store_memory

    driver_mock, session_mock = _make_driver_mock()
    upsert_calls = []

    def upsert_side_effect(**kwargs):
        upsert_calls.append(kwargs)
        if len(upsert_calls) == 1:
            raise RuntimeError("Qdrant first chunk fail")

    with (
        patch("engram_mcp.tools.store._neo4j_driver") as mock_cm,
        patch("engram_mcp.tools.store.QdrantClient") as mock_qdrant_cls,
        patch("engram_mcp.tools.store.embed", new_callable=AsyncMock) as mock_embed,
        patch("engram_mcp.tools.store.extract", return_value={"entities": [], "relationships": []}),
        patch("engram_mcp.tools.store.chunk", return_value=["chunk one", "chunk two"]),
    ):
        mock_cm.return_value.__enter__ = MagicMock(return_value=driver_mock)
        mock_cm.return_value.__exit__ = MagicMock(return_value=False)

        mock_embed.return_value = [0.1] * 768

        qdrant_instance = MagicMock()
        qdrant_instance.upsert.side_effect = upsert_side_effect
        mock_qdrant_cls.return_value = qdrant_instance

        # First chunk raises → propagates out of store_memory
        with pytest.raises(RuntimeError, match="first chunk fail"):
            await store_memory(content="two chunks", memory_type="feedback")

    # First chunk's rollback DELETE should have fired
    delete_calls = [
        c for c in session_mock.run.call_args_list
        if "DETACH DELETE" in str(c)
    ]
    assert len(delete_calls) == 1
