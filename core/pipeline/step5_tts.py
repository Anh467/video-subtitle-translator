"""
Step 5 — TTS + Audio Mix

Pipeline:
  1. Tạo TTS tiếng Việt theo từng segment (có silence padding để sync timestamp)
  2. Mix: TTS + background music (từ Step 4 nếu có)
  3. Ghép audio mới vào video (thay thế audio gốc)

Backends:
  gtts       — Google TTS, free, không cần key
  elevenlabs — Chất lượng tốt nhất, có cảm xúc, 10k chars/tháng free
  openai_tts — OpenAI TTS, rẻ, tự nhiên

Mix modes:
  replace    — Chỉ TTS, bỏ hết audio gốc
  bgm_only   — TTS + background music (vocals đã tách ở Step 4)
  full_mix   — TTS + background + giọng gốc (volume thấp)
"""

import os
import subprocess
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.pipeline.base import BaseStep, CancelledError

TTS_BACKENDS = {
    "FPT AI TTS (free ⭐ VI)": "fpt",
    "Zalo AI TTS (free VI)": "zalo",
    "gTTS (Google, free)": "gtts",
    "OpenAI TTS (natural)": "openai_tts",
    "ElevenLabs (best+emotion)": "elevenlabs",
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

MIX_MODES = {
    "TTS only (replace original)": "replace",
    "TTS + Background music (Step 4)": "bgm_only",
    "TTS + BGM + Original voice (low vol)": "full_mix",
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
        self._mix_group = None
        self._tts_vol_slider = None
        self._bgm_vol_slider = None
        self._orig_vol_slider = None
        self._speed_spin = None

    # ── Run ───────────────────────────────────────────────────────────────────

    def run(self, session, config, log, cancel):
        backend = config["backend"]
        lang = config["lang"]
        api_key = config.get("api_key")
        voice_id = config.get("voice_id", "")
        mix_mode = config.get("mix_mode", "bgm_only")
        tts_vol = config.get("tts_vol", 1.0)
        bgm_vol = config.get("bgm_vol", 0.3)
        orig_vol = config.get("orig_vol", 0.1)

        segments = session.load_translated()
        if not segments:
            raise RuntimeError("No translated segments — run Step 2 first.")

        log(f"🗣️  Backend: {backend} | Lang: {lang} | Mix: {mix_mode}")
        log(
            f"   TTS vol: {tts_vol:.0%} | BGM vol: {bgm_vol:.0%} | Orig vol: {orig_vol:.0%}"
        )

        if cancel.is_set():
            raise CancelledError()

        # ── 1. Generate TTS audio ──────────────────────────────────────────
        tts_path = str(session.step5_tts)
        log("🎙️  Generating TTS audio…")
        self._generate_tts(
            segments, lang, backend, api_key, voice_id, tts_path, log, cancel
        )
        log(f"✅ TTS audio → {Path(tts_path).name}")

        if cancel.is_set():
            raise CancelledError()

        # ── 2. Mix audio tracks ────────────────────────────────────────────
        mixed_audio = self._mix_audio(
            session=session,
            tts_path=tts_path,
            mix_mode=mix_mode,
            tts_vol=tts_vol,
            bgm_vol=bgm_vol,
            orig_vol=orig_vol,
            log=log,
        )

        if cancel.is_set():
            raise CancelledError()

        # ── 3. Mux mixed audio into video ──────────────────────────────────
        input_video = session.latest_video()
        out_video = str(session.step5_video)

        if input_video == str(session.step3_video):
            log("🔗 Chaining: using Step 3 (subtitled) video as base")

        log("🎬 Muxing audio into video…")
        self._mux(input_video, mixed_audio, out_video, log)

        # Cleanup temp mixed audio if it's a temp file
        if mixed_audio != tts_path and os.path.exists(mixed_audio):
            os.unlink(mixed_audio)

        log(f"✅ Final video → {Path(out_video).name}")
        return out_video

    # ── Audio mixing ──────────────────────────────────────────────────────────

    def _mix_audio(self, session, tts_path, mix_mode, tts_vol, bgm_vol, orig_vol, log):
        """
        Mix TTS + optional tracks using ffmpeg filter_complex.
        Returns path to mixed audio file.
        """
        has_bgm = session.step4_done and Path(session.step4_background).exists()
        has_orig = Path(session.source_file).exists()

        if mix_mode == "replace":
            # Just apply volume to TTS, no mixing needed
            if tts_vol == 1.0:
                return tts_path
            log(f"🔊 Applying TTS volume ({tts_vol:.0%})…")
            return self._apply_volume(tts_path, tts_vol)

        elif mix_mode == "bgm_only":
            if not has_bgm:
                log("⚠️  No background music (Step 4 not run) — using TTS only")
                return tts_path
            log("🎵 Mixing TTS + background music…")
            log(f"   TTS: {tts_vol:.0%}  |  BGM: {bgm_vol:.0%}")
            return self._ffmpeg_mix(
                [
                    (tts_path, tts_vol),
                    (str(session.step4_background), bgm_vol),
                ],
                log,
            )

        elif mix_mode == "full_mix":
            tracks = [(tts_path, tts_vol)]
            if has_bgm:
                tracks.append((str(session.step4_background), bgm_vol))
                log("🎵 Mixing TTS + BGM + original voice")
            else:
                log("🎵 Mixing TTS + original voice (no BGM — run Step 4 first)")

            # Extract original audio
            orig_audio = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
            r = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    session.source_file,
                    "-vn",
                    "-c:a",
                    "mp3",
                    "-b:a",
                    "128k",
                    orig_audio,
                ],
                capture_output=True,
                text=True,
            )
            if r.returncode == 0:
                tracks.append((orig_audio, orig_vol))
                log(
                    f"   TTS: {tts_vol:.0%}  |  BGM: {bgm_vol:.0%}  |  Orig: {orig_vol:.0%}"
                )
            else:
                log("⚠️  Could not extract original audio")

            result = self._ffmpeg_mix(tracks, log)

            # Cleanup temp orig audio
            if os.path.exists(orig_audio):
                os.unlink(orig_audio)
            return result

        return tts_path

    def _ffmpeg_mix(self, tracks, log):
        """Mix multiple audio tracks with individual volumes using ffmpeg."""
        out = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        out.close()

        # Build filter_complex: each input gets volume, then amix
        inputs = []
        filter_parts = []
        for i, (path, vol) in enumerate(tracks):
            inputs += ["-i", path]
            filter_parts.append(f"[{i}:a]volume={vol:.3f}[a{i}]")

        mix_inputs = "".join(f"[a{i}]" for i in range(len(tracks)))
        filter_parts.append(
            f"{mix_inputs}amix=inputs={len(tracks)}:duration=first:dropout_transition=2[out]"
        )

        cmd = (
            ["ffmpeg", "-y"]
            + inputs
            + [
                "-filter_complex",
                ";".join(filter_parts),
                "-map",
                "[out]",
                "-c:a",
                "mp3",
                "-b:a",
                "192k",
                out.name,
            ]
        )

        log(f"   Mixing {len(tracks)} audio tracks…")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg mix failed:\n{r.stderr[-1500:]}")
        return out.name

    def _apply_volume(self, audio_path, volume):
        """Apply volume to a single audio file."""
        out = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        out.close()
        r = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                audio_path,
                "-af",
                f"volume={volume:.3f}",
                "-c:a",
                "mp3",
                out.name,
            ],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            return audio_path  # fallback
        return out.name

    def _mux(self, video_path, audio_path, out_path, log):
        """Replace video's audio track with mixed audio."""
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            video_path,
            "-i",
            audio_path,
            "-c:v",
            "copy",  # no video re-encode
            "-map",
            "0:v:0",  # video from first input
            "-map",
            "1:a:0",  # audio from second input
            "-shortest",
            out_path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg mux failed:\n{r.stderr[-1500:]}")

    # ── TTS generation ────────────────────────────────────────────────────────

    def _generate_tts(
        self, segments, lang, backend, api_key, voice_id, out_path, log, cancel
    ):
        if backend == "fpt":
            self._fpt(segments, api_key, voice_id, out_path, log, cancel)
        elif backend == "zalo":
            self._zalo(segments, api_key, voice_id, out_path, log, cancel)
        elif backend == "gtts":
            self._gtts(segments, lang, out_path, log, cancel)
        elif backend == "openai_tts":
            self._openai_tts(segments, lang, api_key, out_path, log, cancel)
        elif backend == "elevenlabs":
            self._elevenlabs(segments, lang, api_key, voice_id, out_path, log, cancel)
        else:
            raise RuntimeError(f"Unknown TTS backend: {backend}")

    # ── FPT AI TTS ────────────────────────────────────────────────────────────

    def _fpt(self, segments, api_key, voice_id, out_path, log, cancel):
        """
        FPT AI TTS — tốt nhất cho tiếng Việt, 1M ký tự free.
        Lấy key tại: fpt.ai/tts
        Voice IDs: banmai, leminh, lannhi, minhquang, giahuy, linhsan
        """
        import requests
        from pydub import AudioSegment

        key = api_key or os.environ.get("FPT_API_KEY", "")
        if not key:
            raise RuntimeError(
                "FPT AI API key required.\n"
                "Get FREE key at: fpt.ai/tts → Đăng ký\n"
                "Set FPT_API_KEY env var or enter in UI."
            )

        voice = voice_id or "banmai"  # default: giọng nữ miền Nam
        log(f"🎙️  FPT AI TTS | voice: {voice}")

        result = AudioSegment.silent(duration=0)
        cursor_ms = 0
        total = len(segments)

        for i, seg in enumerate(segments, 1):
            if cancel.is_set():
                raise CancelledError()

            start_ms = int(seg.start * 1000)
            if start_ms > cursor_ms:
                result += AudioSegment.silent(duration=start_ms - cursor_ms)
                cursor_ms = start_ms

            txt = seg.translated.strip()
            if not txt:
                continue

            try:
                resp = requests.post(
                    "https://api.fpt.ai/hmi/tts/v5",
                    headers={
                        "api-key": key,
                        "voice": voice,
                        "Cache-Control": "no-cache",
                        "Content-Type": "application/json",
                    },
                    json={"text": txt},
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()

                # FPT returns async URL — need to poll
                async_url = data.get("async", "")
                if not async_url:
                    raise RuntimeError(f"FPT: no async URL in response: {data}")

                # Poll for audio (FPT processes async, usually ready in 1-3s)
                audio_url = self._fpt_poll(async_url, log)
                audio_resp = requests.get(audio_url, timeout=30)
                audio_resp.raise_for_status()

                tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
                tmp.write(audio_resp.content)
                tmp.close()
                try:
                    audio = AudioSegment.from_mp3(tmp.name)
                    result += audio
                    cursor_ms += len(audio)
                finally:
                    os.unlink(tmp.name)

            except Exception as e:
                log(f"   ⚠️  Seg {i} failed: {e}")

            if i % 5 == 0 or i == total:
                log(f"   [{i}/{total}] TTS generated")
            time.sleep(0.2)  # rate limit

        result.export(out_path, format="mp3")

    def _fpt_poll(self, url, log, max_wait=30):
        """Poll FPT async URL until audio is ready."""
        import requests

        for _ in range(max_wait):
            try:
                r = requests.head(url, timeout=5)
                if r.status_code == 200:
                    return url
            except Exception:
                pass
            time.sleep(1)
        # Try GET anyway
        return url

    # ── Zalo AI TTS ───────────────────────────────────────────────────────────

    def _zalo(self, segments, api_key, voice_id, out_path, log, cancel):
        """
        Zalo AI TTS — giọng Việt native, mượt.
        Lấy key tại: zalo.ai → Developers → TTS API
        Voice codes: 1 (nữ miền Nam), 2 (nam miền Nam),
                     3 (nữ miền Bắc), 4 (nam miền Bắc)
        """
        import requests
        from pydub import AudioSegment

        key = api_key or os.environ.get("ZALO_API_KEY", "")
        if not key:
            raise RuntimeError(
                "Zalo AI API key required.\n"
                "Get key at: zalo.ai → Developers\n"
                "Set ZALO_API_KEY env var or enter in UI."
            )

        voice_code = voice_id or "1"  # default: nữ miền Nam
        log(f"🎙️  Zalo AI TTS | voice: {voice_code}")

        result = AudioSegment.silent(duration=0)
        cursor_ms = 0
        total = len(segments)

        for i, seg in enumerate(segments, 1):
            if cancel.is_set():
                raise CancelledError()

            start_ms = int(seg.start * 1000)
            if start_ms > cursor_ms:
                result += AudioSegment.silent(duration=start_ms - cursor_ms)
                cursor_ms = start_ms

            txt = seg.translated.strip()
            if not txt:
                continue

            try:
                resp = requests.post(
                    "https://api.zalo.ai/v1/tts/synthesize",
                    headers={
                        "apikey": key,
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    data={
                        "input": txt,
                        "voice_id": str(voice_code),
                        "speed": "1.0",
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()

                audio_url = data.get("data", {}).get("url", "")
                if not audio_url:
                    raise RuntimeError(f"Zalo: no URL in response: {data}")

                audio_resp = requests.get(audio_url, timeout=30)
                audio_resp.raise_for_status()

                tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
                tmp.write(audio_resp.content)
                tmp.close()
                try:
                    audio = AudioSegment.from_mp3(tmp.name)
                    result += audio
                    cursor_ms += len(audio)
                finally:
                    os.unlink(tmp.name)

            except Exception as e:
                log(f"   ⚠️  Seg {i} failed: {e}")

            if i % 5 == 0 or i == total:
                log(f"   [{i}/{total}] TTS generated")
            time.sleep(0.2)

        result.export(out_path, format="mp3")

    def _gtts(self, segments, lang, out_path, log, cancel):
        try:
            from gtts import gTTS
            from pydub import AudioSegment
        except ImportError:
            raise RuntimeError("Run: pip install gtts pydub audioop-lts")

        result = AudioSegment.silent(duration=0)
        cursor_ms = 0

        for i, seg in enumerate(segments, 1):
            if cancel.is_set():
                raise CancelledError()
            start_ms = int(seg.start * 1000)
            if start_ms > cursor_ms:
                result += AudioSegment.silent(duration=start_ms - cursor_ms)
                cursor_ms = start_ms

            txt = seg.translated.strip()
            if not txt:
                continue

            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            tmp.close()
            try:
                gTTS(text=txt, lang=lang[:2], slow=False).save(tmp.name)
                audio = AudioSegment.from_mp3(tmp.name)
                result += audio
                cursor_ms += len(audio)
            except Exception as e:
                log(f"   ⚠️  Seg {i} TTS failed: {e}")
            finally:
                if os.path.exists(tmp.name):
                    os.unlink(tmp.name)

            if i % 10 == 0 or i == len(segments):
                log(f"   [{i}/{len(segments)}] TTS generated")

        result.export(out_path, format="mp3")

    def _openai_tts(self, segments, lang, api_key, out_path, log, cancel):
        try:
            from openai import OpenAI
            from pydub import AudioSegment
        except ImportError:
            raise RuntimeError("Run: pip install openai pydub audioop-lts")

        key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError("Set OPENAI_API_KEY env var.")

        # Map lang to OpenAI voice with best match
        VOICE_MAP = {
            "vi": "nova",  # nova sounds most natural for Vietnamese
            "en": "alloy",
            "ja": "shimmer",
            "ko": "nova",
            "zh": "shimmer",
        }
        voice = VOICE_MAP.get(lang[:2], "nova")
        client = OpenAI(api_key=key)
        result = AudioSegment.silent(duration=0)
        cursor_ms = 0

        log(f"   OpenAI TTS voice: {voice}")

        for i, seg in enumerate(segments, 1):
            if cancel.is_set():
                raise CancelledError()
            start_ms = int(seg.start * 1000)
            if start_ms > cursor_ms:
                result += AudioSegment.silent(duration=start_ms - cursor_ms)
                cursor_ms = start_ms

            txt = seg.translated.strip()
            if not txt:
                continue

            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            tmp.close()
            try:
                resp = client.audio.speech.create(
                    model="tts-1",
                    voice=voice,
                    input=txt,
                    response_format="mp3",
                )
                resp.stream_to_file(tmp.name)
                audio = AudioSegment.from_mp3(tmp.name)
                result += audio
                cursor_ms += len(audio)
            except Exception as e:
                log(f"   ⚠️  Seg {i} TTS failed: {e}")
            finally:
                if os.path.exists(tmp.name):
                    os.unlink(tmp.name)

            if i % 5 == 0 or i == len(segments):
                log(f"   [{i}/{len(segments)}] TTS generated")

        result.export(out_path, format="mp3")

    def _elevenlabs(self, segments, lang, api_key, voice_id, out_path, log, cancel):
        try:
            from elevenlabs import VoiceSettings
            from elevenlabs.client import ElevenLabs
            from pydub import AudioSegment
        except ImportError:
            raise RuntimeError("Run: pip install elevenlabs pydub audioop-lts")

        key = api_key or os.environ.get("ELEVENLABS_API_KEY", "")
        if not key:
            raise RuntimeError("Set ELEVENLABS_API_KEY env var.")

        client = ElevenLabs(api_key=key)
        vid = voice_id or "EXAVITQu4vr4xnSDxMaL"  # Bella
        result = AudioSegment.silent(duration=0)
        cursor_ms = 0

        log(f"   ElevenLabs voice: {vid}")

        for i, seg in enumerate(segments, 1):
            if cancel.is_set():
                raise CancelledError()
            start_ms = int(seg.start * 1000)
            if start_ms > cursor_ms:
                result += AudioSegment.silent(duration=start_ms - cursor_ms)
                cursor_ms = start_ms

            txt = seg.translated.strip()
            if not txt:
                continue

            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            try:
                audio_bytes = b"".join(
                    client.text_to_speech.convert(
                        voice_id=vid,
                        text=txt,
                        model_id="eleven_multilingual_v2",
                        voice_settings=VoiceSettings(
                            stability=0.4,
                            similarity_boost=0.75,
                            style=0.5,
                            use_speaker_boost=True,
                        ),
                    )
                )
                tmp.write(audio_bytes)
                tmp.close()
                audio = AudioSegment.from_mp3(tmp.name)
                result += audio
                cursor_ms += len(audio)
            except Exception as e:
                log(f"   ⚠️  Seg {i} ElevenLabs failed: {e}")
                tmp.close()
            finally:
                if os.path.exists(tmp.name):
                    os.unlink(tmp.name)

            if i % 5 == 0 or i == len(segments):
                log(f"   [{i}/{len(segments)}] TTS generated")

        result.export(out_path, format="mp3")

    # ── Config widget ─────────────────────────────────────────────────────────

    def build_config_widget(self, parent=None):
        w = QWidget(parent)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        # Backend
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Backend:"))
        self._backend_combo = QComboBox()
        self._backend_combo.addItems(TTS_BACKENDS.keys())
        self._backend_combo.currentIndexChanged.connect(self._on_backend_changed)
        r1.addWidget(self._backend_combo)
        r1.addStretch()
        v.addLayout(r1)

        # Language
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Language:"))
        self._lang_combo = QComboBox()
        self._lang_combo.addItems(GTTS_LANGS.keys())
        self._lang_combo.setCurrentText("Vietnamese")
        r2.addWidget(self._lang_combo)
        r2.addStretch()
        v.addLayout(r2)

        # API key + voice id
        self._api_lbl = QLabel("API Key:")
        self._api_edit = QLineEdit()
        self._api_edit.setPlaceholderText("API key")
        self._api_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_lbl.setVisible(False)
        self._api_edit.setVisible(False)
        v.addWidget(self._api_lbl)
        v.addWidget(self._api_edit)

        # Voice selector (FPT / Zalo)
        self._voice_lbl = QLabel("Voice:")
        self._voice_combo = QComboBox()
        self._voice_lbl.setVisible(False)
        self._voice_combo.setVisible(False)
        v.addWidget(self._voice_lbl)
        v.addWidget(self._voice_combo)

        # Voice ID (ElevenLabs custom)
        self._voice_id_lbl = QLabel("Voice ID:")
        self._voice_edit = QLineEdit()
        self._voice_edit.setPlaceholderText("ElevenLabs voice ID (blank = default)")
        self._voice_id_lbl.setVisible(False)
        self._voice_edit.setVisible(False)
        v.addWidget(self._voice_id_lbl)
        v.addWidget(self._voice_edit)

        self._on_backend_changed(0)

        # ── Mix mode ──
        v.addWidget(self._sep_label("🎚️  Audio Mix Mode"))
        mix_widget = QWidget()
        mv = QVBoxLayout(mix_widget)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(3)
        self._mix_group = QButtonGroup(w)
        self._mix_radios = {}
        for label, key in MIX_MODES.items():
            rb = QRadioButton(label)
            rb.setToolTip(
                {
                    "replace": "Remove original audio, use TTS only",
                    "bgm_only": "TTS + background music from Step 4",
                    "full_mix": "TTS + background + quiet original voice",
                }[key]
            )
            self._mix_group.addButton(rb)
            self._mix_radios[key] = rb
            mv.addWidget(rb)
        self._mix_radios["bgm_only"].setChecked(True)
        v.addWidget(mix_widget)

        # ── Volume sliders ──
        v.addWidget(self._sep_label("🔊  Volume"))
        self._tts_vol_slider = self._vol_row(v, "TTS voice:", 100)
        self._bgm_vol_slider = self._vol_row(v, "Background music:", 30)
        self._orig_vol_slider = self._vol_row(v, "Original voice:", 10)

        return w

    def _sep_label(self, text):
        l = QLabel(text)
        l.setStyleSheet(
            "color:#a0a8ff;font-size:11px;font-weight:600;" "margin-top:4px;"
        )
        return l

    def _vol_row(self, parent_layout, label, default_pct):
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setFixedWidth(130)
        row.addWidget(lbl)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 150)
        slider.setValue(default_pct)
        row.addWidget(slider)
        val_lbl = QLabel(f"{default_pct}%")
        val_lbl.setFixedWidth(38)
        val_lbl.setStyleSheet("color:#888;font-size:11px;")
        slider.valueChanged.connect(lambda v, l=val_lbl: l.setText(f"{v}%"))
        row.addWidget(val_lbl)
        parent_layout.addLayout(row)
        return slider

    def _on_backend_changed(self, idx):
        # 0=FPT, 1=Zalo, 2=gTTS, 3=OpenAI, 4=ElevenLabs
        backend = (
            list(TTS_BACKENDS.values())[idx] if idx < len(TTS_BACKENDS) else "gtts"
        )

        needs_key = backend in ("fpt", "zalo", "openai_tts", "elevenlabs")
        needs_combo = backend in ("fpt", "zalo")
        needs_el_id = backend == "elevenlabs"

        # API key placeholder
        placeholders = {
            "fpt": "FPT API key — fpt.ai/tts (1M ký tự free)",
            "zalo": "Zalo AI key — zalo.ai/developers",
            "openai_tts": "OpenAI API key — platform.openai.com",
            "elevenlabs": "ElevenLabs key — elevenlabs.io (10k chars free)",
        }
        if self._api_edit:
            self._api_edit.setPlaceholderText(placeholders.get(backend, "API key"))

        self._api_lbl.setVisible(needs_key)
        self._api_edit.setVisible(needs_key)
        self._voice_lbl.setVisible(needs_combo)
        self._voice_combo.setVisible(needs_combo)
        self._voice_id_lbl.setVisible(needs_el_id)
        self._voice_edit.setVisible(needs_el_id)

        # Populate voice combo
        if backend == "fpt":
            self._voice_lbl.setText("Voice (FPT):")
            self._voice_combo.clear()
            self._voice_combo.addItems(
                [
                    "banmai — Nữ miền Nam (default)",
                    "leminh — Nam miền Nam",
                    "lannhi — Nữ miền Bắc",
                    "minhquang — Nam miền Bắc",
                    "giahuy — Nam miền Nam (trẻ)",
                    "linhsan — Nữ miền Trung",
                    "myan — Nữ miền Nam (nhẹ nhàng)",
                    "ngoclam — Nữ miền Bắc (trẻ)",
                ]
            )
        elif backend == "zalo":
            self._voice_lbl.setText("Voice (Zalo):")
            self._voice_combo.clear()
            self._voice_combo.addItems(
                [
                    "1 — Nữ miền Nam (default)",
                    "2 — Nam miền Nam",
                    "3 — Nữ miền Bắc",
                    "4 — Nam miền Bắc",
                ]
            )

    def collect_config(self):
        mix_mode = "bgm_only"
        for key, rb in self._mix_radios.items():
            if rb.isChecked():
                mix_mode = key
                break
        key_text = self._backend_combo.currentText() if self._backend_combo else ""
        backend = TTS_BACKENDS.get(key_text, "gtts")

        # Get voice — combo for FPT/Zalo, text edit for ElevenLabs
        voice_id = ""
        if backend in ("fpt", "zalo") and self._voice_combo:
            # Extract code from combo text e.g. "banmai — Nữ miền Nam" → "banmai"
            combo_text = self._voice_combo.currentText()
            voice_id = combo_text.split(" — ")[0].strip() if combo_text else ""
        elif backend == "elevenlabs" and self._voice_edit:
            voice_id = self._voice_edit.text().strip()

        return {
            "backend": backend,
            "lang": GTTS_LANGS.get(
                self._lang_combo.currentText() if self._lang_combo else "Vietnamese",
                "vi",
            ),
            "api_key": self._api_edit.text().strip() or None,
            "voice_id": voice_id or None,
            "mix_mode": mix_mode,
            "tts_vol": self._tts_vol_slider.value() / 100.0,
            "bgm_vol": self._bgm_vol_slider.value() / 100.0,
            "orig_vol": self._orig_vol_slider.value() / 100.0,
        }
