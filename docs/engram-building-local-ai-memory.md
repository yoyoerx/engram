# From Blueprint to Brain: Building Engram, a Local Hybrid Memory for Claude Code

*This article was ideated and architected by the author and prepared with the help of Claude.*

---

In [Part 1 of this series](https://medium.com/@yoyoerx/the-ai-amnesia-problem-architecting-long-term-memory-for-local-llms-cbe3d5c6c93e), we diagnosed the AI Amnesia problem and laid out a blueprint. We established that the context window is fundamentally ephemeral, that vector search alone lacks relational precision, and that the hybrid approach -- combining a vector store for semantic similarity with a knowledge graph for structural relationships -- was the gold standard worth building toward. We ended with a four-step execution plan: stand up the infrastructure, design the schema, build the MCP server, and get the retrieval logic right.

This article is what happened when we actually built it.

The result is **Engram** -- a fully local, open-source hybrid RAG memory backend for Claude Code, built on Qdrant, Neo4j, Ollama, and FastMCP. All data stays on your machine. No cloud storage. No context-window amnesia. The only external API call is to Claude Haiku during ingestion -- fractions of a cent per memory stored.

---

## Why "Engram"?

An engram is the physical or chemical trace that a memory leaves in neural tissue -- the literal imprint of experience on the brain. It felt like the right name for a system designed to give an AI the same: persistent, queryable, structurally-linked traces of past work.

---

## The Stack We Chose and Why

Part 1 identified two paths: the Mem0 ecosystem (convenient but expensive and cloud-locked for graph features) and the custom power stack (Neo4j + a local vector DB). We went with the custom stack, but made a few specific choices worth explaining.

**Qdrant over ChromaDB.** Part 1 listed both as viable options. After evaluating them, Qdrant won for a few reasons: it ships with a web dashboard at `localhost:6333/dashboard`, has excellent payload filtering (we filter by `memory_type` and `project` on every query), and its REST API is clean enough that the client library feels like a thin wrapper rather than an abstraction layer. ChromaDB is simpler to start but has had embedded-mode instability and weaker filtering. For a system that has to run reliably in the background, Qdrant's operational maturity matters.

**nomic-embed-text via Ollama.** We need 768-dimensional embeddings. Rather than paying per-call for OpenAI's `text-embedding-3-small`, we run `nomic-embed-text` locally via Ollama. It benchmarks within a few points of OpenAI's model on standard retrieval benchmarks, the model is ~274MB, and once it's downloaded, it costs nothing and never sends your memories to a third party.

**FastMCP.** The raw Anthropic MCP SDK requires significant ceremony to expose Python functions as tools. FastMCP reduces this to decorated functions. The entire MCP server -- six tools -- is about 40 lines of registration code.

**Claude Haiku for entity extraction.** This is the one part of the pipeline that calls an external API. During ingestion, each memory chunk is sent to `claude-haiku-4-5-20251001` with a structured prompt to extract entities and relationships. Why not a local model? Because entity extraction quality directly determines whether the knowledge graph is useful. Haiku reliably follows structured JSON schemas, is fast, and costs roughly $0.0001 per ingestion call -- about $0.10 for 1,000 stored memories. A local small model would be cheaper but meaningfully less reliable at structured output. This is a tunable decision; a `--local-extraction` flag is on the roadmap.

---

## How It Works

### Ingestion: From Text to Two Stores

When Claude calls `store_memory`, the content goes through a three-stage pipeline before anything is written to disk.

**Stage 1 -- Chunking.** Long content is split into 512-character chunks using a sentence-aware splitter. Rather than cutting on character count alone, the chunker respects sentence boundaries and adds a 64-character overlap between adjacent chunks so context doesn't vanish at the seam.

**Stage 2 -- Entity extraction.** Each chunk is sent to Haiku with a prompt asking for a JSON object containing entities (with labels like `Tool`, `Project`, `Concept`, `Error`, `Feedback`) and typed relationships between them (`USES`, `ABOUT`, `APPLIES_TO`, `PREVENTS`). If extraction fails for any reason -- network blip, malformed JSON, rate limit -- the pipeline falls back to empty results and continues. Ingestion is never blocked by a failed extraction.

**Stage 3 -- Embedding + storage.** The chunk is embedded via Ollama and written to Qdrant. The extracted entities and relationships are written to Neo4j with `MERGE` semantics (so the same entity mentioned across multiple memories becomes a single node with multiple edges, rather than duplicates). The Qdrant point carries a `neo4j_node_id` in its payload, and the Neo4j node carries a `vector_id` back, creating a bidirectional index between the two stores.

The result of storing "Claude Code uses the Qdrant client library for vector search" is not just a vector in a database -- it's also a graph node for `Claude Code`, a node for `Qdrant`, a `Tool` node for the client library, and edges between them that future graph traversals can follow.

### Retrieval: Parallel Search, Merged Results

When Claude calls `retrieve_context`, the query is handled in parallel:

- The query text is embedded and sent to Qdrant for nearest-neighbor search
- Entity hints are extracted from the query (capitalized tokens like `Qdrant`, `Neo4j`, `Claude`) and used to seed a Neo4j traversal up to two hops out from matching nodes

Both searches over-fetch by 3x, then hand off to the merger. The merger deduplicates by `chunk_id`, combines scores using a 60/40 weighted formula (60% vector similarity, 40% graph relevance), sorts descending, and returns the top N results.

The weighting reflects a deliberate prior: semantic similarity is slightly more reliable as a general signal than graph proximity, but graph proximity captures structural relationships that vectors miss entirely. Both weights are tunable in `config.py`.

---

## The Problems We Didn't Anticipate

Implementation never matches the blueprint exactly. Three bugs are worth documenting because they're the kind of thing no architecture document warns you about.

**The package naming collision.** The local package was initially named `mcp/`. This silently shadows the installed `mcp` library that FastMCP depends on, producing a cryptic `ImportError: cannot import name 'McpError' from 'mcp'` that points at your own code rather than the real problem. The fix was renaming the package to `engram_mcp` and setting `PYTHONPATH` in the MCP server registration. The lesson: never name a local package after a dependency.

**The Qdrant API deprecation.** qdrant-client v1.9 deprecated the `.search()` method in favor of `.query_points()`, which returns a response object whose results live at `response.points` rather than being returned directly. The old method still existed at the time of writing but will be removed in a future version. If you're starting fresh, use `.query_points()` from the beginning.

**The Neo4j Cypher alias bug.** The graph traversal runs two queries: a 1-hop query matching `Memory` nodes directly about an entity, and a 2-hop query that walks through intermediate nodes. The 2-hop query aliases its result node as `m2` to distinguish it from the 1-hop `m`. But the type filter string was built with the 1-hop alias (`m.memory_type IN $types`), so the 2-hop query was silently ignoring the `memory_types` filter. The retrieval integration test for type filtering passed only because the seeded data happened to be the right type. The fix was generating separate filter strings for each alias. Naming matters in Cypher the same way it does in any query language.

---

## Connecting It to Claude Code

Anthropic's Model Context Protocol is what turns this from a standalone Python service into something Claude actively uses. The server runs as a stdio subprocess -- Claude Code spawns it, sends tool calls over stdin, and reads results from stdout. No HTTP server, no port management.

One non-obvious detail: MCP servers should be registered via the `claude mcp add` CLI command, which writes to `~/.claude.json`. Adding `mcpServers` directly to `~/.claude/settings.json` does not work -- that key is not valid in the settings schema and is silently ignored.

```bash
claude mcp add engram -s user \
  -e PYTHONPATH=C:\path\to\engram \
  -- python -m engram_mcp.server
```

Once registered, Claude Code shows the server as connected with all six tools available: `store_memory`, `retrieve_context`, `get_related`, `update_memory`, `forget`, and `list_memories`.

---

## Making Memory Proactive

Having the tools available is necessary but not sufficient. If Claude only stores memories when explicitly asked, the system is no better than the flat-file approach -- it still depends on the user remembering to save things.

The fix is `~/.claude/CLAUDE.md` -- a global instructions file that Claude Code loads at the start of every session. Ours instructs Claude to call `store_memory` immediately whenever it encounters:

- A user correction or confirmation of a non-obvious approach (memory type: `feedback`)
- A new fact about the user's role, preferences, or expertise (`user`)
- A project decision or rationale (`project` or `decision`)
- A recurring bug pattern or workaround (`error`)
- A useful external resource or location (`reference`)

It also instructs Claude to call `retrieve_context` at the start of any session where prior work is relevant, rather than waiting to be asked.

This is the difference between memory as a tool and memory as a behavior. The former requires the user to manage it. The latter just works.

---

## Migration: Importing What Already Exists

Before Engram, memories lived as flat `.md` files in `~/.claude/projects/.../memory/`. Rather than starting fresh, we wrote `scripts/migrate.py` to ingest those files through the standard pipeline.

The migrator parses YAML frontmatter to extract the `memory_type`, strips the frontmatter to get the body, and calls `store_memory` for each file. A `--dry-run` flag previews what would be stored without writing anything.

Running it on the existing five memory files produced 40 chunks and 319 extracted entities -- a knowledge graph that had never existed before in the flat-file system. The relationships between memories (which feedback applies to which project, which errors are caused by which decisions) are now traversable.

---

## What "Working" Actually Looks Like

The smoke test that confirmed the system was real: asking for context about "How does Engram store knowledge graph entities?" against the migrated memories returned a chunk from `project_engram.md` with a vector + graph combined score of 0.723. Asking about "OVR manifest SDK bug fix" surfaced the feedback memory about XML comment placement in Android manifests with a score of 0.586 -- a feedback entry written months ago in a completely different project, retrieved in under 500ms.

The knowledge graph doesn't just store facts; it connects them. A question about one project can surface a pattern discovered in another, because the underlying entities -- `Neo4j`, `Claude Code`, `MCP`, `Docker` -- are shared nodes rather than isolated mentions.

---

## What's Next

Engram is functional but not hardened. The Phase 7 work still ahead includes:

- **Atomic rollback:** If the Neo4j write fails after the Qdrant write succeeds, the vector is orphaned. This needs a compensating transaction or a reconciliation job.
- **Retry logic:** Transient Ollama and Neo4j failures currently surface as errors. They should be retried with backoff.
- **Memory aging:** Memories that haven't been retrieved recently should decay in relevance score, prompting a review cycle rather than silently accumulating.
- **Local entity extraction:** A `--local-extraction` flag using a capable Ollama model to eliminate the Anthropic API dependency for ingestion.

---

## The Honest Verdict

The setup is not trivial. Docker, Ollama, Neo4j, a Python virtual environment, CLAUDE.md configuration, MCP registration -- there are more moving parts than a flat-file system. But the flat-file system doesn't scale. Every session that adds a new memory file makes the next session slightly more expensive in tokens and slightly less relevant in context.

The first time Claude says "based on what we decided last month, the right approach here is X" without being prompted, and it's *right* -- that's when the overhead pays off. It's not just better recall. It's a collaborator with a continuous working memory instead of a brilliant colleague who resets every morning.

The AI Amnesia problem is solvable. The tools exist, they're free, and they run locally. The only cost is the setup.

---

*The full architecture document and source code for Engram are at [github.com/yoyoerx/engram](https://github.com/yoyoerx/engram).*

**Tags:** AI, Claude, RAG, Neo4j, Qdrant, MCP, Local AI, Developer Tools, Knowledge Graph
