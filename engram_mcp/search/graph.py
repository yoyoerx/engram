"""Neo4j graph traversal with lightweight entity hint extraction."""

import re
from neo4j import GraphDatabase

from engram_mcp.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

# Match Title-Case words and ALL_CAPS tokens as candidate entity names
_ENTITY_HINT_RE = re.compile(r'\b([A-Z][a-zA-Z]{2,}|[A-Z]{2,})\b')


def _extract_hints(query: str) -> list[str]:
    """Pull capitalised tokens from the query as candidate entity names."""
    return list({m.group(1) for m in _ENTITY_HINT_RE.finditer(query)})


def traverse(
    query: str,
    limit: int = 20,
    memory_types: list[str] | None = None,
    project: str | None = None,
) -> list[dict]:
    """
    Find Memory nodes connected (up to 2 hops) to entities hinted at by the query.
    Each result: {chunk_id, content, score, memory_type, project, timestamp, neo4j_node_id}.
    Graph relevance score = 1 / (hop_distance + 1), capped at 1.0.
    """
    hints = _extract_hints(query)
    if not hints:
        return []

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    results: dict[str, dict] = {}

    type_filter_m = "AND m.memory_type IN $types " if memory_types else ""
    type_filter_m2 = "AND m2.memory_type IN $types " if memory_types else ""
    project_filter = "AND m.project = $project " if project else ""
    project_filter_m2 = "AND m2.project = $project " if project else ""

    try:
        with driver.session() as session:
            for hint in hints:
                # Direct: Memory nodes ABOUT an entity matching the hint
                rows = session.run(
                    f"""
                    MATCH (e) WHERE toLower(e.name) CONTAINS toLower($hint)
                    MATCH (m:Memory)-[:ABOUT]->(e)
                    WHERE m.tombstone = false {type_filter_m}{project_filter}
                    RETURN m.chunk_id AS chunk_id, m.content AS content,
                           m.memory_type AS memory_type, m.project AS project,
                           m.timestamp AS timestamp, elementId(m) AS neo4j_node_id,
                           1 AS hops
                    LIMIT $limit
                    """,
                    hint=hint,
                    types=memory_types or [],
                    project=project or "",
                    limit=limit,
                )
                for row in rows:
                    cid = row["chunk_id"]
                    score = 1.0 / (row["hops"] + 1)
                    if cid not in results or results[cid]["score"] < score:
                        results[cid] = {
                            "chunk_id": cid,
                            "content": row["content"],
                            "score": score,
                            "memory_type": row["memory_type"],
                            "project": row["project"],
                            "timestamp": row["timestamp"],
                            "neo4j_node_id": row["neo4j_node_id"],
                            "_source": "graph",
                        }

                # 2-hop: Memory → entity → related entity → Memory
                rows2 = session.run(
                    f"""
                    MATCH (e) WHERE toLower(e.name) CONTAINS toLower($hint)
                    MATCH (m:Memory)-[:ABOUT]->()-[*1..2]-(e2)<-[:ABOUT]-(m2:Memory)
                    WHERE m2.tombstone = false AND m2.chunk_id <> m.chunk_id
                    {type_filter_m2}{project_filter_m2}
                    RETURN m2.chunk_id AS chunk_id, m2.content AS content,
                           m2.memory_type AS memory_type, m2.project AS project,
                           m2.timestamp AS timestamp, elementId(m2) AS neo4j_node_id,
                           2 AS hops
                    LIMIT $limit
                    """,
                    hint=hint,
                    types=memory_types or [],
                    project=project or "",
                    limit=limit,
                )
                for row in rows2:
                    cid = row["chunk_id"]
                    score = 1.0 / (row["hops"] + 1)
                    if cid not in results or results[cid]["score"] < score:
                        results[cid] = {
                            "chunk_id": cid,
                            "content": row["content"],
                            "score": score,
                            "memory_type": row["memory_type"],
                            "project": row["project"],
                            "timestamp": row["timestamp"],
                            "neo4j_node_id": row["neo4j_node_id"],
                            "_source": "graph",
                        }
    finally:
        driver.close()

    return list(results.values())


def get_related_entities(entity: str, relationship: str | None = None, depth: int = 2) -> dict:
    """Traverse from a named entity and return connected nodes."""
    rel_pattern = f"[r:{relationship}*1..{depth}]" if relationship else f"[*1..{depth}]"

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    relationships = []
    try:
        with driver.session() as session:
            rows = session.run(
                f"""
                MATCH (start) WHERE toLower(start.name) = toLower($entity)
                MATCH (start)-{rel_pattern}-(related)
                WHERE related <> start
                RETURN type(last(relationships(
                    (start)-[*1..{depth}]-(related)
                ))) AS rel_type,
                labels(related)[0] AS label,
                related.name AS name
                LIMIT 50
                """,
                entity=entity,
            )
            for row in rows:
                relationships.append({
                    "type": row["rel_type"],
                    "target": row["name"],
                    "label": row["label"],
                })
    finally:
        driver.close()

    return {"entity": entity, "relationships": relationships}
