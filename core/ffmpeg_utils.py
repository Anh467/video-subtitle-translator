"""FFmpeg binary resolution and filter-graph escaping (cross-platform)."""

from __future__ import annotations

import os


def ffmpeg_executable() -> str:
    """Prefer FFMPEG_EXECUTABLE, then ffmpeg on PATH."""
    return os.environ.get("FFMPEG_EXECUTABLE", "ffmpeg")


def subtitles_filter_clause(subs_path: str | os.PathLike[str]) -> str:
    """
    Builds the full `subtitles=…` filter substring (no `[in]`/`[out]` pads).

    macOS FFmpeg 8.x + filter_complex often mis-parses quoted subtitles paths followed
    by a pad label. Bare POSIX paths (no quotes) avoid that bug.

    On Windows use single-quoted paths with ':' escaped inside (SubSync historically).
    """
    from pathlib import Path

    subs_p = Path(subs_path).expanduser()
    try:
        subs_p = subs_p.resolve(strict=False)
    except OSError:
        subs_p = Path(subs_path).expanduser()
    s = subs_p.as_posix().replace("\\", "/")

    if os.name == "nt" or (len(s) >= 2 and s[1] == ":"):  # Windows path
        inner = escape_for_ffmpeg_single_quoted_fragment(s)
        return f"subtitles='{inner}'"

    # POSIX (macOS / Linux): unquoted unless filter-unsafe chars (then best-effort quote).
    bad = tuple(" '\"[],;&=#")
    needs_quote = any(ch in s for ch in bad) or any(ord(ch) < 32 for ch in s)
    if needs_quote:
        inner = escape_for_ffmpeg_single_quoted_fragment(s)
        return f"subtitles='{inner}'"
    return f"subtitles={s}"


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
