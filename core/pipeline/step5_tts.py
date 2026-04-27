"""
Step 5 — Text-to-Speech: generate Vietnamese (or any) voiceover and mix into video.

Backends:
  gtts     — Google TTS, free, no API key, decent quality
  coqui    — Coqui TTS, free, runs local, better quality
  elevenlabs — Best quality + emotion, 10k chars/month free

Output:
  step5_tts.mp3        — raw TTS audio (full)
  step5_output.<ext>   — video with TTS mixed in
"""

import os
import subprocess
import tempfile
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.pipeline.base import BaseStep, CancelledError

TTS_BACKENDS = {
    "gTTS (Google, free)": "gtts",
    "Coqui TTS (local, free)": "coqui",
    "ElevenLabs (best quality)": "elevenlabs",
}

GTTS_LANGS = {
    "Vietnamese": "vi",
    "English": "en",
    "Japanese": "ja",
    "Korean": "ko",
    "Chinese": "zh-CN",
    "French": "fr",
    "German": "de",
    "Spanish": "es",
}


class TTSStep(BaseStep):
    STEP_ID = "step5_tts"
    LABEL = "⑤ Add Voice (TTS)"
    COLOR = "#5a1a6a"
    ENABLED_BY_DEFAULT = False

    def __init__(self):
        self._backend_combo = None
        self._lang_combo = None
        self._api_lbl = self._api_edit = None
        self._voice_lbl = self._voice_edit = None
        self._vol_slider = None
        self._mix_bgm_chk = None

    def run(self, session, config, log, cancel):
        backend = config["backend"]
        lang = config["lang"]
        api_key = config.get("api_key")
        voice_id = config.get("voice_id", "")
        bgm_vol = config.get("bgm_volume", 0.3)  # 0.0–1.0
        mix_bgm = config.get("mix_bgm", True)

        # Load translated segments
        segments = session.load_translated()
        total_duration = segments[-1].end if segments else 0

        log(f"🗣️  Generating TTS ({backend}) for {len(segments)} segments…")

        if cancel.is_set():
            raise CancelledError()

        # Generate full TTS audio
        tts_path = str(session.step5_tts)
        self._generate_tts(
            segments, lang, backend, api_key, voice_id, tts_path, log, cancel
        )

        if cancel.is_set():
            raise CancelledError()

        # Use latest processed video as base (chains with Step 3 if already run)
        input_video = session.latest_video()
        if input_video == str(session.step3_video):
            log("🔗 Chaining: using Step 3 (subtitled) video as base")
        elif input_video != session.source_file:
            log("🔗 Chaining: using existing processed video as base")

        # Mix TTS into video
        out_video = str(session.step5_video)
        self._mix_into_video(
            input_video,
            tts_path,
            out_video,
            mix_bgm,
            bgm_vol,
            step4_bg=str(session.step4_background) if session.step4_done else None,
            log=log,
        )

        return out_video

    # ── TTS generation ────────────────────────────────────────────────────────

    def _generate_tts(
        self, segments, lang, backend, api_key, voice_id, out_path, log, cancel
    ):
        """Generate one audio file with per-segment timing using silence padding."""

        if backend == "gtts":
            self._gtts_segments(segments, lang, out_path, log, cancel)
        elif backend == "coqui":
            self._coqui_segments(segments, lang, out_path, log, cancel)
        elif backend == "elevenlabs":
            self._elevenlabs_segments(
                segments, lang, api_key, voice_id, out_path, log, cancel
            )
        else:
            raise RuntimeError(f"Unknown TTS backend: {backend}")

    def _gtts_segments(self, segments, lang, out_path, log, cancel):
        try:
            from gtts import gTTS
        except ImportError:
            raise RuntimeError("Run: pip install gtts")
        from pydub import AudioSegment

        log("🔊 Generating gTTS segments…")
        result = AudioSegment.silent(duration=0)
        cursor_ms = 0

        for i, seg in enumerate(segments, 1):
            if cancel.is_set():
                raise CancelledError()
            start_ms = int(seg.start * 1000)
            # Add silence if segment starts later than cursor
            if start_ms > cursor_ms:
                result += AudioSegment.silent(duration=start_ms - cursor_ms)
                cursor_ms = start_ms

            # Generate TTS for this segment
            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            tmp.close()
            try:
                tts = gTTS(text=seg.translated, lang=lang[:2], slow=False)
                tts.save(tmp.name)
                audio = AudioSegment.from_mp3(tmp.name)
                result += audio
                cursor_ms += len(audio)
            finally:
                os.unlink(tmp.name)

            if i % 10 == 0 or i == len(segments):
                log(f"   [{i}/{len(segments)}] segments done")

        result.export(out_path, format="mp3")
        log(f"✅ TTS audio → {Path(out_path).name}")

    def _coqui_segments(self, segments, lang, out_path, log, cancel):
        try:
            from TTS.api import TTS as CoquiTTS
        except ImportError:
            raise RuntimeError(
                "Run: pip install TTS\n"
                "(Coqui TTS — may take a few minutes to install)"
            )
        from pydub import AudioSegment

        log("🔊 Loading Coqui TTS model (first run downloads ~1GB)…")
        tts = CoquiTTS("tts_models/multilingual/multi-dataset/xtts_v2")

        result = AudioSegment.silent(duration=0)
        cursor_ms = 0

        for i, seg in enumerate(segments, 1):
            if cancel.is_set():
                raise CancelledError()
            start_ms = int(seg.start * 1000)
            if start_ms > cursor_ms:
                result += AudioSegment.silent(duration=start_ms - cursor_ms)
                cursor_ms = start_ms

            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp.close()
            try:
                tts.tts_to_file(
                    text=seg.translated, language=lang[:2], file_path=tmp.name
                )
                audio = AudioSegment.from_wav(tmp.name)
                result += audio
                cursor_ms += len(audio)
            finally:
                os.unlink(tmp.name)

            if i % 5 == 0 or i == len(segments):
                log(f"   [{i}/{len(segments)}] segments done")

        result.export(out_path, format="mp3")
        log(f"✅ TTS audio → {Path(out_path).name}")

    def _elevenlabs_segments(
        self, segments, lang, api_key, voice_id, out_path, log, cancel
    ):
        try:
            from elevenlabs import VoiceSettings
            from elevenlabs.client import ElevenLabs
        except ImportError:
            raise RuntimeError("Run: pip install elevenlabs")
        from pydub import AudioSegment

        key = api_key or os.environ.get("ELEVENLABS_API_KEY", "")
        if not key:
            raise RuntimeError("Set ELEVENLABS_API_KEY env var or enter key in UI.")

        client = ElevenLabs(api_key=key)
        vid = voice_id or "EXAVITQu4vr4xnSDxMaL"  # default: Bella (emotional)
        log(f"🔊 Generating ElevenLabs TTS (voice: {vid})…")

        result = AudioSegment.silent(duration=0)
        cursor_ms = 0

        for i, seg in enumerate(segments, 1):
            if cancel.is_set():
                raise CancelledError()
            start_ms = int(seg.start * 1000)
            if start_ms > cursor_ms:
                result += AudioSegment.silent(duration=start_ms - cursor_ms)
                cursor_ms = start_ms

            audio_bytes = b"".join(
                client.text_to_speech.convert(
                    voice_id=vid,
                    text=seg.translated,
                    model_id="eleven_multilingual_v2",
                    voice_settings=VoiceSettings(
                        stability=0.4,
                        similarity_boost=0.75,
                        style=0.5,
                        use_speaker_boost=True,
                    ),
                )
            )
            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            tmp.write(audio_bytes)
            tmp.close()
            try:
                audio = AudioSegment.from_mp3(tmp.name)
                result += audio
                cursor_ms += len(audio)
            finally:
                os.unlink(tmp.name)

            if i % 5 == 0 or i == len(segments):
                log(f"   [{i}/{len(segments)}] segments done")

        result.export(out_path, format="mp3")
        log(f"✅ TTS audio → {Path(out_path).name}")

    # ── Mix into video ────────────────────────────────────────────────────────

    def _mix_into_video(
        self, video_path, tts_path, out_path, mix_bgm, bgm_vol, step4_bg, log
    ):
        """Replace or mix original audio with TTS + optional background."""
        log("🎬 Mixing audio into video…")

        if mix_bgm and step4_bg and Path(step4_bg).exists():
            # Mix TTS + background music, then mux with video
            mixed = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            mixed.close()
            cmd_mix = [
                "ffmpeg",
                "-y",
                "-i",
                tts_path,
                "-i",
                step4_bg,
                "-filter_complex",
                f"[0:a]volume=1.0[tts];[1:a]volume={bgm_vol}[bgm];"
                f"[tts][bgm]amix=inputs=2:duration=first[out]",
                "-map",
                "[out]",
                mixed.name,
            ]
            subprocess.run(cmd_mix, capture_output=True, check=True)
            audio_source = mixed.name
        else:
            audio_source = tts_path
            mixed = None

        # Mux mixed audio into video (replace original audio)
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            video_path,
            "-i",
            audio_source,
            "-c:v",
            "copy",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-shortest",
            out_path,
        ]
        log(f"   $ {' '.join(cmd)}")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg mix failed:\n{r.stderr[-1500:]}")

        if mixed:
            os.unlink(mixed.name)

        log(f"✅ Video with TTS → {Path(out_path).name}")

    # ── Config widget ─────────────────────────────────────────────────────────

    def build_config_widget(self, parent=None):
        w = QWidget(parent)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Backend:"))
        self._backend_combo = QComboBox()
        self._backend_combo.addItems(TTS_BACKENDS.keys())
        self._backend_combo.currentIndexChanged.connect(self._on_backend_changed)
        r1.addWidget(self._backend_combo)
        r1.addStretch()
        v.addLayout(r1)

        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Language:"))
        self._lang_combo = QComboBox()
        self._lang_combo.addItems(GTTS_LANGS.keys())
        self._lang_combo.setCurrentText("Vietnamese")
        r2.addWidget(self._lang_combo)
        r2.addStretch()
        v.addLayout(r2)

        self._api_lbl = QLabel("API Key:")
        self._api_edit = QLineEdit()
        self._api_edit.setPlaceholderText("ElevenLabs key (or ELEVENLABS_API_KEY env)")
        self._api_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_lbl.setVisible(False)
        self._api_edit.setVisible(False)
        v.addWidget(self._api_lbl)
        v.addWidget(self._api_edit)

        self._voice_lbl = QLabel("Voice ID:")
        self._voice_edit = QLineEdit()
        self._voice_edit.setPlaceholderText(
            "ElevenLabs voice ID (leave blank for default)"
        )
        self._voice_lbl.setVisible(False)
        self._voice_edit.setVisible(False)
        v.addWidget(self._voice_lbl)
        v.addWidget(self._voice_edit)

        # BGM mix
        self._mix_bgm_chk = QCheckBox("Mix background music (from Step 4)")
        self._mix_bgm_chk.setChecked(True)
        v.addWidget(self._mix_bgm_chk)

        r3 = QHBoxLayout()
        r3.addWidget(QLabel("BGM volume:"))
        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(30)
        r3.addWidget(self._vol_slider)
        self._vol_lbl = QLabel("30%")
        self._vol_slider.valueChanged.connect(lambda v: self._vol_lbl.setText(f"{v}%"))
        r3.addWidget(self._vol_lbl)
        v.addLayout(r3)
        return w

    def _on_backend_changed(self, idx):
        is_eleven = idx == 2
        self._api_lbl.setVisible(is_eleven)
        self._api_edit.setVisible(is_eleven)
        self._voice_lbl.setVisible(is_eleven)
        self._voice_edit.setVisible(is_eleven)

    def collect_config(self):
        key = (
            self._backend_combo.currentText()
            if self._backend_combo
            else list(TTS_BACKENDS.keys())[0]
        )
        return {
            "backend": TTS_BACKENDS.get(key, "gtts"),
            "lang": GTTS_LANGS.get(self._lang_combo.currentText(), "vi"),
            "api_key": self._api_edit.text().strip() or None,
            "voice_id": self._voice_edit.text().strip() or None,
            "mix_bgm": self._mix_bgm_chk.isChecked(),
            "bgm_volume": self._vol_slider.value() / 100.0,
        }
