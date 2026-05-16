"""Unit tests for prompt_hook.py and session_cache.py. No live services required."""

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from prompt_hook import _detect_project, _query_hash, _format_age, _format_memories
from session_cache import (
    load_cache, save_cache, has_seen_query, record_query,
    increment_exchange, reset_exchange_count, add_stored_hashes, get_stored_hashes,
)


# ── _detect_project ────────────────────────────────────────────────────────────

def test_detect_project_simple():
    assert _detect_project(r"C:\Users\Kevin\Projects\engram") == "engram"


def test_detect_project_posix():
    assert _detect_project("/home/user/projects/myapp") == "myapp"


def test_detect_project_generic_basename_uses_parent():
    # "src" is generic — should look one level up
    result = _detect_project(r"C:\Users\Kevin\Projects\myproject\src")
    assert result == "myproject"


def test_detect_project_empty_cwd():
    assert _detect_project("") is None


def test_detect_project_generic_returns_none():
    # Both levels are generic
    assert _detect_project(r"C:\projects\src") is None


def test_detect_project_projects_root_returns_none():
    # cwd is the Projects root — "Projects" is generic, parent "Kevin" is a username
    assert _detect_project(r"C:\Users\Kevin\Projects") is None


def test_detect_project_username_dir_returns_none():
    # cwd is the user's home dir — parent is "Users", so it's a username, not a project
    assert _detect_project(r"C:\Users\Kevin") is None


# ── _query_hash ────────────────────────────────────────────────────────────────

def test_query_hash_is_deterministic():
    assert _query_hash("hello world") == _query_hash("hello world")


def test_query_hash_differs_for_different_inputs():
    assert _query_hash("foo") != _query_hash("bar")


def test_query_hash_length():
    assert len(_query_hash("anything")) == 16


# ── _format_memories ──────────────────────────────────────────────────────────

def test_format_memories_header():
    result = _format_memories([])
    assert result == "[Engram context]"


def test_format_memories_single():
    memories = [
        {
            "memory_type": "feedback",
            "content": "Don't mock the database",
            "project": "engram",
            "timestamp": None,
            "chunk_id": "abc123",
        }
    ]
    result = _format_memories(memories)
    assert "[feedback]" in result
    assert "Don't mock the database" in result
    assert "engram" in result


def test_format_memories_content_truncated_at_120():
    long_content = "x" * 200
    memories = [{"memory_type": "project", "content": long_content, "project": None, "timestamp": None}]
    result = _format_memories(memories)
    # The line should contain "..." and not be excessively long
    lines = result.splitlines()
    assert any("..." in line for line in lines)


# ── session_cache ─────────────────────────────────────────────────────────────

def test_load_cache_returns_empty_for_new_session(tmp_path, monkeypatch):
    monkeypatch.setattr("session_cache._SESSIONS_DIR", tmp_path)
    cache = load_cache("newsession123")
    assert cache["query_hashes"] == []
    assert cache["seen_chunk_ids"] == []
    assert "created_at" in cache


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("session_cache._SESSIONS_DIR", tmp_path)
    cache = load_cache("sess1")
    cache = record_query(cache, "hash_a", ["chunk1", "chunk2"])
    save_cache("sess1", cache)

    loaded = load_cache("sess1")
    assert "hash_a" in loaded["query_hashes"]
    assert "chunk1" in loaded["seen_chunk_ids"]


def test_has_seen_query_false_for_new_hash(tmp_path, monkeypatch):
    monkeypatch.setattr("session_cache._SESSIONS_DIR", tmp_path)
    cache = load_cache("sess2")
    assert not has_seen_query(cache, "newhash")


def test_has_seen_query_true_after_record(tmp_path, monkeypatch):
    monkeypatch.setattr("session_cache._SESSIONS_DIR", tmp_path)
    cache = load_cache("sess3")
    cache = record_query(cache, "myhash", [])
    assert has_seen_query(cache, "myhash")


def test_dedup_same_query_skipped(tmp_path, monkeypatch):
    """Simulates the prompt_hook dedup: second identical query hash → no output."""
    monkeypatch.setattr("session_cache._SESSIONS_DIR", tmp_path)
    cache = load_cache("sess4")
    q_hash = _query_hash("In project engram: how does decay work?")

    assert not has_seen_query(cache, q_hash)
    cache = record_query(cache, q_hash, ["chunk_x"])
    assert has_seen_query(cache, q_hash)  # second call would be skipped


def test_query_hash_ring_buffer_trimmed(tmp_path, monkeypatch):
    """Cache trims query_hashes to last 20 entries."""
    monkeypatch.setattr("session_cache._SESSIONS_DIR", tmp_path)
    cache = load_cache("sess5")
    for i in range(25):
        cache = record_query(cache, f"hash_{i}", [])
    assert len(cache["query_hashes"]) == 20


# ── exchange counter ───────────────────────────────────────────────────────────

def test_increment_exchange_starts_at_one(tmp_path, monkeypatch):
    monkeypatch.setattr("session_cache._SESSIONS_DIR", tmp_path)
    cache = load_cache("exc1")
    count = increment_exchange(cache)
    assert count == 1
    assert cache["exchange_count"] == 1


def test_increment_exchange_accumulates(tmp_path, monkeypatch):
    monkeypatch.setattr("session_cache._SESSIONS_DIR", tmp_path)
    cache = load_cache("exc2")
    for _ in range(4):
        increment_exchange(cache)
    assert cache["exchange_count"] == 4


def test_reset_exchange_count(tmp_path, monkeypatch):
    monkeypatch.setattr("session_cache._SESSIONS_DIR", tmp_path)
    cache = load_cache("exc3")
    for _ in range(5):
        increment_exchange(cache)
    reset_exchange_count(cache)
    assert cache["exchange_count"] == 0


# ── stored content hash dedup ─────────────────────────────────────────────────

def test_get_stored_hashes_empty_on_new_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("session_cache._SESSIONS_DIR", tmp_path)
    cache = load_cache("hash1")
    assert get_stored_hashes(cache) == set()


def test_add_and_get_stored_hashes(tmp_path, monkeypatch):
    monkeypatch.setattr("session_cache._SESSIONS_DIR", tmp_path)
    cache = load_cache("hash2")
    add_stored_hashes(cache, ["abc", "def"])
    assert get_stored_hashes(cache) == {"abc", "def"}


def test_stored_hashes_persisted_across_load(tmp_path, monkeypatch):
    monkeypatch.setattr("session_cache._SESSIONS_DIR", tmp_path)
    cache = load_cache("hash3")
    add_stored_hashes(cache, ["aaa", "bbb"])
    save_cache("hash3", cache)
    loaded = load_cache("hash3")
    assert "aaa" in get_stored_hashes(loaded)


def test_stored_hashes_trimmed_to_cap(tmp_path, monkeypatch):
    monkeypatch.setattr("session_cache._SESSIONS_DIR", tmp_path)
    cache = load_cache("hash4")
    add_stored_hashes(cache, [f"h{i}" for i in range(160)])
    assert len(cache["stored_content_hashes"]) == 150


# ── load_cache backward compat ────────────────────────────────────────────────

def test_load_cache_fills_missing_keys_from_old_cache(tmp_path, monkeypatch):
    """Old cache files without new keys get defaults merged in."""
    monkeypatch.setattr("session_cache._SESSIONS_DIR", tmp_path)
    old = {"query_hashes": ["x"], "seen_chunk_ids": [], "created_at": "2025-01-01T00:00:00Z"}
    (tmp_path / "old_sess.json").write_text(
        __import__("json").dumps(old), encoding="utf-8"
    )
    cache = load_cache("old_sess")
    assert "exchange_count" in cache
    assert "stored_content_hashes" in cache
    assert cache["query_hashes"] == ["x"]  # existing data preserved
