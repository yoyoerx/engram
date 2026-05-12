"""Quick smoke test: retrieve memories from Engram and print top results."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from engram_mcp.tools.retrieve import retrieve_context


async def main():
    queries = [
        "How does Engram store knowledge graph entities?",
        "Medium article about MetaGimbalVision streaming",
        "OVR manifest SDK bug fix",
    ]
    for query in queries:
        print(f"\nQuery: {query}")
        results = await retrieve_context(query, limit=3)
        for r in results:
            print(f"  [{r['memory_type']}] score={r['score']:.3f}  {r['content'][:100]}")


asyncio.run(main())
