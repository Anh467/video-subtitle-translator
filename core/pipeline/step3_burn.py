"""Step 3 — Burn/attach subtitles into video (ffmpeg)."""

import os
import subprocess
import tempfile
from pathlib import Path

from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.pipeline.base import BaseStep

SUB_POSITIONS = {
    "Bottom center (default)": 2,
    "Top center": 8,
    "Middle center": 5,
    "Bottom left": 1,
    "Bottom right": 3,
}
FONT_COLORS = ["white", "yellow", "cyan", "green", "red", "black"]
OUTLINE_COLORS = ["black", "white", "none"]
COLOR_MAP = {
    "white": "FFFFFF",
    "yellow": "00FFFF",
    "cyan": "FFFF00",
    "black": "000000",
    "red": "0000FF",
    "green": "00FF00",
}


def _bgr(c):
    rgb = COLOR_MAP.get(c.lower(), "FFFFFF")
    return rgb[4:6] + rgb[2:4] + rgb[0:2]


def _srt_time(s):
    h, r = divmod(int(s), 3600)
    m, sec = divmod(r, 60)
    return f"{h:02}:{m:02}:{sec:02},{int((s-int(s))*1000):03}"


def write_srt(segments, path, field="translated"):
    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(
            f"{i}\n{_srt_time(seg.start)} --> {_srt_time(seg.end)}\n"
            f"{getattr(seg,field,seg.translated).strip()}\n"
        )
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return path


class BurnStep(BaseStep):
    STEP_ID = "step3_burn"
    LABEL = "③ Burn Subtitles"
    COLOR = "#6a4800"
    ENABLED_BY_DEFAULT = True

    def __init__(self):
        self._radio_soft = self._radio_hard = None
        self._font_spin = self._color_combo = None
        self._outline_combo = self._pos_combo = None

    def run(self, session, config, log, cancel):
        translated = session.load_translated()
        out = str(session.step3_video)
        mode = config["mode"]

        # Use latest processed video as input (chains with Step 5 if already run)
        input_video = session.latest_video()
        # If step5 already exists, use it as base — result replaces step3_video
        if input_video == str(session.step5_video):
            log("🔗 Chaining: using Step 5 output as base video")
        elif input_video != session.source_file:
            log("🔗 Chaining: using existing processed video as base")

        log(f"{'📎' if mode=='soft' else '🔥'} Burning subtitles ({mode})…")

        tmp = tempfile.NamedTemporaryFile(
            suffix=".srt", delete=False, mode="w", encoding="utf-8"
        )
        tmp.close()
        write_srt(translated, tmp.name)

        try:
            cmd = (
                _soft_cmd(input_video, tmp.name, out)
                if mode == "soft"
                else _hard_cmd(
                    input_video,
                    tmp.name,
                    out,
                    config["font_size"],
                    config["font_color"],
                    config["outline_color"],
                    config["alignment"],
                )
            )
            log(f"   $ {' '.join(cmd)}")
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg failed:\n{r.stderr[-2000:]}")
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)

        log(f"✅ Output → {out}")
        return out

    def build_config_widget(self, parent=None):
        w = QWidget(parent)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        # Mode
        r_mode = QHBoxLayout()
        r_mode.addWidget(QLabel("Mode:"))
        self._radio_soft = QRadioButton("Soft (track)")
        self._radio_hard = QRadioButton("Hard (burned)")
        self._radio_hard.setChecked(True)  # default: Hard
        grp = QButtonGroup(w)
        grp.addButton(self._radio_soft)
        grp.addButton(self._radio_hard)
        r_mode.addWidget(self._radio_soft)
        r_mode.addWidget(self._radio_hard)
        r_mode.addStretch()
        v.addLayout(r_mode)

        # Font size
        r_fs = QHBoxLayout()
        r_fs.addWidget(QLabel("Font size:"))
        self._font_spin = QSpinBox()
        self._font_spin.setRange(12, 72)
        self._font_spin.setValue(24)
        self._font_spin.setMinimumWidth(80)
        self._font_spin.setFixedHeight(32)
        r_fs.addWidget(self._font_spin)
        r_fs.addStretch()
        v.addLayout(r_fs)

        # Color / outline
        r_col = QHBoxLayout()
        r_col.addWidget(QLabel("Color:"))
        self._color_combo = QComboBox()
        self._color_combo.addItems(FONT_COLORS)
        r_col.addWidget(self._color_combo)
        r_col.addSpacing(8)
        r_col.addWidget(QLabel("Outline:"))
        self._outline_combo = QComboBox()
        self._outline_combo.addItems(OUTLINE_COLORS)
        r_col.addWidget(self._outline_combo)
        r_col.addStretch()
        v.addLayout(r_col)

        # Position
        r_pos = QHBoxLayout()
        r_pos.addWidget(QLabel("Position:"))
        self._pos_combo = QComboBox()
        self._pos_combo.addItems(SUB_POSITIONS.keys())
        r_pos.addWidget(self._pos_combo)
        r_pos.addStretch()
        v.addLayout(r_pos)
        return w

    def collect_config(self):
        outline = self._outline_combo.currentText()
        return {
            "mode": "hard" if self._radio_hard.isChecked() else "soft",
            "font_size": self._font_spin.value(),
            "font_color": self._color_combo.currentText(),
            "outline_color": "" if outline == "none" else outline,
            "alignment": SUB_POSITIONS[self._pos_combo.currentText()],
        }


def _soft_cmd(video, srt, out):
    codec = "srt" if Path(out).suffix.lower() == ".mkv" else "mov_text"
    return [
        "ffmpeg",
        "-y",
        "-i",
        video,
        "-i",
        srt,
        "-c",
        "copy",
        "-c:s",
        codec,
        "-metadata:s:s:0",
        "language=vie",
        out,
    ]


def _hard_cmd(video, srt, out, font_size, font_color, outline_color, alignment):
    escaped = srt.replace("\\", "/").replace(":", "\\:")
    outline = (
        f"Outline=2,OutlineColour=&H00{_bgr(outline_color)},"
        if outline_color
        else "Outline=0,"
    )
    vf = (
        f"subtitles='{escaped}':force_style='"
        f"FontSize={font_size},PrimaryColour=&H00{_bgr(font_color)},"
        f"{outline}Shadow=1,Alignment={alignment}'"
    )
    return [
        "ffmpeg",
        "-y",
        "-i",
        video,
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-crf",
        "18",
        "-c:a",
        "copy",
        out,
    ]
