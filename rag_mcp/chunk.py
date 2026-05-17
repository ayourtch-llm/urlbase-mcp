from __future__ import annotations

import re
from typing import Iterable, List

_SPLIT_PATTERNS = [
    r"\n\s*\n",   # paragraph breaks
    r"(?<=[.!?])\s+",  # sentence boundary
    r"\n",        # line break
    r"\s+",       # whitespace
]


def _split_keep(text: str, pattern: str) -> List[str]:
    parts = re.split(pattern, text)
    return [p for p in parts if p]


def _recursive(text: str, max_chars: int, level: int = 0) -> List[str]:
    if len(text) <= max_chars:
        return [text]
    if level >= len(_SPLIT_PATTERNS):
        # Hard split.
        return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]
    pieces = _split_keep(text, _SPLIT_PATTERNS[level])
    if len(pieces) <= 1:
        return _recursive(text, max_chars, level + 1)
    out: List[str] = []
    for p in pieces:
        if len(p) <= max_chars:
            out.append(p)
        else:
            out.extend(_recursive(p, max_chars, level + 1))
    return out


def _merge_with_overlap(
    pieces: Iterable[str], max_chars: int, overlap: int
) -> List[str]:
    chunks: List[str] = []
    buf = ""
    for p in pieces:
        p = p.strip()
        if not p:
            continue
        if not buf:
            buf = p
            continue
        if len(buf) + 1 + len(p) <= max_chars:
            buf = buf + " " + p
        else:
            chunks.append(buf)
            if overlap > 0 and len(buf) > overlap:
                tail = buf[-overlap:]
                buf = tail + " " + p
            else:
                buf = p
    if buf:
        chunks.append(buf)
    return chunks


def chunk_text(text: str, max_chars: int, overlap: int) -> List[str]:
    text = text.strip()
    if not text:
        return []
    pieces = _recursive(text, max_chars)
    return _merge_with_overlap(pieces, max_chars, overlap)
