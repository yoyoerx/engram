"""Entity and relationship extraction via Claude Haiku."""

import json
import anthropic
from engram_mcp.config import ANTHROPIC_API_KEY, EXTRACT_MODEL
from engram_mcp.retry import call_with_retry_sync

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


_SYSTEM = """\
You are an entity extraction engine. Given a memory chunk, extract all named entities
and relationships and return ONLY a JSON object. No prose, no markdown fences.

Use only these node labels:
  User, Project, Feedback, Reference, Error, Decision, Concept, Tool, Memory

Use only these relationship types:
  APPLIES_TO, PREVENTS, CAUSED_BY, USES, INVOLVES, SUPERSEDES, SIMILAR_TO, LINKED_TO, ABOUT

Return this exact structure:
{
  "entities": [
    {"label": "<NodeLabel>", "name": "<canonical name>", "properties": {}}
  ],
  "relationships": [
    {"from": "<entity name>", "type": "<REL_TYPE>", "to": "<entity name>"}
  ]
}

Rules:
- Canonical names: title-case for proper nouns, lowercase for concepts.
- Only include relationships where BOTH entities appear in your entities list.
- If nothing clear can be extracted, return {"entities": [], "relationships": []}.
- Do NOT invent entities or relationships not implied by the text."""

_USER_TMPL = "Extract entities and relationships from this memory chunk:\n\n{chunk}"


def extract(chunk: str) -> dict:
    """
    Call Haiku to extract entities and relationships from a text chunk.
    Returns {"entities": [...], "relationships": [...]}.
    Falls back to empty result on any error to keep ingestion non-blocking.
    """
    try:
        message = call_with_retry_sync(
            _get_client().messages.create,
            model=EXTRACT_MODEL,
            max_tokens=1024,
            system=_SYSTEM,
            messages=[{"role": "user", "content": _USER_TMPL.format(chunk=chunk)}],
            max_attempts=3,
            base_delay=1.0,
            backoff=2.0,
        )
        raw = message.content[0].text.strip()
        # Strip accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        # Validate structure
        if "entities" not in result or "relationships" not in result:
            return {"entities": [], "relationships": []}
        return result
    except Exception:
        return {"entities": [], "relationships": []}
