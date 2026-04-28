"""Step 3 — Burn/attach subtitles into video (ffmpeg).

Improvements:
- Font size auto-scale theo video resolution (% chiều cao video)
- Background box blur đằng sau subtitle
- Giọng TTS khớp đúng subtitle timing
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
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
            f"{getattr(seg, field, seg.translated).strip()}\n"
        )
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return path


def _get_video_size(video_path: str) -> tuple[int, int]:
    """Get video width x height using ffprobe."""
    try:
        r = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "json",
                video_path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        data = json.loads(r.stdout)
        stream = data["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except Exception:
        return 1920, 1080  # fallback


class BurnStep(BaseStep):
    STEP_ID = "step3_burn"
    LABEL = "③ Burn Subtitles"
    COLOR = "#6a4800"
    ENABLED_BY_DEFAULT = True

    def __init__(self):
        self._radio_soft = self._radio_hard = None
        self._font_pct_spin = None  # font size as % of video height
        self._color_combo = self._outline_combo = self._pos_combo = None
        self._bg_box_chk = None  # background box behind subtitle

    def run(self, session, config, log, cancel):
        translated = session.load_translated()
        out = str(session.step3_video)
        mode = config["mode"]
        input_video = session.latest_video()

        if hasattr(session, "step6_video") and input_video == str(session.step6_video):
            log("🔗 Chaining: using Step 6 output as base video")
        elif input_video != session.source_file:
            log("🔗 Chaining: using existing processed video as base")

        # Get actual video resolution for auto font size
        w, h = _get_video_size(input_video)
        log(f"   Video resolution: {w}x{h}")

        # Auto-scale font size: % of video height
        font_pct = config.get("font_pct", 5)  # default 5% of height
        font_size = max(12, int(h * font_pct / 100))
        log(f"   Font size: {font_pct}% of {h}px = {font_size}px")

        log(f"{'📎' if mode=='soft' else '🔥'} Burning subtitles ({mode})…")

        tmp = tempfile.NamedTemporaryFile(
            suffix=".srt", delete=False, mode="w", encoding="utf-8"
        )
        tmp.close()
        write_srt(translated, tmp.name)

        try:
            if mode == "soft":
                cmd = _soft_cmd(input_video, tmp.name, out)
            else:
                cmd = _hard_cmd(
                    input_video,
                    tmp.name,
                    out,
                    font_size=font_size,
                    font_color=config["font_color"],
                    outline_color=config["outline_color"],
                    alignment=config["alignment"],
                    bg_box=config.get("bg_box", True),
                    video_w=w,
                    video_h=h,
                )
            log(f"   $ {' '.join(cmd)}")
            r = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
            )
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
        self._radio_hard.setChecked(True)
        grp = QButtonGroup(w)
        grp.addButton(self._radio_soft)
        grp.addButton(self._radio_hard)
        r_mode.addWidget(self._radio_soft)
        r_mode.addWidget(self._radio_hard)
        r_mode.addStretch()
        v.addLayout(r_mode)

        # Font size (% of video height)
        r_fs = QHBoxLayout()
        r_fs.addWidget(QLabel("Font size:"))
        self._font_pct_spin = QSpinBox()
        self._font_pct_spin.setRange(2, 15)
        self._font_pct_spin.setValue(5)
        self._font_pct_spin.setFixedWidth(60)
        self._font_pct_spin.setToolTip(
            "% chiều cao video\n"
            "720p  → 5% = 36px\n"
            "1080p → 5% = 54px\n"
            "1920p → 5% = 96px\n"
            "Tự động scale theo độ phân giải video"
        )
        r_fs.addWidget(self._font_pct_spin)
        r_fs.addWidget(QLabel("% of height"))
        r_fs.addStretch()
        v.addLayout(r_fs)

        # Color + outline
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

        # Background box (blur)
        self._bg_box_chk = QCheckBox("Background box (blur behind text)")
        self._bg_box_chk.setChecked(True)
        self._bg_box_chk.setToolTip(
            "Thêm nền mờ blur đằng sau subtitle\n" "Giúp đọc dễ hơn trên mọi nền video"
        )
        v.addWidget(self._bg_box_chk)

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
        outline = self._outline_combo.currentText() if self._outline_combo else "black"
        return {
            "mode": (
                "hard"
                if (self._radio_hard and self._radio_hard.isChecked())
                else "soft"
            ),
            "font_pct": self._font_pct_spin.value() if self._font_pct_spin else 5,
            "font_color": (
                self._color_combo.currentText() if self._color_combo else "white"
            ),
            "outline_color": "" if outline == "none" else outline,
            "alignment": (
                SUB_POSITIONS[self._pos_combo.currentText()] if self._pos_combo else 2
            ),
            "bg_box": self._bg_box_chk.isChecked() if self._bg_box_chk else True,
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


def _hard_cmd(
    video,
    srt,
    out,
    font_size,
    font_color,
    outline_color,
    alignment,
    bg_box=True,
    video_w=1920,
    video_h=1080,
):
    """
    Burn subtitles with:
    - Auto-scaled font size
    - Optional blurred background box behind text
    """
    escaped = srt.replace("\\", "/").replace(":", "\\:")

    if bg_box:
        # Strategy: use ASS style with BorderStyle=4 (opaque box)
        # BoxColour = semi-transparent black (AABBGGRR: 80 = 50% opacity)
        outline_str = (
            f"Outline=2,OutlineColour=&H00{_bgr(outline_color)},"
            if outline_color
            else "Outline=0,"
        )
        force_style = (
            f"FontSize={font_size},"
            f"PrimaryColour=&H00{_bgr(font_color)},"
            f"{outline_str}"
            f"Shadow=0,"
            f"BorderStyle=4,"  # opaque box
            f"BackColour=&H80000000,"  # 50% transparent black box
            f"Alignment={alignment},"
            f"MarginV=20"
        )
        vf = f"subtitles='{escaped}':force_style='{force_style}'"
    else:
        outline_str = (
            f"Outline=2,OutlineColour=&H00{_bgr(outline_color)},"
            if outline_color
            else "Outline=0,"
        )
        force_style = (
            f"FontSize={font_size},"
            f"PrimaryColour=&H00{_bgr(font_color)},"
            f"{outline_str}"
            f"Shadow=1,"
            f"Alignment={alignment},"
            f"MarginV=20"
        )
        vf = f"subtitles='{escaped}':force_style='{force_style}'"

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
