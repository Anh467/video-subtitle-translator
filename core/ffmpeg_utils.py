"""Resolve ffmpeg/ffprobe and build subtitle filter strings for macOS SubSync."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _bundled_binary_candidates(name: str) -> list[Path]:
    """PyInstaller bundle, app dir, or ``tools/ffmpeg/`` checkout drop."""
    out: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        root = Path(meipass)
        out.extend([root / name, root / "ffmpeg" / name])
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        out.extend([exe_dir / name, exe_dir / "ffmpeg" / name])
    out.append(Path(__file__).resolve().parent.parent / "tools" / "ffmpeg" / name)
    return out


def ffmpeg_executable() -> str:
    """
    Resolve ffmpeg:

    1. ``FFMPEG_EXECUTABLE`` if set (full path, e.g. ``/opt/homebrew/bin/ffmpeg``).
    2. Bundled next to frozen app / ``tools/ffmpeg/ffmpeg``.
    3. ``ffmpeg`` from ``PATH`` (Homebrew).
    """
    env = os.environ.get("FFMPEG_EXECUTABLE")
    if env:
        return env
    for p in _bundled_binary_candidates("ffmpeg"):
        try:
            if p.is_file():
                return str(p)
        except OSError:
            continue
    return "ffmpeg"


def ffprobe_executable() -> str:
    """
    Resolve ffprobe:

    1. ``FFPROBE_EXECUTABLE`` if set.
    2. Same bundle dirs as ffmpeg, then sibling of resolved ffmpeg, then ``ffprobe`` on PATH.
    """
    env = os.environ.get("FFPROBE_EXECUTABLE")
    if env:
        return env
    for p in _bundled_binary_candidates("ffprobe"):
        try:
            if p.is_file():
                return str(p)
        except OSError:
            continue
    ff = ffmpeg_executable()
    if ff != "ffmpeg":
        sib = Path(ff).parent / "ffprobe"
        try:
            if sib.is_file():
                return str(sib)
        except OSError:
            pass
    return "ffprobe"


def subtitles_filter_clause(subs_path: str | os.PathLike[str]) -> str:
    """
    Full ``subtitles=…`` filter token (no input/output pads).

    FFmpeg ~8.x on macOS rejects some quoted subtitled paths followed by ``[pad]``;
    unquoted POSIX paths avoid that when the path contains no filter-meta characters.
    """
    subs_p = Path(subs_path).expanduser()
    try:
        subs_p = subs_p.resolve(strict=False)
    except OSError:
        subs_p = Path(subs_path).expanduser()
    s = subs_p.as_posix().replace("\\", "/")

    bad = tuple(" '\"[],;&=#")
    needs_quote = any(ch in s for ch in bad) or any(ord(ch) < 32 for ch in s)
    if needs_quote:
        inner = escape_for_ffmpeg_single_quoted_fragment(s)
        return f"subtitles='{inner}'"
    return f"subtitles={s}"


def escape_for_ffmpeg_single_quoted_fragment(text: str) -> str:
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
