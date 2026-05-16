"""Step 6 — mux TTS / BGM into final video."""

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.ffmpeg_utils import ffmpeg_executable, ffprobe_executable
from core.pipeline.base import BaseStep, CancelledError
from core.pipeline.demucs_invoke import run_demucs_in_process
from core.pipeline.step6_add_voice.constants import (
    BACKEND_LABELS,
    MIX_MODES,
    TTS_VOLUME_BOOST,
    VIDEO_EXTS,
)
from core.pipeline.tts_assets import (
    compose_timeline_audio,
    resolve_manifests,
    resolve_single_tts_path,
)


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
        self._sync_combo = None

    def run(self, session, config, log, cancel):
        source_mode = config.get("source_mode", "latest")
        tts_source = (config.get("tts_path") or "").strip()
        manifest_pick = (config.get("manifest_pick") or "").strip() or None
        sync_mode = config.get("sync_mode", "trim")
        mix_mode = config.get("mix_mode", "bgm_only")
        tts_vol = self._effective_tts_volume(config.get("tts_vol", 1.0))
        bgm_vol = config.get("bgm_vol", 0.3)
        orig_vol = config.get("orig_vol", 0.1)

        tts_path, temp_files = self._resolve_tts_source(
            session,
            source_mode,
            tts_source,
            sync_mode,
            log,
            manifest_pick=manifest_pick,
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

        mixed_audio, temp_mix_files = self._mix_audio(
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
        has_video = self._has_video_stream(input_media)
        voice_tag = self._detect_voice_tag(
            session=session,
            source_mode=source_mode,
            tts_source=tts_source,
            tts_path=tts_path,
            manifest_pick=manifest_pick,
        )
        if has_video:
            ext = Path(input_media).suffix.lower() or ".mp4"
            if ext not in VIDEO_EXTS:
                ext = ".mp4"
        else:
            ext = ".mp3"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_video = str(session.result_dir / f"result_output_{voice_tag}_{ts}{ext}")
        Path(out_video).parent.mkdir(parents=True, exist_ok=True)
        if input_media == str(session.step3_video):
            log("🔗 Chaining: using Step 3 (subtitled) video as base")

        if has_video:
            log("🎬 Muxing audio into video…")
            self._mux(input_media, mixed_audio, out_video, log)
        else:
            log("⚠️  Input has no video stream — exporting final audio only")
            if os.path.abspath(mixed_audio) != os.path.abspath(out_video):
                shutil.copy2(mixed_audio, out_video)

        if mixed_audio != tts_path and os.path.exists(mixed_audio):
            os.unlink(mixed_audio)
        for p in temp_mix_files:
            if p and os.path.exists(p):
                if os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    os.unlink(p)
        for p in temp_files:
            if os.path.exists(p):
                os.unlink(p)

        log(f"✅ Final output → {Path(out_video).name}")
        return out_video

    @staticmethod
    def _safe_slug(text: str) -> str:
        value = (text or "").strip().lower()
        value = re.sub(r"[^a-z0-9._-]+", "_", value)
        value = value.strip("._-")
        return value[:80] or "default"

    def _detect_voice_tag(
        self,
        session,
        source_mode: str,
        tts_source: str,
        tts_path: str,
        manifest_pick: str | None = None,
    ) -> str:
        manifests = resolve_manifests(
            session, source_mode, tts_source, manifest_pick=manifest_pick
        )
        if manifests:
            mf = manifests[-1]
            try:
                data = json.loads(Path(mf).read_text(encoding="utf-8"))
                backend = str(data.get("backend", "") or "").strip()
                voice = str(data.get("voice_id", "") or "").strip()
                parts = [p for p in (backend, voice) if p]
                if parts:
                    return self._safe_slug("_".join(parts))
            except Exception:
                pass
            return self._safe_slug(Path(mf).stem)

        return self._safe_slug(Path(tts_path).stem)

    def _resolve_tts_source(
        self,
        session,
        source_mode: str,
        tts_source: str,
        sync_mode: str,
        log,
        manifest_pick: str | None = None,
    ):
        temp_files = []

        if source_mode == "single":
            path = resolve_single_tts_path(session, tts_source)
            return path, temp_files

        manifests = resolve_manifests(
            session, source_mode, tts_source, manifest_pick=manifest_pick
        )
        if not manifests:
            path = resolve_single_tts_path(session, tts_source)
            return path, temp_files

        segments = session.load_translated()
        composed_path = compose_timeline_audio(
            manifests, segments, log, sync_mode=sync_mode
        )
        if composed_path:
            temp_files.append(composed_path)
        return composed_path, temp_files

    @staticmethod
    def _effective_tts_volume(slider_value: float) -> float:
        """Map UI 0–200% slider to ffmpeg gain (with headroom for quiet TTS)."""
        return min(2.0, max(0.0, float(slider_value)) * TTS_VOLUME_BOOST)

    def _mix_audio(self, session, tts_path, mix_mode, tts_vol, bgm_vol, orig_vol, log):
        temp_files = []
        has_bgm = session.step4_done and Path(session.step4_background).exists()

        if mix_mode == "replace":
            if abs(tts_vol - 1.0) < 0.02:
                return tts_path, temp_files
            out = self._apply_volume(tts_path, tts_vol)
            if out != tts_path:
                temp_files.append(out)
            return out, temp_files

        elif mix_mode == "bgm_only":
            bgm_path = None
            if has_bgm:
                bgm_path = str(session.step4_background)
            else:
                bgm_path, extra_temp = self._ensure_background_track(session, log)
                temp_files.extend(extra_temp)

            if not bgm_path:
                log("⚠️  Could not isolate background — fallback to TTS only")
                return tts_path, temp_files

            log("🎵 Mixing TTS + background music…")
            log(f"   TTS: {tts_vol:.0%}  |  BGM: {bgm_vol:.0%}")
            mixed = self._ffmpeg_mix(
                [
                    (tts_path, tts_vol),
                    (bgm_path, bgm_vol),
                ],
                log,
            )
            temp_files.append(mixed)
            return mixed, temp_files

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
                    ffmpeg_executable(),
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
            temp_files.append(result)
            return result, temp_files

        return tts_path, temp_files

    def _ensure_background_track(self, session, log):
        """Return a background (non-vocal) track path for Step 6 mixing.

        Priority:
        1) Use Step 4 background if exists.
        2) Auto-run Demucs 2-stem in Step 6 to get no_vocals.
        """
        if session.step4_done and Path(session.step4_background).exists():
            return str(session.step4_background), []

        try:
            import demucs  # noqa: F401
        except Exception:
            log("⚠️  Step 4 background missing and Demucs not installed")
            log("   Install with: pip install demucs")
            return None, []

        log(
            "🎛️  Step 4 background missing — auto isolating non-vocal track (Demucs 2-stem)…"
        )
        tmp_root = Path(tempfile.mkdtemp(prefix="step6_demucs_"))
        tmp_input = tmp_root / "step6_input.wav"

        conv = subprocess.run(
            [
                ffmpeg_executable(),
                "-y",
                "-i",
                str(session.source_file),
                "-ac",
                "2",
                "-ar",
                "44100",
                "-vn",
                str(tmp_input),
            ],
            capture_output=True,
        )
        if conv.returncode != 0 or not tmp_input.exists():
            log("⚠️  Could not convert source to WAV for Demucs")
            shutil.rmtree(tmp_root, ignore_errors=True)
            return None, []

        demucs_out = tmp_root / "demucs_out"
        demucs_opts = [
            "--name",
            "htdemucs",
            "--out",
            str(demucs_out),
            "--mp3",
            "--two-stems",
            "vocals",
            str(tmp_input),
        ]
        log("   Running Demucs (htdemucs, two-stems=vocals)…")
        try:
            run_demucs_in_process(demucs_opts, log)
        except Exception as e:
            log("⚠️  Demucs auto-isolation failed")
            log(f"   {e}")
            shutil.rmtree(tmp_root, ignore_errors=True)
            return None, []

        model_dir = demucs_out / "htdemucs"
        stem_dir = model_dir / "step6_input"
        if not stem_dir.exists() and model_dir.exists():
            cands = [p for p in model_dir.iterdir() if p.is_dir()]
            if cands:
                stem_dir = cands[0]

        bgm = stem_dir / "no_vocals.mp3"
        if not bgm.exists():
            log("⚠️  Demucs finished but no non-vocal track found")
            shutil.rmtree(tmp_root, ignore_errors=True)
            return None, []

        log("✅ Auto background isolated from source (voice reduced/removed)")
        return str(bgm), [str(tmp_root)]

    def _ffmpeg_mix(self, tracks, log):
        out = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        out.close()
        inputs, filter_parts = [], []
        for i, (path, vol) in enumerate(tracks):
            inputs += ["-i", path]
            filter_parts.append(f"[{i}:a]volume={vol:.3f}[a{i}]")
        mix_inputs = "".join(f"[a{i}]" for i in range(len(tracks)))
        filter_parts.append(
            f"{mix_inputs}amix=inputs={len(tracks)}:duration=first:"
            f"dropout_transition=2:normalize=0[out]"
        )
        cmd = (
            [ffmpeg_executable(), "-y"]
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
                ffmpeg_executable(),
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
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        log("   Keeping video quality: stream copy mode (-c:v copy, no re-encode)")
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
            ffmpeg_executable(),
            "-y",
            "-i",
            video_path,
            "-i",
            audio_path,
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
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
            ffprobe_executable(),
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
            probe = self._run_cmd(
                [ffmpeg_executable(), "-hide_banner", "-i", media_path]
            )
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
        # ── TTS manifest selector (replaces Source mode + path input) ────────
        r_manifest = QHBoxLayout()
        r_manifest.addWidget(QLabel("TTS voice:"))
        self._manifest_combo = QComboBox()
        self._manifest_combo.setToolTip(
            "Chọn TTS run để ghép vào video.\n"
            "Danh sách tự động load từ step5_tts_assets/.\n"
            "Mới nhất ở trên cùng."
        )
        self._manifest_combo.setMinimumWidth(220)
        self._manifest_combo.addItem(
            "(No TTS assets yet — run Step 5 first)", userData=None
        )
        r_manifest.addWidget(self._manifest_combo)

        btn_refresh = QPushButton("↻")
        btn_refresh.setFixedSize(28, 26)
        btn_refresh.setToolTip("Reload manifest list from disk")
        btn_refresh.setStyleSheet(
            "QPushButton{background:#1a2a3a;color:#60aaff;border:1px solid #2a4a6a;"
            "border-radius:4px;font-size:13px;}"
            "QPushButton:hover{background:#2a4a6a;}"
        )
        btn_refresh.clicked.connect(self._refresh_manifests)
        r_manifest.addWidget(btn_refresh)
        r_manifest.addStretch()
        v.addLayout(r_manifest)

        row = QHBoxLayout()
        row.addWidget(QLabel("Source mode:"))
        self._source_mode_combo = QComboBox()
        self._source_mode_combo.addItems(
            [
                "Latest Step 5 manifest only",
                "All Step 5 session assets",
                "Single audio file (legacy)",
                "Custom manifest/audio list",
            ]
        )
        self._source_mode_combo.setCurrentIndex(0)
        self._source_mode_combo.setToolTip(
            "Latest: one TTS run only (dropdown defaults to newest). "
            "All: merge every manifest in step5_tts_assets/ (can blend multiple runs)."
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
            "C:/.../session_name/step5_tts_assets/fpt_xxx.json ; C:/.../step5_tts.mp3"
        )
        v.addWidget(self._tts_path_edit)

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
        self._tts_vol_slider = self._vol_row(v, "TTS voice:", 150, max_pct=200)
        self._bgm_vol_slider = self._vol_row(v, "Background music:", 145)
        self._orig_vol_slider = self._vol_row(v, "Original voice:", 31)
        return w

    def _sep_label(self, text):
        l = QLabel(text)
        l.setStyleSheet("color:#a0a8ff;font-size:11px;font-weight:600;margin-top:4px;")
        return l

    def _vol_row(self, parent_layout, label, default_pct, max_pct=150):
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setFixedWidth(130)
        row.addWidget(lbl)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, max_pct)
        slider.setValue(default_pct)
        row.addWidget(slider)
        val_lbl = QLabel(f"{default_pct}%")
        val_lbl.setFixedWidth(38)
        val_lbl.setStyleSheet("color:#888;font-size:11px;")
        slider.valueChanged.connect(lambda v, l=val_lbl: l.setText(f"{v}%"))
        row.addWidget(val_lbl)
        parent_layout.addLayout(row)
        return slider

    def _refresh_manifests(self):
        """Reload manifests from disk — called by refresh button."""
        if (
            hasattr(self, "_current_session_for_manifests")
            and self._current_session_for_manifests
        ):
            self.populate_manifest_picker(self._current_session_for_manifests)

    def populate_manifest_picker(self, session):
        """
        Scan step5_tts_assets/ and populate the manifest dropdown.
        Called by MainWindow on session load and after Step 5 completes.
        Multi session: not called — _latest_manifest_path() used directly.
        """
        self._current_session_for_manifests = session
        if not self._manifest_combo:
            return
        self._manifest_combo.blockSignals(True)
        self._manifest_combo.clear()
        if session is None:
            self._manifest_combo.addItem("(No session loaded)", userData=None)
            self._manifest_combo.blockSignals(False)
            return
        assets_dir = session.step5_tts_assets_dir
        if not assets_dir.exists() or not any(assets_dir.glob("*.json")):
            self._manifest_combo.addItem(
                "(No TTS assets yet — run Step 5 first)", userData=None
            )
            self._manifest_combo.blockSignals(False)
            return
        manifests = sorted(
            assets_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,  # newest first
        )
        for m in manifests:
            try:
                mtime = m.stat().st_mtime
                dt = datetime.fromtimestamp(mtime).strftime("%m/%d %H:%M")

                data = json.loads(m.read_text(encoding="utf-8"))
                backend_key = (data.get("backend") or "").strip().lower()
                backend_name = BACKEND_LABELS.get(backend_key, backend_key or "TTS")

                voice_id = (data.get("voice_id") or "").strip()
                if not voice_id:
                    voice_id = "default"

                lang = (data.get("lang") or "").strip()
                lang_part = f" | {lang}" if lang else ""
                label = f"{backend_name} | Voice: {voice_id}{lang_part}  [{dt}]"
            except Exception:
                label = m.stem[:50]
            self._manifest_combo.addItem(label, userData=str(m))
        self._manifest_combo.blockSignals(False)
        if self._manifest_combo.count() > 0 and self._manifest_combo.itemData(0):
            self._manifest_combo.setCurrentIndex(0)

    def apply_config(self, config: dict) -> None:
        if not config:
            return
        _MODE_LABEL = {
            "all_cache": "All Step 5 session assets",
            "latest": "Latest Step 5 manifest only",
            "single": "Single audio file (legacy)",
            "custom": "Custom manifest/audio list",
        }
        if self._source_mode_combo and config.get("source_mode"):
            self._source_mode_combo.setCurrentText(
                _MODE_LABEL.get(config["source_mode"], "Latest Step 5 manifest only")
            )
        if self._sync_combo and config.get("sync_mode"):
            # match prefix of combo items
            sm = config["sync_mode"]
            for i in range(self._sync_combo.count()):
                if self._sync_combo.itemText(i).split("—")[0].strip() == sm:
                    self._sync_combo.setCurrentIndex(i)
                    break
        if (
            self._mix_radios
            and config.get("mix_mode")
            and config["mix_mode"] in self._mix_radios
        ):
            self._mix_radios[config["mix_mode"]].setChecked(True)
        if self._tts_vol_slider and config.get("tts_vol") is not None:
            self._tts_vol_slider.setValue(int(round(config["tts_vol"] * 100)))
        if self._bgm_vol_slider and config.get("bgm_vol") is not None:
            self._bgm_vol_slider.setValue(int(round(config["bgm_vol"] * 100)))
        if self._orig_vol_slider and config.get("orig_vol") is not None:
            self._orig_vol_slider.setValue(int(round(config["orig_vol"] * 100)))
        pick = config.get("manifest_pick")
        if self._manifest_combo and pick:
            want = str(pick)
            for i in range(self._manifest_combo.count()):
                if str(self._manifest_combo.itemData(i) or "") == want:
                    self._manifest_combo.setCurrentIndex(i)
                    break

    def collect_config(self):
        mode_text = (
            self._source_mode_combo.currentText() if self._source_mode_combo else ""
        )
        source_mode = {
            "All Step 5 session assets": "all_cache",
            "Latest Step 5 manifest only": "latest",
            "Single audio file (legacy)": "single",
            "Custom manifest/audio list": "custom",
        }.get(mode_text, "latest")

        pick = None
        if self._manifest_combo:
            d = self._manifest_combo.currentData()
            if d:
                pick = str(d)

        mix_mode = "bgm_only"
        for key, rb in self._mix_radios.items():
            if rb.isChecked():
                mix_mode = key
                break

        return {
            "source_mode": source_mode,
            "manifest_pick": pick,
            "tts_path": self._tts_path_edit.text().strip() or None,
            "sync_mode": (
                self._sync_combo.currentText().split("—")[0].strip()
                if self._sync_combo
                else "trim"
            ),
            "mix_mode": mix_mode,
            "tts_vol": self._tts_vol_slider.value() / 100.0,
            "bgm_vol": self._bgm_vol_slider.value() / 100.0,
            "orig_vol": self._orig_vol_slider.value() / 100.0,
        }
