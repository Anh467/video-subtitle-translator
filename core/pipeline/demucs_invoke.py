"""Run Demucs in-process so PyInstaller apps never invoke ``SubSync -m demucs``."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout


class _LineLogStream:
    """Forward ``print`` lines to a SubSync ``log`` callback."""

    encoding = "utf-8"

    def __init__(self, log: Callable[[str], None], prefix: str = "   "):
        self._log = log
        self._prefix = prefix
        self._buf = ""

    def write(self, s: str | None) -> int:
        if not s:
            return 0
        if not isinstance(s, str):
            s = str(s)
        self._buf += s
        parts = self._buf.split("\n")
        self._buf = parts[-1]
        for line in parts[:-1]:
            t = line.strip()
            if t:
                self._log(f"{self._prefix}{t}")
        return len(s)

    def flush(self) -> None:
        if self._buf.strip():
            self._log(f"{self._prefix}{self._buf.strip()}")
            self._buf = ""


def run_demucs_in_process(opts: list[str], log: Callable[[str], None]) -> None:
    """
    Call ``demucs.separate.main`` with an argv-style list (no script name).

    ``dora.fatal`` / argparse may call ``sys.exit`` — converted to ``RuntimeError``.
    """
    from demucs.separate import main as demucs_main

    stream = _LineLogStream(log)
    log(f"   $ demucs {' '.join(opts)}")
    try:
        with redirect_stdout(stream), redirect_stderr(stream):
            demucs_main(opts)
    except SystemExit as e:
        code = e.code
        if code not in (0, None):
            raise RuntimeError(f"Demucs failed (exit {code!r})") from e
    finally:
        stream.flush()
