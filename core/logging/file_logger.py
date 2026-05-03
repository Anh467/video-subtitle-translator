"""File-backed logger: one line per write, rotated by calendar day."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from core.logging.log_text import detect_level, strip_emoji


class FileLogger:
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self._log_dir: Path | None = None
        self._step_name: str = "app"
        self._enabled: bool = False
        self._file_lock = threading.Lock()
        self._cache_date: str = ""
        self._cache_path: Path | None = None

    # ── Singleton ─────────────────────────────────────────────────────────────

    @classmethod
    def get(cls) -> FileLogger:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── Setup ─────────────────────────────────────────────────────────────────

    def init(self, base_dir: str):
        """Call when user selects base session folder."""
        self._log_dir = Path(base_dir) / "logs"
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._enabled = True
        self._invalidate_path_cache()
        self.write(f"{'=' * 60}", level="SEP")
        self.write(f"SubSync session started — base: {base_dir}", level="INFO")
        self.write(f"Log folder: {self._log_dir}", level="INFO")

    def set_step(self, step_name: str):
        """Set current step context for log prefix."""
        self._step_name = step_name

    def disable(self):
        self._enabled = False

    def _invalidate_path_cache(self):
        self._cache_date = ""
        self._cache_path = None

    def _current_log_path(self) -> Path | None:
        if not self._log_dir:
            return None
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._cache_date or self._cache_path is None:
            self._cache_date = today
            self._cache_path = self._log_dir / f"subsync_{today}.log"
        return self._cache_path

    @property
    def log_path(self) -> Path | None:
        """Today's log file path (computes cached path each access for correct day rollover)."""
        return self._current_log_path()

    # ── Write ─────────────────────────────────────────────────────────────────

    def write(self, message: str, level: str = "INFO"):
        if not self._enabled or not self._log_dir:
            return
        try:
            path = self._current_log_path()
            if path is None:
                return
            now = datetime.now()
            now_str = now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            step = self._step_name[:20].ljust(20)
            lvl = level[:5].ljust(5)
            clean = strip_emoji(message.strip())
            line = f"[{now_str}] [{lvl}] [{step}] {clean}\n"
            with self._file_lock:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line)
        except Exception:
            pass  # Never crash the app due to logging

    def write_separator(self, label: str = ""):
        self.write(f"{'─' * 60}" + (f" {label}" if label else ""), level="SEP")

    def list_log_files(self) -> list[Path]:
        if not self._log_dir or not self._log_dir.exists():
            return []
        return sorted(self._log_dir.glob("subsync_*.log"), reverse=True)

    # ── Hook into pipeline log callbacks ─────────────────────────────────────

    def make_log_fn(self, ui_log_fn, step_name: str = ""):
        """Wrap a UI log callback to also write to file."""
        if step_name:
            self._step_name = step_name

        def _log(msg: str):
            ui_log_fn(msg)
            lvl = detect_level(msg)
            self.write(msg, level=lvl)

        return _log
