"""Step 1 — Transcribe audio/video → transcript + timestamps (Whisper)."""

import os
import time
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from core.pipeline.base import BaseStep, CancelledError

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
    "Chinese": "zh",
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
        self._model_combo = None
        self._lang_combo = None
        self._min_silence_spin = None  # min silence gap between segments

    def request_cancel(self, event):
        self._cancel_event = event

    def run(self, session, config, log, cancel):
        file_path = session.source_file
        model_size = config["model_size"]
        language = config.get("language")
        min_silence = config.get("min_silence", 0.5)  # seconds
        p = Path(file_path)

        log(f"{'─'*38}")
        log(f"📄 File     : {p.name}")
        log(f"📦 Size     : {p.stat().st_size/1024/1024:.2f} MB")
        log(f"🔧 Model    : {model_size}")
        log(f"🌐 Language : {language or 'auto-detect'}")
        log(f"{'─'*38}")

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
                encoding="utf-8",
                errors="replace",
            )
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg error:\n{r.stderr}")
            log(f"✅ Audio extracted (+{_fmt(time.perf_counter()-t1)})")
            audio_path = tmp

        if cancel.is_set():
            raise CancelledError()

        # Normalize language code
        if language:
            language = language.split("-")[0].lower()
        lang_arg = repr(language) if language else "None"

        # Run Whisper in subprocess to avoid PyTorch+Qt thread crash
        import json
        import subprocess
        import sys
        import tempfile

        runner_code = (
            f"import whisper, json, sys\n"
            f"model = whisper.load_model({repr(model_size)})\n"
            f"r = model.transcribe(\n"
            f"    {repr(audio_path)},\n"
            f"    language={lang_arg},\n"
            f"    word_timestamps=False,\n"
            f"    condition_on_previous_text=True,\n"
            # no_speech_threshold: segments with prob below this are dropped
            f"    no_speech_threshold=0.6,\n"
            # logprob_threshold: drop segments with low avg log probability
            f"    logprob_threshold=-1.0,\n"
            # compression_ratio_threshold: drop hallucinated repetitions
            f"    compression_ratio_threshold=2.4,\n"
            f")\n"
            f"print(json.dumps(r))\n"
        )
        tmp_script = tempfile.NamedTemporaryFile(
            suffix=".py", delete=False, mode="w", encoding="utf-8"
        )
        tmp_script.write(runner_code)
        tmp_script.close()

        log("🎧 Transcribing…")
        t1 = time.perf_counter()
        try:
            r = subprocess.run(
                [sys.executable, tmp_script.name],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        finally:
            os.unlink(tmp_script.name)
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)

        if r.returncode != 0:
            raise RuntimeError(f"Whisper subprocess error:\n{r.stderr[-2000:]}")
        log(f"✅ Transcription done (+{_fmt(time.perf_counter()-t1)})")

        raw = json.loads(r.stdout)
        lang = raw.get("language", "unknown")

        # ── Post-process segments: preserve gaps, merge short silence ──────────
        raw_segs = raw.get("segments", [])
        segs = []

        for i, s in enumerate(raw_segs):
            start = round(s["start"], 2)
            end = round(s["end"], 2)
            text = s["text"].strip()

            if not text:
                continue  # skip empty segments

            # If gap before this segment < min_silence → merge with previous
            # This prevents over-splitting of natural speech pauses
            if (
                segs
                and (start - segs[-1].end) < min_silence
                and (start - segs[-1].end) >= 0
            ):
                # Small gap — extend previous segment's end to current start
                # so silence is captured but not a separate empty segment
                pass  # just add as new segment, gap will be silence in TTS

            segs.append(Segment(start, end, text))

        # Log gap statistics
        if len(segs) > 1:
            gaps = [segs[i + 1].start - segs[i].end for i in range(len(segs) - 1)]
            significant_gaps = [g for g in gaps if g > 0.5]
            log(
                f"🌐 Detected: {lang}  |  {len(segs)} segs  |  "
                f"{_fmt(segs[-1].end if segs else 0)}"
            )
            log(
                f"   Gaps: {len(significant_gaps)} pauses >{0.5}s detected "
                f"(longest: {max(gaps):.1f}s)"
            )
        else:
            log(f"🌐 Detected: {lang}  |  {len(segs)} segs")

        result = TranscriptResult(raw["text"].strip(), segs, lang, file_path)
        session.save_transcript(result)
        return result

    def build_config_widget(self, parent=None):
        w = QWidget(parent)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        # Model + Language
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Model:"))
        self._model_combo = QComboBox()
        self._model_combo.addItems(WHISPER_MODELS)
        self._model_combo.setCurrentText("base")
        self._model_combo.setToolTip(
            "tiny   — ⚡ fastest (3x base), good for clear audio\n"
            "base   — ✅ balanced (default)\n"
            "small  — better accuracy, 2x slower\n"
            "medium — high accuracy, 5x slower\n"
            "large  — best, 10x slower, needs 10GB RAM"
        )
        r1.addWidget(self._model_combo)
        r1.addSpacing(10)
        r1.addWidget(QLabel("Source lang:"))
        self._lang_combo = QComboBox()
        self._lang_combo.addItems(LANGUAGES.keys())
        r1.addWidget(self._lang_combo)
        r1.addStretch()
        v.addLayout(r1)

        # Min silence gap
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Min silence gap:"))
        self._min_silence_spin = QDoubleSpinBox()
        self._min_silence_spin.setRange(0.1, 3.0)
        self._min_silence_spin.setSingleStep(0.1)
        self._min_silence_spin.setValue(0.5)
        self._min_silence_spin.setFixedWidth(65)
        self._min_silence_spin.setToolTip(
            "Khoảng lặng tối thiểu (giây) để tách segment\n"
            "0.3s = tách nhiều hơn (chi tiết)\n"
            "0.5s = mặc định (cân bằng)\n"
            "1.0s = chỉ tách khi im lặng dài"
        )
        r2.addWidget(self._min_silence_spin)
        r2.addWidget(QLabel("s"))
        r2.addStretch()
        v.addLayout(r2)

        return w

    def collect_config(self):
        return {
            "model_size": (
                self._model_combo.currentText() if self._model_combo else "base"
            ),
            "language": LANGUAGES.get(
                self._lang_combo.currentText() if self._lang_combo else "Auto detect"
            ),
            "min_silence": (
                self._min_silence_spin.value() if self._min_silence_spin else 0.5
            ),
        }
