"""Unit tests for session-based decay scoring in merger.py. No live services required."""

import math
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from engram_mcp.search.merger import _adjusted_score
from engram_mcp.config import DECAY_LAMBDA, USAGE_BOOST_MAX


# ── _adjusted_score ────────────────────────────────────────────────────────────

def test_never_retrieved_returns_base():
    """Memories never retrieved get no decay and no boost — base score unchanged."""
    payload = {"retrieval_count": 0, "last_retrieved_session": None}
    assert _adjusted_score(0.8, payload, current_session=10) == 0.8


def test_never_retrieved_missing_fields_returns_base():
    """Missing fields are treated the same as never retrieved."""
    payload = {}
    assert _adjusted_score(0.5, payload, current_session=5) == 0.5


def test_retrieved_last_session_no_decay():
    """Memory retrieved in the current session gets a usage boost, no decay."""
    payload = {"retrieval_count": 1, "last_retrieved_session": 10}
    score = _adjusted_score(1.0, payload, current_session=10)
    # sessions_since=0 → freshness=1.0, usage_boost = min(0.5, 0.1*1) = 0.1
    expected = 1.0 * (1.0 + 0.1) * math.exp(0)
    assert abs(score - expected) < 1e-9


def test_retrieved_zero_sessions_ago_freshness_is_one():
    """Freshness factor is 1.0 when sessions_since=0."""
    payload = {"retrieval_count": 3, "last_retrieved_session": 7}
    score = _adjusted_score(1.0, payload, current_session=7)
    usage_boost = min(USAGE_BOOST_MAX, 0.1 * 3)
    assert abs(score - (1.0 * (1.0 + usage_boost))) < 1e-9


def test_stale_memory_30_sessions_ago():
    """Memory not retrieved for 30 sessions is significantly deprioritized."""
    payload = {"retrieval_count": 2, "last_retrieved_session": 0}
    score = _adjusted_score(1.0, payload, current_session=30)
    freshness = math.exp(-DECAY_LAMBDA * 30)
    usage_boost = min(USAGE_BOOST_MAX, 0.1 * 2)
    expected = 1.0 * (1.0 + usage_boost) * freshness
    assert abs(score - expected) < 1e-9
    assert score < 0.2  # well below base score


def test_usage_boost_capped():
    """Usage boost does not exceed USAGE_BOOST_MAX regardless of retrieval count."""
    payload = {"retrieval_count": 1000, "last_retrieved_session": 5}
    score = _adjusted_score(1.0, payload, current_session=5)
    max_score = 1.0 * (1.0 + USAGE_BOOST_MAX)  # sessions_since=0 → freshness=1
    assert score <= max_score + 1e-9


def test_no_time_decay_without_sessions():
    """If session count has not advanced, a month gap causes zero decay."""
    # Simulate: memory retrieved at session 5, current session is still 5
    # (user hasn't opened Claude Code since)
    payload = {"retrieval_count": 1, "last_retrieved_session": 5}
    score_same_session = _adjusted_score(1.0, payload, current_session=5)

    # If we advance only 1 session later, decay is minimal
    score_one_session_later = _adjusted_score(1.0, payload, current_session=6)

    assert score_same_session > score_one_session_later
    # But the one-session decay should be very small
    assert score_one_session_later > 0.85  # exp(-0.1*1) ≈ 0.90


def test_sessions_since_never_negative():
    """sessions_since is clamped to 0 if last_retrieved_session > current_session."""
    payload = {"retrieval_count": 1, "last_retrieved_session": 99}
    score = _adjusted_score(1.0, payload, current_session=5)
    # sessions_since = max(0, 5-99) = 0
    assert score >= 1.0  # no decay, usage boost applied
