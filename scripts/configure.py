"""
Engram configuration CLI.

Manage global (~/.engram/config.json) and per-project (.engram.json) settings,
and install or inspect Engram hooks in Claude Code settings.json files.

Usage:
  python scripts/configure.py show [--project PATH]
  python scripts/configure.py set KEY VALUE [--project PATH]
  python scripts/configure.py hooks install [--project PATH]
  python scripts/configure.py hooks status [--project PATH]

Config keys:
  exchange_threshold  int   Stop events between auto-store runs (default: 5)
  retrieve_limit      int   Max memories injected per prompt (default: 5)
  auto_retrieve       bool  Enable UserPromptSubmit memory injection (default: true)
  auto_store          bool  Enable background auto-storage (default: true)

Examples:
  python scripts/configure.py show
  python scripts/configure.py show --project C:/Dev/myproject
  python scripts/configure.py set exchange_threshold 3
  python scripts/configure.py set auto_retrieve false --project .
  python scripts/configure.py hooks install
  python scripts/configure.py hooks install --project .
  python scripts/configure.py hooks status
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from engram_config import (
    GLOBAL_CONFIG_PATH, _DEFAULTS, _load_json, load_config,
    save_global_config, save_project_config, key_help,
)

# Hook definitions — timeout values match hook type semantics
_HOOKS = {
    "SessionStart":     ("scripts/session_start.py", 30),
    "UserPromptSubmit": ("scripts/prompt_hook.py",   10),
    "Stop":             ("scripts/stop_hook.py",     10),
    "PreCompact":       ("scripts/compact_hook.py",   5),
}

_BOOL_KEYS = {"auto_retrieve", "auto_store"}
_INT_KEYS   = {"exchange_threshold", "retrieve_limit"}


def _fmt_value(k: str, v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _parse_value(key: str, raw: str):
    if key in _BOOL_KEYS:
        if raw.lower() in ("true", "yes", "1", "on"):
            return True
        if raw.lower() in ("false", "no", "0", "off"):
            return False
        print(f"Error: {key!r} must be a boolean (true/false)")
        sys.exit(1)
    if key in _INT_KEYS:
        try:
            v = int(raw)
            if v < 1:
                raise ValueError
            return v
        except ValueError:
            print(f"Error: {key!r} must be a positive integer")
            sys.exit(1)
    print(f"Error: unknown key {key!r}")
    sys.exit(1)


# ── show ──────────────────────────────────────────────────────────────────────

def cmd_show(args) -> None:
    project = args.project
    global_data = _load_json(GLOBAL_CONFIG_PATH)
    project_data = _load_json(Path(project) / ".engram.json") if project else {}
    effective = load_config(project)
    help_text = key_help()

    print("\nEngram Configuration")
    print("=" * 60)

    print(f"\nGlobal config: {GLOBAL_CONFIG_PATH}")
    if not global_data:
        print("  (no overrides -- all defaults apply)")
    else:
        for k in _DEFAULTS:
            if k in global_data:
                print(f"  {k:<22} {_fmt_value(k, global_data[k])}")

    if project:
        project_file = Path(project) / ".engram.json"
        print(f"\nProject config: {project_file}")
        if not project_data:
            print("  (not found -- global config applies)")
        else:
            for k in _DEFAULTS:
                if k in project_data:
                    print(f"  {k:<22} {_fmt_value(k, project_data[k])}")

    print("\nEffective settings (what the hooks use):")
    for k in _DEFAULTS:
        source = ""
        if project and k in project_data:
            source = "  [project]"
        elif k in global_data:
            source = "  [global]"
        v = effective[k]
        desc = help_text.get(k, "")
        print(f"  {k:<22} {_fmt_value(k, v):<8}{source}")
    print()


# ── set ───────────────────────────────────────────────────────────────────────

def cmd_set(args) -> None:
    key = args.key
    if key not in _DEFAULTS:
        print(f"Error: unknown key {key!r}")
        print(f"Valid keys: {', '.join(sorted(_DEFAULTS))}")
        sys.exit(1)

    value = _parse_value(key, args.value)

    if args.project:
        save_project_config(args.project, {key: value})
        project_file = Path(args.project) / ".engram.json"
        print(f"Set {key} = {_fmt_value(key, value)} in project config ({project_file})")
    else:
        save_global_config({key: value})
        print(f"Set {key} = {_fmt_value(key, value)} in global config ({GLOBAL_CONFIG_PATH})")


# ── hooks ─────────────────────────────────────────────────────────────────────

def _hook_command(script_rel: str) -> str:
    """Absolute path with forward slashes (required on Windows hook runner)."""
    return f"python {(ROOT / script_rel).as_posix()}"


def _engram_hook_entry(script_rel: str, timeout: int) -> dict:
    return {"hooks": [{"type": "command", "command": _hook_command(script_rel), "timeout": timeout}]}


def _settings_path(project: str | None) -> Path:
    if project:
        return Path(project) / ".claude" / "settings.json"
    return Path.home() / ".claude" / "settings.json"


def _is_engram_entry(entry: dict, script_name: str) -> bool:
    for h in entry.get("hooks", []):
        if script_name in h.get("command", ""):
            return True
    return False


def _merge_hooks(data: dict, hooks_to_install: dict) -> tuple[dict, list[str]]:
    """Merge Engram hook entries into data['hooks'] without wiping other entries.
    Returns updated data and list of installed event names."""
    installed = []
    existing = data.setdefault("hooks", {})
    for event, (script_rel, timeout) in hooks_to_install.items():
        script_name = Path(script_rel).name
        new_entry = _engram_hook_entry(script_rel, timeout)
        entries = existing.setdefault(event, [])
        # Update in-place if already present, else append
        for i, entry in enumerate(entries):
            if _is_engram_entry(entry, script_name):
                entries[i] = new_entry
                installed.append(event)
                break
        else:
            entries.append(new_entry)
            installed.append(event)
    return data, installed


def cmd_hooks_install(args) -> None:
    target = _settings_path(args.project)

    try:
        data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    except Exception as exc:
        print(f"Error reading {target}: {exc}")
        sys.exit(1)

    if args.project:
        # Per-project: install all hooks (user opted into project-local hooks)
        hooks_to_install = dict(_HOOKS)
        label = f"{Path(args.project).resolve()}"
    else:
        # Global: install all hooks
        hooks_to_install = dict(_HOOKS)
        label = "global"

    data, installed = _merge_hooks(data, hooks_to_install)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")

    print(f"\nEngram hooks installed in {target} ({label})")
    for event in installed:
        script_rel, timeout = _HOOKS[event]
        cmd = _hook_command(script_rel)
        print(f"  {event:<18} {cmd}  ({timeout}s)")
    print()


def cmd_hooks_status(args) -> None:
    targets = []
    if args.project:
        targets.append((_settings_path(args.project), f"project ({Path(args.project).resolve()})"))
    targets.append((_settings_path(None), "global"))

    print("\nEngram Hook Status")
    print("=" * 60)
    for target, label in targets:
        print(f"\n{label} ({target}):")
        try:
            data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
        except Exception:
            data = {}
        hooks = data.get("hooks", {})
        for event, (script_rel, _) in _HOOKS.items():
            script_name = Path(script_rel).name
            entries = hooks.get(event, [])
            found = any(_is_engram_entry(e, script_name) for e in entries)
            status = "[installed]" if found else "[not found]"
            print(f"  {event:<18} {status}")
    print()


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Engram configuration and hook management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # show
    p_show = sub.add_parser("show", help="Display current configuration")
    p_show.add_argument("--project", metavar="PATH", help="Include per-project config from PATH")

    # set
    p_set = sub.add_parser("set", help="Set a configuration value")
    p_set.add_argument("key", choices=sorted(_DEFAULTS), metavar="KEY")
    p_set.add_argument("value", metavar="VALUE")
    p_set.add_argument("--project", metavar="PATH",
                       help="Write to per-project config at PATH instead of global")

    # hooks
    p_hooks = sub.add_parser("hooks", help="Install or inspect Engram hooks")
    hooks_sub = p_hooks.add_subparsers(dest="hooks_command", required=True)

    p_install = hooks_sub.add_parser("install", help="Install Engram hooks into settings.json")
    p_install.add_argument("--project", metavar="PATH",
                           help="Install into PATH/.claude/settings.json instead of global")

    p_status = hooks_sub.add_parser("status", help="Show which hooks are installed")
    p_status.add_argument("--project", metavar="PATH",
                          help="Also check PATH/.claude/settings.json")

    args = parser.parse_args()

    if args.command == "show":
        cmd_show(args)
    elif args.command == "set":
        cmd_set(args)
    elif args.command == "hooks":
        if args.hooks_command == "install":
            cmd_hooks_install(args)
        elif args.hooks_command == "status":
            cmd_hooks_status(args)


if __name__ == "__main__":
    main()
