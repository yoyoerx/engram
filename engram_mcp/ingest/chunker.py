"""Sentence-aware text splitter with overlap."""

import re
from engram_mcp.config import CHUNK_SIZE, CHUNK_OVERLAP

# Sentence boundary: ends with . ! ? followed by whitespace or end-of-string,
# but not abbreviations like "Dr." or decimal numbers.
_SENTENCE_END = re.compile(r'(?<=[.!?])\s+(?=[A-Z\"\'])')


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_END.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


def chunk(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    Split text into chunks of at most chunk_size chars, respecting sentence
    boundaries where possible. Adjacent chunks share `overlap` chars of context.
    """
    if len(text) <= chunk_size:
        return [text.strip()]

    sentences = _split_sentences(text)
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        candidate = (current + " " + sentence).strip() if current else sentence

        if len(candidate) <= chunk_size:
            current = candidate
        else:
            if current:
                chunks.append(current)
            # If a single sentence exceeds chunk_size, hard-split it
            if len(sentence) > chunk_size:
                for i in range(0, len(sentence), chunk_size - overlap):
                    chunks.append(sentence[i : i + chunk_size])
                current = sentence[max(0, len(sentence) - overlap):]
            else:
                current = sentence

    if current:
        chunks.append(current)

    # Add overlap: prefix each chunk (after the first) with the tail of the previous
    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            tail = chunks[i - 1][-overlap:]
            overlapped.append((tail + " " + chunks[i]).strip())
        return overlapped

    return chunks
