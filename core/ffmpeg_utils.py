"""Resolve ffmpeg/ffprobe and build subtitle filter strings for macOS SubSync."""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def init_bundled_tools_path() -> None:
    """
    When running as a PyInstaller frozen app, prepend bundled ``bin/`` to ``PATH``.

    Layout: ``--add-data "bin:bin"`` so ``ffmpeg`` / ``ffprobe`` live under
    ``sys._MEIPASS/bin/``. Also prepends ``bin/`` next to the executable if present
    (onedir / manual copy).
    """
    if not getattr(sys, "frozen", False):
        return
    sep = os.pathsep
    prefixes: list[str] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        b = Path(meipass) / "bin"
        try:
            if b.is_dir():
                prefixes.append(str(b.resolve(strict=False)))
        except OSError:
            pass
    try:
        exe_dir = Path(sys.executable).resolve(strict=False).parent
        for d in (exe_dir / "bin", exe_dir.parent / "Resources" / "bin"):
            try:
                if d.is_dir():
                    prefixes.append(str(d.resolve(strict=False)))
            except OSError:
                continue
    except OSError:
        pass
    seen: set[str] = set()
    ordered: list[str] = []
    for p in prefixes:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    if not ordered:
        return
    old = os.environ.get("PATH", "")
    os.environ["PATH"] = sep.join(ordered + ([old] if old else []))


# Shown when ``ffmpeg -h filter=subtitles`` fails (e.g. Homebrew core ffmpeg without libass).
FFMPEG_LIBASS_INSTALL_HINT = """\
Homebrew’s default formula ``brew install ffmpeg`` (8.x) is built **without libass**, so the
``subtitles`` filter does not exist. SubSync hard-burn needs FFmpeg **with** ``--enable-libass``.

**macOS — recommended fix**

```bash
brew uninstall ffmpeg
brew tap homebrew-ffmpeg/ffmpeg
brew install homebrew-ffmpeg/ffmpeg/ffmpeg
```

Check:

```bash
ffmpeg -hide_banner -h filter=subtitles | head -n 3
```

You should see filter help text, not “Unknown filter”.

Alternatively set **FFMPEG_EXECUTABLE** to a full path of any FFmpeg build that includes libass
(official static builds, MacPorts, self-compiled with ``--enable-libass``).
"""


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
    """PyInstaller ``bin/``, MEIPASS, app dir, or ``tools/ffmpeg/`` checkout."""
    out: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        root = Path(meipass)
        out.append(root / "bin" / name)
        out.extend([root / name, root / "ffmpeg" / name])
    if getattr(sys, "frozen", False):
        try:
            exe_dir = Path(sys.executable).resolve(strict=False).parent
        except OSError:
            exe_dir = Path(sys.executable).parent
        out.extend(
            [
                exe_dir / "bin" / name,
                exe_dir / name,
                exe_dir / "ffmpeg" / name,
            ]
        )
        if exe_dir.name == "MacOS":
            res = exe_dir.parent / "Resources" / "bin" / name
            out.append(res)
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


@functools.lru_cache(maxsize=16)
def ffmpeg_has_subtitles_filter(executable: str) -> bool:
    """
    True if this FFmpeg exposes the libavfilter ``subtitles`` filter (requires libass).

    Homebrew ``ffmpeg`` 8.x core bottles often omit libass; ``subtitles`` is then missing
    entirely (``No such filter: 'subtitles'``).
    """
    try:
        p = subprocess.run(
            [executable, "-hide_banner", "-h", "filter=subtitles"],
            capture_output=True,
            text=True,
            timeout=25,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if p.returncode != 0:
        return False
    combined = (p.stdout or "") + (p.stderr or "")
    if re.search(r"(?i)unknown\s+filter", combined):
        return False
    return bool(combined.strip())


def assert_ffmpeg_subtitles_filter(executable: str) -> None:
    """Raise ``RuntimeError`` with install hints if ``subtitles`` is unavailable."""
    if not ffmpeg_has_subtitles_filter(executable):
        raise RuntimeError(
            f"FFmpeg {executable!r} does not provide the `subtitles` filter (libass).\n\n"
            + FFMPEG_LIBASS_INSTALL_HINT
        )


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
