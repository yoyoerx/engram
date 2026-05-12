"""retrieve_context tool — full implementation."""

import asyncio
from typing import Annotated
from pydantic import Field

from engram_mcp.config import DEFAULT_RETRIEVE_LIMIT, MEMORY_TYPES
from engram_mcp.ingest.embedder import embed
from engram_mcp.search.vector import search as vector_search
from engram_mcp.search.graph import traverse as graph_traverse
from engram_mcp.search.merger import merge


async def retrieve_context(
    query: Annotated[str, Field(description="Natural language query to search memories.")],
    limit: Annotated[
        int,
        Field(description="Maximum number of results to return.", ge=1, le=50),
    ] = DEFAULT_RETRIEVE_LIMIT,
    memory_types: Annotated[
        list[str] | None,
        Field(description=f"Filter to specific memory types. Options: {', '.join(sorted(MEMORY_TYPES))}."),
    ] = None,
    project: Annotated[
        str | None,
        Field(description="Scope search to a specific project (optional)."),
    ] = None,
) -> list[dict]:
    """Retrieve relevant memories using hybrid vector + graph search."""
    fetch_limit = min(limit * 3, 50)  # over-fetch before merging

    # Embed query and run vector + graph searches in parallel
    query_vector, graph_results = await asyncio.gather(
        embed(query),
        asyncio.to_thread(graph_traverse, query, fetch_limit, memory_types, project),
    )

    vector_results = await asyncio.to_thread(
        vector_search, query_vector, fetch_limit, memory_types, project
    )

    return merge(vector_results, graph_results, limit=limit)
