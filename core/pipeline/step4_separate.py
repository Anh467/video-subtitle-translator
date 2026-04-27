"""
Step 4 — Separate audio stems using Demucs (Meta, free, local).

Modes:
  2-stem: vocals + background (no_vocals)
  4-stem: vocals + drums + bass + other
          → drums chứa: trống, percussion, tiếng gõ, lục đục
          → other chứa: nhạc nền còn lại

Output files:
  step4_vocals.mp3      — giọng người
  step4_background.mp3  — toàn bộ nhạc nền (2-stem) hoặc tổng hợp (4-stem)
  step4_drums.mp3       — trống + percussion + tiếng động (4-stem only)
  step4_bass.mp3        — bass (4-stem only)
  step4_other.mp3       — nhạc nền còn lại (4-stem only)
"""

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

from core.pipeline.base import BaseStep

DEMUCS_MODELS = {
    "htdemucs (recommended)": "htdemucs",
    "htdemucs_ft (fine-tuned)": "htdemucs_ft",
    "mdx_extra (MDX Net)": "mdx_extra",
}

STEM_MODES = {
    "2-stem: vocals + background": "2",
    "4-stem: vocals + drums + bass + other": "4",
}


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

        # Copy source to ASCII temp name to avoid UnicodeEncodeError on Windows
        import shutil as _shutil
        import tempfile

        ext = Path(source).suffix
        tmp_input = Path(tempfile.mkdtemp()) / f"demucs_input{ext}"
        _shutil.copy2(source, tmp_input)
        log(f"   Input copied to safe path: {tmp_input.name}")

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

        # Cleanup temp input
        try:
            tmp_input.unlink(missing_ok=True)
            tmp_input.parent.rmdir()
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
            ["ffmpeg", "-y"]
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
