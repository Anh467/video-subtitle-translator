"""
FileLogger — ghi log vào file theo ngày, giống .NET logging pattern.

Log folder: <base_dir>/logs/
Log files:  subsync_2026-04-28.log
            subsync_2026-04-29.log
            ...

Format:
  [2026-04-28 10:23:45.123] [INFO ] [step1_transcribe] message
  [2026-04-28 10:23:45.456] [ERROR] [step5_tts      ] ❌ Seg 3 failed: ...

Usage:
    from core.log_file import FileLogger
    logger = FileLogger.get()          # singleton
    logger.init(base_dir)              # call once when folder selected
    logger.write("some message")       # called automatically via hook
    logger.write("msg", level="ERROR")
"""

import threading
from datetime import datetime
from pathlib import Path


class FileLogger:
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self._log_dir: Path | None = None
        self._step_name: str = "app"
        self._enabled: bool = False
        self._file_lock = threading.Lock()

    # ── Singleton ─────────────────────────────────────────────────────────────

    @classmethod
    def get(cls) -> "FileLogger":
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
        self.write(f"{'='*60}", level="SEP")
        self.write(f"SubSync session started — base: {base_dir}", level="INFO")
        self.write(f"Log folder: {self._log_dir}", level="INFO")

    def set_step(self, step_name: str):
        """Set current step context for log prefix."""
        self._step_name = step_name

    def disable(self):
        self._enabled = False

    @property
    def log_path(self) -> Path | None:
        if not self._log_dir:
            return None
        today = datetime.now().strftime("%Y-%m-%d")
        return self._log_dir / f"subsync_{today}.log"

    # ── Write ─────────────────────────────────────────────────────────────────

    def write(self, message: str, level: str = "INFO"):
        if not self._enabled or not self._log_dir:
            return
        try:
            path = self.log_path
            if path is None:
                return
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            step = self._step_name[:20].ljust(20)
            lvl = level[:5].ljust(5)
            # Strip emoji for cleaner file log
            clean = _strip_emoji(message.strip())
            line = f"[{now}] [{lvl}] [{step}] {clean}\n"
            with self._file_lock:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line)
        except Exception:
            pass  # Never crash the app due to logging

    def write_separator(self, label: str = ""):
        self.write(f"{'─'*60}" + (f" {label}" if label else ""), level="SEP")

    def list_log_files(self) -> list[Path]:
        if not self._log_dir or not self._log_dir.exists():
            return []
        return sorted(self._log_dir.glob("subsync_*.log"), reverse=True)

    # ── Hook into pipeline log callbacks ─────────────────────────────────────

    def make_log_fn(self, ui_log_fn, step_name: str = ""):
        """
        Wrap a UI log callback to also write to file.
        Use this to replace any `log` callback in steps.

        Example:
            log = file_logger.make_log_fn(self._log, "step1_transcribe")
        """
        if step_name:
            self._step_name = step_name

        def _log(msg: str):
            # 1. Send to UI
            ui_log_fn(msg)
            # 2. Write to file
            level = _detect_level(msg)
            self.write(msg, level=level)

        return _log


# ── Utility ───────────────────────────────────────────────────────────────────


def _detect_level(msg: str) -> str:
    """Infer log level from message content."""
    m = msg.strip()
    if any(x in m for x in ("❌", "ERROR", "FAILED", "failed", "Exception")):
        return "ERROR"
    if any(x in m for x in ("⚠️", "WARNING", "warning", "⚠")):
        return "WARN"
    if any(x in m for x in ("✅", "done", "Done", "complete", "Complete")):
        return "OK"
    if any(x in m for x in ("🚀", "▶", "Running", "Starting")):
        return "START"
    if "─" * 5 in m or "=" * 5 in m:
        return "SEP"
    return "INFO"


def _strip_emoji(text: str) -> str:
    """Remove common emoji for cleaner log files."""
    # Keep text readable but remove decorative emoji
    replacements = {
        "🎬": "[VIDEO]",
        "🎧": "[AUDIO]",
        "📄": "[FILE]",
        "✅": "[OK]",
        "❌": "[ERR]",
        "⚠️": "[WARN]",
        "🌏": "[TRANS]",
        "🔥": "[BURN]",
        "🎵": "[MUSIC]",
        "🎙️": "[TTS]",
        "📡": "[HTTP]",
        "📤": "[SEND]",
        "📥": "[RECV]",
        "⏳": "[WAIT]",
        "⏱️": "[TIME]",
        "🔍": "[DEBUG]",
        "💾": "[SAVE]",
        "📊": "[STAT]",
        "🚀": "[START]",
        "🎉": "[DONE]",
        "🔗": "[CHAIN]",
        "💡": "[TIP]",
        "🔑": "[KEY]",
        "⏸️": "[GAP]",
        "📖": "[INFO]",
        "🏁": "[END]",
        "▶": ">",
        "⭐": "*",
        "✨": "*",
        "→": "->",
        "║": "|",
        "─": "-",
        "═": "=",
    }
    for emoji, replacement in replacements.items():
        text = text.replace(emoji, replacement)
    # Remove remaining non-ASCII emoji
    result = []
    for ch in text:
        if ord(ch) < 0x2000:
            result.append(ch)
        else:
            # Keep common unicode punctuation
            if ch in "→←↑↓•·…–—":
                result.append(ch)
    return "".join(result)
