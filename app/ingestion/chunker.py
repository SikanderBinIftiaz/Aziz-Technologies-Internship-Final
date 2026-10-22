"""Split page text into overlapping chunks that respect paragraph and sentence boundaries."""
import re
from dataclasses import dataclass

from app.ingestion.parsers import ParsedPage

_UNIT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass
class Chunk:
    page: int
    index: int
    text: str


def _hard_split(text: str, size: int) -> list[str]:
    """Split an oversized unit on word boundaries (and on characters for unbroken strings)."""
    parts: list[str] = []
    current = ""
    for word in text.split(" "):
        while len(word) > size:
            if current:
                parts.append(current)
                current = ""
            parts.append(word[:size])
            word = word[size:]
        if current and len(current) + 1 + len(word) > size:
            parts.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        parts.append(current)
    return parts


def _units(text: str, size: int) -> list[str]:
    units: list[str] = []
    for raw in _UNIT_SPLIT.split(text):
        unit = raw.strip()
        if not unit:
            continue
        if len(unit) > size:
            units.extend(_hard_split(unit, size))
        else:
            units.append(unit)
    return units


MIN_CHUNK_CHARS = 200


def _merge_small(chunks: list[Chunk], size: int) -> list[Chunk]:
    """Fold tiny fragments (a title line, a one-line section) into the next chunk on the same page.

    Very short chunks are mostly keywords with no content, so they match many queries and rank above
    the passages that actually hold the answer.
    """
    merged: list[Chunk] = []
    pending: Chunk | None = None
    for chunk in chunks:
        if pending is not None:
            if pending.page == chunk.page and len(pending.text) + 1 + len(chunk.text) <= size:
                chunk = Chunk(chunk.page, pending.index, f"{pending.text} {chunk.text}")
            else:
                merged.append(pending)
            pending = None
        if len(chunk.text) < MIN_CHUNK_CHARS:
            pending = chunk
        else:
            merged.append(chunk)
    if pending is not None:
        merged.append(pending)
    return [Chunk(c.page, i, c.text) for i, c in enumerate(merged)]


def chunk_pages(pages: list[ParsedPage], size: int = 800, overlap: int = 150) -> list[Chunk]:
    if size <= 0:
        raise ValueError("size must be positive")
    if not 0 <= overlap < size:
        raise ValueError("overlap must be >= 0 and smaller than size")

    chunks: list[Chunk] = []
    index = 0
    for page in pages:
        current: list[str] = []
        length = 0
        fresh = 0  # units added since the last flush (carried-over overlap does not count)
        for unit in _units(page.text, size):
            starts_section = unit.startswith("#")  # a Markdown heading begins a new topic
            if current and fresh and (starts_section or length + len(unit) + 1 > size):
                chunks.append(Chunk(page.page, index, " ".join(current)))
                index += 1
                carried: list[str] = []
                carried_len = 0
                if not starts_section:  # overlap helps inside a topic, but is not carried across headings
                    for previous in reversed(current):
                        if carried_len + len(previous) > overlap:
                            break
                        carried.insert(0, previous)
                        carried_len += len(previous) + 1
                current, length, fresh = carried, carried_len, 0
            current.append(unit)
            length += len(unit) + 1
            fresh += 1
        if current and fresh:
            chunks.append(Chunk(page.page, index, " ".join(current)))
            index += 1
    return _merge_small(chunks, size)
