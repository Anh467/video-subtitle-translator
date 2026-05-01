"""Background cost estimate for multi-session selection."""

from __future__ import annotations

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from core.pipeline.step1_transcribe import WHISPER_API_COST_PER_MINUTE
from core.pipeline.step2_translate import TRANSLATION_COST_PER_1M_CHARS
from core.pipeline.step5_tts import COST_PER_1M as TTS_COST_PER_1M
from core.session import Session


class CostWorkerSignals(QObject):
    finished = pyqtSignal(int, float, float, float, bool)


class SelectedCostWorker(QRunnable):
    def __init__(
        self,
        request_id: int,
        sessions: list[dict],
        translate_backend: str,
        tts_backend: str,
    ):
        super().__init__()
        self.setAutoDelete(True)
        self.request_id = request_id
        self.sessions = sessions
        self.translate_backend = translate_backend
        self.tts_backend = tts_backend
        self.signals = CostWorkerSignals()

    def run(self):
        total_step1 = 0.0
        total_step2 = 0.0
        total_step5 = 0.0
        step1_unknown = False

        if self.tts_backend == "all":
            step5_rate = sum(TTS_COST_PER_1M.values())
        else:
            step5_rate = TTS_COST_PER_1M.get(self.tts_backend, 0.0)
        step2_rate = TRANSLATION_COST_PER_1M_CHARS.get(self.translate_backend, 0.0)

        for sess_data in self.sessions:
            try:
                session = Session.load(sess_data["folder"])
            except Exception:
                continue

            duration_minutes = session.step1_duration_minutes()
            if duration_minutes is None:
                step1_unknown = True
            else:
                total_step1 += duration_minutes * WHISPER_API_COST_PER_MINUTE

            if session.step1_done:
                total_step2 += session.step1_transcript_chars() / 1_000_000 * step2_rate

            if session.step2_done:
                total_step5 += session.step2_translated_chars() / 1_000_000 * step5_rate

        self.signals.finished.emit(
            self.request_id,
            total_step1,
            total_step2,
            total_step5,
            step1_unknown,
        )
