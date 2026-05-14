# Engram

> *An engram is the physical trace a memory leaves in neural tissue. Engram gives Claude the same.*

**Local-first hybrid RAG memory for Claude Code.** Engram replaces the flat-file memory system with query-driven retrieval: semantic vector search (Qdrant) combined with knowledge graph traversal (Neo4j), connected to Claude via MCP. Retrieval happens automatically before every prompt. Storage happens automatically in the background. All data stays on your machine.

**Read the series:**
- [The AI Amnesia Problem](https://medium.com/@yoyoerx/the-ai-amnesia-problem-architecting-long-term-memory-for-local-llms-cbe3d5c6c93e) — why flat-file memory is the wrong approach
- [From Blueprint to Brain](https://medium.com/@yoyoerx/from-blueprint-to-brain-building-engram-a-local-hybrid-memory-for-claude-code-25d6c38fd620) — building the hybrid retrieval backend
- [Teaching Engram to Remember on Its Own](docs/engram-biomimetic-memory-agent.md) — autonomous memory operations (Phase 8)

---

## The Problem

Claude's context window is ephemeral. Flat-file memory dumps load everything on every session, burning tokens on irrelevant history. There is no semantic understanding of *what matters* for a given query, no model of how memories relate to each other, and no mechanism for memories to surface without being explicitly asked for.

Engram solves this with **hybrid retrieval** and **autonomous operation**: ask a question, get back the memories that are semantically similar and structurally connected. And you never have to ask — retrieval fires before every prompt, storage fires in the background after every few exchanges.

---

## How It Works

```
                        UserPromptSubmit hook fires
                               |
                        retrieve_context(query, project)
                               |
                 +-- embed query -> Qdrant nearest-neighbours
                 +-- extract entity hints -> Neo4j 2-hop traversal
                 +-- merge, decay-score, inject as systemMessage
                               |
                        Claude sees relevant memories
                        before processing your prompt


store_memory("Always use async/await with Ollama", type="feedback")
     |
     +-- Chunker (512-char, sentence-aware)
     +-- Extractor (claude-haiku -> entities + relationships -> Neo4j)
     +-- Embedder (Ollama nomic-embed-text -> vector -> Qdrant)


                        Stop hook fires (every 5 exchanges)
                               |
                        auto_store.py (detached background process)
                               |
                 +-- Read new transcript messages since last run
                 +-- Claude Haiku extracts memorable facts
                 +-- store_memory for each candidate
                 +-- Cross-run dedup via session cache
```

---

## Stack

| Component | Technology | Purpose |
|---|---|---|
| Vector store | [Qdrant](https://qdrant.tech) (Docker) | Semantic similarity search |
| Knowledge graph | [Neo4j Community](https://neo4j.com) (Docker) | Entity relationship traversal |
| Embeddings | [Ollama](https://ollama.com) + `nomic-embed-text` | Local 768-dim embeddings, no API cost |
| Entity extraction | `claude-haiku-4-5-20251001` | Structured JSON entity/relationship extraction |
| Auto-storage | `claude-haiku-4-5-20251001` | Background memory curation from session transcripts |
| MCP server | [FastMCP](https://github.com/jlowin/fastmcp) (Python) | Tool interface for Claude Code |

---

## Prerequisites

- [Docker Desktop](https://docs.docker.com/get-docker/) (with Compose v2)
- [Ollama](https://ollama.com/download)
- Python 3.11+
- [Claude Code CLI](https://claude.ai/code)
- An Anthropic API key (used for entity extraction and auto-storage)

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
8. `python scripts/configure.py hooks install` (wires all four lifecycle hooks)
9. Merges memory routing instructions into `~/.claude/CLAUDE.md`

Then restart Claude Code.

**Options:**

```bash
python scripts/install.py --skip-mcp        # skip MCP registration
python scripts/install.py --skip-claude-md  # skip CLAUDE.md update
python scripts/install.py --non-interactive # CI / headless setup
```

All steps are idempotent — safe to re-run.

---

## Autonomous Memory

Engram's hook layer makes memory operations invisible. Four hooks wire into the Claude Code lifecycle:

| Hook | Script | Fires | What it does |
|---|---|---|---|
| `SessionStart` | `session_start.py` | Every session open | Start Docker services, increment session counter |
| `UserPromptSubmit` | `prompt_hook.py` | Before every prompt | Retrieve relevant memories, inject as system context |
| `Stop` | `stop_hook.py` | Every N responses | Spawn background agent to extract and store memories |
| `PreCompact` | `compact_hook.py` | Before context compaction | Capture memories before they're summarized away |

The `[Engram context]` block that appears before Claude's response is the hook at work:

```
[Engram context]
[feedback] Never use Unicode arrows in Python print statements on Windows -- use ASCII -> instead. (global, 3d ago)
[decision] Engram stop_hook fires every 5 exchanges to balance cost vs. coverage. (engram, today)
```

Once installed, you don't interact with this layer — it runs automatically on every session, across every project.

---

## Configuration

Hook behavior can be tuned globally or per-project without touching source files.

```bash
# View effective configuration
python scripts/configure.py show
python scripts/configure.py show --project .

# Adjust settings
python scripts/configure.py set exchange_threshold 3     # store more frequently
python scripts/configure.py set retrieve_limit 8         # surface more memories per prompt
python scripts/configure.py set auto_retrieve false --project .  # disable injection for this project

# Manage hooks
python scripts/configure.py hooks status
python scripts/configure.py hooks install
```

| Key | Default | Description |
|---|---|---|
| `exchange_threshold` | 5 | Claude responses between auto-store runs |
| `retrieve_limit` | 5 | Max memories injected per prompt |
| `auto_retrieve` | true | Enable automatic memory injection |
| `auto_store` | true | Enable background auto-storage |

Global config lives at `~/.engram/config.json`. Per-project overrides go in `{project}/.engram.json`. Environment variables (`ENGRAM_EXCHANGE_THRESHOLD`, `ENGRAM_RETRIEVE_LIMIT`, `ENGRAM_AUTO_RETRIEVE`, `ENGRAM_AUTO_STORE`) override both.

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

### Memory types

`feedback` · `user` · `project` · `reference` · `decision` · `error`

### Example

```python
store_memory(
    content="Never use Unicode arrows in Python print statements on Windows -- use ASCII -> instead.",
    memory_type="feedback",
    tags=["windows", "encoding"]
)

retrieve_context(query="Windows encoding issues in Python", limit=5)

get_related(entity="Neo4j", depth=2)
```

---

## Decay Scoring

Retrieval scores are adjusted by usage history. Memories that haven't been retrieved in many sessions quietly fall in ranking; memories retrieved frequently get a small boost. Decay is measured in **sessions**, not time — a month without coding is zero sessions and produces zero decay. A long gap is exactly when persistent memory is most valuable.

```
adjusted_score = base * (1 + min(0.5, 0.1 * retrieval_count)) * exp(-0.1 * sessions_since_retrieved)
```

Half-life: ~7 sessions. Memories never retrieved are not penalized — their base score is returned unchanged.

---

## Migrating from Flat Files

If you have an existing `~/.claude/projects/.../memory/` directory:

```bash
python scripts/migrate.py --dry-run         # preview what would be imported
python scripts/migrate.py                   # import
python scripts/migrate.py --project myapp  # tag all entries with a project name
```

---

## Project Structure

```
engram/
├── engram_mcp/                  # MCP server package
│   ├── server.py                # FastMCP entry point
│   ├── config.py                # Service URLs, model names, decay constants
│   ├── ingest/                  # chunker.py, extractor.py, embedder.py
│   ├── search/                  # vector.py, graph.py, merger.py (decay scoring)
│   └── tools/                   # store.py, retrieve.py, graph_tools.py, manage.py
│
├── scripts/
│   ├── configure.py             # Config CLI: show / set / hooks install / hooks status
│   ├── engram_config.py         # Shared config loader (global + per-project + env vars)
│   ├── prompt_hook.py           # UserPromptSubmit hook — auto-retrieval
│   ├── stop_hook.py             # Stop hook — throttled auto-storage launcher
│   ├── compact_hook.py          # PreCompact hook — capture before compaction
│   ├── auto_store.py            # Background agent — incremental Haiku extraction + store
│   ├── session_cache.py         # Per-session state (dedup, exchange counter, stored hashes)
│   ├── session_start.py         # SessionStart hook — service startup + session counter
│   ├── install.py               # One-command setup
│   ├── init_db.py               # Qdrant collection + Neo4j schema
│   ├── migrate.py               # Flat-file memory importer
│   ├── health_check.py          # Service liveness check
│   ├── start.py                 # Cold-start: Docker + Ollama + health check
│   └── benchmark.py             # Retrieval latency benchmark (p50/p95/p99)
│
├── tests/                       # 65 tests
├── docs/                        # Article drafts
├── docker-compose.yml
├── pyproject.toml
├── .env.example
└── architecture.md              # Living architecture document
```

Per-project config (optional, in any project root): `.engram.json`

---

## Service Configuration

Service URLs and retrieval weights go in `.env` (copied from `.env.example`):

```env
ANTHROPIC_API_KEY=sk-ant-...      # Required
NEO4J_PASSWORD=your_password      # Must match docker-compose.yml

# Service URLs (defaults work with the provided docker-compose.yml)
QDRANT_URL=http://localhost:6333
NEO4J_URI=bolt://localhost:7687
OLLAMA_BASE_URL=http://localhost:11434

# Retrieval tuning
VECTOR_WEIGHT=0.6
GRAPH_WEIGHT=0.4
DECAY_LAMBDA=0.1
USAGE_BOOST_MAX=0.5
```

Hook behavior (exchange threshold, retrieve limit, auto-retrieve/store on/off) is managed via `configure.py`, not `.env`.

---

## Development

```bash
pip install -e ".[dev]"
python scripts/health_check.py    # verify all services are up
pytest tests/ -v                  # run the test suite (65 tests)
python scripts/benchmark.py       # retrieval latency (p50/p95/p99)
```

---

## Security

- All memory data is stored on local Docker volumes. Nothing is sent externally except Anthropic API calls (entity extraction during ingestion, memory curation in `auto_store.py`).
- `.env` is gitignored. Never commit it.
- `forget(hard=True)` provides hard deletion for sensitive content.
- The MCP server uses stdio transport — no network port exposed.

---

## Architecture

See [architecture.md](architecture.md) for the full design document: system diagrams, data flow sequences, all ADRs, and the development history through Phase 9.

---

## License

[BSD-3-Clause](LICENSE)
