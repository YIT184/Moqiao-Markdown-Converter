"""Markdown output helpers: table normalization, escaping, asset naming."""

from __future__ import annotations

import re
import os
import hashlib
from typing import List, Optional


# ---------------------------------------------------------------------------
# Table helpers
# ---------------------------------------------------------------------------

_PIPE_RE = re.compile(r"\|")
_NEWLINE_RE = re.compile(r"[\n\r]+")


def escape_cell(text: str) -> str:
    """Escape pipe and newline characters for a single Markdown table cell."""
    text = str(text)
    text = _PIPE_RE.sub(r"\|", text)
    text = _NEWLINE_RE.sub(" ", text)
    return text


def normalize_table(rows: List[List[str]]) -> str:
    """Convert a list-of-lists (rows × cols) to a valid Markdown table string.

    * Escapes pipe and newline characters in every cell.
    * Normalises uneven row lengths by padding shorter rows with empty cells.
    * Returns the full table including the header-separator line.
    * If *rows* is empty or contains no non-empty cells, returns an empty string.
    """
    if not rows:
        return ""

    # Strip trailing fully-empty rows (common in pdfplumber output)
    while rows and all(not cell.strip() for cell in rows[-1]):
        rows.pop()
    if not rows:
        return ""

    # Determine column count from the widest row
    col_count = max(len(row) for row in rows)

    # Escape and pad
    escaped: List[List[str]] = []
    for row in rows:
        padded = [escape_cell(row[i]) if i < len(row) else "" for i in range(col_count)]
        escaped.append(padded)
    rows = escaped

    # Build header row
    header = rows[0]
    lines: List[str] = []
    lines.append("| " + " | ".join(header) + " |")

    # Separator line
    lines.append("| " + " | ".join(["---"] * col_count) + " |")

    # Data rows
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Asset / filename helpers
# ---------------------------------------------------------------------------

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_MAX_BASENAME_LEN = 80


def safe_stem(name: str) -> str:
    """Return a safe filename stem: replace invalid chars, collapse whitespace."""
    name = _INVALID_FILENAME_CHARS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip()
    # If the result is empty or consists only of underscores, use fallback
    if not name or name.strip("_") == "":
        name = "unnamed"
    if len(name) > _MAX_BASENAME_LEN:
        name = name[:_MAX_BASENAME_LEN].rstrip()
    return name


def asset_filename(stem: str, prefix: str, ext: str, counter: int) -> str:
    """Build a consistent asset filename like ``prefix_000.png``."""
    safe = safe_stem(stem)
    return f"{safe}_{prefix}_{counter:03d}{ext}"


def collision_free_name(directory: str, preferred: str) -> str:
    """Return a filename that does not collide inside *directory*.

    Appends ``_N`` before the extension if the preferred name already exists.
    """
    base, ext = os.path.splitext(preferred)
    candidate = preferred
    n = 1
    while os.path.exists(os.path.join(directory, candidate)):
        candidate = f"{base}_{n}{ext}"
        n += 1
    return candidate


# ---------------------------------------------------------------------------
# Rectangle / geometry helpers
# ---------------------------------------------------------------------------

def rects_overlap(
    x1: float, y1: float, x2: float, y2: float,
    u1: float, v1: float, u2: float, v2: float,
) -> bool:
    """Return True if two axis-aligned rectangles intersect."""
    return not (x2 <= u1 or u2 <= x1 or y2 <= v1 or v2 <= y1)


def intersection_area(
    x1: float, y1: float, x2: float, y2: float,
    u1: float, v1: float, u2: float, v2: float,
) -> float:
    """Return the area of the intersection of two axis-aligned rectangles."""
    ix1 = max(x1, u1)
    iy1 = max(y1, v1)
    ix2 = min(x2, u2)
    iy2 = min(y2, v2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    return (ix2 - ix1) * (iy2 - iy1)


def box_area(x1: float, y1: float, x2: float, y2: float) -> float:
    """Return the area of a rectangle."""
    return (x2 - x1) * (y2 - y1)


def box_center(
    x1: float, y1: float, x2: float, y2: float,
) -> tuple[float, float]:
    """Return (cx, cy) center of a rectangle."""
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0


# ---------------------------------------------------------------------------
# Hash helper
# ---------------------------------------------------------------------------

def content_hash(data: bytes) -> str:
    """Return a short hex digest for deduplication."""
    return hashlib.sha256(data).hexdigest()[:16]
