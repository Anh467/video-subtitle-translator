"""Step 1 — Transcribe audio/video → transcript + timestamps (Whisper)."""

import os
import time
from dataclasses import dataclass
from pathlib import Path

from core.pipeline.base import BaseStep, CancelledError
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

SUPPORTED_AUDIO = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma"}
SUPPORTED_VIDEO = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv"}
SUPPORTED_FORMATS = SUPPORTED_AUDIO | SUPPORTED_VIDEO
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large"]

LANGUAGES = {
    "Auto detect": None,
    "Vietnamese": "vi",
    "English": "en",
    "Japanese": "ja",
    "Korean": "ko",
    "Chinese": "zh-CN",
    "French": "fr",
    "German": "de",
    "Spanish": "es",
    "Thai": "th",
    "Indonesian": "id",
}


def _fmt(s):
    return f"{s:.2f}s" if s < 60 else f"{int(s//60)}m {s%60:.1f}s"


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptResult:
    text: str
    segments: list
    language: str
    source_file: str


class TranscribeStep(BaseStep):
    STEP_ID = "step1_transcribe"
    LABEL = "① Transcribe"
    COLOR = "#6c63ff"
    ENABLED_BY_DEFAULT = True

    def __init__(self):
        self._model = None
        self._model_size = None
        # widget refs (set in build_config_widget)
        self._model_combo = None
        self._lang_combo = None

    def request_cancel(self, event):
        self._cancel_event = (
            event  # Whisper doesn't support mid-run cancel; checked between stages
        )

    def run(self, session, config, log, cancel):
        file_path = session.source_file
        model_size = config["model_size"]
        language = config["language"]
        t0 = time.perf_counter()

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        p = Path(file_path)
        log(f"{'─'*38}")
        log(f"📄 File     : {p.name}")
        log(f"📦 Size     : {p.stat().st_size/1024/1024:.2f} MB")
        log(f"🔧 Model    : {model_size}")
        log(f"🌐 Language : {language or 'auto-detect'}")
        log(f"{'─'*38}")

        if cancel.is_set():
            raise CancelledError()

        # Load model
        if self._model is None or self._model_size != model_size:
            log(f"⏳ Loading Whisper model '{model_size}'…")
            t1 = time.perf_counter()
            import whisper

            self._model = whisper.load_model(model_size)
            self._model_size = model_size
            log(f"✅ Model loaded (+{_fmt(time.perf_counter()-t1)})")

        if cancel.is_set():
            raise CancelledError()

        # Extract audio if video
        audio_path, tmp = file_path, None
        if Path(file_path).suffix.lower() in SUPPORTED_VIDEO:
            import subprocess
            import tempfile

            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
            log("🎬 Extracting audio…")
            t1 = time.perf_counter()
            r = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    file_path,
                    "-vn",
                    "-acodec",
                    "pcm_s16le",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    tmp,
                ],
                capture_output=True,
                text=True,
            )
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg error:\n{r.stderr}")
            log(f"✅ Audio extracted (+{_fmt(time.perf_counter()-t1)})")
            audio_path = tmp

        if cancel.is_set():
            raise CancelledError()

        try:
            log("🎧 Transcribing…")
            t1 = time.perf_counter()
            raw = self._model.transcribe(audio_path, language=language)
            log(f"✅ Transcription done (+{_fmt(time.perf_counter()-t1)})")
        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)

        segs = [
            Segment(round(s["start"], 2), round(s["end"], 2), s["text"].strip())
            for s in raw.get("segments", [])
        ]
        lang = raw.get("language", "unknown")
        dur = segs[-1].end if segs else 0
        log(f"🌐 Detected: {lang}  |  {len(segs)} segs  |  {_fmt(dur)}")

        result = TranscriptResult(raw["text"].strip(), segs, lang, file_path)
        session.save_transcript(result)
        return result

    def build_config_widget(self, parent=None):
        w = QWidget(parent)
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel("Model:"))
        self._model_combo = QComboBox()
        self._model_combo.addItems(WHISPER_MODELS)
        self._model_combo.setCurrentText("base")
        h.addWidget(self._model_combo)
        h.addSpacing(10)
        h.addWidget(QLabel("Source lang:"))
        self._lang_combo = QComboBox()
        self._lang_combo.addItems(LANGUAGES.keys())
        h.addWidget(self._lang_combo)
        h.addStretch()
        return w

    def collect_config(self):
        return {
            "model_size": (
                self._model_combo.currentText() if self._model_combo else "base"
            ),
            "language": LANGUAGES.get(
                self._lang_combo.currentText() if self._lang_combo else "Auto detect"
            ),
        }
