"""
Step 5 — TTS generation only (single or multi backend).
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
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
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv"}


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
        self._mix_group = self._mix_radios = None
        self._tts_vol_slider = self._bgm_vol_slider = self._orig_vol_slider = None
        self._sync_combo = None

    # ── Run ───────────────────────────────────────────────────────────────────

    def run(self, session, config, log, cancel):
        config = config or {}
        backend = config.get("backend", "gtts")
        lang = config.get("lang", "vi")
        api_key = config.get("api_key")
        voice_id = config.get("voice_id", "")
        sync_mode = config.get("sync_mode", "trim")

        segments = session.load_translated()
        if not segments:
            raise RuntimeError("No translated segments — run Step 2 first.")

        backends = self._resolve_backends(backend)
        log(f"🗣️  TTS Backends: {', '.join(backends)} | Lang: {lang}")

        if cancel.is_set():
            raise CancelledError()

        primary_output = ""
        success = 0
        errors = []

        for idx, one_backend in enumerate(backends, 1):
            if cancel.is_set():
                raise CancelledError()

            out_file = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
            log(f"🎙️  [{idx}/{len(backends)}] Generating with backend: {one_backend}…")
            try:
                self._generate_tts(
                    segments,
                    lang,
                    one_backend,
                    api_key,
                    voice_id,
                    out_file,
                    log,
                    cancel,
                    sync_mode=sync_mode,
                )
                lib_audio, lib_manifest = self._save_tts_library(
                    session,
                    out_file,
                    one_backend,
                    voice_id,
                    lang,
                    segments,
                )
                log(f"✅ [{one_backend}] audio → {lib_audio.name}")
                log(f"🕒 [{one_backend}] timeline → {lib_manifest.name}")
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
        if backend_key == "all":
            return list(TTS_BACKENDS.values())
        return [backend_key]

    def _save_tts_library(self, session, tts_path, backend, voice_id, lang, segments):
        library_dir = session.step5_tts_library_dir
        library_dir.mkdir(parents=True, exist_ok=True)

        voice = (voice_id or "default").strip().replace(" ", "_")
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{backend}_{voice}_{lang}_{ts}.mp3"
        filename = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)
        audio_target = library_dir / filename
        manifest_target = library_dir / f"{Path(filename).stem}.json"

        shutil.copy2(tts_path, audio_target)

        manifest = {
            "version": 1,
            "created_at": datetime.now().isoformat(),
            "backend": backend,
            "voice_id": voice_id or "",
            "lang": lang,
            "audio_file": audio_target.name,
            "segments": [
                {
                    "index": i,
                    "start": float(seg.start),
                    "end": float(seg.end),
                    "text": getattr(seg, "translated", "") or "",
                }
                for i, seg in enumerate(segments)
            ],
        }
        manifest_target.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return audio_target, manifest_target

    # ── Audio mixing ──────────────────────────────────────────────────────────

    def _mix_audio(self, session, tts_path, mix_mode, tts_vol, bgm_vol, orig_vol, log):
        has_bgm = session.step4_done and Path(session.step4_background).exists()

        if mix_mode == "replace":
            if tts_vol == 1.0:
                return tts_path
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
                log("🎵 Mixing TTS + original voice (no BGM)")

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
            if os.path.exists(orig_audio):
                os.unlink(orig_audio)
            return result

        return tts_path

    def _ffmpeg_mix(self, tracks, log):
        out = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        out.close()
        inputs, filter_parts = [], []
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
        r = self._run_cmd(cmd)
        if r.returncode != 0:
            raise RuntimeError(
                f"ffmpeg mix failed (code {self._code(r.returncode)}):\n{self._tail_output(r)}"
            )
        return out.name

    def _apply_volume(self, audio_path, volume):
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
        return out.name if r.returncode == 0 else audio_path

    def _mux(self, video_path, audio_path, out_path, log):
        in_place = os.path.abspath(video_path) == os.path.abspath(out_path)
        actual_out = out_path
        if in_place:
            fd, tmp_path = tempfile.mkstemp(
                prefix="step5_mux_",
                suffix=Path(out_path).suffix or ".mp4",
                dir=str(Path(out_path).parent),
            )
            os.close(fd)
            actual_out = tmp_path
            log(
                "⚠️  Output trùng input video — using temp output to avoid in-place edit"
            )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            video_path,
            "-i",
            audio_path,
            "-c:v",
            "copy",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-shortest",
            actual_out,
        ]
        r = self._run_cmd(cmd)
        if r.returncode != 0:
            if in_place and os.path.exists(actual_out):
                os.unlink(actual_out)
            raise RuntimeError(
                f"ffmpeg mux failed (code {self._code(r.returncode)}):\n{self._tail_output(r)}"
            )

        if in_place:
            shutil.move(actual_out, out_path)

    def _has_video_stream(self, media_path: str) -> bool:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            media_path,
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
            return r.returncode == 0 and (r.stdout or "").strip().lower() == "video"
        except FileNotFoundError:
            # Fallback when ffprobe is unavailable: inspect ffmpeg probe output.
            probe = self._run_cmd(["ffmpeg", "-hide_banner", "-i", media_path])
            txt = self._tail_output(probe, max_chars=4000).lower()
            if "video:" in txt:
                return True
            if "audio:" in txt:
                return False
            # Last resort by extension when probe output is unavailable.
            return Path(media_path).suffix.lower() in VIDEO_EXTS

    @staticmethod
    def _run_cmd(cmd):
        try:
            return subprocess.run(cmd, capture_output=True)
        except FileNotFoundError as e:
            tool = cmd[0] if cmd else "command"
            raise RuntimeError(f"{tool} not found in PATH") from e

    @staticmethod
    def _code(code: int) -> int:
        # Windows sometimes reports unsigned 32-bit exit codes.
        return code - (1 << 32) if code > 0x7FFFFFFF else code

    @staticmethod
    def _tail_output(proc: subprocess.CompletedProcess, max_chars: int = 1500) -> str:
        stderr = proc.stderr
        stdout = proc.stdout

        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")

        text = (stderr or stdout or "").strip()
        if not text:
            return "No ffmpeg error text available."
        return text[-max_chars:]

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
        sync_mode="trim",
    ):
        if backend == "fpt":
            self._fpt(segments, api_key, voice_id, out_path, log, cancel, sync_mode)
        elif backend == "zalo":
            self._zalo(segments, api_key, voice_id, out_path, log, cancel, sync_mode)
        elif backend == "gtts":
            self._gtts(segments, lang, out_path, log, cancel, sync_mode)
        elif backend == "openai_tts":
            self._openai_tts(segments, lang, api_key, out_path, log, cancel, sync_mode)
        elif backend == "elevenlabs":
            self._elevenlabs(
                segments, lang, api_key, voice_id, out_path, log, cancel, sync_mode
            )
        else:
            raise RuntimeError(f"Unknown TTS backend: {backend}")

    # ── Audio sync ────────────────────────────────────────────────────────────

    def _fit_audio_to_segment(
        self, audio, seg_duration_ms: int, sync_mode: str = "trim"
    ):
        """
        trim    — dài hơn → speed up vừa đủ (không cắt chữ), ngắn → giữ nguyên
        pad     — dài → speed up, ngắn → thêm silence
        stretch — luôn speed up/slow down khớp chính xác
        none    — không làm gì
        """
        from pydub import AudioSegment

        audio_ms = len(audio)

        if sync_mode == "none" or seg_duration_ms <= 0:
            return audio

        if sync_mode == "trim":
            if audio_ms <= seg_duration_ms:
                return audio
            return self._speed_up(audio, audio_ms, seg_duration_ms)

        elif sync_mode == "pad":
            if audio_ms < seg_duration_ms:
                return audio + AudioSegment.silent(duration=seg_duration_ms - audio_ms)
            elif audio_ms > seg_duration_ms:
                return self._speed_up(audio, audio_ms, seg_duration_ms)
            return audio

        elif sync_mode == "stretch":
            if abs(audio_ms - seg_duration_ms) < 100:
                return audio
            return self._speed_up(audio, audio_ms, seg_duration_ms)

        return audio

    def _speed_up(self, audio, audio_ms: int, target_ms: int):
        """Speed up/slow down audio using ffmpeg atempo. No content is cut."""
        from pydub import AudioSegment

        ratio = max(0.5, min(2.0, audio_ms / target_ms))
        if abs(ratio - 1.0) < 0.05:
            return audio

        tmp_in = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp_out = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp_in.close()
        tmp_out.close()
        try:
            audio.export(tmp_in.name, format="mp3")
            atempo = (
                f"atempo={ratio:.4f}"
                if ratio <= 2.0
                else f"atempo=2.0,atempo={ratio/2.0:.4f}"
            )
            r = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    tmp_in.name,
                    "-af",
                    atempo,
                    "-c:a",
                    "mp3",
                    tmp_out.name,
                ],
                capture_output=True,
                text=True,
            )
            return AudioSegment.from_mp3(tmp_out.name) if r.returncode == 0 else audio
        except Exception:
            return audio
        finally:
            os.unlink(tmp_in.name)
            if os.path.exists(tmp_out.name):
                os.unlink(tmp_out.name)

    # ── Parallel TTS helper ───────────────────────────────────────────────────

    def _run_parallel(
        self, segments, worker_fn, log, cancel, max_workers: int = 1, label: str = "TTS"
    ) -> list:
        """
        Run worker_fn(seg, idx) in parallel using ThreadPoolExecutor.
        Returns list of (seg, audio_bytes_or_None) in original order.
        worker_fn signature: (seg, idx, total) -> bytes | None
        """
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

    def _assemble_audio(self, segments, audio_list, log, sync_mode="trim"):
        """
        Assemble audio pieces in correct timestamp order.
        audio_list: list of AudioSegment | None (same order as segments)
        """
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

            seg_dur = int((seg.end - seg.start) * 1000)
            audio = self._fit_audio_to_segment(audio, seg_dur, sync_mode)
            result += audio
            cursor_ms = max(cursor_ms + len(audio), int(seg.end * 1000))

        return result

    # ── FPT AI TTS ────────────────────────────────────────────────────────────

    def _fpt(
        self, segments, api_key, voice_id, out_path, log, cancel, sync_mode="trim"
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
                "FPT AI API key required.\n" "Get FREE key at: console.fpt.ai"
            )

        voice = voice_id or "banmai"
        api.info(f"Voice: {voice} | Sync: {sync_mode} | Segments: {len(segments)}")

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
                    if retry_after.isdigit():
                        wait = max(1, int(retry_after))
                    else:
                        wait = min(2**post_attempt, 16)
                    if post_attempt < 4:
                        log(
                            f"   ⏳ Seg {idx+1}/{total} hit 429, retry in {wait}s "
                            f"({post_attempt+1}/5)"
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
            # Poll
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

        # ── Parallel execution ──
        with api.timer("FPT parallel fetch"):
            audio_list = self._run_parallel(
                segments,
                fetch_one,
                log,
                cancel,
                max_workers=1,  # Avoid aggressive burst causing 429 on free tier
                label="FPT TTS",
            )

        with api.timer("Assemble audio"):
            result = self._assemble_audio(segments, audio_list, log, sync_mode)

        result.export(out_path, format="mp3")
        ok = sum(1 for a in audio_list if a is not None)
        api.summary(ok, len(segments) - ok, len(segments), "FPT TTS")
        api.info(
            f"Saved: {Path(out_path).name} ({Path(out_path).stat().st_size/1024:.1f}KB)"
        )

    def _fpt_poll_download(self, url, api, max_wait=60):
        import requests

        api.info(f"Polling audio (max {max_wait}s)…")
        for attempt in range(max_wait):
            try:
                r = requests.get(url, timeout=10)
                if r.status_code == 200 and len(r.content) > 100:
                    if r.content[:3] == b"ID3" or r.content[:2] in (
                        b"\xff\xfb",
                        b"\xff\xf3",
                        b"\xff\xf2",
                    ):
                        api.ok(
                            f"Audio ready after {attempt+1}s | {len(r.content)/1024:.1f}KB"
                        )
                        return r.content
                    if attempt > 5:
                        api.warn(
                            f"Attempt {attempt+1}: {len(r.content)}B header={r.content[:4].hex()} not MP3"
                        )
                elif r.status_code == 404:
                    if attempt < 10:
                        pass  # Normal — file not ready yet
                    else:
                        api.warn(f"Attempt {attempt+1}: 404 still not ready")
                else:
                    api.warn(f"Attempt {attempt+1}: HTTP {r.status_code}")
            except Exception as e:
                api.warn(f"Poll {attempt+1} error: {e}")
            time.sleep(1)
        api.error(f"Polling timeout after {max_wait}s — {url}")
        return None

    # ── Zalo AI TTS ───────────────────────────────────────────────────────────

    def _zalo(
        self, segments, api_key, voice_id, out_path, log, cancel, sync_mode="trim"
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
                        wait = 2**attempt
                        log(f"   ⏳ Rate limited, waiting {wait}s…")
                        time.sleep(wait)
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
                    preview = content[:100].decode("utf-8", errors="replace")
                    raise RuntimeError(f"Zalo: unknown format: {preview[:80]}")
                tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
                tmp.write(content)
                tmp.close()
                try:
                    audio = (
                        AudioSegment.from_wav(tmp.name)
                        if suffix == ".wav"
                        else AudioSegment.from_mp3(tmp.name)
                    )
                    seg_dur_ms = int((seg.end - seg.start) * 1000)
                    audio = self._fit_audio_to_segment(audio, seg_dur_ms, sync_mode)
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

    # ── gTTS ──────────────────────────────────────────────────────────────────

    def _gtts(self, segments, lang, out_path, log, cancel, sync_mode="trim"):
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
            segments,
            fetch_one,
            log,
            cancel,
            max_workers=5,  # gTTS free, can parallelize more
            label="gTTS",
        )
        result = self._assemble_audio(segments, audio_list, log, sync_mode)
        result.export(out_path, format="mp3")
        ok = sum(1 for a in audio_list if a is not None)
        log(f"   🏁 gTTS: {ok}/{len(segments)} ok")

    # ── OpenAI TTS ────────────────────────────────────────────────────────────

    def _openai_tts(
        self, segments, lang, api_key, out_path, log, cancel, sync_mode="trim"
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
        result = self._assemble_audio(segments, audio_list, log, sync_mode)
        result.export(out_path, format="mp3")
        ok = sum(1 for a in audio_list if a is not None)
        log(f"   🏁 OpenAI TTS: {ok}/{len(segments)} ok")

    # ── ElevenLabs ────────────────────────────────────────────────────────────

    def _elevenlabs(
        self, segments, lang, api_key, voice_id, out_path, log, cancel, sync_mode="trim"
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
                seg_dur_ms = int((seg.end - seg.start) * 1000)
                audio = self._fit_audio_to_segment(audio, seg_dur_ms, sync_mode)
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

        # Sync mode
        v.addWidget(self._sep_label("⏱️  Audio Sync"))
        sw = QWidget()
        sl = QHBoxLayout(sw)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(QLabel("Sync mode:"))
        self._sync_combo = QComboBox()
        self._sync_combo.addItems(
            [
                "trim    — Speed up nếu dài quá (recommended)",
                "pad     — Speed up nếu dài + silence nếu ngắn",
                "stretch — Tự động tăng/giảm tốc độ để khớp",
                "none    — Không điều chỉnh",
            ]
        )
        self._sync_combo.setCurrentIndex(0)
        self._sync_combo.setToolTip(
            "trim:    TTS dài → tăng tốc vừa đủ, không cắt chữ (recommended)\n"
            "pad:     speed up nếu dài + thêm silence nếu ngắn\n"
            "stretch: kéo giãn/nén tốc độ đọc để khớp timestamp\n"
            "none:    giữ nguyên, không sync"
        )
        sl.addWidget(self._sync_combo)
        sl.addStretch()
        v.addWidget(sw)

        self._backend_combo.setCurrentIndex(0)
        self._on_backend_changed(0)
        return w

    def _sep_label(self, text):
        l = QLabel(text)
        l.setStyleSheet("color:#a0a8ff;font-size:11px;font-weight:600;margin-top:4px;")
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
        if idx == 0:
            backend = "all"
        else:
            mapped_idx = idx - 1
            backend = (
                list(TTS_BACKENDS.values())[mapped_idx]
                if mapped_idx < len(TTS_BACKENDS)
                else "gtts"
            )
        needs_key = backend in ("fpt", "zalo", "openai_tts", "elevenlabs")
        needs_combo = backend in ("fpt", "zalo")
        needs_el_id = backend == "elevenlabs"
        placeholders = {
            "fpt": "FPT API key — fpt.ai/tts (1M ký tự free)",
            "zalo": "Zalo AI key — zalo.ai/developers",
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
        key_text = self._backend_combo.currentText() if self._backend_combo else ""
        backend = (
            "all"
            if key_text == "All backends (batch run)"
            else TTS_BACKENDS.get(key_text, "gtts")
        )
        voice_id = ""
        if backend in ("fpt", "zalo") and self._voice_combo:
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
            "sync_mode": (
                self._sync_combo.currentText().split("—")[0].strip()
                if self._sync_combo
                else "trim"
            ),
        }
