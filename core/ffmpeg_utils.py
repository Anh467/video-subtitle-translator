"""FFmpeg binary resolution and filter-graph escaping (cross-platform)."""

from __future__ import annotations

import os


def ffmpeg_executable() -> str:
    """Prefer FFMPEG_EXECUTABLE, then ffmpeg on PATH."""
    return os.environ.get("FFMPEG_EXECUTABLE", "ffmpeg")


def escape_for_ffmpeg_single_quoted_fragment(text: str) -> str:
    """
    Characters that break FFmpeg option parsing unless escaped inside '...'.

    Most relevant for ffmpeg 8.x + filter_complex where commas separate
    filter options unless written as \\\\,.
    """
    escaped: list[str] = []
    for ch in text:
        if ch == "\\":
            escaped.append("\\\\")
        elif ch == "'":
            escaped.append("\\'")
        elif ch == ",":
            escaped.append("\\,")
        elif ch == ":":
            escaped.append("\\:")
        elif ch == "[":
            escaped.append("\\[")
        elif ch == "]":
            escaped.append("\\]")
        elif ch == "#":
            escaped.append("\\#")
        elif ch == "&":
            escaped.append("\\&")
        elif ch == ";":
            escaped.append("\\;")
        else:
            escaped.append(ch)
    return "".join(escaped)
