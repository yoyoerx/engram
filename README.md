# Engram

> *An engram is the physical trace a memory leaves in neural tissue. Engram gives Claude the same.*

**Local-first hybrid RAG memory for Claude Code.** Engram replaces the flat-file memory system with query-driven retrieval: semantic vector search (Qdrant) combined with knowledge graph traversal (Neo4j), connected to Claude via MCP. All data stays on your machine.

Inspired by [The AI Amnesia Problem](https://medium.com/@yoyoerx/the-ai-amnesia-problem-architecting-long-term-memory-for-local-llms-cbe3d5c6c93e).

---

## The Problem

Claude's context window is ephemeral. Flat-file memory dumps load everything on every session, burning tokens on irrelevant history. There is no semantic understanding of *what matters* for a given query, and no model of how memories relate to each other.

Engram solves this with **hybrid retrieval**: ask a question, get back the memories that are semantically similar *and* structurally connected — nothing more.

---

## How It Works

```
store_memory("Always use async/await with Ollama", type="feedback")
     |
     +-- Chunker (512-char, sentence-aware)
     +-- Extractor (claude-haiku -> entities + relationships -> Neo4j)
     +-- Embedder (Ollama nomic-embed-text -> vector -> Qdrant)

retrieve_context("How should I call Ollama?")
     |
     +-- Parallel: embed query -> Qdrant nearest-neighbours
     +-- Parallel: extract entity hints -> Neo4j 2-hop traversal
     +-- Merger: deduplicate, score (60% vector + 40% graph), return top N
```

---

## Stack

| Component | Technology | Purpose |
|---|---|---|
| Vector store | [Qdrant](https://qdrant.tech) (Docker) | Semantic similarity search |
| Knowledge graph | [Neo4j Community](https://neo4j.com) (Docker) | Entity relationship traversal |
| Embeddings | [Ollama](https://ollama.com) + `nomic-embed-text` | Local 768-dim embeddings, no API cost |
| Entity extraction | `claude-haiku-4-5-20251001` | Structured JSON entity/relationship extraction |
| MCP server | [FastMCP](https://github.com/jlowin/fastmcp) (Python) | Tool interface for Claude Code |

---

## Prerequisites

Install these manually before running the installer:

- [Docker Desktop](https://docs.docker.com/get-docker/) (with Compose v2)
- [Ollama](https://ollama.com/download)
- Python 3.11+
- [Claude Code CLI](https://claude.ai/code)
- An Anthropic API key (used only for entity extraction during ingestion)

---

## Installation

```bash
git clone https://github.com/yoyoerx/engram.git
cd engram
python scripts/install.py
```

The installer handles everything else:

1. Creates `.env` interactively (API key + Neo4j password)
2. `pip install -e .` (installs `engram_mcp` as a package)
3. `docker compose up -d` (Qdrant + Neo4j)
4. `ollama pull nomic-embed-text`
5. `python scripts/init_db.py` (Qdrant collection + Neo4j schema)
6. `claude mcp add engram -s user` (registers MCP server globally)
7. Adds tool permissions to `~/.claude/settings.json`
8. Merges memory routing instructions into `~/.claude/CLAUDE.md`

Then restart Claude Code.

**Options:**

```bash
python scripts/install.py --skip-mcp        # skip MCP registration
python scripts/install.py --skip-claude-md  # skip CLAUDE.md update
python scripts/install.py --non-interactive # CI / headless setup
```

All steps are idempotent — safe to re-run.

---

## MCP Tools

Six tools are available in Claude Code once connected:

| Tool | Description |
|---|---|
| `store_memory` | Store text to both vector store and knowledge graph |
| `retrieve_context` | Hybrid search: semantic + graph, merged and ranked |
| `get_related` | Traverse the knowledge graph from a named entity |
| `update_memory` | Replace a chunk; old one tombstoned with a SUPERSEDES edge |
| `forget` | Soft-delete (tombstone) or hard-delete a memory chunk |
| `list_memories` | Browse stored memories with optional type/project filters |

### Example usage

```
store_memory(
    content="Never use → in Python print statements on Windows — use -> instead.",
    memory_type="feedback"
)

retrieve_context(
    query="Windows encoding issues in Python",
    limit=5
)

get_related(entity="Neo4j", depth=2)
```

### Memory types

`feedback` · `user` · `project` · `reference` · `decision` · `error`

---

## Proactive Memory

After installation, `~/.claude/CLAUDE.md` tells Claude Code to:

- Call `retrieve_context` at the start of any session where prior work is relevant
- Call `store_memory` automatically when it learns user corrections, project decisions, bug patterns, or useful references — without being asked

---

## Migrating from Flat Files

If you have an existing `~/.claude/projects/.../memory/` directory:

```bash
python scripts/migrate.py                        # dry run first
python scripts/migrate.py                        # run for real
python scripts/migrate.py --project my-project  # tag with a project name
```

---

## Project Structure

```
engram/
├── engram_mcp/           # MCP server package
│   ├── server.py         # FastMCP entry point
│   ├── config.py         # All config loaded from .env
│   ├── ingest/           # chunker, extractor, embedder
│   ├── search/           # vector.py, graph.py, merger.py
│   └── tools/            # store, retrieve, graph_tools, manage
├── scripts/
│   ├── install.py        # One-command setup
│   ├── init_db.py        # Schema and collection creation
│   ├── migrate.py        # Flat-file memory importer
│   └── health_check.py   # Service liveness check
├── tests/
│   ├── test_ingest.py    # 7 tests
│   └── test_retrieve.py  # 8 tests
├── docs/                 # Article drafts
├── docker-compose.yml
├── pyproject.toml
├── .env.example
└── architecture.md       # Living architecture document
```

---

## Configuration

All configuration is in `.env` (copied from `.env.example`):

```env
ANTHROPIC_API_KEY=sk-ant-...      # Required for entity extraction
NEO4J_PASSWORD=your_password      # Must match docker-compose.yml

# Defaults — change only if your services run on different ports
QDRANT_URL=http://localhost:6333
NEO4J_URI=bolt://localhost:7687
OLLAMA_BASE_URL=http://localhost:11434

# Retrieval tuning
VECTOR_WEIGHT=0.6
GRAPH_WEIGHT=0.4
DEFAULT_RETRIEVE_LIMIT=10
```

---

## Development

```bash
pip install -e ".[dev]"
python scripts/health_check.py     # verify all services are up
pytest tests/ -v                   # run the test suite (15 tests)
```

---

## Security

- All memory data is stored on local Docker volumes. Nothing is sent to external services except Anthropic (entity extraction only).
- `.env` is gitignored. Never commit it.
- `forget(hard=True)` provides hard deletion for sensitive content.
- The MCP server uses stdio transport — no network port exposed.

---

## Architecture

See [architecture.md](architecture.md) for the full design document including system diagrams, data flow sequences, all ADRs, and the development roadmap.

---

## License

[BSD-3-Clause](LICENSE)
