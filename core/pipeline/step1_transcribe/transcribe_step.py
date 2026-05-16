"""Step 1 — transcribe (UI + Whisper local/API)."""

import math
import os
import time
from pathlib import Path

# OpenAI whisper-1 limit is 25 MiB; leave margin for MP3 framing / multipart.
_WHISPER_API_MAX_BYTES = 26214400
_WHISPER_API_SAFE_BYTES = 25000000

from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from core.pipeline.base import BaseStep, CancelledError
from core.pipeline.step1_transcribe.constants import (
    LANGUAGES,
    SUPPORTED_AUDIO,
    SUPPORTED_VIDEO,
    WHISPER_MODELS,
)
from core.pipeline.step1_transcribe.format_utils import format_elapsed
from core.pipeline.step1_transcribe.models import Segment, TranscriptResult

class TranscribeStep(BaseStep):
    STEP_ID = "step1_transcribe"
    LABEL = "① Transcribe"
    COLOR = "#6c63ff"
    ENABLED_BY_DEFAULT = True

    def __init__(self):
        self._model = None
        self._model_size = None
        self._backend_combo = None
        self._model_combo = None
        self._lang_combo = None
        self._min_silence_spin = None
        self._api_key_lbl = None
        self._api_key_edit = None
        self._local_opts = None
        self._api_opts = None

    def request_cancel(self, event):
        self._cancel_event = event

    def run(self, session, config, log, cancel):
        backend = config.get("backend", "local")
        if backend == "api":
            return self._run_api(session, config, log, cancel)
        return self._run_local(session, config, log, cancel)

    @staticmethod
    def _ffprobe_duration(subprocess, path: str) -> float:
        r = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode != 0:
            return 0.0
        try:
            return max(0.0, float((r.stdout or "").strip()))
        except ValueError:
            return 0.0

    @staticmethod
    def _ffmpeg_extract_mp3_chunk(
        subprocess, src: str, dst: str, start_sec: float, dur_sec: float
    ) -> None:
        r = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{max(0.0, start_sec):.3f}",
                "-i",
                src,
                "-t",
                f"{max(0.1, dur_sec):.3f}",
                "-vn",
                "-acodec",
                "mp3",
                "-b:a",
                "64k",
                "-ar",
                "16000",
                "-ac",
                "1",
                dst,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg chunk extract failed:\n{r.stderr}")

    @staticmethod
    def _chunk_length_seconds(file_size: int, duration_sec: float) -> float:
        """Length of each API chunk so compressed size stays under the limit."""
        if duration_sec <= 0:
            return 300.0
        bps = file_size / duration_sec
        target = _WHISPER_API_SAFE_BYTES * 0.88
        sec = target / max(bps, 8.0)
        return max(45.0, min(sec, 4 * 3600.0))

    @staticmethod
    def _api_response_to_segments(response) -> tuple[list, str, str]:
        raw_segs = getattr(response, "segments", []) or []
        segs = []
        for s in raw_segs:
            start = round(
                float(s.get("start", 0) if isinstance(s, dict) else s.start), 2
            )
            end = round(float(s.get("end", 0) if isinstance(s, dict) else s.end), 2)
            text = (s.get("text", "") if isinstance(s, dict) else s.text).strip()
            if text:
                segs.append(Segment(start, end, text))
        full_text = getattr(
            response, "text", " ".join(x.text for x in segs)
        ).strip()
        lang = getattr(response, "language", None) or "unknown"
        return segs, full_text, lang

    def _transcribe_openai_file(
        self, client, path: str, language_code: str | None
    ):
        with open(path, "rb") as f:
            kwargs = {
                "model": "whisper-1",
                "file": f,
                "response_format": "verbose_json",
                "timestamp_granularities": ["segment"],
            }
            if language_code:
                kwargs["language"] = language_code.split("-")[0].lower()
            return client.audio.transcriptions.create(**kwargs)

    def _run_api_chunked(
        self,
        *,
        client,
        audio_path: str,
        language_cfg: str | None,
        file_size: int,
        duration_sec: float,
        log,
        cancel,
        subprocess,
        tempfile,
    ) -> tuple[list, str, str]:
        """Split long audio into time ranges; transcribe each; merge with time offset."""
        dur = float(duration_sec or 0.0)
        if dur <= 0 and file_size > 0:
            dur = file_size / 7500.0
        chunk_len = self._chunk_length_seconds(file_size, dur)
        n_est = max(1, int(math.ceil(dur / max(chunk_len, 1.0))))
        log(
            f"📎 Vượt giới hạn 25MB — transcribe ~{n_est} phần (~{chunk_len / 60:.0f} phút/phần)…"
        )

        all_segs: list = []
        text_parts: list[str] = []
        merged_lang: str | None = None
        lang_fixed = (language_cfg or "").strip() or None

        t0 = 0.0
        part = 0
        while t0 < dur - 0.01:
            if cancel.is_set():
                raise CancelledError()
            actual_dur = min(chunk_len, dur - t0)
            if actual_dur < 0.2:
                break
            part += 1
            tmp_c = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            tmp_c.close()
            try:
                self._ffmpeg_extract_mp3_chunk(
                    subprocess, audio_path, tmp_c.name, t0, actual_dur
                )
                sz = os.path.getsize(tmp_c.name)
                if sz > _WHISPER_API_MAX_BYTES:
                    raise RuntimeError(
                        f"Phần {part} vẫn {sz / 1024 / 1024:.2f} MB — thử giảm bitrate trong code."
                    )
                log(
                    f"   ▶ Phần {part} ({t0 / 60:.1f}–{(t0 + actual_dur) / 60:.1f} phút, "
                    f"{sz / 1024 / 1024:.2f} MB)…"
                )
                response = self._transcribe_openai_file(client, tmp_c.name, lang_fixed)
                segs, chunk_text, chunk_lang = self._api_response_to_segments(response)
                if merged_lang is None and chunk_lang:
                    merged_lang = chunk_lang
                for seg in segs:
                    all_segs.append(
                        Segment(
                            round(seg.start + t0, 2),
                            round(seg.end + t0, 2),
                            seg.text,
                        )
                    )
                if chunk_text:
                    text_parts.append(chunk_text)
            finally:
                if os.path.exists(tmp_c.name):
                    try:
                        os.unlink(tmp_c.name)
                    except OSError:
                        pass
            t0 += actual_dur

        full_text = " ".join(text_parts).strip()
        if not full_text and all_segs:
            full_text = " ".join(s.text for s in all_segs)
        lang_out = merged_lang or language_cfg or "unknown"
        return all_segs, full_text, lang_out

    # ── Whisper API ───────────────────────────────────────────────────────────

    def _run_api(self, session, config, log, cancel):
        import subprocess
        import tempfile

        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("Run: pip install openai")

        api_key = config.get("api_key") or ""
        if not api_key:
            # Fallback: read directly from ApiKeyManager (handles multi-session case
            # where widget field may be empty but key is saved in .subsync_keys)
            try:
                from core.api_keys import get_key

                api_key = get_key("openai") or os.environ.get("OPENAI_API_KEY", "")
            except Exception:
                api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "OpenAI API key required for Whisper API.\n"
                "Enter key in Step 1 config or set via API Keys Manager."
            )

        language = config.get("language")
        file_path = session.source_file
        p = Path(file_path)

        log(f"{'─'*38}")
        log(f"📄 File     : {p.name}")
        log(f"📦 Size     : {p.stat().st_size/1024/1024:.2f} MB")
        log("🔧 Backend  : OpenAI Whisper API")
        log(f"🌐 Language : {language or 'auto-detect'}")
        log(f"{'─'*38}")

        if cancel.is_set():
            raise CancelledError()

        # Extract audio if video (API accepts mp3/mp4/wav/m4a/webm up to 25MB)
        audio_path = file_path
        tmp_audio = None
        file_size_mb = p.stat().st_size / 1024 / 1024

        if p.suffix.lower() in SUPPORTED_VIDEO or file_size_mb > 24:
            log("🎬 Extracting/compressing audio for API (max 25MB)…")
            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            tmp.close()
            tmp_audio = tmp.name
            t1 = time.perf_counter()
            # Use 64k bitrate to stay well under 25MB limit
            r = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    file_path,
                    "-vn",
                    "-acodec",
                    "mp3",
                    "-b:a",
                    "64k",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    tmp_audio,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg error:\n{r.stderr}")
            log(f"✅ Audio extracted (+{format_elapsed(time.perf_counter()-t1)})")
            audio_path = tmp_audio

        if cancel.is_set():
            if tmp_audio and os.path.exists(tmp_audio):
                os.unlink(tmp_audio)
            raise CancelledError()

        audio_sz = os.path.getsize(audio_path)
        duration_sec = self._ffprobe_duration(subprocess, audio_path)
        if duration_sec <= 0 and audio_sz > 0:
            # ffprobe failed — approximate length from ~64k MP3 equivalent (~8000 B/s).
            duration_sec = audio_sz / 7500.0

        client = OpenAI(api_key=api_key)
        log("🎧 Calling Whisper API…")
        t1 = time.perf_counter()

        try:
            if audio_sz <= _WHISPER_API_SAFE_BYTES:
                response = self._transcribe_openai_file(client, audio_path, language)
                segs, full_text, lang = self._api_response_to_segments(response)
                lang = (
                    getattr(response, "language", None)
                    or (language if language else None)
                    or lang
                    or "unknown"
                )
            else:
                segs, full_text, lang = self._run_api_chunked(
                    client=client,
                    audio_path=audio_path,
                    language_cfg=language,
                    file_size=audio_sz,
                    duration_sec=duration_sec,
                    log=log,
                    cancel=cancel,
                    subprocess=subprocess,
                    tempfile=tempfile,
                )
        finally:
            if tmp_audio and os.path.exists(tmp_audio):
                os.unlink(tmp_audio)

        log(f"✅ Transcription done (+{format_elapsed(time.perf_counter()-t1)})")

        if len(segs) > 1:
            gaps = [segs[i + 1].start - segs[i].end for i in range(len(segs) - 1)]
            significant = [g for g in gaps if g > 0.5]
            log(
                f"🌐 Detected: {lang}  |  {len(segs)} segs  |  "
                f"{format_elapsed(segs[-1].end if segs else 0)}"
            )
            if significant:
                log(
                    f"   Gaps: {len(significant)} pauses >0.5s (longest: {max(gaps):.1f}s)"
                )
        else:
            log(f"🌐 Detected: {lang}  |  {len(segs)} segs")

        result = TranscriptResult(full_text, segs, lang, file_path)
        session.save_transcript(result)
        return result

    # ── Local Whisper ─────────────────────────────────────────────────────────

    def _run_local(self, session, config, log, cancel):
        import json
        import subprocess
        import sys
        import tempfile

        file_path = session.source_file
        model_size = config["model_size"]
        language = config.get("language")
        min_silence = config.get("min_silence", 0.5)
        p = Path(file_path)

        log(f"{'─'*38}")
        log(f"📄 File     : {p.name}")
        log(f"📦 Size     : {p.stat().st_size/1024/1024:.2f} MB")
        log(f"🔧 Model    : {model_size}")
        log(f"🌐 Language : {language or 'auto-detect'}")
        log(f"{'─'*38}")

        if cancel.is_set():
            raise CancelledError()

        audio_path, tmp = file_path, None
        if Path(file_path).suffix.lower() in SUPPORTED_VIDEO:
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
            log(f"✅ Audio extracted (+{format_elapsed(time.perf_counter()-t1)})")
            audio_path = tmp

        if cancel.is_set():
            raise CancelledError()

        if language:
            language = language.split("-")[0].lower()
        lang_arg = repr(language) if language else "None"

        runner_code = (
            f"import whisper, json, sys\n"
            f"model = whisper.load_model({repr(model_size)})\n"
            f"r = model.transcribe(\n"
            f"    {repr(audio_path)},\n"
            f"    language={lang_arg},\n"
            f"    word_timestamps=False,\n"
            f"    condition_on_previous_text=True,\n"
            f"    no_speech_threshold=0.6,\n"
            f"    logprob_threshold=-1.0,\n"
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
        log(f"✅ Transcription done (+{format_elapsed(time.perf_counter()-t1)})")

        raw = json.loads(r.stdout)
        lang = raw.get("language", "unknown")
        raw_segs = raw.get("segments", [])
        segs = []

        for s in raw_segs:
            start = round(s["start"], 2)
            end = round(s["end"], 2)
            text = s["text"].strip()
            if not text:
                continue
            segs.append(Segment(start, end, text))

        if len(segs) > 1:
            gaps = [segs[i + 1].start - segs[i].end for i in range(len(segs) - 1)]
            significant_gaps = [g for g in gaps if g > 0.5]
            log(
                f"🌐 Detected: {lang}  |  {len(segs)} segs  |  "
                f"{format_elapsed(segs[-1].end if segs else 0)}"
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

    # ── Config widget ─────────────────────────────────────────────────────────

    def _on_api_key_changed(self, text: str):
        """Sync key typed directly into Step 1 field back to ApiKeyManager."""
        try:
            from core.api_keys import get_manager

            mgr = get_manager()
            mgr.set("OPENAI_API_KEY", text.strip())
        except Exception:
            pass

    def build_config_widget(self, parent=None):
        w = QWidget(parent)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        # Backend selector
        r0 = QHBoxLayout()
        r0.addWidget(QLabel("Backend:"))
        self._backend_combo = QComboBox()
        self._backend_combo.addItems(
            [
                "OpenAI Whisper API (recommended ⭐)",
                "Local Whisper (free, needs RAM)",
            ]
        )
        self._backend_combo.setCurrentIndex(0)
        self._backend_combo.currentIndexChanged.connect(self._on_backend_changed)
        r0.addWidget(self._backend_combo)
        r0.addStretch()
        v.addLayout(r0)

        # ── API options ──
        self._api_opts = QWidget()
        av = QVBoxLayout(self._api_opts)
        av.setContentsMargins(0, 0, 0, 0)
        av.setSpacing(4)

        self._api_key_lbl = QLabel("OpenAI API Key:")
        self._api_key_edit = QLineEdit()
        self._api_key_edit.setPlaceholderText("sk-... (platform.openai.com)")
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.textChanged.connect(self._on_api_key_changed)
        av.addWidget(self._api_key_lbl)
        av.addWidget(self._api_key_edit)

        api_hint = QLabel(
            "~$0.006/min · no local GPU needed · max 25MB per request "
            "(video dài tự chia chunk)"
        )
        api_hint.setStyleSheet("color:#555;font-size:10px;")
        av.addWidget(api_hint)
        v.addWidget(self._api_opts)

        # ── Local options ──
        self._local_opts = QWidget()
        lv = QVBoxLayout(self._local_opts)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(4)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Model:"))
        self._model_combo = QComboBox()
        self._model_combo.addItems(WHISPER_MODELS)
        self._model_combo.setCurrentText("base")
        self._model_combo.setToolTip(
            "tiny   — fastest, good for clear audio\n"
            "base   — balanced (default)\n"
            "small  — better accuracy\n"
            "medium — high accuracy\n"
            "large  — best, needs 10GB RAM"
        )
        r1.addWidget(self._model_combo)
        r1.addStretch()
        lv.addLayout(r1)
        v.addWidget(self._local_opts)
        self._local_opts.setVisible(False)

        # Language (shared)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Source lang:"))
        self._lang_combo = QComboBox()
        self._lang_combo.addItems(LANGUAGES.keys())
        r2.addWidget(self._lang_combo)
        r2.addStretch()
        v.addLayout(r2)

        # Min silence (local only — not used by API but harmless to show)
        r3 = QHBoxLayout()
        r3.addWidget(QLabel("Min silence gap:"))
        self._min_silence_spin = QDoubleSpinBox()
        self._min_silence_spin.setRange(0.1, 3.0)
        self._min_silence_spin.setSingleStep(0.1)
        self._min_silence_spin.setValue(0.5)
        self._min_silence_spin.setFixedWidth(65)
        self._min_silence_spin.setFixedHeight(26)
        r3.addWidget(self._min_silence_spin)
        r3.addWidget(QLabel("s"))
        r3.addStretch()
        v.addLayout(r3)

        self._on_backend_changed(0)
        return w

    def _on_backend_changed(self, idx: int):
        is_api = idx == 0
        if self._api_opts:
            self._api_opts.setVisible(is_api)
        if self._local_opts:
            self._local_opts.setVisible(not is_api)

    def apply_config(self, config: dict) -> None:
        if not config:
            return
        _LANG_BY_CODE = {v: k for k, v in LANGUAGES.items() if v}
        if self._backend_combo:
            self._backend_combo.setCurrentIndex(
                0 if config.get("backend") == "api" else 1
            )
        if self._model_combo and config.get("model_size"):
            self._model_combo.setCurrentText(config["model_size"])
        if self._lang_combo:
            label = _LANG_BY_CODE.get(config.get("language"), "Auto detect")
            self._lang_combo.setCurrentText(label)
        if self._min_silence_spin and config.get("min_silence") is not None:
            self._min_silence_spin.setValue(float(config["min_silence"]))

    def collect_config(self):
        idx = self._backend_combo.currentIndex() if self._backend_combo else 0
        backend = "api" if idx == 0 else "local"
        return {
            "backend": backend,
            "model_size": (
                self._model_combo.currentText() if self._model_combo else "base"
            ),
            "language": LANGUAGES.get(
                self._lang_combo.currentText() if self._lang_combo else "Auto detect"
            ),
            "min_silence": (
                self._min_silence_spin.value() if self._min_silence_spin else 0.5
            ),
            "api_key": (
                self._api_key_edit.text().strip() if self._api_key_edit else None
            )
            or None,
        }
