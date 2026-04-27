"""
Step 4 — Separate vocals from background music using Demucs (Meta, free, local).

Output:
  step4_vocals.wav       — human voice only
  step4_background.wav   — music / background only
"""

import shutil
import subprocess
from pathlib import Path

from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

from core.pipeline.base import BaseStep

# Demucs model options
DEMUCS_MODELS = {
    "htdemucs (recommended)": "htdemucs",
    "htdemucs_ft (fine-tuned)": "htdemucs_ft",
    "mdx_extra (MDX Net)": "mdx_extra",
}


class SeparateStep(BaseStep):
    STEP_ID = "step4_separate"
    LABEL = "④ Separate Voice"
    COLOR = "#1a3a5a"
    ENABLED_BY_DEFAULT = False

    def __init__(self):
        self._model_combo = None

    def run(self, session, config, log, cancel):
        source = session.source_file
        model = config["model"]
        out_dir = session.folder

        log(f"🎵 Separating vocals using Demucs ({model})…")
        log("   This may take a few minutes on first run (downloads model)…")

        try:
            import demucs  # noqa
        except ImportError:
            raise RuntimeError("Demucs not installed.\nRun: pip install demucs")

        if cancel.is_set():
            from core.pipeline.base import CancelledError

            raise CancelledError()

        # ── Copy source to a safe ASCII filename to avoid UnicodeEncodeError
        # on Windows when Demucs prints the path to console (cp1252/cp1258)
        import shutil as _shutil
        import tempfile

        ext = Path(source).suffix
        tmp_input = Path(tempfile.mkdtemp()) / f"demucs_input{ext}"
        _shutil.copy2(source, tmp_input)
        log(f"   Input copied to safe path: {tmp_input.name}")

        tmp_out = out_dir / "demucs_tmp"
        tmp_out.mkdir(exist_ok=True)

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
        log(f"   $ {' '.join(cmd)}")

        # Force UTF-8 output on Windows via PYTHONUTF8 env var
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
            tmp_input.parent.rmdir() if tmp_input.exists() else None
            tmp_input.unlink(missing_ok=True)
        except Exception:
            pass

        if proc.returncode != 0:
            raise RuntimeError("Demucs failed — check log above.")

        # Move outputs to session root with clean names
        # Demucs uses the stem of the input file as subfolder name
        tmp_stem = tmp_input.stem  # "demucs_input"
        tmp_model_dir = tmp_out / model / tmp_stem

        # Fallback: search for any subfolder if exact name not found
        if not tmp_model_dir.exists():
            candidates = (
                list((tmp_out / model).iterdir()) if (tmp_out / model).exists() else []
            )
            if candidates:
                tmp_model_dir = candidates[0]
                log(f"   Found output dir: {tmp_model_dir.name}")

        vocals_src = tmp_model_dir / "vocals.mp3"
        bgm_src = tmp_model_dir / "no_vocals.mp3"
        vocals_dst = session.step4_vocals
        bgm_dst = session.step4_background

        if vocals_src.exists():
            shutil.move(str(vocals_src), str(vocals_dst))
            log(f"✅ Vocals     → {vocals_dst.name}")
        else:
            log(f"⚠️  vocals.mp3 not found in {tmp_model_dir}")

        if bgm_src.exists():
            shutil.move(str(bgm_src), str(bgm_dst))
            log(f"✅ Background → {bgm_dst.name}")
        else:
            log(f"⚠️  no_vocals.mp3 not found in {tmp_model_dir}")

        shutil.rmtree(str(tmp_out), ignore_errors=True)
        return {"vocals": str(vocals_dst), "background": str(bgm_dst)}

    def build_config_widget(self, parent=None):
        w = QWidget(parent)
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel("Model:"))
        self._model_combo = QComboBox()
        self._model_combo.addItems(DEMUCS_MODELS.keys())
        h.addWidget(self._model_combo)
        h.addStretch()
        return w

    def collect_config(self):
        key = (
            self._model_combo.currentText()
            if self._model_combo
            else "htdemucs (recommended)"
        )
        return {"model": DEMUCS_MODELS.get(key, "htdemucs")}
