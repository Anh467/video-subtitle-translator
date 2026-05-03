"""Step 4 — stem separation (Demucs)."""

import shutil
import subprocess
from pathlib import Path

from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from core.ffmpeg_utils import ffmpeg_executable
from core.pipeline.base import BaseStep
from core.pipeline.step4_separate.constants import DEMUCS_MODELS, STEM_MODES

class SeparateStep(BaseStep):
    STEP_ID = "step4_separate"
    LABEL = "④ Separate Voice"
    COLOR = "#1a3a5a"
    ENABLED_BY_DEFAULT = False

    def __init__(self):
        self._model_combo = None
        self._stem_combo = None

    def run(self, session, config, log, cancel):
        source = session.source_file
        model = config["model"]
        stems = config.get("stems", "2")  # "2" or "4"
        out_dir = session.folder

        log(f"🎵 Separating audio using Demucs ({model}) — {stems}-stem mode…")
        log("   This may take a few minutes on first run (downloads model)…")

        try:
            import demucs  # noqa
        except ImportError:
            raise RuntimeError("Demucs not installed.\nRun: pip install demucs")

        if cancel.is_set():
            from core.pipeline.base import CancelledError

            raise CancelledError()

        # Convert source to WAV (torchaudio may need extra codecs for some containers).
        import shutil as _shutil
        import tempfile

        tmp_dir = Path(tempfile.mkdtemp())
        tmp_input = tmp_dir / "demucs_input.wav"
        log("   Converting input to WAV for Demucs compatibility…")
        wav_cmd = [
            ffmpeg_executable(),
            "-y",
            "-i",
            str(source),
            "-ac",
            "2",
            "-ar",
            "44100",
            "-vn",
            str(tmp_input),
        ]
        wav_proc = subprocess.run(
            wav_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if wav_proc.returncode != 0 or not tmp_input.exists():
            # Fallback: plain copy with safe filename
            tmp_input = tmp_dir / f"demucs_input{Path(source).suffix}"
            _shutil.copy2(source, tmp_input)
            log(f"   WAV conversion failed, using original: {tmp_input.name}")
        else:
            log(f"   Input ready: {tmp_input.name}")

        tmp_out = out_dir / "demucs_tmp"
        tmp_out.mkdir(exist_ok=True)

        if stems == "2":
            cmd = [
                "python",
                "-m",
                "demucs",
                "--name",
                model,
                "--out",
                str(tmp_out),
                "--mp3",
                "--two-stems",
                "vocals",
                str(tmp_input),
            ]
        else:
            # 4-stem: no --two-stems flag
            cmd = [
                "python",
                "-m",
                "demucs",
                "--name",
                model,
                "--out",
                str(tmp_out),
                "--mp3",
                str(tmp_input),
            ]

        log(f"   $ {' '.join(cmd)}")

        import os as _os

        env = _os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )

        for line in proc.stdout:
            line = line.strip()
            if line:
                log(f"   {line}")
            if cancel.is_set():
                proc.terminate()
                from core.pipeline.base import CancelledError

                raise CancelledError()

        proc.wait()

        # Cleanup temp input dir
        try:
            _shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

        if proc.returncode != 0:
            raise RuntimeError("Demucs failed — check log above.")

        # Locate output folder
        tmp_stem = "demucs_input"
        tmp_model_dir = tmp_out / model / tmp_stem
        if not tmp_model_dir.exists():
            candidates = (
                list((tmp_out / model).iterdir()) if (tmp_out / model).exists() else []
            )
            if candidates:
                tmp_model_dir = candidates[0]
                log(f"   Found output dir: {tmp_model_dir.name}")

        result = {}

        if stems == "2":
            # ── 2-stem outputs ──────────────────────────────────────────────
            self._move(
                tmp_model_dir / "vocals.mp3",
                session.step4_vocals,
                "Vocals",
                log,
                result,
            )
            self._move(
                tmp_model_dir / "no_vocals.mp3",
                session.step4_background,
                "Background",
                log,
                result,
            )

        else:
            # ── 4-stem outputs ──────────────────────────────────────────────
            self._move(
                tmp_model_dir / "vocals.mp3",
                session.step4_vocals,
                "Vocals",
                log,
                result,
            )
            self._move(
                tmp_model_dir / "drums.mp3", session.step4_drums, "Drums", log, result
            )
            self._move(
                tmp_model_dir / "bass.mp3", session.step4_bass, "Bass", log, result
            )
            self._move(
                tmp_model_dir / "other.mp3", session.step4_other, "Other", log, result
            )

            # Mix drums + bass + other → background.mp3 for Step 5 compatibility
            log("🎚️  Mixing stems into background track…")
            self._mix_background(
                parts=[
                    str(session.step4_drums) if session.step4_drums.exists() else None,
                    str(session.step4_bass) if session.step4_bass.exists() else None,
                    str(session.step4_other) if session.step4_other.exists() else None,
                ],
                out=str(session.step4_background),
                log=log,
            )
            result["background"] = str(session.step4_background)
            log(f"✅ Background → {session.step4_background.name}")

        shutil.rmtree(str(tmp_out), ignore_errors=True)
        return result

    def _move(self, src: Path, dst: Path, label: str, log, result: dict):
        if src.exists():
            shutil.move(str(src), str(dst))
            log(f"✅ {label:<12} → {dst.name}")
            result[label.lower()] = str(dst)
        else:
            log(f"⚠️  {label}: not found in {src.parent}")

    def _mix_background(self, parts, out, log):
        """Mix multiple audio stems into one background track using ffmpeg."""
        valid = [p for p in parts if p and Path(p).exists()]
        if not valid:
            log("⚠️  No background stems to mix")
            return

        if len(valid) == 1:
            shutil.copy2(valid[0], out)
            return

        inputs = []
        for p in valid:
            inputs += ["-i", p]

        mix_inputs = "".join(f"[{i}:a]" for i in range(len(valid)))
        filter_complex = (
            f"{mix_inputs}amix=inputs={len(valid)}:"
            f"duration=longest:normalize=0[out]"
        )

        r = subprocess.run(
            [ffmpeg_executable(), "-y"]
            + inputs
            + [
                "-filter_complex",
                filter_complex,
                "-map",
                "[out]",
                "-c:a",
                "mp3",
                "-b:a",
                "192k",
                out,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode != 0:
            log(f"⚠️  Background mix failed: {r.stderr[-500:]}")

    def build_config_widget(self, parent=None):
        w = QWidget(parent)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        # Model
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Model:"))
        self._model_combo = QComboBox()
        self._model_combo.addItems(DEMUCS_MODELS.keys())
        r1.addWidget(self._model_combo)
        r1.addStretch()
        v.addLayout(r1)

        # Stem mode
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Stems:"))
        self._stem_combo = QComboBox()
        self._stem_combo.addItems(STEM_MODES.keys())
        self._stem_combo.setCurrentIndex(0)
        self._stem_combo.setToolTip(
            "2-stem: vocals + background (nhanh hơn)\n"
            "4-stem: vocals + drums + bass + other\n"
            "  → drums chứa tiếng gõ, lục đục, percussion\n"
            "  → other chứa nhạc nền còn lại\n"
            "  → tự động mix thành background cho Step 5"
        )
        r2.addWidget(self._stem_combo)
        r2.addStretch()
        v.addLayout(r2)

        return w

    def apply_config(self, config: dict) -> None:
        if not config:
            return
        _MODEL_BY_VAL = {v: k for k, v in DEMUCS_MODELS.items()}
        _STEM_BY_VAL = {v: k for k, v in STEM_MODES.items()}
        if self._model_combo and config.get("model"):
            label = _MODEL_BY_VAL.get(config["model"], "htdemucs (recommended)")
            self._model_combo.setCurrentText(label)
        if self._stem_combo and config.get("stems"):
            label = _STEM_BY_VAL.get(config["stems"], "2-stem: vocals + background")
            self._stem_combo.setCurrentText(label)

    def collect_config(self):
        model_key = (
            self._model_combo.currentText()
            if self._model_combo
            else "htdemucs (recommended)"
        )
        stem_key = (
            self._stem_combo.currentText()
            if self._stem_combo
            else "2-stem: vocals + background"
        )
        return {
            "model": DEMUCS_MODELS.get(model_key, "htdemucs"),
            "stems": STEM_MODES.get(stem_key, "2"),
        }
