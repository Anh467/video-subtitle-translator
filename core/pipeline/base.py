"""
BaseStep — abstract class mọi pipeline step đều kế thừa.

Mỗi step chỉ cần implement:
  - STEP_ID   : str  (vd "step1_transcribe")
  - LABEL     : str  (vd "① Transcribe")
  - COLOR     : str  (hex color cho UI)
  - run(session, config, log) -> Any
  - build_config_widget() -> QWidget  (các control riêng của step)

Worker thread được tạo tự động từ BaseStep.make_worker().
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Callable

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal, pyqtSlot

# ── Signals shared by all workers ────────────────────────────────────────────


class StepSignals(QObject):
    progress = pyqtSignal(str)  # log message
    finished = pyqtSignal(object)  # result object
    error = pyqtSignal(str)  # error message
    cancelled = pyqtSignal()


# ── Generic worker — wraps any BaseStep.run() call ───────────────────────────


class StepWorker(QRunnable):
    def __init__(self, step: "BaseStep", session, config: dict):
        super().__init__()
        self.setAutoDelete(False)
        self._step = step
        self._session = session
        self._config = config
        self.signals = StepSignals()
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()
        self._step.request_cancel(self._cancel)

    @pyqtSlot()
    def run(self):
        t0 = time.perf_counter()
        try:
            result = self._step.run(
                session=self._session,
                config=self._config,
                log=self.signals.progress.emit,
                cancel=self._cancel,
            )
            from core.pipeline.base import _fmt

            self.signals.progress.emit(
                f"⏱  {self._step.LABEL} finished in {_fmt(time.perf_counter()-t0)}"
            )
            self.signals.finished.emit(result)
        except CancelledError:
            self.signals.progress.emit("🚫 Cancelled.")
            self.signals.cancelled.emit()
        except Exception as e:
            self.signals.error.emit(str(e))


def _fmt(s: float) -> str:
    return f"{s:.2f}s" if s < 60 else f"{int(s//60)}m {s%60:.1f}s"


class CancelledError(Exception):
    pass


# ── BaseStep ──────────────────────────────────────────────────────────────────


class BaseStep(ABC):
    # Subclasses must define these
    STEP_ID: str = ""
    LABEL: str = ""
    COLOR: str = "#6c63ff"  # button accent color
    ENABLED_BY_DEFAULT: bool = True

    def request_cancel(self, event: threading.Event):
        """Called when user clicks Cancel. Override if step needs custom cleanup."""
        pass

    @abstractmethod
    def run(self, session, config: dict, log: Callable, cancel: threading.Event) -> Any:
        """Execute the step. Must check cancel.is_set() periodically."""
        ...

    @abstractmethod
    def build_config_widget(self, parent=None):
        """Return a QWidget with this step's settings controls."""
        ...

    @abstractmethod
    def collect_config(self) -> dict:
        """Read current values from config widget → return dict passed to run()."""
        ...

    def apply_config(self, config: dict) -> None:
        """Restore config widget values from a previously saved dict.
        Steps that persist UI state should override this method.
        Default: no-op."""

    def make_worker(self, session, config: dict) -> StepWorker:
        return StepWorker(self, session, config)
