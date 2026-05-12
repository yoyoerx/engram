"""
Engram cold-start script.

Starts Docker Compose services (Qdrant + Neo4j), verifies Ollama is running,
then runs the health check. Safe to run repeatedly — all operations are idempotent.

Usage (from the engram project root):
    python scripts/start.py
    python scripts/start.py --wait          # wait up to 60s for services to be healthy
    python scripts/start.py --health-only   # skip start, just check status
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).parent.parent
ENV_FILE = ROOT / ".env"

# Load .env so OLLAMA_BASE_URL / QDRANT_URL can be overridden
try:
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE)
except ImportError:
    pass

import os

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}", file=sys.stderr)


def _info(msg: str) -> None:
    print(f"         {msg}")


def _header(msg: str) -> None:
    print(f"\n{'=' * 56}")
    print(f"  {msg}")
    print("=" * 56)


# ── Docker ────────────────────────────────────────────────────────────────────

def _docker_running() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _start_compose() -> bool:
    """Run docker compose up -d from the project root."""
    try:
        result = subprocess.run(
            ["docker", "compose", "up", "-d"],
            cwd=str(ROOT),
            capture_output=True, text=True, timeout=60,
        )
        return result.returncode == 0
    except Exception as e:
        _fail(f"docker compose up -d failed: {e}")
        return False


def ensure_docker_services() -> bool:
    if not _docker_running():
        _fail("Docker is not running. Start Docker Desktop first.")
        _info("Once Docker Desktop is open, re-run this script.")
        return False

    print("  Starting Qdrant + Neo4j via docker compose...")
    if _start_compose():
        _ok("docker compose up -d")
        return True
    return False


# ── Ollama ────────────────────────────────────────────────────────────────────

def _ollama_reachable() -> bool:
    try:
        r = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _ollama_has_embed_model() -> bool:
    try:
        r = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3)
        r.raise_for_status()
        models = [m["name"] for m in r.json().get("models", [])]
        return any("nomic-embed-text" in m for m in models)
    except Exception:
        return False


def ensure_ollama() -> bool:
    if _ollama_reachable():
        _ok(f"Ollama reachable at {OLLAMA_BASE_URL}")
        if not _ollama_has_embed_model():
            _fail("nomic-embed-text not pulled. Run: ollama pull nomic-embed-text")
            return False
        _ok("nomic-embed-text model available")
        return True

    # Try to start Ollama in the background (Windows: ollama serve)
    print("  Ollama not reachable — attempting to start...")
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        _fail("'ollama' command not found. Install from https://ollama.ai")
        return False

    # Wait up to 10s for Ollama to start
    for _ in range(10):
        time.sleep(1)
        if _ollama_reachable():
            _ok("Ollama started")
            return True

    _fail("Ollama did not start within 10s. Run 'ollama serve' in a terminal.")
    return False


# ── Health check ──────────────────────────────────────────────────────────────

def _wait_for_health(max_wait: int = 60) -> bool:
    """Poll health_check.py until all services are healthy or timeout."""
    health_script = ROOT / "scripts" / "health_check.py"
    deadline = time.time() + max_wait
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        result = subprocess.run(
            [sys.executable, str(health_script)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return True
        if attempt == 1:
            print(f"  Waiting for services to be healthy (up to {max_wait}s)...", end="", flush=True)
        else:
            print(".", end="", flush=True)
        time.sleep(5)
    print()
    return False


def run_health_check(wait: bool = False) -> bool:
    health_script = ROOT / "scripts" / "health_check.py"
    if wait:
        ok = _wait_for_health(max_wait=60)
        if ok:
            print()
        return ok

    result = subprocess.run(
        [sys.executable, str(health_script)],
        cwd=str(ROOT),
    )
    return result.returncode == 0


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Start Engram services.")
    parser.add_argument("--wait", action="store_true",
                        help="Wait up to 60s for services to become healthy after starting.")
    parser.add_argument("--health-only", action="store_true",
                        help="Skip start, just run health check.")
    args = parser.parse_args()

    _header("Engram Start")

    failures = []

    if not args.health_only:
        if not ensure_docker_services():
            failures.append("docker")
        if not ensure_ollama():
            failures.append("ollama")

    print()
    ok = run_health_check(wait=args.wait or (not args.health_only))

    print()
    if failures or not ok:
        _fail("One or more services failed to start. See messages above.")
        sys.exit(1)
    else:
        _ok("Engram is online. MCP server will start automatically with Claude Code.")


if __name__ == "__main__":
    main()
