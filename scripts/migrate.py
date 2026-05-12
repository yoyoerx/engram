"""Migrate existing flat-file memory/*.md files into Engram (Qdrant + Neo4j).

Usage:
    python scripts/migrate.py --memory-dir PATH [--project PROJECT] [--dry-run]

Example:
    python scripts/migrate.py --memory-dir ~/.claude/projects/<your-project-id>/memory
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

# Add the project root to sys.path so engram_mcp is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from engram_mcp.tools.store import store_memory
from engram_mcp.config import MEMORY_TYPES

DEFAULT_MEMORY_DIR = None  # must be supplied via --memory-dir

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_TYPE_RE = re.compile(r"^\s*type\s*:\s*(\S+)", re.MULTILINE)


def _parse_memory_file(path: Path) -> tuple[str, str] | None:
    """
    Return (memory_type, body) for a memory .md file, or None if it should be skipped.
    memory_type is inferred from the frontmatter `type:` field or the filename prefix.
    """
    if path.name == "MEMORY.md":
        return None

    raw = path.read_text(encoding="utf-8")

    # Strip YAML frontmatter
    body = raw
    fm_match = _FRONTMATTER_RE.match(raw)
    frontmatter = ""
    if fm_match:
        frontmatter = fm_match.group(1)
        body = raw[fm_match.end():].strip()

    if not body:
        return None

    # Resolve memory_type: frontmatter type: field > filename prefix
    memory_type = None
    if frontmatter:
        m = _TYPE_RE.search(frontmatter)
        if m:
            memory_type = m.group(1).lower()

    if memory_type not in MEMORY_TYPES:
        # Try filename prefix (e.g. "feedback_ovr_..." → "feedback")
        prefix = path.stem.split("_")[0].lower()
        if prefix in MEMORY_TYPES:
            memory_type = prefix
        else:
            memory_type = "reference"  # safe fallback

    return memory_type, body


async def migrate(memory_dir: Path, project: str | None, dry_run: bool) -> None:
    files = sorted(memory_dir.glob("*.md"))
    if not files:
        print(f"No .md files found in {memory_dir}")
        return

    print(f"Found {len(files)} file(s) in {memory_dir}")
    if dry_run:
        print("[DRY RUN — nothing will be written]\n")

    success = 0
    skipped = 0
    errors = 0

    for path in files:
        parsed = _parse_memory_file(path)
        if parsed is None:
            print(f"  SKIP  {path.name}  (index file or empty)")
            skipped += 1
            continue

        memory_type, body = parsed
        print(f"  {'DRY ' if dry_run else ''}STORE  {path.name}  [{memory_type}]", end="")

        if dry_run:
            print(f"  ({len(body)} chars)")
            success += 1
            continue

        try:
            result = await store_memory(
                content=body,
                memory_type=memory_type,
                project=project,
                metadata={"source_file": path.name, "migrated": True},
            )
            print(f"  -> {result['chunks_written']} chunk(s), "
                  f"{result['entities_extracted']} entities")
            success += 1
        except Exception as exc:
            print(f"  ERROR: {exc}")
            errors += 1

    print(f"\nDone. {success} stored, {skipped} skipped, {errors} errors.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate flat-file memory to Engram.")
    parser.add_argument(
        "--memory-dir",
        type=Path,
        default=DEFAULT_MEMORY_DIR,
        help="Directory containing memory .md files",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="Project tag to attach to migrated memories",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be stored without writing anything",
    )
    args = parser.parse_args()

    if not args.memory_dir:
        parser.error("--memory-dir is required. Example: ~/.claude/projects/<your-project-id>/memory")

    if not args.memory_dir.is_dir():
        print(f"ERROR: memory directory not found: {args.memory_dir}", file=sys.stderr)
        sys.exit(1)

    asyncio.run(migrate(args.memory_dir, args.project, args.dry_run))


if __name__ == "__main__":
    main()
