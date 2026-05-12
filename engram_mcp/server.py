"""
Engram MCP server — entry point for Claude Code.
Run with: python -m mcp.server  (from the engram/ root)
"""

from fastmcp import FastMCP
from engram_mcp.tools.store import store_memory
from engram_mcp.tools.retrieve import retrieve_context
from engram_mcp.tools.graph_tools import get_related
from engram_mcp.tools.manage import update_memory, forget, list_memories

mcp = FastMCP(
    name="engram",
    instructions=(
        "Engram is a hybrid long-term memory backend. "
        "Use store_memory to persist important context, decisions, feedback, or errors. "
        "Use retrieve_context to recall relevant memories before answering questions about "
        "past work, preferences, or project history. "
        "Use get_related to explore how entities connect in the knowledge graph."
    ),
)

mcp.tool()(store_memory)
mcp.tool()(retrieve_context)
mcp.tool()(get_related)
mcp.tool()(update_memory)
mcp.tool()(forget)
mcp.tool()(list_memories)

if __name__ == "__main__":
    mcp.run()
