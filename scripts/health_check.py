"""
Verify all four Engram services are reachable: Qdrant, Neo4j, Ollama.
Prints a status table and exits with code 1 if any service is down.
"""

import sys
import os
import httpx
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")


def check_qdrant() -> tuple[bool, str]:
    try:
        r = httpx.get(f"{QDRANT_URL}/collections", timeout=5)
        r.raise_for_status()
        collections = [c["name"] for c in r.json().get("result", {}).get("collections", [])]
        detail = f"collections: {collections or '(none yet)'}"
        return True, detail
    except Exception as e:
        return False, str(e)


def check_neo4j() -> tuple[bool, str]:
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
        with driver.session() as session:
            result = session.run("MATCH (n) RETURN count(n) AS count")
            count = result.single()["count"]
        driver.close()
        return True, f"node count: {count}"
    except Exception as e:
        return False, str(e)


def check_ollama() -> tuple[bool, str]:
    try:
        r = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        r.raise_for_status()
        models = [m["name"] for m in r.json().get("models", [])]
        has_embed = any("nomic-embed-text" in m for m in models)
        detail = f"nomic-embed-text: {'YES' if has_embed else 'NOT PULLED'} | all models: {models or '(none)'}"
        return True, detail
    except Exception as e:
        return False, str(e)


CHECKS = [
    ("Qdrant     ", f"{QDRANT_URL}", check_qdrant),
    ("Neo4j      ", f"{NEO4J_URI}", check_neo4j),
    ("Ollama     ", f"{OLLAMA_BASE_URL}", check_ollama),
]

if __name__ == "__main__":
    print("Engram Health Check\n" + "=" * 60)

    failures = []
    for name, url, check_fn in CHECKS:
        ok, detail = check_fn()
        status = "OK  " if ok else "FAIL"
        print(f"  {status}  {name}  {url}")
        if detail:
            print(f"         {detail}")
        if not ok:
            failures.append(name.strip())

    print("=" * 60)
    if failures:
        print(f"DOWN: {', '.join(failures)}")
        sys.exit(1)
    else:
        print("All services healthy.")
