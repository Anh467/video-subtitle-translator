"""FFmpeg binary resolution and filter-graph escaping (cross-platform)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _bundled_ffmpeg_candidates() -> list[Path]:
    """Paths to try when shipping ffmpeg next to a frozen app (PyInstaller, etc.)."""
    name = "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"
    out: list[Path] = []
    # PyInstaller one-file / one-dir: binaries often land in sys._MEIPASS
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        root = Path(meipass)
        out.append(root / name)
        out.append(root / "ffmpeg" / name)
    # Executable directory (one-dir bundle or loose copy)
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        out.append(exe_dir / name)
        out.append(exe_dir / "ffmpeg" / name)
    # Dev: repo-relative tools/ffmpeg (optional manual drop)
    here = Path(__file__).resolve().parent.parent / "tools" / "ffmpeg" / name
    out.append(here)
    return out


def ffmpeg_executable() -> str:
    """
    Resolve ffmpeg binary:

    1. ``FFMPEG_EXECUTABLE`` if set (full path).
    2. Next to / inside a **frozen** app (see ``_bundled_ffmpeg_candidates``).
    3. ``ffmpeg`` / ``ffmpeg.exe`` on **PATH**.

    There is no single cross-OS ffmpeg binary: ship **one Windows build** with the
    Windows installer and **one macOS build** with the ``.app``. Same Python code,
    different bundled executables.
    """
    env = os.environ.get("FFMPEG_EXECUTABLE")
    if env:
        return env
    for p in _bundled_ffmpeg_candidates():
        try:
            if p.is_file():
                return str(p)
        except OSError:
            continue
    return "ffmpeg"


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

    # Windows: FFmpeg expects quoted paths + \\: before drive letter (never bare C:/ in filters).
    # macOS FFmpeg 8.x needs unquoted /private/tmp/… for filter_complex; see subtitles_filter_clause doc.
    windows_like = sys.platform.startswith("win") or os.name == "nt"
    windows_like = windows_like or (len(s) >= 2 and s[0].isascii() and s[1] == ":")
    if windows_like:
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
