"""
Step 6 — Add voice to video from saved Step 5 TTS audio.
"""

import json
import os
import shutil
import subprocess
import tempfile
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

MIX_MODES = {
    "TTS only (replace original)": "replace",
    "TTS + Background music (Step 4)": "bgm_only",
    "TTS + BGM + Original voice (low vol)": "full_mix",
}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv"}


class AddVoiceStep(BaseStep):
    STEP_ID = "step6_add_voice"
    LABEL = "⑥ Add Voice"
    COLOR = "#7a2d15"
    ENABLED_BY_DEFAULT = False

    def __init__(self):
        self._source_mode_combo = None
        self._tts_path_edit = None
        self._mix_group = self._mix_radios = None
        self._tts_vol_slider = self._bgm_vol_slider = self._orig_vol_slider = None

    def run(self, session, config, log, cancel):
        source_mode = config.get("source_mode", "all_cache")
        tts_source = (config.get("tts_path") or "").strip()
        mix_mode = config.get("mix_mode", "bgm_only")
        tts_vol = config.get("tts_vol", 1.0)
        bgm_vol = config.get("bgm_vol", 0.3)
        orig_vol = config.get("orig_vol", 0.1)

        tts_path, temp_files = self._resolve_tts_source(
            session, source_mode, tts_source, log
        )
        if not tts_path:
            raise RuntimeError(
                "No TTS audio found. Run Step 5 Text-to-Speech first, or provide a TTS file path."
            )

        log(f"🗣️  Add Voice source: {Path(tts_path).name} | Mix: {mix_mode}")
        log(
            f"   TTS vol: {tts_vol:.0%} | BGM vol: {bgm_vol:.0%} | Orig vol: {orig_vol:.0%}"
        )

        if cancel.is_set():
            raise CancelledError()

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

        input_media = session.latest_video()
        out_video = str(session.step6_video)
        if input_media == str(session.step3_video):
            log("🔗 Chaining: using Step 3 (subtitled) video as base")

        if self._has_video_stream(input_media):
            log("🎬 Muxing audio into video…")
            self._mux(input_media, mixed_audio, out_video, log)
        else:
            log("⚠️  Input has no video stream — exporting final audio only")
            if os.path.abspath(mixed_audio) != os.path.abspath(out_video):
                shutil.copy2(mixed_audio, out_video)

        if mixed_audio != tts_path and os.path.exists(mixed_audio):
            os.unlink(mixed_audio)
        for p in temp_files:
            if os.path.exists(p):
                os.unlink(p)

        log(f"✅ Final output → {Path(out_video).name}")
        return out_video

    def _resolve_tts_source(self, session, source_mode: str, tts_source: str, log):
        temp_files = []

        if source_mode == "single":
            path = self._resolve_single_tts_path(session, tts_source)
            return path, temp_files

        manifests = self._resolve_manifests(session, source_mode, tts_source)
        if not manifests:
            path = self._resolve_single_tts_path(session, tts_source)
            return path, temp_files

        segments = session.load_translated()
        composed_path = self._compose_timeline_audio(session, manifests, segments, log)
        if composed_path:
            temp_files.append(composed_path)
        return composed_path, temp_files

    def _resolve_single_tts_path(self, session, tts_source: str) -> str:
        if tts_source and Path(tts_source).exists():
            return tts_source

        if Path(session.step5_tts).exists():
            return str(session.step5_tts)

        library_dir = session.step5_tts_library_dir
        if library_dir.exists():
            files = sorted(library_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
            if files:
                return str(files[-1])

        # Fallback for old sessions.
        cache_dir = session.step5_tts_session_dir
        if cache_dir.exists():
            files = sorted(cache_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
            if files:
                return str(files[-1])
        return ""

    def _resolve_manifests(self, session, source_mode: str, tts_source: str):
        manifests = []
        library_dir = session.step5_tts_library_dir
        if library_dir.exists():
            manifests.extend(library_dir.glob("*.json"))
        # Fallback for old session cache layout.
        old_cache_dir = session.step5_tts_session_dir
        if old_cache_dir.exists():
            manifests.extend(old_cache_dir.glob("*.json"))

        if not manifests:
            return []

        manifests = sorted(manifests, key=lambda p: p.stat().st_mtime)

        if source_mode == "latest":
            return manifests[-1:] if manifests else []

        if source_mode == "custom":
            result = []
            for line in tts_source.replace(";", "\n").splitlines():
                raw = line.strip()
                if not raw:
                    continue
                p = Path(raw)
                if p.exists() and p.suffix.lower() == ".json":
                    result.append(p)
                elif p.exists() and p.suffix.lower() == ".mp3":
                    mp3_stem = p.stem
                    candidate = p.with_name(f"{mp3_stem}.json")
                    if candidate.exists():
                        result.append(candidate)
            return result

        return manifests

    def _compose_timeline_audio(self, session, manifests, segments, log):
        import math

        try:
            from pydub import AudioSegment
        except ImportError as e:
            raise RuntimeError("Run: pip install pydub audioop-lts") from e

        loaded = []
        for mf in manifests:
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
                audio_name = data.get("audio_file", "")
                audio_path = mf.with_name(audio_name) if audio_name else None
                if not audio_path or not audio_path.exists():
                    continue
                loaded.append(
                    {
                        "manifest": data,
                        "audio": AudioSegment.from_mp3(str(audio_path)),
                        "name": mf.stem,
                    }
                )
            except Exception:
                continue

        if not loaded:
            return ""

        log(f"🧩 Timeline compose from {len(loaded)} Step 5 source(s)")

        result = AudioSegment.silent(duration=0)
        cursor_ms = 0
        chosen = 0
        silent = 0
        mixed = 0

        for idx, seg in enumerate(segments):
            start_ms = int(seg.start * 1000)
            end_ms = int(seg.end * 1000)
            seg_ms = max(0, end_ms - start_ms)

            if start_ms > cursor_ms:
                result += AudioSegment.silent(duration=start_ms - cursor_ms)
                cursor_ms = start_ms

            parts = []
            for src in loaded:
                audio = src["audio"]
                if start_ms >= len(audio):
                    continue
                slice_end = min(end_ms, len(audio))
                part = audio[start_ms:slice_end]
                if len(part) < max(80, int(seg_ms * 0.25)):
                    continue
                if len(part) > seg_ms > 0:
                    part = part[:seg_ms]
                parts.append(part)

            if not parts:
                result += AudioSegment.silent(duration=seg_ms)
                cursor_ms = max(cursor_ms + seg_ms, end_ms)
                silent += 1
                continue

            if len(parts) == 1:
                clip = parts[0]
            else:
                # Normalize level before overlay to avoid clipping when combining many APIs.
                gain_down = min(12.0, 20.0 * math.log10(len(parts)))
                clip = parts[0].apply_gain(-gain_down)
                for part in parts[1:]:
                    clip = clip.overlay(part.apply_gain(-gain_down))
                mixed += 1

            result += clip
            cursor_ms = max(cursor_ms + len(clip), end_ms)
            chosen += 1

            if (idx + 1) % 10 == 0 or idx + 1 == len(segments):
                log(f"   [{idx+1}/{len(segments)}] compose")

        out = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        out.close()
        result.export(out.name, format="mp3")
        log(
            f"✅ Composed voice track: {chosen} segments, {mixed} mixed segments, {silent} silence fallback"
        )
        return out.name

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
        )
        return out.name if r.returncode == 0 else audio_path

    def _mux(self, video_path, audio_path, out_path, log):
        in_place = os.path.abspath(video_path) == os.path.abspath(out_path)
        actual_out = out_path
        if in_place:
            fd, tmp_path = tempfile.mkstemp(
                prefix="step6_mux_",
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
            probe = self._run_cmd(["ffmpeg", "-hide_banner", "-i", media_path])
            txt = self._tail_output(probe, max_chars=4000).lower()
            if "video:" in txt:
                return True
            if "audio:" in txt:
                return False
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

    def build_config_widget(self, parent=None):
        w = QWidget(parent)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        row = QHBoxLayout()
        row.addWidget(QLabel("Source mode:"))
        self._source_mode_combo = QComboBox()
        self._source_mode_combo.addItems(
            [
                "All Step 5 library manifests",
                "Latest Step 5 manifest only",
                "Single audio file (legacy)",
                "Custom manifest/audio list",
            ]
        )
        row.addWidget(self._source_mode_combo)
        row.addStretch()
        v.addLayout(row)

        v.addWidget(
            QLabel(
                "Optional path/list (for custom or single mode; split by newline or ;):"
            )
        )
        self._tts_path_edit = QLineEdit()
        self._tts_path_edit.setPlaceholderText(
            "C:/.../_tts_library/fpt_xxx.json ; C:/.../step5_tts.mp3"
        )
        v.addWidget(self._tts_path_edit)

        v.addWidget(self._sep_label("🎚️  Audio Mix Mode"))
        mw = QWidget()
        mv = QVBoxLayout(mw)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(3)
        self._mix_group = QButtonGroup(w)
        self._mix_radios = {}
        for label, key in MIX_MODES.items():
            rb = QRadioButton(label)
            self._mix_group.addButton(rb)
            self._mix_radios[key] = rb
            mv.addWidget(rb)
        self._mix_radios["bgm_only"].setChecked(True)
        v.addWidget(mw)

        v.addWidget(self._sep_label("🔊  Volume"))
        self._tts_vol_slider = self._vol_row(v, "TTS voice:", 100)
        self._bgm_vol_slider = self._vol_row(v, "Background music:", 30)
        self._orig_vol_slider = self._vol_row(v, "Original voice:", 10)
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

    def collect_config(self):
        mode_text = (
            self._source_mode_combo.currentText() if self._source_mode_combo else ""
        )
        source_mode = {
            "All Step 5 library manifests": "all_cache",
            "Latest Step 5 manifest only": "latest",
            "Single audio file (legacy)": "single",
            "Custom manifest/audio list": "custom",
        }.get(mode_text, "all_cache")

        mix_mode = "bgm_only"
        for key, rb in self._mix_radios.items():
            if rb.isChecked():
                mix_mode = key
                break

        return {
            "source_mode": source_mode,
            "tts_path": self._tts_path_edit.text().strip() or None,
            "mix_mode": mix_mode,
            "tts_vol": self._tts_vol_slider.value() / 100.0,
            "bgm_vol": self._bgm_vol_slider.value() / 100.0,
            "orig_vol": self._orig_vol_slider.value() / 100.0,
        }
