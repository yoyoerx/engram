"""
Engram Memory Maintenance -- near-duplicate dedup sweep.

Usage:
    python scripts/maintain.py [--threshold 0.92] [--dry-run] [--verbose]
    python scripts/maintain.py --stats

The dedup sweep finds near-duplicate memory chunks (cosine similarity >=
threshold), keeps the newer one, and tombstones the older with a SUPERSEDES
edge in Neo4j. Both Qdrant payload and Neo4j node are marked tombstone=true
so the chunk is consistently excluded from retrieval and list operations.

All tombstones are soft-deletes -- the content and graph edges are preserved
and the Memory node remains in Neo4j. Hard deletion requires forget(hard=True).

--stats prints a summary of the collection without modifying anything.
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from qdrant_client import QdrantClient

from engram_mcp.config import (
    QDRANT_URL, QDRANT_COLLECTION,
    NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
)
from engram_mcp.retry import neo4j_driver as _neo4j_driver

DEFAULT_THRESHOLD = 0.92
SCROLL_BATCH = 100


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _header(msg: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {msg}")
    print('=' * 60)


def _preview(content: str, width: int = 62) -> str:
    content = content.replace("\n", " ").strip()
    content = content.encode("ascii", errors="replace").decode("ascii")
    return content[:width - 3] + "..." if len(content) > width else content


def _parse_ts(ts: str | None) -> datetime:
    if not ts:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Neo4j helpers
# ---------------------------------------------------------------------------

def _neo4j_tombstoned_ids() -> set[str]:
    """Return chunk_ids already tombstoned in Neo4j."""
    result: set[str] = set()
    try:
        with _neo4j_driver(NEO4J_URI, (NEO4J_USER, NEO4J_PASSWORD)) as driver:
            with driver.session() as session:
                records = session.run(
                    "MATCH (m:Memory) WHERE m.tombstone = true RETURN m.chunk_id AS cid"
                )
                for r in records:
                    if r["cid"]:
                        result.add(r["cid"])
    except Exception as exc:
        print(f"  [WARN] Neo4j tombstone query failed: {exc}")
    return result


def _neo4j_tombstone(driver, older_cid: str, newer_cid: str) -> None:
    with driver.session() as session:
        session.run(
            "MATCH (m:Memory {chunk_id: $cid}) SET m.tombstone = true",
            cid=older_cid,
        )
    with driver.session() as session:
        session.run(
            """
            MATCH (newer:Memory {chunk_id: $new_id})
            MATCH (older:Memory {chunk_id: $old_id})
            MERGE (newer)-[:SUPERSEDES]->(older)
            """,
            new_id=newer_cid,
            old_id=older_cid,
        )


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def cmd_stats() -> None:
    _header("Engram Collection Stats")
    client = QdrantClient(url=QDRANT_URL)

    # Qdrant total
    info = client.get_collection(QDRANT_COLLECTION)
    total_qdrant = info.points_count

    # Neo4j breakdown
    try:
        with _neo4j_driver(NEO4J_URI, (NEO4J_USER, NEO4J_PASSWORD)) as driver:
            with driver.session() as session:
                r = session.run(
                    """
                    MATCH (m:Memory)
                    RETURN
                        count(m) AS total,
                        sum(CASE WHEN m.tombstone = true THEN 1 ELSE 0 END) AS tombstoned,
                        sum(CASE WHEN coalesce(m.tombstone, false) = false THEN 1 ELSE 0 END) AS active
                    """
                )
                row = r.single()
                total_neo4j = row["total"]
                tombstoned = row["tombstoned"]
                active = row["active"]

            with driver.session() as session:
                r = session.run(
                    """
                    MATCH (m:Memory)
                    WHERE coalesce(m.tombstone, false) = false
                    RETURN m.memory_type AS type, count(m) AS n
                    ORDER BY n DESC
                    """
                )
                by_type = [(rec["type"], rec["n"]) for rec in r]

            with driver.session() as session:
                r = session.run("MATCH (n) RETURN count(n) AS total")
                all_nodes = r.single()["total"]

        print(f"\n  Qdrant points (total):  {total_qdrant}")
        print(f"  Neo4j memory nodes:     {total_neo4j}")
        print(f"    Active:               {active}")
        print(f"    Tombstoned:           {tombstoned}")
        print(f"  Neo4j nodes (all):      {all_nodes}  (includes entities, projects, etc.)")
        print()
        print("  Active memories by type:")
        for mtype, n in by_type:
            print(f"    {(mtype or 'unknown'):<12}  {n}")

    except Exception as exc:
        print(f"  [WARN] Neo4j stats failed: {exc}")

    print('=' * 60 + "\n")


# ---------------------------------------------------------------------------
# Dedup sweep
# ---------------------------------------------------------------------------

def _scroll_all(client: QdrantClient) -> list:
    """Scroll all Qdrant points with payloads and vectors."""
    points = []
    offset = None
    while True:
        batch, next_offset = client.scroll(
            collection_name=QDRANT_COLLECTION,
            offset=offset,
            limit=SCROLL_BATCH,
            with_payload=True,
            with_vectors=True,
        )
        points.extend(batch)
        if next_offset is None:
            break
        offset = next_offset
    return points


def _apply_tombstone(client: QdrantClient, older_cid: str, newer_cid: str) -> None:
    try:
        client.set_payload(
            collection_name=QDRANT_COLLECTION,
            payload={"tombstone": True},
            points=[older_cid],
        )
    except Exception as exc:
        print(f"  [WARN] Qdrant payload update failed for {older_cid}: {exc}")

    try:
        with _neo4j_driver(NEO4J_URI, (NEO4J_USER, NEO4J_PASSWORD)) as driver:
            _neo4j_tombstone(driver, older_cid, newer_cid)
    except Exception as exc:
        print(f"  [WARN] Neo4j tombstone failed for {older_cid}: {exc}")


def cmd_dedup(threshold: float, dry_run: bool, verbose: bool) -> None:
    action = "DRY RUN" if dry_run else "LIVE"
    _header(f"Engram Dedup Sweep [{action}]  threshold={threshold}")

    client = QdrantClient(url=QDRANT_URL)

    print("  Loading Neo4j tombstone list...")
    tombstoned = _neo4j_tombstoned_ids()
    print(f"  Already tombstoned: {len(tombstoned)}")

    print("  Scrolling Qdrant collection...")
    all_points = _scroll_all(client)
    total = len(all_points)
    print(f"  Points in Qdrant:   {total}")

    pairs: list[tuple] = []

    print(f"\n  Scanning {total} points for near-duplicates...\n")

    for i, point in enumerate(all_points):
        payload = point.payload or {}
        chunk_id = payload.get("chunk_id", str(point.id))

        if chunk_id in tombstoned:
            continue
        if point.vector is None:
            continue

        try:
            response = client.query_points(
                collection_name=QDRANT_COLLECTION,
                query=point.vector,
                limit=6,  # +1 for self
                with_payload=True,
            )
            hits = response.points
        except Exception as exc:
            if verbose:
                print(f"  [WARN] ANN query failed for {chunk_id[:8]}: {exc}")
            continue

        for hit in hits:
            hit_payload = hit.payload or {}
            hit_cid = hit_payload.get("chunk_id", str(hit.id))

            if hit_cid == chunk_id:
                continue
            if hit_cid in tombstoned:
                continue
            if hit.score < threshold:
                break  # results sorted desc by score

            # Determine which is older
            ts_a = _parse_ts(payload.get("timestamp"))
            ts_b = _parse_ts(hit_payload.get("timestamp"))

            if ts_a >= ts_b:
                newer_cid, newer_p = chunk_id, payload
                older_cid, older_p = hit_cid, hit_payload
            else:
                newer_cid, newer_p = hit_cid, hit_payload
                older_cid, older_p = chunk_id, payload

            pairs.append((older_cid, newer_cid, hit.score, older_p, newer_p))
            tombstoned.add(older_cid)

            if not dry_run:
                _apply_tombstone(client, older_cid, newer_cid)

        if verbose and (i + 1) % 50 == 0:
            print(f"  [{i + 1:>4}/{total}] {len(pairs)} pairs found so far")

    # Report
    print(f"\n{'=' * 60}")
    if not pairs:
        print(f"  No near-duplicates found at threshold {threshold}.")
    else:
        verb = "Would remove" if dry_run else "Removed"
        print(f"  PAIRS FOUND: {len(pairs)}\n")
        for older_cid, newer_cid, score, older_p, newer_p in pairs:
            mtype = newer_p.get("memory_type", "?")
            proj = newer_p.get("project") or "global"
            older_date = (older_p.get("timestamp") or "?")[:10]
            newer_date = (newer_p.get("timestamp") or "?")[:10]
            print(f"  [{mtype}] [{proj}]  similarity={score:.4f}")
            print(f"    keep   {newer_date}  {_preview(newer_p.get('content', ''))}")
            print(f"    remove {older_date}  {_preview(older_p.get('content', ''))}")
            print()
        print(f"  {verb}: {len(pairs)} older duplicate(s)")
        if dry_run:
            print("  Re-run without --dry-run to apply.")

    print('=' * 60 + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Engram memory maintenance.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Print collection statistics and exit.",
    )
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_THRESHOLD,
        help=f"Cosine similarity threshold (default: {DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report duplicates without tombstoning.",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print progress every 50 points.",
    )
    args = parser.parse_args()

    if args.stats:
        cmd_stats()
    else:
        cmd_dedup(args.threshold, args.dry_run, args.verbose)


if __name__ == "__main__":
    main()
