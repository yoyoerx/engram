"""
Initialize Qdrant collection and Neo4j schema constraints/indexes.
Run once after `docker compose up -d` before starting the MCP server.
"""

import sys
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

COLLECTION_NAME = "engram_memories"
VECTOR_SIZE = 768  # nomic-embed-text


def init_qdrant():
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PayloadSchemaType

    client = QdrantClient(url=QDRANT_URL)

    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME in existing:
        print(f"[qdrant] Collection '{COLLECTION_NAME}' already exists — skipping.")
        return

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
    )

    # Payload indexes for filtered search
    for field, schema_type in [
        ("memory_type", PayloadSchemaType.KEYWORD),
        ("project", PayloadSchemaType.KEYWORD),
        ("timestamp", PayloadSchemaType.DATETIME),
    ]:
        client.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name=field,
            field_schema=schema_type,
        )

    print(f"[qdrant] Created collection '{COLLECTION_NAME}' with {VECTOR_SIZE}-dim cosine vectors.")


NEO4J_CONSTRAINTS = [
    "CREATE CONSTRAINT memory_chunk_id IF NOT EXISTS FOR (m:Memory) REQUIRE m.chunk_id IS UNIQUE",
    "CREATE CONSTRAINT user_name IF NOT EXISTS FOR (u:User) REQUIRE u.name IS UNIQUE",
    "CREATE CONSTRAINT project_name IF NOT EXISTS FOR (p:Project) REQUIRE p.name IS UNIQUE",
    "CREATE CONSTRAINT tool_name IF NOT EXISTS FOR (t:Tool) REQUIRE t.name IS UNIQUE",
    "CREATE CONSTRAINT concept_name IF NOT EXISTS FOR (c:Concept) REQUIRE c.name IS UNIQUE",
    "CREATE CONSTRAINT error_id IF NOT EXISTS FOR (e:Error) REQUIRE e.chunk_id IS UNIQUE",
    "CREATE CONSTRAINT feedback_id IF NOT EXISTS FOR (f:Feedback) REQUIRE f.chunk_id IS UNIQUE",
    "CREATE CONSTRAINT decision_id IF NOT EXISTS FOR (d:Decision) REQUIRE d.chunk_id IS UNIQUE",
    "CREATE CONSTRAINT reference_id IF NOT EXISTS FOR (r:Reference) REQUIRE r.chunk_id IS UNIQUE",
]

NEO4J_INDEXES = [
    "CREATE INDEX memory_type_idx IF NOT EXISTS FOR (m:Memory) ON (m.memory_type)",
    "CREATE INDEX memory_project_idx IF NOT EXISTS FOR (m:Memory) ON (m.project)",
    "CREATE INDEX memory_timestamp_idx IF NOT EXISTS FOR (m:Memory) ON (m.timestamp)",
    "CREATE FULLTEXT INDEX memory_content_fts IF NOT EXISTS FOR (m:Memory) ON EACH [m.content]",
]


def init_neo4j():
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    with driver.session() as session:
        for stmt in NEO4J_CONSTRAINTS:
            session.run(stmt)
        for stmt in NEO4J_INDEXES:
            session.run(stmt)
    driver.close()

    print(f"[neo4j]  Applied {len(NEO4J_CONSTRAINTS)} constraints and {len(NEO4J_INDEXES)} indexes.")


if __name__ == "__main__":
    errors = []

    print("Initializing Engram databases...\n")

    try:
        init_qdrant()
    except Exception as e:
        print(f"[qdrant] ERROR: {e}")
        errors.append("qdrant")

    try:
        init_neo4j()
    except Exception as e:
        print(f"[neo4j]  ERROR: {e}")
        errors.append("neo4j")

    if errors:
        print(f"\nFailed: {', '.join(errors)}. Are the Docker services running?")
        sys.exit(1)

    print("\nDone. Engram databases are ready.")
