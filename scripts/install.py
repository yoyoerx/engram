"""
Engram installer — sets up everything needed to run Engram on a new machine.

Usage:
    python scripts/install.py [--skip-mcp] [--skip-claude-md] [--non-interactive]

Steps:
    1. Check prerequisites (Docker, Ollama, Python, Claude Code CLI)
    2. Create .env from .env.example if missing (prompts for secrets)
    3. Install Python package in editable mode (pip install -e .)
    4. Start Docker services (docker compose up -d)
    5. Pull Ollama embedding model
    6. Run init_db.py (create Qdrant collection + Neo4j schema)
    7. Register Engram MCP server with Claude Code
    8. Add MCP tool permissions to ~/.claude/settings.json
    9. Merge memory routing instructions into ~/.claude/CLAUDE.md
"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent.parent.resolve()
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
CLAUDE_DIR = Path.home() / ".claude"
SETTINGS_FILE = CLAUDE_DIR / "settings.json"
CLAUDE_MD = CLAUDE_DIR / "CLAUDE.md"

IS_WINDOWS = platform.system() == "Windows"

ENGRAM_MCP_TOOLS = [
    "mcp__engram__store_memory",
    "mcp__engram__retrieve_context",
    "mcp__engram__list_memories",
    "mcp__engram__forget",
    "mcp__engram__update_memory",
    "mcp__engram__get_related",
]

CLAUDE_MD_BLOCK = """\
## Memory -- use Engram, not flat files

Engram is a hybrid RAG memory backend (Qdrant + Neo4j) available as an MCP server.
Use it instead of writing flat .md files to the memory directory.

**Saving memories:**
- Call `store_memory` whenever you learn something worth remembering.
- Choose the correct `memory_type`: `feedback`, `user`, `project`, `reference`, `decision`, or `error`.
- Set `project` to the relevant project name when the memory is project-scoped; omit for global memories.
- Do NOT write .md files to the memory directory -- Engram replaces that system.

**Recalling context:**
- Call `retrieve_context` at the start of any session where prior work is relevant.
- Use `list_memories` to browse what is stored when orientation is needed.
- Use `get_related` to explore entity relationships in the knowledge graph.

**Proactive memory -- store as you learn:**
- User corrects your approach or confirms a non-obvious one worked (`memory_type: feedback`)
- You learn a new fact about the user's role, preferences, or expertise (`memory_type: user`)
- A project decision is made or rationale becomes clear (`memory_type: project` or `decision`)
- A bug pattern or workaround is discovered that could recur (`memory_type: error`)
- A useful external resource or location is identified (`memory_type: reference`)
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _header(msg: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {msg}")
    print('=' * 60)


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def _info(msg: str) -> None:
    print(f"         {msg}")


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _prompt(question: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"  {question}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return value or default


# ---------------------------------------------------------------------------
# Step 1: Prerequisites
# ---------------------------------------------------------------------------

def check_prerequisites() -> bool:
    _header("Step 1 of 9: Checking prerequisites")
    all_ok = True

    # Python version
    if sys.version_info >= (3, 11):
        _ok(f"Python {sys.version_info.major}.{sys.version_info.minor}")
    else:
        _fail(f"Python 3.11+ required (found {sys.version_info.major}.{sys.version_info.minor})")
        all_ok = False

    # Docker
    result = _run(["docker", "info"])
    if result.returncode == 0:
        _ok("Docker is running")
    else:
        _fail("Docker is not running or not installed")
        _info("Install: https://docs.docker.com/get-docker/")
        _info("Then start Docker Desktop and re-run this script.")
        all_ok = False

    # docker compose (v2 plugin)
    result = _run(["docker", "compose", "version"])
    if result.returncode == 0:
        _ok("docker compose (v2)")
    else:
        _fail("docker compose v2 not found")
        _info("Update Docker Desktop or install the compose plugin.")
        all_ok = False

    # Ollama
    if shutil.which("ollama"):
        _ok("Ollama installed")
    else:
        _fail("Ollama not found")
        _info("Install: https://ollama.com/download")
        all_ok = False

    # Claude Code CLI
    if shutil.which("claude"):
        _ok("Claude Code CLI found")
    else:
        _fail("Claude Code CLI not found")
        _info("Install: https://claude.ai/code")
        all_ok = False

    return all_ok


# ---------------------------------------------------------------------------
# Step 2: .env setup
# ---------------------------------------------------------------------------

def setup_env(non_interactive: bool) -> bool:
    _header("Step 2 of 9: Environment configuration (.env)")

    if ENV_FILE.exists():
        _ok(".env already exists -- skipping")
        return True

    if not ENV_EXAMPLE.exists():
        _fail(".env.example not found -- cannot create .env")
        return False

    template = ENV_EXAMPLE.read_text(encoding="utf-8")

    if non_interactive:
        _info("--non-interactive: copying .env.example to .env unchanged.")
        _info("Edit .env and set ANTHROPIC_API_KEY and NEO4J_PASSWORD before continuing.")
        ENV_FILE.write_text(template, encoding="utf-8")
        return False  # caller should warn user

    print("  .env not found. Answer a few questions to create it.\n")

    api_key = _prompt("Anthropic API key (sk-ant-...)")
    neo4j_pw = _prompt("Neo4j password (choose any string)", default="engram_local")

    content = template
    content = content.replace("your_key_here", api_key or "your_key_here")
    content = content.replace("your_neo4j_password_here", neo4j_pw)
    ENV_FILE.write_text(content, encoding="utf-8")

    # Also patch docker-compose.yml if password differs from default
    _patch_docker_compose(neo4j_pw)

    _ok(".env created")
    return True


def _patch_docker_compose(password: str) -> None:
    compose_file = ROOT / "docker-compose.yml"
    if not compose_file.exists():
        return
    text = compose_file.read_text(encoding="utf-8")
    # Replace NEO4J_AUTH value
    patched = re.sub(
        r"(NEO4J_AUTH:\s*neo4j/)([^\s\n]+)",
        f"NEO4J_AUTH: neo4j/{password}",
        text,
    )
    if patched != text:
        compose_file.write_text(patched, encoding="utf-8")


# ---------------------------------------------------------------------------
# Step 3: pip install -e .
# ---------------------------------------------------------------------------

def install_package() -> bool:
    _header("Step 3 of 9: Installing engram_mcp package (pip install -e .)")
    result = _run([sys.executable, "-m", "pip", "install", "-e", ".", "--quiet"], cwd=ROOT)
    if result.returncode == 0:
        _ok("engram_mcp installed in editable mode")
        return True
    _fail("pip install failed")
    _info(result.stderr[:500])
    return False


# ---------------------------------------------------------------------------
# Step 4: Docker services
# ---------------------------------------------------------------------------

def start_docker_services() -> bool:
    _header("Step 4 of 9: Starting Docker services (Qdrant + Neo4j)")
    result = _run(["docker", "compose", "up", "-d"], cwd=ROOT)
    if result.returncode == 0:
        _ok("Docker services started")
        _info("Qdrant: http://localhost:6333/dashboard")
        _info("Neo4j:  http://localhost:7474")
        return True
    _fail("docker compose up failed")
    _info(result.stderr[:500])
    return False


# ---------------------------------------------------------------------------
# Step 5: Ollama model pull
# ---------------------------------------------------------------------------

def pull_ollama_model() -> bool:
    _header("Step 5 of 9: Pulling nomic-embed-text embedding model")

    # Check if already present
    result = _run(["ollama", "list"])
    if result.returncode == 0 and "nomic-embed-text" in result.stdout:
        _ok("nomic-embed-text already present")
        return True

    _info("Pulling nomic-embed-text (~274 MB, first time only)...")
    result = subprocess.run(["ollama", "pull", "nomic-embed-text"])
    if result.returncode == 0:
        _ok("nomic-embed-text pulled")
        return True
    _fail("ollama pull failed")
    return False


# ---------------------------------------------------------------------------
# Step 6: init_db
# ---------------------------------------------------------------------------

def init_db() -> bool:
    _header("Step 6 of 9: Initialising databases (Qdrant collection + Neo4j schema)")
    _info("Waiting a moment for services to be ready...")

    import time
    time.sleep(3)

    result = _run([sys.executable, str(ROOT / "scripts" / "init_db.py")], cwd=ROOT)
    if result.returncode == 0:
        _ok("Databases initialised")
        if result.stdout:
            for line in result.stdout.strip().splitlines():
                _info(line)
        return True

    # init_db errors on "already exists" constraints — that is fine on re-runs
    if "already exists" in result.stderr.lower() or "equivalent" in result.stderr.lower():
        _ok("Schema already applied (skipping)")
        return True

    _fail("init_db.py failed")
    _info(result.stderr[:500])
    return False


# ---------------------------------------------------------------------------
# Step 7: Register MCP server
# ---------------------------------------------------------------------------

def register_mcp_server() -> bool:
    _header("Step 7 of 9: Registering Engram MCP server with Claude Code")

    # Check if already registered
    result = _run(["claude", "mcp", "list"])
    if result.returncode == 0 and "engram" in result.stdout:
        _ok("Engram MCP server already registered")
        return True

    cmd = [
        "claude", "mcp", "add", "engram",
        "-s", "user",
        "--",
        sys.executable, "-m", "engram_mcp.server",
    ]
    result = _run(cmd)
    if result.returncode == 0:
        _ok("MCP server registered")
        _info("Registered as: python -m engram_mcp.server")
        _info("No PYTHONPATH needed (package installed via pip).")
        return True

    _fail("claude mcp add failed")
    _info(result.stderr[:500])
    return False


# ---------------------------------------------------------------------------
# Step 8: settings.json permissions
# ---------------------------------------------------------------------------

def update_settings_permissions() -> bool:
    _header("Step 8 of 9: Adding MCP tool permissions to ~/.claude/settings.json")
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)

    settings: dict = {}
    if SETTINGS_FILE.exists():
        try:
            settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            _info("settings.json is malformed -- creating fresh copy")

    permissions = settings.setdefault("permissions", {})
    allow: list = permissions.setdefault("allow", [])

    added = []
    for tool in ENGRAM_MCP_TOOLS:
        if tool not in allow:
            allow.append(tool)
            added.append(tool)

    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")

    if added:
        _ok(f"Added {len(added)} tool permission(s)")
        for t in added:
            _info(t)
    else:
        _ok("All tool permissions already present")
    return True


# ---------------------------------------------------------------------------
# Step 9: CLAUDE.md
# ---------------------------------------------------------------------------

def update_claude_md() -> bool:
    _header("Step 9 of 9: Merging memory instructions into ~/.claude/CLAUDE.md")
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)

    marker = "## Memory -- use Engram"

    if CLAUDE_MD.exists():
        existing = CLAUDE_MD.read_text(encoding="utf-8")
        if marker in existing:
            _ok("Engram memory instructions already in CLAUDE.md")
            return True
        # Append to existing file
        updated = existing.rstrip() + "\n\n" + CLAUDE_MD_BLOCK
        CLAUDE_MD.write_text(updated, encoding="utf-8")
        _ok("Appended Engram memory instructions to existing CLAUDE.md")
    else:
        CLAUDE_MD.write_text(f"# Claude Code Global Instructions\n\n{CLAUDE_MD_BLOCK}", encoding="utf-8")
        _ok("Created ~/.claude/CLAUDE.md with Engram memory instructions")

    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Install and configure Engram.")
    parser.add_argument("--skip-mcp", action="store_true", help="Skip MCP server registration")
    parser.add_argument("--skip-claude-md", action="store_true", help="Skip CLAUDE.md update")
    parser.add_argument("--non-interactive", action="store_true", help="Never prompt for input")
    args = parser.parse_args()

    print("\nEngram Installer")
    print("Local-first hybrid RAG memory for Claude Code")
    print(f"Project root: {ROOT}\n")

    steps = [
        ("Prerequisites",         check_prerequisites),
        ("Environment (.env)",    lambda: setup_env(args.non_interactive)),
        ("Package install",       install_package),
        ("Docker services",       start_docker_services),
        ("Ollama model",          pull_ollama_model),
        ("Database init",         init_db),
    ]

    if not args.skip_mcp:
        steps.append(("MCP registration",   register_mcp_server))

    steps.append(("Settings permissions", update_settings_permissions))

    if not args.skip_claude_md:
        steps.append(("CLAUDE.md",          update_claude_md))

    failures = []
    for name, fn in steps:
        try:
            ok = fn()
        except Exception as exc:
            _fail(f"Unexpected error in '{name}': {exc}")
            ok = False
        if not ok:
            failures.append(name)

    print(f"\n{'=' * 60}")
    if failures:
        print(f"  Completed with issues in: {', '.join(failures)}")
        print("  Fix the issues above and re-run -- completed steps are safe to repeat.")
        sys.exit(1)
    else:
        print("  Engram is ready.")
        print("  Restart Claude Code to load the MCP server and CLAUDE.md.")
    print('=' * 60 + "\n")


if __name__ == "__main__":
    main()
