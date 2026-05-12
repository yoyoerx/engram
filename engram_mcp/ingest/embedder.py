"""Ollama HTTP client for generating nomic-embed-text embeddings."""

import httpx
from engram_mcp.config import OLLAMA_BASE_URL, EMBED_MODEL, VECTOR_SIZE


async def embed(text: str) -> list[float]:
    """Return a 768-dim embedding vector for the given text."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
        )
        response.raise_for_status()
        vector = response.json()["embedding"]

    if len(vector) != VECTOR_SIZE:
        raise ValueError(f"Expected {VECTOR_SIZE}-dim vector, got {len(vector)}")

    return vector


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed multiple texts sequentially (Ollama has no batch endpoint)."""
    return [await embed(t) for t in texts]
