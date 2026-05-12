"""
SessionStart hook for Engram.

Wired into ~/.claude/settings.json SessionStart hook.
Starts Docker Compose services and verifies Ollama in the background,
then outputs a systemMessage so Claude Code reports service status
at the top of each session.

Designed to be fast: does not wait for services to be fully healthy,
just fires the start commands and checks what's immediately reachable.
Full health polling is available via: python scripts/start.py --wait
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).parent.parent

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")


def _docker_available() -> bool:
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def _compose_up() -> bool:
    try:
        r = subprocess.run(
            ["docker", "compose", "up", "-d"],
            cwd=str(ROOT),
            capture_output=True,
            timeout=20,
        )
        return r.returncode == 0
    except Exception:
        return False


def _service_up(url: str) -> bool:
    try:
        httpx.get(url, timeout=2).raise_for_status()
        return True
    except Exception:
        return False


def _start_ollama() -> None:
    """Fire ollama serve in the background if not reachable."""
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def main() -> None:
    parts = []

    if not _docker_available():
        parts.append("Docker not running -- start Docker Desktop, then run: python scripts/start.py")
        print(json.dumps({"systemMessage": " | ".join(parts)}, ensure_ascii=True))
        return

    compose_ok = _compose_up()

    qdrant_ok = _service_up(f"{QDRANT_URL}/collections")
    ollama_ok = _service_up(f"{OLLAMA_BASE_URL}/api/tags")

    if not ollama_ok:
        _start_ollama()

    statuses = []
    if compose_ok:
        statuses.append("Qdrant+Neo4j: started")
    else:
        statuses.append("Qdrant+Neo4j: compose error (run scripts/start.py)")

    statuses.append(f"Ollama: {'up' if ollama_ok else 'starting'}")

    all_ok = compose_ok and qdrant_ok and ollama_ok
    prefix = "[Engram] " + ("ready" if all_ok else "starting up")
    msg = prefix + " -- " + ", ".join(statuses)

    print(json.dumps({"systemMessage": msg}, ensure_ascii=True))


if __name__ == "__main__":
    main()
