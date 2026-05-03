"""Resolve ffmpeg/ffprobe and build subtitle filter strings for macOS SubSync."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _resolve_system_tool(name: str) -> str | None:
    """
    Find ffmpeg/ffprobe when ``PATH`` is minimal (e.g. PyInstaller ``.app`` launched from Finder).

    Homebrew installs to ``/opt/homebrew/bin`` (Apple Silicon) or ``/usr/local/bin`` (Intel).
    """
    hit = shutil.which(name)
    if hit:
        p = Path(hit)
        try:
            if p.is_file():
                return str(p.resolve(strict=False))
        except OSError:
            if p.is_file():
                return str(p)
    if sys.platform == "darwin":
        for base in ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin"):
            p = Path(base) / name
            try:
                if p.is_file():
                    return str(p)
            except OSError:
                continue
    return None


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
    3. ``shutil.which("ffmpeg")`` then common Homebrew locations (Finder-launched apps often lack ``PATH``).
    4. Fallback bare ``"ffmpeg"`` (may still fail if nothing is installed).
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
    found = _resolve_system_tool("ffmpeg")
    return found or "ffmpeg"


def ffprobe_executable() -> str:
    """
    Resolve ffprobe:

    1. ``FFPROBE_EXECUTABLE`` if set.
    2. Same bundle dirs as ffmpeg, sibling of ``FFMPEG_EXECUTABLE``, sibling of bundled ``ffmpeg``,
       then ``which`` / Homebrew paths (same as ``ffmpeg``), then sibling of resolved ``ffmpeg``.
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
    env_ff = os.environ.get("FFMPEG_EXECUTABLE")
    if env_ff:
        sib = Path(env_ff).expanduser().parent / "ffprobe"
        try:
            if sib.is_file():
                return str(sib)
        except OSError:
            pass
    for p in _bundled_binary_candidates("ffmpeg"):
        try:
            if p.is_file():
                sib = p.parent / "ffprobe"
                if sib.is_file():
                    return str(sib)
        except OSError:
            continue
    probe = _resolve_system_tool("ffprobe")
    if probe:
        return probe
    ff = _resolve_system_tool("ffmpeg")
    if ff:
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

    FFmpeg 8.x parses the shorthand ``subtitles=/abs/path.ass`` incorrectly (the ``/``
    after ``=`` is treated as starting a new token — error *No option name near '/…'*).
    Always use the explicit ``filename='…'`` form with a single-quoted, escaped path.
    """
    subs_p = Path(subs_path).expanduser()
    try:
        subs_p = subs_p.resolve(strict=False)
    except OSError:
        subs_p = Path(subs_path).expanduser()
    s = subs_p.as_posix().replace("\\", "/")
    inner = escape_for_ffmpeg_single_quoted_fragment(s)
    return f"subtitles=filename='{inner}'"


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
