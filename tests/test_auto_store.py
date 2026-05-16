"""Unit tests for auto_store._parse_haiku_response."""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from auto_store import _parse_haiku_response


def test_clean_json_array():
    text = '[{"content": "foo", "memory_type": "decision", "project": null}]'
    result = _parse_haiku_response(text)
    assert len(result) == 1
    assert result[0]["content"] == "foo"


def test_empty_array():
    assert _parse_haiku_response("[]") == []


def test_trailing_text_after_array():
    # The "Extra data" failure case — JSON followed by explanation text
    text = '[{"content": "bar", "memory_type": "project", "project": "x"}]\nNothing notable.'
    result = _parse_haiku_response(text)
    assert len(result) == 1
    assert result[0]["project"] == "x"


def test_empty_array_with_trailing_text():
    # [] followed by a newline and text — previously caused "Extra data: line 2 column 1 (char 3)"
    text = "[]\nI found nothing worth storing in this transcript."
    assert _parse_haiku_response(text) == []


def test_markdown_fence():
    text = '```json\n[{"content": "baz", "memory_type": "feedback", "project": null}]\n```'
    result = _parse_haiku_response(text)
    assert len(result) == 1
    assert result[0]["memory_type"] == "feedback"


def test_markdown_fence_with_trailing_text():
    text = '```\n[{"content": "qux", "memory_type": "error", "project": "p"}]\n```\nSome note.'
    result = _parse_haiku_response(text)
    assert len(result) == 1


def test_no_array_returns_empty():
    assert _parse_haiku_response("Nothing here.") == []


def test_non_list_json_returns_empty():
    assert _parse_haiku_response('{"content": "oops"}') == []


def test_whitespace_only():
    assert _parse_haiku_response("   ") == []
