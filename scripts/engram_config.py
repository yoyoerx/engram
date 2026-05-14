"""
Shared configuration loader for Engram hooks and scripts.

Config resolution order (later overrides earlier):
  1. Hardcoded defaults
  2. ~/.engram/config.json       (global user config)
  3. {cwd}/.engram.json          (per-project config, when cwd is provided)
  4. ENGRAM_* environment variables (highest priority)

Hooks and scripts should call load_config(cwd) rather than reading env vars directly.
"""

import json
import os
from pathlib import Path

GLOBAL_CONFIG_PATH = Path.home() / ".engram" / "config.json"

_DEFAULTS: dict = {
    "exchange_threshold": 5,   # Stop events between auto-store runs
    "retrieve_limit": 5,       # Max memories injected per prompt
    "auto_retrieve": True,     # Enable UserPromptSubmit memory injection
    "auto_store": True,        # Enable background auto-storage via Stop hook
}

# Maps config key -> (env var name, cast function)
_ENV_OVERRIDES: dict = {
    "exchange_threshold": ("ENGRAM_EXCHANGE_THRESHOLD", int),
    "retrieve_limit":     ("ENGRAM_RETRIEVE_LIMIT", int),
    "auto_retrieve":      ("ENGRAM_AUTO_RETRIEVE", lambda v: v.lower() not in ("0", "false", "no", "off")),
    "auto_store":         ("ENGRAM_AUTO_STORE",    lambda v: v.lower() not in ("0", "false", "no", "off")),
}

_KEY_HELP: dict = {
    "exchange_threshold": "Stop events between auto-store runs (default: 5)",
    "retrieve_limit":     "Max memories injected per prompt (default: 5)",
    "auto_retrieve":      "Enable UserPromptSubmit memory injection (default: true)",
    "auto_store":         "Enable background auto-storage (default: true)",
}


def _load_json(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def load_config(cwd: str | None = None) -> dict:
    """Return effective config: defaults -> global -> project -> env vars."""
    config = dict(_DEFAULTS)

    for k, v in _load_json(GLOBAL_CONFIG_PATH).items():
        if k in _DEFAULTS:
            config[k] = v

    if cwd:
        for k, v in _load_json(Path(cwd) / ".engram.json").items():
            if k in _DEFAULTS:
                config[k] = v

    for key, (env_var, cast) in _ENV_OVERRIDES.items():
        raw = os.getenv(env_var)
        if raw is not None:
            try:
                config[key] = cast(raw)
            except Exception:
                pass

    return config


def save_global_config(updates: dict) -> None:
    GLOBAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_json(GLOBAL_CONFIG_PATH)
    existing.update(updates)
    GLOBAL_CONFIG_PATH.write_text(json.dumps(existing, indent=2, ensure_ascii=True), encoding="utf-8")


def save_project_config(cwd: str, updates: dict) -> None:
    path = Path(cwd) / ".engram.json"
    existing = _load_json(path)
    existing.update(updates)
    path.write_text(json.dumps(existing, indent=2, ensure_ascii=True), encoding="utf-8")


def key_help() -> dict:
    return dict(_KEY_HELP)
