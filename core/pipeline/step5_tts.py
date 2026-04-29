"""
Step 5 — TTS generation only (single or multi backend).
"""

import json
import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from core.pipeline.base import BaseStep, CancelledError
from core.pipeline.selection import (
    TTS_BACKEND_LABEL_TO_KEY,
    expand_tts_backends,
    tts_backend_from_label,
)

TTS_BACKENDS = dict(TTS_BACKEND_LABEL_TO_KEY)
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

# Approximate cost per 1M chars (USD) for reference display
COST_PER_1M = {
    "fpt": 0.00,  # free tier
    "zalo": 0.00,  # free tier
    "gtts": 0.00,  # free
    "google_cloud_tts": 4.00,  # $4/1M chars (Standard), $16/1M (WaveNet/Neural2)
    "openai_tts": 15.00,  # $15/1M chars
    "elevenlabs": 30.00,  # ~$30/1M chars (Creator plan)
}


def _count_chars(session) -> tuple[int, str]:
    """
    Count total translated characters in session.
    Returns (char_count, display_string).
    """
    try:
        if not session.step2_done:
            return 0, "No translated script yet"
        segs = session.load_translated()
        total = sum(len(s.translated.strip()) for s in segs)
        return total, f"{total:,} characters  ({len(segs)} segments)"
    except Exception as e:
        return 0, f"Cannot read script: {e}"


def _estimate_cost(char_count: int, backend_key: str) -> str:
    """Return cost estimate string for given char count and backend."""
    if char_count == 0:
        return ""
    cost_per_1m = COST_PER_1M.get(backend_key, 0)
    if cost_per_1m == 0:
        return "Free"
    usd = char_count / 1_000_000 * cost_per_1m
    vnd = usd * 25_000
    if vnd < 1:
        return f"~${usd:.4f} USD"
    return f"~${usd:.3f} USD  (~{vnd:,.0f} VNĐ)"


class TTSStep(BaseStep):
    STEP_ID = "step5_tts"
    LABEL = "⑤ Text-to-Speech"
    COLOR = "#5a1a6a"
    ENABLED_BY_DEFAULT = False

    def __init__(self):
        self._backend_combo = None
        self._lang_combo = None
        self._api_lbl = self._api_edit = None
        self._voice_lbl = self._voice_combo = None
        self._voice_id_lbl = self._voice_edit = None
        self._char_count_lbl = None  # shows char count
        self._cost_lbl = None  # shows cost estimate

    # ── Public: called by MainWindow / MultiSessionWindow ─────────────────────

    def update_char_count(self, session=None):
        """
        Refresh the character count label from a session object.
        Call this whenever the active session changes.
        """
        if self._char_count_lbl is None:
            return

        if session is None:
            self._char_count_lbl.setText("No session loaded")
            self._char_count_lbl.setStyleSheet("color:#555;font-size:10px;")
            if self._cost_lbl:
                self._cost_lbl.setText("")
            return

        count, display = _count_chars(session)

        if count == 0:
            self._char_count_lbl.setText(display)
            self._char_count_lbl.setStyleSheet("color:#555;font-size:10px;")
            if self._cost_lbl:
                self._cost_lbl.setText("")
            return

        self._char_count_lbl.setText(f"📝 {display}")
        self._char_count_lbl.setStyleSheet("color:#a0c8ff;font-size:10px;")

        # Update cost estimate
        if self._cost_lbl and self._backend_combo:
            backend_key = tts_backend_from_label(self._backend_combo.currentText())
            cost = _estimate_cost(count, backend_key)
            if cost == "Free":
                self._cost_lbl.setText("💸 Cost: Free")
                self._cost_lbl.setStyleSheet("color:#5dca8e;font-size:10px;")
            elif cost:
                self._cost_lbl.setText(f"💸 Cost: {cost}")
                self._cost_lbl.setStyleSheet("color:#ffaa55;font-size:10px;")
            else:
                self._cost_lbl.setText("")

        # Store count for backend-change recalculation
        self._last_char_count = count

    # ── Run ───────────────────────────────────────────────────────────────────

    def run(self, session, config, log, cancel):
        config = config or {}
        backend = config.get("backend", "gtts")
        lang = config.get("lang", "vi")
        api_key = config.get("api_key")
        voice_id = config.get("voice_id", "")

        segments = session.load_translated()
        if not segments:
            raise RuntimeError("No translated segments — run Step 2 first.")

        total_chars = sum(len(s.translated.strip()) for s in segments)
        backends = self._resolve_backends(backend)
        log(f"🗣️  TTS Backends: {', '.join(backends)} | Lang: {lang}")
        log(f"📝 Characters to send: {total_chars:,}")

        if cancel.is_set():
            raise CancelledError()

        primary_output = ""
        success = 0
        errors = []

        for idx, one_backend in enumerate(backends, 1):
            if cancel.is_set():
                raise CancelledError()

            out_file = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            voice = (voice_id or "default").strip().replace(" ", "_")
            asset_prefix = f"{one_backend}_{voice}_{lang}_{ts}"
            asset_prefix = "".join(
                c if c.isalnum() or c in "._-" else "_" for c in asset_prefix
            )
            request_dir = session.step5_tts_assets_dir / f"{asset_prefix}_segments"
            log(f"🎙️  [{idx}/{len(backends)}] Generating with backend: {one_backend}…")
            try:
                segment_files = self._generate_tts(
                    segments,
                    lang,
                    one_backend,
                    api_key,
                    voice_id,
                    out_file,
                    log,
                    cancel,
                    request_dir=request_dir,
                )
                lib_audio, lib_manifest = self._save_tts_library(
                    session,
                    out_file,
                    one_backend,
                    voice_id,
                    lang,
                    segments,
                    asset_prefix,
                    request_dir,
                    segment_files,
                )
                log(f"✅ [{one_backend}] audio asset → {lib_audio.name}")
                log(f"🕒 [{one_backend}] timing asset → {lib_manifest.name}")
                if not primary_output:
                    shutil.copy2(out_file, str(session.step5_tts))
                    primary_output = str(session.step5_tts)
                success += 1
            except Exception as e:
                errors.append(f"{one_backend}: {e}")
                log(f"❌ [{one_backend}] failed: {e}")
            finally:
                if os.path.exists(out_file):
                    os.unlink(out_file)

        if success == 0:
            raise RuntimeError("All TTS backends failed:\n" + "\n".join(errors[:5]))

        log(f"🏁 Step 5 done: {success}/{len(backends)} backend(s) generated")
        return primary_output or str(session.step5_tts)

    @staticmethod
    def _resolve_backends(backend_key: str) -> list[str]:
        return expand_tts_backends(backend_key)

    def _save_tts_library(
        self,
        session,
        tts_path,
        backend,
        voice_id,
        lang,
        segments,
        asset_prefix,
        request_dir,
        segment_files,
    ):
        library_dir = session.step5_tts_assets_dir
        library_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{asset_prefix}.mp3"
        audio_target = library_dir / filename
        manifest_target = library_dir / f"{asset_prefix}.json"

        shutil.copy2(tts_path, audio_target)

        manifest = {
            "version": 1,
            "created_at": datetime.now().isoformat(),
            "backend": backend,
            "voice_id": voice_id or "",
            "lang": lang,
            "audio_file": audio_target.name,
            "segment_dir": request_dir.name,
            "segments": [
                {
                    "index": i,
                    "start": float(seg.start),
                    "end": float(seg.end),
                    "text": getattr(seg, "translated", "") or "",
                    "clip_file": (
                        segment_files[i]
                        if i < len(segment_files) and segment_files[i]
                        else None
                    ),
                }
                for i, seg in enumerate(segments)
            ],
        }
        manifest_target.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return audio_target, manifest_target

    # ── TTS dispatch ──────────────────────────────────────────────────────────

    def _generate_tts(
        self,
        segments,
        lang,
        backend,
        api_key,
        voice_id,
        out_path,
        log,
        cancel,
        request_dir=None,
    ):
        if backend == "fpt":
            return self._fpt(
                segments, api_key, voice_id, out_path, log, cancel, request_dir
            )
        elif backend == "zalo":
            return self._zalo(
                segments, api_key, voice_id, out_path, log, cancel, request_dir
            )
        elif backend == "gtts":
            return self._gtts(segments, lang, out_path, log, cancel, request_dir)
        elif backend == "openai_tts":
            return self._openai_tts(
                segments, lang, api_key, out_path, log, cancel, request_dir
            )
        elif backend == "google_cloud_tts":
            return self._google_cloud_tts(
                segments, lang, api_key, voice_id, out_path, log, cancel, request_dir
            )
        elif backend == "elevenlabs":
            return self._elevenlabs(
                segments, lang, api_key, voice_id, out_path, log, cancel, request_dir
            )
        else:
            raise RuntimeError(f"Unknown TTS backend: {backend}")

    # ── Parallel TTS helper ───────────────────────────────────────────────────

    def _run_parallel(
        self, segments, worker_fn, log, cancel, max_workers=1, label="TTS"
    ):
        import concurrent.futures

        total = len(segments)
        results = [None] * total
        done = [0]
        log(f"   ⚡ Parallel {label}: {total} segments × {max_workers} workers")
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(worker_fn, seg, i, total): i for i, seg in enumerate(segments)
            }
            for fut in concurrent.futures.as_completed(futures):
                if cancel.is_set():
                    ex.shutdown(wait=False, cancel_futures=True)
                    raise CancelledError()
                idx = futures[fut]
                try:
                    results[idx] = fut.result()
                except Exception as e:
                    log(f"   ⚠️  Seg {idx+1} failed: {e}")
                    results[idx] = None
                done[0] += 1
                if done[0] % max(1, total // 10) == 0 or done[0] == total:
                    log(f"   📊 [{done[0]}/{total}] {label} done")
        return results

    def _assemble_audio(self, segments, audio_list, log):
        from pydub import AudioSegment

        result = AudioSegment.silent(duration=0)
        cursor_ms = 0
        for i, (seg, audio) in enumerate(zip(segments, audio_list)):
            start_ms = int(seg.start * 1000)
            if start_ms > cursor_ms:
                result += AudioSegment.silent(duration=start_ms - cursor_ms)
                cursor_ms = start_ms
            if audio is None:
                log(f"   ⚠️  Seg {i+1}: no audio, inserting silence")
                seg_dur = int((seg.end - seg.start) * 1000)
                result += AudioSegment.silent(duration=seg_dur)
                cursor_ms = max(cursor_ms + seg_dur, int(seg.end * 1000))
                continue
            result += audio
            cursor_ms = max(cursor_ms + len(audio), int(seg.end * 1000))
        return result

    def _export_segment_assets(self, audio_list, request_dir: Path) -> list[str | None]:
        request_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for i, audio in enumerate(audio_list):
            if audio is None:
                files.append(None)
                continue
            name = f"seg_{i:04d}.mp3"
            out = request_dir / name
            audio.export(str(out), format="mp3")
            files.append(name)
        return files

    # ── FPT AI TTS ────────────────────────────────────────────────────────────

    def _fpt(
        self, segments, api_key, voice_id, out_path, log, cancel, request_dir=None
    ):
        import requests as _req
        from pydub import AudioSegment

        from core.api_keys import get_key
        from core.logger import ApiLogger

        api = ApiLogger(log, prefix="   ")
        key = api_key or get_key("fpt") or os.environ.get("FPT_API_KEY", "")
        api.api_key_info("FPT", key)
        if not key:
            raise RuntimeError(
                "FPT AI API key required.\nGet FREE key at: console.fpt.ai"
            )

        voice = voice_id or "banmai"
        api.info(f"Voice: {voice} | Segments: {len(segments)}")

        def fetch_one(seg, idx, total):
            txt = seg.translated.strip()
            if not txt:
                return None
            data = None
            for post_attempt in range(5):
                resp = _req.post(
                    "https://api.fpt.ai/hmi/tts/v5",
                    headers={
                        "api_key": key,
                        "voice": voice,
                        "speed": "0",
                        "Cache-Control": "no-cache",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    data=txt.encode("utf-8"),
                    timeout=15,
                )
                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After", "")
                    wait = (
                        max(1, int(retry_after))
                        if retry_after.isdigit()
                        else min(2**post_attempt, 16)
                    )
                    if post_attempt < 4:
                        log(
                            f"   ⏳ Seg {idx+1}/{total} hit 429, retry in {wait}s ({post_attempt+1}/5)"
                        )
                        time.sleep(wait)
                        continue
                    raise RuntimeError("FPT 429 Too Many Requests (retries exhausted)")
                resp.raise_for_status()
                data = resp.json()
                break
            if data is None:
                raise RuntimeError("FPT request failed: no response payload")
            if data.get("error", 0) != 0:
                raise RuntimeError(
                    f"FPT error {data.get('error')}: {data.get('message')}"
                )
            async_url = data.get("async", "")
            if not async_url:
                raise RuntimeError(f"No async URL: {data}")
            for attempt in range(60):
                r = _req.get(async_url, timeout=10)
                if r.status_code == 200 and len(r.content) > 100:
                    hdr = r.content[:3]
                    if hdr == b"ID3" or r.content[:2] in (
                        b"\xff\xfb",
                        b"\xff\xf3",
                        b"\xff\xf2",
                    ):
                        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
                        tmp.write(r.content)
                        tmp.close()
                        try:
                            audio = AudioSegment.from_mp3(tmp.name)
                            log(
                                f"   ✅ Seg {idx+1}/{total} ready ({len(r.content)//1024}KB)"
                            )
                            return audio
                        finally:
                            os.unlink(tmp.name)
                time.sleep(1)
            raise RuntimeError(f"Timeout polling {async_url}")

        with api.timer("FPT parallel fetch"):
            audio_list = self._run_parallel(
                segments, fetch_one, log, cancel, max_workers=1, label="FPT TTS"
            )
        with api.timer("Assemble audio"):
            result = self._assemble_audio(segments, audio_list, log)
        result.export(out_path, format="mp3")
        segment_files = self._export_segment_assets(audio_list, request_dir)
        ok = sum(1 for a in audio_list if a is not None)
        api.summary(ok, len(segments) - ok, len(segments), "FPT TTS")
        return segment_files

    # ── Zalo AI TTS ───────────────────────────────────────────────────────────

    def _zalo(
        self, segments, api_key, voice_id, out_path, log, cancel, request_dir=None
    ):
        import requests
        from pydub import AudioSegment

        key = api_key or os.environ.get("ZALO_API_KEY", "")
        if not key:
            raise RuntimeError(
                "Zalo AI API key required.\nGet key at: zalo.ai → Developers"
            )
        voice_code = voice_id or "1"
        log(f"🎙️  Zalo AI TTS | voice: {voice_code}")
        result, cursor_ms, total = AudioSegment.silent(duration=0), 0, len(segments)
        audio_list = [None] * len(segments)
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
                resp = None
                for attempt in range(3):
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
                    if resp.status_code == 429:
                        time.sleep(2**attempt)
                        continue
                    break
                resp.raise_for_status()
                data = resp.json()
                if data.get("error_code", 0) != 0:
                    raise RuntimeError(
                        f"Zalo error {data.get('error_code')}: {data.get('error_message')}"
                    )
                audio_url = data.get("data", {}).get("url", "")
                if not audio_url:
                    raise RuntimeError(f"Zalo: no URL: {data}")
                audio_resp = requests.get(audio_url, timeout=30)
                audio_resp.raise_for_status()
                content = audio_resp.content
                if len(content) < 100:
                    raise RuntimeError(f"Zalo: audio too small ({len(content)} bytes)")
                if content[:4] == b"RIFF":
                    suffix = ".wav"
                elif content[:3] in (b"ID3",) or content[:2] in (
                    b"\xff\xfb",
                    b"\xff\xf3",
                    b"\xff\xf2",
                ):
                    suffix = ".mp3"
                else:
                    raise RuntimeError("Zalo: unknown format")
                tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
                tmp.write(content)
                tmp.close()
                try:
                    audio = (
                        AudioSegment.from_wav(tmp.name)
                        if suffix == ".wav"
                        else AudioSegment.from_mp3(tmp.name)
                    )
                    audio_list[i - 1] = audio
                    result += audio
                    cursor_ms = max(cursor_ms + len(audio), int(seg.end * 1000))
                finally:
                    os.unlink(tmp.name)
            except Exception as e:
                log(f"   ⚠️  Seg {i} failed: {e}")
            if i % 5 == 0 or i == total:
                log(f"   [{i}/{total}] TTS generated")
            time.sleep(1.0)
        result.export(out_path, format="mp3")
        return self._export_segment_assets(audio_list, request_dir)

    # ── gTTS ──────────────────────────────────────────────────────────────────

    def _gtts(self, segments, lang, out_path, log, cancel, request_dir=None):
        try:
            from gtts import gTTS
            from pydub import AudioSegment
        except ImportError:
            raise RuntimeError("Run: pip install gtts pydub audioop-lts")

        log(f"   ⚡ gTTS parallel | lang: {lang} | segments: {len(segments)}")

        def fetch_one(seg, idx, total):
            txt = seg.translated.strip()
            if not txt:
                return None
            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            tmp.close()
            try:
                gTTS(text=txt, lang=lang[:2], slow=False).save(tmp.name)
                return AudioSegment.from_mp3(tmp.name)
            finally:
                if os.path.exists(tmp.name):
                    os.unlink(tmp.name)

        audio_list = self._run_parallel(
            segments, fetch_one, log, cancel, max_workers=5, label="gTTS"
        )
        result = self._assemble_audio(segments, audio_list, log)
        result.export(out_path, format="mp3")
        segment_files = self._export_segment_assets(audio_list, request_dir)
        ok = sum(1 for a in audio_list if a is not None)
        log(f"   🏁 gTTS: {ok}/{len(segments)} ok")
        return segment_files

    # ── OpenAI TTS ────────────────────────────────────────────────────────────

    def _openai_tts(
        self, segments, lang, api_key, out_path, log, cancel, request_dir=None
    ):
        try:
            from openai import OpenAI
            from pydub import AudioSegment
        except ImportError:
            raise RuntimeError("Run: pip install openai pydub audioop-lts")

        key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError("Set OPENAI_API_KEY env var.")

        VOICE_MAP = {
            "vi": "nova",
            "en": "alloy",
            "ja": "shimmer",
            "ko": "nova",
            "zh": "shimmer",
        }
        voice = VOICE_MAP.get(lang[:2], "nova")
        client = OpenAI(api_key=key)
        log(f"   ⚡ OpenAI TTS parallel | voice: {voice} | segments: {len(segments)}")

        def fetch_one(seg, idx, total):
            txt = seg.translated.strip()
            if not txt:
                return None
            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            tmp.close()
            try:
                resp = client.audio.speech.create(
                    model="tts-1", voice=voice, input=txt, response_format="mp3"
                )
                resp.stream_to_file(tmp.name)
                return AudioSegment.from_mp3(tmp.name)
            finally:
                if os.path.exists(tmp.name):
                    os.unlink(tmp.name)

        audio_list = self._run_parallel(
            segments, fetch_one, log, cancel, max_workers=5, label="OpenAI TTS"
        )
        result = self._assemble_audio(segments, audio_list, log)
        result.export(out_path, format="mp3")
        segment_files = self._export_segment_assets(audio_list, request_dir)
        ok = sum(1 for a in audio_list if a is not None)
        log(f"   🏁 OpenAI TTS: {ok}/{len(segments)} ok")
        return segment_files

    # ── Google Cloud TTS ──────────────────────────────────────────────────────

    def _google_cloud_tts(
        self, segments, lang, api_key, voice_id, out_path, log, cancel, request_dir=None
    ):
        """
        Google Cloud Text-to-Speech API.
        Supports Standard, WaveNet, Neural2, Studio voices.
        Pricing: Standard=$4/1M chars, WaveNet/Neural2=$16/1M chars.

        Setup:
          1. Enable Cloud TTS API at console.cloud.google.com
          2. Create API key (or use service account JSON)
          3. Enter API key in Step 5 config
        """
        try:
            from pydub import AudioSegment
        except ImportError:
            raise RuntimeError("Run: pip install pydub audioop-lts")

        import base64

        import requests as _req

        key = api_key or os.environ.get("GOOGLE_CLOUD_TTS_KEY", "")
        if not key:
            raise RuntimeError(
                "Google Cloud TTS API key required.\n"
                "1. Go to console.cloud.google.com\n"
                "2. Enable Cloud Text-to-Speech API\n"
                "3. Create API key → paste here"
            )

        # Voice selection
        # voice_id format: "vi-VN-Neural2-A" or "vi-VN-Wavenet-A" etc.
        # Default: Neural2 for Vietnamese
        LANG_VOICE_MAP = {
            "vi": ("vi-VN", "vi-VN-Neural2-A", "FEMALE"),
            "en": ("en-US", "en-US-Neural2-F", "FEMALE"),
            "ja": ("ja-JP", "ja-JP-Neural2-B", "FEMALE"),
            "ko": ("ko-KR", "ko-KR-Neural2-A", "FEMALE"),
            "zh": ("cmn-CN", "cmn-CN-Wavenet-A", "FEMALE"),
            "zh-cn": ("cmn-CN", "cmn-CN-Wavenet-A", "FEMALE"),
            "fr": ("fr-FR", "fr-FR-Neural2-A", "FEMALE"),
            "de": ("de-DE", "de-DE-Neural2-F", "FEMALE"),
            "es": ("es-ES", "es-ES-Neural2-A", "FEMALE"),
        }
        lang_key = lang[:2].lower() if lang else "vi"
        default_lang_code, default_voice, default_gender = LANG_VOICE_MAP.get(
            lang_key, ("vi-VN", "vi-VN-Neural2-A", "FEMALE")
        )

        if voice_id and voice_id.strip():
            # User specified voice, infer lang_code from voice name
            # e.g. "vi-VN-Neural2-A" → lang_code = "vi-VN"
            parts = voice_id.strip().split("-")
            lang_code = (
                f"{parts[0]}-{parts[1]}" if len(parts) >= 2 else default_lang_code
            )
            voice_name = voice_id.strip()
        else:
            lang_code = default_lang_code
            voice_name = default_voice

        log(
            f"   ⚡ Google Cloud TTS | voice: {voice_name} | lang: {lang_code} | segs: {len(segments)}"
        )

        url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={key}"

        def fetch_one(seg, idx, total):
            txt = seg.translated.strip()
            if not txt:
                return None
            payload = {
                "input": {"text": txt},
                "voice": {
                    "languageCode": lang_code,
                    "name": voice_name,
                },
                "audioConfig": {
                    "audioEncoding": "MP3",
                    "speakingRate": 1.0,
                    "pitch": 0.0,
                },
            }
            for attempt in range(3):
                try:
                    resp = _req.post(url, json=payload, timeout=15)
                    if resp.status_code == 429:
                        wait = 2**attempt
                        log(f"   ⏳ Seg {idx+1}/{total} rate limited, wait {wait}s")
                        time.sleep(wait)
                        continue
                    if resp.status_code == 400:
                        err = resp.json().get("error", {})
                        raise RuntimeError(
                            f"Bad request: {err.get('message', resp.text[:200])}"
                        )
                    if resp.status_code == 401:
                        raise RuntimeError(
                            "401 Unauthorized — API key invalid or Cloud TTS not enabled.\n"
                            "Check: console.cloud.google.com → APIs & Services → Cloud TTS"
                        )
                    resp.raise_for_status()
                    data = resp.json()
                    audio_b64 = data.get("audioContent", "")
                    if not audio_b64:
                        raise RuntimeError("Empty audio response")
                    audio_bytes = base64.b64decode(audio_b64)
                    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
                    tmp.write(audio_bytes)
                    tmp.close()
                    try:
                        audio = AudioSegment.from_mp3(tmp.name)
                        return audio
                    finally:
                        os.unlink(tmp.name)
                except RuntimeError:
                    raise
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(1)
            return None

        audio_list = self._run_parallel(
            segments, fetch_one, log, cancel, max_workers=5, label="Google Cloud TTS"
        )
        result = self._assemble_audio(segments, audio_list, log)
        result.export(out_path, format="mp3")
        segment_files = self._export_segment_assets(audio_list, request_dir)
        ok = sum(1 for a in audio_list if a is not None)
        log(f"   🏁 Google Cloud TTS: {ok}/{len(segments)} ok")
        return segment_files

    # ── ElevenLabs ────────────────────────────────────────────────────────────

    def _elevenlabs(
        self, segments, lang, api_key, voice_id, out_path, log, cancel, request_dir=None
    ):
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
        vid = voice_id or "EXAVITQu4vr4xnSDxMaL"
        result, cursor_ms = AudioSegment.silent(duration=0), 0
        audio_list = [None] * len(segments)
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
                audio_list[i - 1] = audio
                result += audio
                cursor_ms = max(cursor_ms + len(audio), int(seg.end * 1000))
            except Exception as e:
                log(f"   ⚠️  Seg {i} ElevenLabs failed: {e}")
                tmp.close()
            finally:
                if os.path.exists(tmp.name):
                    os.unlink(tmp.name)
            if i % 5 == 0 or i == len(segments):
                log(f"   [{i}/{len(segments)}] TTS generated")
        result.export(out_path, format="mp3")
        return self._export_segment_assets(audio_list, request_dir)

    # ── Config widget ─────────────────────────────────────────────────────────

    def build_config_widget(self, parent=None):
        w = QWidget(parent)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Backend:"))
        self._backend_combo = QComboBox()
        self._backend_combo.addItem("All backends (batch run)")
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
        self._api_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_lbl.setVisible(False)
        self._api_edit.setVisible(False)
        v.addWidget(self._api_lbl)
        v.addWidget(self._api_edit)

        self._voice_lbl = QLabel("Voice:")
        self._voice_combo = QComboBox()
        self._voice_lbl.setVisible(False)
        self._voice_combo.setVisible(False)
        v.addWidget(self._voice_lbl)
        v.addWidget(self._voice_combo)

        self._voice_id_lbl = QLabel("Voice ID:")
        self._voice_edit = QLineEdit()
        self._voice_edit.setPlaceholderText("ElevenLabs voice ID (blank = default)")
        self._voice_id_lbl.setVisible(False)
        self._voice_edit.setVisible(False)
        v.addWidget(self._voice_id_lbl)
        v.addWidget(self._voice_edit)

        # ── Character count + cost estimate ──────────────────────────────────
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background:#2d2d4e;margin:2px 0;")
        v.addWidget(sep)

        self._char_count_lbl = QLabel("No session loaded")
        self._char_count_lbl.setStyleSheet("color:#555;font-size:10px;")
        self._char_count_lbl.setWordWrap(True)
        v.addWidget(self._char_count_lbl)

        self._cost_lbl = QLabel("")
        self._cost_lbl.setStyleSheet("color:#ffaa55;font-size:10px;")
        v.addWidget(self._cost_lbl)

        # Store last char count for recalc when backend changes
        self._last_char_count = 0

        self._backend_combo.setCurrentIndex(3)  # Default: gTTS (Google, free)
        self._on_backend_changed(3)
        return w

    def _sep_label(self, text):
        l = QLabel(text)
        l.setStyleSheet("color:#a0a8ff;font-size:11px;font-weight:600;margin-top:4px;")
        return l

    def _on_backend_changed(self, idx):
        key_text = self._backend_combo.currentText() if self._backend_combo else ""
        backend = tts_backend_from_label(key_text)
        needs_key = backend in ("fpt", "zalo", "openai_tts", "elevenlabs")
        needs_combo = backend in ("fpt", "zalo", "google_cloud_tts")
        needs_el_id = backend == "elevenlabs"
        placeholders = {
            "fpt": "FPT API key — fpt.ai/tts (1M ký tự free)",
            "zalo": "Zalo AI key — zalo.ai/developers",
            "google_cloud_tts": "Google Cloud API key — console.cloud.google.com",
            "openai_tts": "OpenAI API key — platform.openai.com",
            "elevenlabs": "ElevenLabs key — elevenlabs.io",
        }
        if self._api_edit:
            self._api_edit.setPlaceholderText(placeholders.get(backend, "API key"))
        self._api_lbl.setVisible(needs_key)
        self._api_edit.setVisible(needs_key)
        self._voice_lbl.setVisible(needs_combo)
        self._voice_combo.setVisible(needs_combo)
        self._voice_id_lbl.setVisible(needs_el_id)
        self._voice_edit.setVisible(needs_el_id)
        if backend == "google_cloud_tts":
            self._voice_lbl.setText("Voice name:")
            self._voice_combo.clear()
            self._voice_combo.addItems(
                [
                    "vi-VN-Neural2-A — Nữ Neural2 (recommended)",
                    "vi-VN-Neural2-D — Nam Neural2",
                    "vi-VN-Wavenet-A — Nữ WaveNet",
                    "vi-VN-Wavenet-B — Nam WaveNet",
                    "vi-VN-Wavenet-C — Nữ WaveNet 2",
                    "vi-VN-Standard-A — Nữ Standard (rẻ nhất)",
                    "vi-VN-Standard-B — Nam Standard",
                ]
            )
            self._voice_lbl.setVisible(True)
            self._voice_combo.setVisible(True)
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

        # Recalculate cost estimate when backend changes
        if self._cost_lbl and self._last_char_count > 0:
            cost = _estimate_cost(self._last_char_count, backend)
            if cost == "Free":
                self._cost_lbl.setText("💸 Cost: Free")
                self._cost_lbl.setStyleSheet("color:#5dca8e;font-size:10px;")
            elif cost:
                self._cost_lbl.setText(f"💸 Cost: {cost}")
                self._cost_lbl.setStyleSheet("color:#ffaa55;font-size:10px;")

    def collect_config(self):
        key_text = self._backend_combo.currentText() if self._backend_combo else ""
        backend = tts_backend_from_label(key_text)
        voice_id = ""
        if backend in ("fpt", "zalo", "google_cloud_tts") and self._voice_combo:
            voice_id = self._voice_combo.currentText().split(" — ")[0].strip()
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
        }
