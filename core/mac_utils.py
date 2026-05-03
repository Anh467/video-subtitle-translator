"""macOS helpers (this branch targets Finder + Homebrew-style tooling)."""

from __future__ import annotations

import subprocess
from pathlib import Path


def reveal_in_finder(path: str | Path) -> None:
    """Reveal a file or folder in Finder (``open``)."""
    p = Path(path).expanduser()
    try:
        p = p.resolve(strict=False)
    except OSError:
        p = Path(path).expanduser()
    subprocess.run(["open", str(p)], check=False)
