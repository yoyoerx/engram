"""get_related tool."""

import asyncio
from typing import Annotated
from pydantic import Field
from engram_mcp.search.graph import get_related_entities


async def get_related(
    entity: Annotated[str, Field(description="Entity name to start graph traversal from.")],
    relationship: Annotated[
        str | None,
        Field(description="Filter by relationship type, e.g. APPLIES_TO, USES, PREVENTS (optional)."),
    ] = None,
    depth: Annotated[
        int,
        Field(description="Maximum hops to traverse (1–3).", ge=1, le=3),
    ] = 2,
) -> dict:
    """Traverse the knowledge graph from a named entity."""
    return await asyncio.to_thread(get_related_entities, entity, relationship, depth)
