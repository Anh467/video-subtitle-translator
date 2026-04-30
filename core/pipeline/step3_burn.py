"""Step 3 — Burn/attach subtitles into video (ffmpeg).

Improvements:
- Font size auto-scale theo video resolution (% chiều cao video)
- Background box blur đằng sau subtitle
- Giọng TTS khớp đúng subtitle timing
- Remove existing hardcoded subtitle via FFmpeg delogo filter
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
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
BG_COLORS = [
    "black",
    "white",
    "yellow",
    "blue",
    "red",
    "green",
    "purple",
    "orange",
    "gray",
]
FONT_FAMILIES = [
    "Arial",
    "Arial Bold",
    "Impact",
    "Tahoma",
    "Verdana",
    "Trebuchet MS",
    "Times New Roman",
    "Courier New",
]
BG_BOX_STYLES = {
    "None": "none",
    "Semi-transparent box": "semi",
    "Opaque box": "opaque",
}
PREVIEW_ASPECTS = {
    "Auto (from source video)": None,
    "16:9 (Landscape)": 16 / 9,
    "9:16 (Portrait)": 9 / 16,
    "1:1 (Square)": 1.0,
    "4:3": 4 / 3,
}
PREVIEW_ASPECT_AUTO = "Auto (from source video)"
PRESET_OPTIONS = ["ultrafast", "veryfast", "fast", "medium", "slow"]
CRF_RANGE = (18, 28)
DEFAULT_CRF = 20
DEFAULT_PRESET = "medium"
# libass uses script resolution (PlayResY, often 288) to scale style size.
# Keep this aligned so burn output matches preview percent sizing.
ASS_PLAYRES_Y = 288
COLOR_MAP = {
    "white": "FFFFFF",
    "yellow": "FFFF00",
    "cyan": "00FFFF",
    "black": "000000",
    "red": "FF0000",
    "green": "00FF00",
    "blue": "0000FF",
    "purple": "800080",
    "orange": "FFA500",
    "gray": "808080",
}

CHANNEL_PROFILE_FILE = ".subsync_channel_profiles.json"
CHANNEL_PROFILE_ASSETS = ".subsync_channel_profiles"
BRAND_POSITIONS = {
    "Random": "random",
    "Top left": "top_left",
    "Top right": "top_right",
    "Bottom left": "bottom_left",
    "Bottom right": "bottom_right",
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
        return 1920, 1080


def _profiles_root(base_dir: str) -> Path:
    return Path(base_dir) / CHANNEL_PROFILE_ASSETS


def _safe_profile_dir_name(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (name or "").strip())
    return safe.strip("_") or "channel"


def _find_avatar_in_dir(profile_dir: Path) -> Path | None:
    exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    candidates = [
        p for p in profile_dir.iterdir() if p.is_file() and p.suffix.lower() in exts
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda p: (0 if p.stem.lower() == "avatar" else 1, p.name.lower())
    )
    return candidates[0]


def _load_channel_profiles(base_dir: str) -> dict:
    if not base_dir:
        return {}
    root = _profiles_root(base_dir)
    if not root.exists():
        return {}
    profiles = {}
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir():
            continue
        name_file = entry / "channel_name.txt"
        if name_file.exists():
            display_name = name_file.read_text(
                encoding="utf-8", errors="ignore"
            ).strip()
        else:
            display_name = entry.name
        if not display_name:
            continue
        avatar_file = _find_avatar_in_dir(entry)
        profiles[display_name] = {
            "avatar": str(avatar_file) if avatar_file else "",
            "folder": str(entry),
        }
    return profiles


def _store_profile_image(base_dir: str, src_path: str, profile_name: str) -> str:
    root = _profiles_root(base_dir)
    root.mkdir(parents=True, exist_ok=True)
    profile_dir = root / _safe_profile_dir_name(profile_name)
    profile_dir.mkdir(parents=True, exist_ok=True)
    src = Path(src_path)
    ext = src.suffix.lower() or ".png"
    dst = profile_dir / f"avatar{ext}"
    for old in profile_dir.glob("avatar.*"):
        if old.resolve() != dst.resolve():
            old.unlink(missing_ok=True)
    shutil.copy2(src, dst)
    (profile_dir / "channel_name.txt").write_text(
        profile_name.strip(), encoding="utf-8"
    )
    return str(dst)


def _escape_drawtext_text(text: str) -> str:
    return (
        (text or "")
        .replace("\\", r"\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("%", r"\%")
        .replace(",", r"\,")
    )


# ── Delogo: build the removal filter string ───────────────────────────────────


def _delogo_filter(x: int, y: int, w: int, h: int) -> str:
    """
    FFmpeg delogo filter — interpolates border pixels to fill the region.
    Much better than blur because it reconstructs background rather than smearing.

    x, y = top-left corner of subtitle region
    w, h = width and height of region
    show=0 means don't show debug border
    """
    return f"delogo=x={x}:y={y}:w={w}:h={h}:show=0"


def _auto_detect_sub_region(
    video_path: str, video_w: int, video_h: int
) -> tuple[int, int, int, int] | None:
    """
    Auto-detect subtitle region using ffprobe + a heuristic:
    sample frame at 10s, run ffmpeg cropdetect on bottom 25% of frame.
    Returns (x, y, w, h) or None if detection fails.

    This is a best-effort heuristic — user-defined region is more reliable.
    """
    try:
        bottom_y = int(video_h * 0.72)
        crop_h = int(video_h * 0.25)

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()

        # Extract frame at 10s
        r = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                "10",
                "-i",
                video_path,
                "-vframes",
                "1",
                "-vf",
                f"crop={video_w}:{crop_h}:0:{bottom_y}",
                tmp.name,
            ],
            capture_output=True,
        )
        if r.returncode != 0:
            return None

        # Run cropdetect on the cropped bottom strip
        r2 = subprocess.run(
            ["ffmpeg", "-i", tmp.name, "-vf", "cropdetect=24:2:0", "-f", "null", "-"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        os.unlink(tmp.name)

        # Parse cropdetect output: crop=W:H:X:Y
        import re

        matches = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r2.stderr)
        if not matches:
            return None

        # Take the most common crop result
        cw, ch, cx, cy = map(int, matches[-1])
        # Translate cy back to full-video coordinates
        full_y = bottom_y + cy
        # Add padding
        pad = 4
        return (
            max(0, cx - pad),
            max(0, full_y - pad),
            min(cw + pad * 2, video_w),
            min(ch + pad * 2, video_h - full_y),
        )

    except Exception:
        return None


class BurnStep(BaseStep):
    STEP_ID = "step3_burn"
    LABEL = "③ Burn Subtitles"
    COLOR = "#6a4800"
    ENABLED_BY_DEFAULT = True

    def __init__(self):
        self._radio_soft = self._radio_hard = None
        self._font_pct_spin = None
        self._color_combo = self._outline_combo = self._pos_combo = None
        self._bg_box_combo = None  # replaces _bg_box_chk
        self._bg_color_combo = None
        self._bg_opacity_spin = None
        self._outline_width_spin = None
        self._shadow_spin = None
        self._bold_chk = self._italic_chk = None
        self._font_family_combo = None
        self._margin_v_spin = None
        self._bg_box_chk = None  # kept for backward compat (not shown in new UI)
        self._base_dir = ""
        self._profiles = {}

        # Preview
        self._preview_lbl = None
        self._preview_timer = None
        self._preview_text_edit = None
        self._preview_ratio_combo = None
        self._preview_meta_lbl = None

        # Encoding optimization
        self._crf_spin = None
        self._preset_combo = None

        # Source file tracking (for preview aspect ratio detection)
        self._source_file = None

        # Delogo controls
        self._delogo_chk = None
        self._delogo_x_spin = self._delogo_y_spin = None
        self._delogo_w_spin = self._delogo_h_spin = None
        self._delogo_frame = None
        self._btn_auto_detect = None

        # Branding controls
        self._brand_enable_chk = None
        self._brand_profile_combo = None
        self._brand_name_edit = None
        self._brand_avatar_edit = None
        self._brand_avatar_pct_spin = None
        self._brand_opacity_spin = None
        self._brand_pos_combo = None
        self._brand_margin_pct_spin = None
        self._brand_name_pct_spin = None

    def set_base_dir(self, base_dir: str):
        self._base_dir = base_dir or ""
        self._profiles = _load_channel_profiles(self._base_dir)
        self._refresh_profiles_ui()

    def set_source_file(self, source_file: str | None):
        """Set source media for preview auto-aspect and force refresh."""
        self._source_file = source_file or None
        if self._preview_timer:
            self._preview_timer.stop()
            self._preview_timer.start()
        elif self._preview_lbl:
            QTimer.singleShot(0, self._refresh_preview)

    def _refresh_profiles_ui(self):
        if not self._brand_profile_combo:
            return
        current = self._brand_profile_combo.currentText()
        self._brand_profile_combo.blockSignals(True)
        self._brand_profile_combo.clear()
        self._brand_profile_combo.addItem("(No profile)")
        for name in sorted(self._profiles.keys()):
            self._brand_profile_combo.addItem(name)
        if current and self._brand_profile_combo.findText(current) >= 0:
            self._brand_profile_combo.setCurrentText(current)
        self._brand_profile_combo.blockSignals(False)

    def _on_profile_selected(self, _idx):
        if not self._brand_profile_combo:
            return
        name = self._brand_profile_combo.currentText()
        data = self._profiles.get(name)
        if not data:
            return
        if self._brand_name_edit:
            self._brand_name_edit.setText(name)
        if self._brand_avatar_edit:
            self._brand_avatar_edit.setText(data.get("avatar", ""))

    def _browse_avatar(self):
        p, _ = QFileDialog.getOpenFileName(
            None,
            "Upload channel avatar",
            "",
            "Images (*.png *.jpg *.jpeg *.webp)",
        )
        if p and self._brand_avatar_edit:
            self._brand_avatar_edit.setText(p)

    def _save_profile_clicked(self):
        if not self._base_dir:
            QMessageBox.information(
                None, "No base folder", "Choose session base folder first."
            )
            return
        name = self._brand_name_edit.text().strip() if self._brand_name_edit else ""
        avatar = (
            self._brand_avatar_edit.text().strip() if self._brand_avatar_edit else ""
        )
        if not name or not avatar:
            QMessageBox.warning(
                None, "Missing data", "Enter channel name and avatar image first."
            )
            return
        if not Path(avatar).exists():
            QMessageBox.warning(
                None, "Avatar not found", "Avatar image path does not exist."
            )
            return
        _store_profile_image(self._base_dir, avatar, name)
        self._profiles = _load_channel_profiles(self._base_dir)
        self._refresh_profiles_ui()
        if self._brand_profile_combo:
            self._brand_profile_combo.setCurrentText(name)

    def _on_delogo_toggled(self, checked: bool):
        if self._delogo_frame:
            self._delogo_frame.setEnabled(checked)

    def _auto_detect_region(self):
        """Try to auto-detect sub region from current session source file."""
        # Try to get source file path from the session dir edit in parent window
        # We do this by scanning the session base dir for recent sessions
        src = self._get_current_source()
        if not src:
            QMessageBox.information(
                None,
                "No source file",
                "Cannot auto-detect: no source video loaded.\n"
                "Run Step 1 first or set the region manually.",
            )
            return

        w, h = _get_video_size(src)
        result = _auto_detect_sub_region(src, w, h)
        if result:
            x, y, rw, rh = result
            self._delogo_x_spin.setValue(x)
            self._delogo_y_spin.setValue(y)
            self._delogo_w_spin.setValue(rw)
            self._delogo_h_spin.setValue(rh)
            QMessageBox.information(
                None,
                "Auto-detect result",
                f"Detected region: x={x}, y={y}, w={rw}, h={rh}\n"
                "Check and adjust if needed before running.",
            )
        else:
            QMessageBox.warning(
                None,
                "Auto-detect failed",
                "Could not auto-detect subtitle region.\n"
                "Set the region manually using x, y, w, h fields.\n\n"
                "Tip: Use a media player to find the pixel coordinates of the subtitle area.",
            )

    def _get_current_source(self) -> str | None:
        """Best-effort: find source file from recent session or parent widget.
        Priority: _source_file (set by run()) → parent widget chain → None
        """
        # 1. Check if source file was set during run()
        if self._source_file and Path(self._source_file).exists():
            return self._source_file

        # 2. Walk up parent widget chain to find MainWindow._file
        widget = self._delogo_frame
        while widget is not None:
            if hasattr(widget, "_file") and widget._file:
                if Path(widget._file).exists():
                    return widget._file
            widget = widget.parent() if widget else None

        return None

    def run(self, session, config, log, cancel):
        translated = session.load_translated()
        out = str(session.step3_video)
        mode = config["mode"]
        input_video = session.source_file
        # Store source file for preview aspect ratio detection
        self._source_file = input_video
        if getattr(session, "step3_done", False):
            log(
                "♻️  Rebuilding subtitles from original source (overwrite previous Step 3)"
            )

        w, h = _get_video_size(input_video)
        log(f"   Video resolution: {w}x{h}")
        log(
            "   Subtitle style: "
            f"bg_style={config.get('bg_style', 'semi')}, "
            f"bg_color={config.get('bg_color', 'black')}, "
            f"bg_opacity={config.get('bg_opacity', 50)}%, "
            f"outline_color={config.get('outline_color', 'black') or 'none'}, "
            f"outline_width={config.get('outline_width', 2)}"
        )

        font_pct = config.get("font_pct", 2.0)
        # subtitles filter (libass) expects style size in ASS script space.
        # Convert percent to PlayResY-space so on-screen size tracks video percent.
        font_size = max(6, int(ASS_PLAYRES_Y * font_pct / 100))
        approx_px = int(h * font_pct / 100)
        log(
            f"   Font size: {font_pct}% of video height (~{approx_px}px onscreen), ASS size={font_size}"
        )

        # Delogo config
        delogo_cfg = config.get("delogo")
        if delogo_cfg and delogo_cfg.get("enabled"):
            dx, dy = delogo_cfg["x"], delogo_cfg["y"]
            dw, dh = delogo_cfg["w"], delogo_cfg["h"]
            # Clamp to video bounds — delogo crashes if region extends outside frame
            dx = max(0, min(dx, w - 1))
            dy = max(0, min(dy, h - 1))
            dw = max(1, min(dw, w - dx))
            dh = max(1, min(dh, h - dy))
            # Minimum size check — delogo needs at least 3x3 region
            if dw < 3 or dh < 3:
                log(f"⚠️  Delogo region too small after clamping ({dw}x{dh}) — skipping")
                delogo_cfg = None
            else:
                # Update config with clamped values
                delogo_cfg = {**delogo_cfg, "x": dx, "y": dy, "w": dw, "h": dh}
                log(
                    f"🧹 Remove existing sub: delogo x={dx} y={dy} w={dw} h={dh} (clamped to {w}x{h})"
                )

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
                    font_family=config.get("font_family", "Arial"),
                    bold=config.get("bold", False),
                    italic=config.get("italic", False),
                    font_color=config["font_color"],
                    outline_color=config["outline_color"],
                    outline_width=config.get("outline_width", 2),
                    shadow=config.get("shadow", 0),
                    bg_style=config.get("bg_style", "semi"),
                    bg_color=config.get("bg_color", "black"),
                    bg_opacity=config.get("bg_opacity", 50),
                    alignment=config["alignment"],
                    margin_v=config.get("margin_v", 6),
                    video_w=w,
                    video_h=h,
                    crf=config.get("crf", DEFAULT_CRF),
                    preset=config.get("preset", DEFAULT_PRESET),
                    delogo=delogo_cfg,
                    branding={
                        "enabled": config.get("brand_enabled", True),
                        "name": config.get("brand_name", ""),
                        "avatar": config.get("brand_avatar", ""),
                        "avatar_pct": config.get("brand_avatar_pct", 9.0),
                        "opacity": config.get("brand_opacity", 30),
                        "pos": config.get("brand_pos", "random"),
                        "margin_pct": config.get("brand_margin_pct", 2.0),
                        "name_pct": config.get("brand_name_pct", 2.0),
                    },
                )
            log(f"   $ {' '.join(str(c) for c in cmd)}")
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
        v.setSpacing(5)

        # ── Mode ─────────────────────────────────────────────────────────────
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

        # ── ① Encoding & Performance ─────────────────────────────────────────
        v.addWidget(self._sep_label("⚡ Encoding & Performance"))

        enc_row = QHBoxLayout()
        enc_row.addWidget(QLabel("CRF:"))
        self._crf_spin = QSpinBox()
        self._crf_spin.setRange(CRF_RANGE[0], CRF_RANGE[1])
        self._crf_spin.setValue(DEFAULT_CRF)
        self._crf_spin.setFixedWidth(50)
        self._crf_spin.setToolTip(
            "CRF: 18=highest quality/slowest, 20-22=near-original, 28=fastest"
        )
        enc_row.addWidget(self._crf_spin)
        enc_row.addSpacing(8)
        enc_row.addWidget(QLabel("(18=slow/best, 28=fast/lower)"))
        enc_row.addSpacing(16)
        enc_row.addWidget(QLabel("Preset:"))
        self._preset_combo = QComboBox()
        self._preset_combo.addItems(PRESET_OPTIONS)
        self._preset_combo.setCurrentText(DEFAULT_PRESET)
        self._preset_combo.setFixedWidth(95)
        self._preset_combo.setToolTip(
            "Speed: ultrafast → veryfast → fast → medium → slow"
        )
        enc_row.addWidget(self._preset_combo)
        enc_row.addStretch()
        v.addLayout(enc_row)

        # ── ② Subtitle Style ─────────────────────────────────────────────────
        v.addWidget(self._sep_label("🔤 Subtitle Style"))

        # Row: Font family + size
        r_font = QHBoxLayout()
        r_font.addWidget(QLabel("Font:"))
        self._font_family_combo = QComboBox()
        self._font_family_combo.addItems(FONT_FAMILIES)
        self._font_family_combo.setFixedWidth(130)
        r_font.addWidget(self._font_family_combo)
        r_font.addSpacing(6)
        r_font.addWidget(QLabel("Size:"))
        self._font_pct_spin = QDoubleSpinBox()
        self._font_pct_spin.setDecimals(1)
        self._font_pct_spin.setSingleStep(0.5)
        self._font_pct_spin.setRange(0.5, 15)
        self._font_pct_spin.setValue(2.0)
        self._font_pct_spin.setFixedWidth(58)
        r_font.addWidget(self._font_pct_spin)
        r_font.addWidget(QLabel("% h"))
        r_font.addSpacing(8)
        self._bold_chk = QCheckBox("Bold")
        self._italic_chk = QCheckBox("Italic")
        r_font.addWidget(self._bold_chk)
        r_font.addWidget(self._italic_chk)
        r_font.addStretch()
        v.addLayout(r_font)

        # Row: Color + outline
        r_col = QHBoxLayout()
        r_col.addWidget(QLabel("Color:"))
        self._color_combo = QComboBox()
        self._color_combo.addItems(FONT_COLORS)
        self._color_combo.setFixedWidth(70)
        r_col.addWidget(self._color_combo)
        r_col.addSpacing(8)
        r_col.addWidget(QLabel("Outline:"))
        self._outline_combo = QComboBox()
        self._outline_combo.addItems(OUTLINE_COLORS)
        self._outline_combo.setFixedWidth(65)
        r_col.addWidget(self._outline_combo)
        r_col.addSpacing(4)
        r_col.addWidget(QLabel("Width:"))
        self._outline_width_spin = QSpinBox()
        self._outline_width_spin.setRange(0, 8)
        self._outline_width_spin.setValue(2)
        self._outline_width_spin.setFixedWidth(46)
        r_col.addWidget(self._outline_width_spin)
        r_col.addSpacing(8)
        r_col.addWidget(QLabel("Shadow:"))
        self._shadow_spin = QSpinBox()
        self._shadow_spin.setRange(0, 5)
        self._shadow_spin.setValue(0)
        self._shadow_spin.setFixedWidth(46)
        r_col.addWidget(self._shadow_spin)
        r_col.addStretch()
        v.addLayout(r_col)

        # Row: Background box + position + margin
        r_bg = QHBoxLayout()
        r_bg.addWidget(QLabel("Background:"))
        self._bg_box_combo = QComboBox()
        self._bg_box_combo.addItems(BG_BOX_STYLES.keys())
        self._bg_box_combo.setCurrentText("Semi-transparent box")
        self._bg_box_combo.setFixedWidth(145)
        r_bg.addWidget(self._bg_box_combo)
        r_bg.addSpacing(6)
        r_bg.addWidget(QLabel("Color:"))
        self._bg_color_combo = QComboBox()
        self._bg_color_combo.addItems(BG_COLORS)
        self._bg_color_combo.setCurrentText("black")
        self._bg_color_combo.setFixedWidth(82)
        r_bg.addWidget(self._bg_color_combo)
        r_bg.addSpacing(4)
        r_bg.addWidget(QLabel("Opacity:"))
        self._bg_opacity_spin = QSpinBox()
        self._bg_opacity_spin.setRange(0, 100)
        self._bg_opacity_spin.setValue(50)
        self._bg_opacity_spin.setFixedWidth(50)
        r_bg.addWidget(self._bg_opacity_spin)
        r_bg.addWidget(QLabel("%"))
        r_bg.addStretch()
        v.addLayout(r_bg)

        r_pos = QHBoxLayout()
        r_pos.addWidget(QLabel("Position:"))
        self._pos_combo = QComboBox()
        self._pos_combo.addItems(SUB_POSITIONS.keys())
        self._pos_combo.setFixedWidth(165)
        r_pos.addWidget(self._pos_combo)
        r_pos.addSpacing(8)
        r_pos.addWidget(QLabel("Margin V:"))
        self._margin_v_spin = QSpinBox()
        self._margin_v_spin.setRange(0, 200)
        self._margin_v_spin.setValue(6)
        self._margin_v_spin.setFixedWidth(55)
        r_pos.addWidget(self._margin_v_spin)
        r_pos.addWidget(QLabel("px"))
        r_pos.addStretch()
        v.addLayout(r_pos)

        # ── ③ Subtitle Preview ───────────────────────────────────────────────
        v.addWidget(self._sep_label("👁  Subtitle Preview"))

        prev_row = QHBoxLayout()
        # Sample text field
        self._preview_text_edit = QLineEdit()
        self._preview_text_edit.setPlaceholderText("Preview text…")
        self._preview_text_edit.setText("Đây là ví dụ phụ đề tiếng Việt")
        self._preview_text_edit.setFixedWidth(200)
        prev_row.addWidget(QLabel("Text:"))
        prev_row.addWidget(self._preview_text_edit)
        prev_row.addSpacing(8)
        prev_row.addWidget(QLabel("Ratio:"))
        self._preview_ratio_combo = QComboBox()
        self._preview_ratio_combo.addItems(PREVIEW_ASPECTS.keys())
        self._preview_ratio_combo.setCurrentText(PREVIEW_ASPECT_AUTO)
        self._preview_ratio_combo.setFixedWidth(130)
        prev_row.addWidget(self._preview_ratio_combo)
        prev_row.addStretch()
        v.addLayout(prev_row)

        self._preview_lbl = QLabel()
        self._preview_lbl.setFixedHeight(190)
        self._preview_lbl.setMinimumWidth(340)
        self._preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_lbl.setStyleSheet(
            "background:#111;border:1px solid #333;border-radius:4px;"
        )
        v.addWidget(self._preview_lbl)

        self._preview_meta_lbl = QLabel("Auto ratio: waiting for source video…")
        self._preview_meta_lbl.setStyleSheet("color:#777;font-size:10px;")
        self._preview_meta_lbl.setWordWrap(True)
        v.addWidget(self._preview_meta_lbl)

        # Wire all style controls to trigger preview refresh
        self._preview_timer = QTimer()
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(120)
        self._preview_timer.timeout.connect(self._refresh_preview)

        def _schedule():
            self._preview_timer.stop()
            self._preview_timer.start()

        for widget in [
            self._font_family_combo,
            self._color_combo,
            self._outline_combo,
            self._bg_box_combo,
            self._bg_color_combo,
            self._pos_combo,
            self._preview_ratio_combo,
        ]:
            widget.currentIndexChanged.connect(_schedule)
        for widget in [
            self._font_pct_spin,
            self._outline_width_spin,
            self._shadow_spin,
            self._bg_opacity_spin,
            self._margin_v_spin,
        ]:
            widget.valueChanged.connect(_schedule)
        self._bold_chk.toggled.connect(_schedule)
        self._italic_chk.toggled.connect(_schedule)
        self._preview_text_edit.textChanged.connect(_schedule)

        # Initial render
        QTimer.singleShot(0, self._refresh_preview)

        # ── ④ Remove existing subtitle (delogo) ─────────────────────────────
        v.addWidget(self._sep_label("🧹 Remove Existing Subtitle"))

        self._delogo_chk = QCheckBox("Remove hardcoded subtitle with delogo filter")
        self._delogo_chk.setChecked(False)
        self._delogo_chk.setToolTip(
            "FFmpeg delogo: reconstructs background pixels in the specified region.\n"
            "Better than blur — uses border pixel interpolation, not smearing.\n"
            "Set x,y,w,h to cover the old subtitle area."
        )
        self._delogo_chk.toggled.connect(self._on_delogo_toggled)
        v.addWidget(self._delogo_chk)

        # Delogo region controls
        self._delogo_frame = QWidget()
        self._delogo_frame.setEnabled(False)
        dv = QVBoxLayout(self._delogo_frame)
        dv.setContentsMargins(0, 0, 0, 0)
        dv.setSpacing(4)

        # Auto-detect button
        auto_row = QHBoxLayout()
        self._btn_auto_detect = QPushButton("Auto-detect region")
        self._btn_auto_detect.setToolTip(
            "Tries to detect subtitle region automatically by\n"
            "analyzing a sample frame from the source video.\n"
            "Result may need manual adjustment."
        )
        self._btn_auto_detect.setStyleSheet(
            "QPushButton{background:#1a3a5a;color:#60aaff;border:1px solid #2a5a8a;"
            "border-radius:5px;padding:4px 10px;font-size:11px;}"
            "QPushButton:hover{background:#2a5a8a;}"
        )
        self._btn_auto_detect.clicked.connect(self._auto_detect_region)
        auto_row.addWidget(self._btn_auto_detect)
        hint = QLabel("or set manually:")
        hint.setStyleSheet("color:#666;font-size:11px;")
        auto_row.addWidget(hint)
        auto_row.addStretch()
        dv.addLayout(auto_row)

        # x, y, w, h spinboxes
        coord_row = QHBoxLayout()
        for label, attr, default, maxv in [
            ("x:", "_delogo_x_spin", 0, 7680),
            ("y:", "_delogo_y_spin", 0, 4320),
            ("w:", "_delogo_w_spin", 400, 7680),
            ("h:", "_delogo_h_spin", 60, 4320),
        ]:
            coord_row.addWidget(QLabel(label))
            spin = QSpinBox()
            spin.setRange(0, maxv)
            spin.setValue(default)
            spin.setFixedWidth(72)
            spin.setToolTip(
                "x = left edge of subtitle region (pixels from left)\n"
                "y = top edge (pixels from top)\n"
                "w = width of region\n"
                "h = height of region\n\n"
                "Tip: Use VLC → Tools → Media Info to find subtitle pixel position."
            )
            setattr(self, attr, spin)
            coord_row.addWidget(spin)
            coord_row.addSpacing(4)
        coord_row.addStretch()
        dv.addLayout(coord_row)

        # Helpful hint
        hint2 = QLabel(
            "Tip: In VLC → View → Advanced Controls → frame by frame to find coordinates."
        )
        hint2.setStyleSheet("color:#555;font-size:10px;")
        hint2.setWordWrap(True)
        dv.addWidget(hint2)

        v.addWidget(self._delogo_frame)

        # ── ⑤ Channel branding ──────────────────────────────────────────────
        v.addWidget(self._sep_label("📌 Channel Branding"))
        self._brand_enable_chk = QCheckBox("Enable channel avatar + name")
        self._brand_enable_chk.setChecked(True)
        v.addWidget(self._brand_enable_chk)

        r_pf = QHBoxLayout()
        r_pf.addWidget(QLabel("Profile:"))
        self._brand_profile_combo = QComboBox()
        self._brand_profile_combo.addItem("(No profile)")
        self._brand_profile_combo.currentIndexChanged.connect(self._on_profile_selected)
        r_pf.addWidget(self._brand_profile_combo)
        btn_save_pf = QPushButton("Save Profile")
        btn_save_pf.clicked.connect(self._save_profile_clicked)
        r_pf.addWidget(btn_save_pf)
        v.addLayout(r_pf)

        r_name = QHBoxLayout()
        r_name.addWidget(QLabel("Channel name:"))
        self._brand_name_edit = QLineEdit()
        self._brand_name_edit.setPlaceholderText("Enter channel name")
        r_name.addWidget(self._brand_name_edit)
        v.addLayout(r_name)

        r_av = QHBoxLayout()
        r_av.addWidget(QLabel("Avatar image:"))
        self._brand_avatar_edit = QLineEdit()
        self._brand_avatar_edit.setPlaceholderText(
            "Use Upload avatar... to select image"
        )
        self._brand_avatar_edit.setReadOnly(True)
        r_av.addWidget(self._brand_avatar_edit)
        btn_browse_av = QPushButton("Upload avatar...")
        btn_browse_av.clicked.connect(self._browse_avatar)
        r_av.addWidget(btn_browse_av)
        v.addLayout(r_av)

        r_a = QHBoxLayout()
        r_a.addWidget(QLabel("Avatar size:"))
        self._brand_avatar_pct_spin = QDoubleSpinBox()
        self._brand_avatar_pct_spin.setDecimals(1)
        self._brand_avatar_pct_spin.setSingleStep(0.5)
        self._brand_avatar_pct_spin.setRange(2.0, 40.0)
        self._brand_avatar_pct_spin.setValue(9.0)
        self._brand_avatar_pct_spin.setFixedWidth(70)
        r_a.addWidget(self._brand_avatar_pct_spin)
        r_a.addWidget(QLabel("% of video width"))
        r_a.addSpacing(12)
        r_a.addWidget(QLabel("Opacity:"))
        self._brand_opacity_spin = QSpinBox()
        self._brand_opacity_spin.setRange(0, 100)
        self._brand_opacity_spin.setValue(30)
        self._brand_opacity_spin.setFixedWidth(60)
        r_a.addWidget(self._brand_opacity_spin)
        r_a.addWidget(QLabel("%"))
        r_a.addStretch()
        v.addLayout(r_a)

        r_b = QHBoxLayout()
        r_b.addWidget(QLabel("Name size:"))
        self._brand_name_pct_spin = QDoubleSpinBox()
        self._brand_name_pct_spin.setDecimals(1)
        self._brand_name_pct_spin.setSingleStep(0.5)
        self._brand_name_pct_spin.setRange(1.0, 10.0)
        self._brand_name_pct_spin.setValue(2.0)
        self._brand_name_pct_spin.setFixedWidth(70)
        r_b.addWidget(self._brand_name_pct_spin)
        r_b.addWidget(QLabel("% of video height"))
        r_b.addSpacing(12)
        r_b.addWidget(QLabel("Position:"))
        self._brand_pos_combo = QComboBox()
        self._brand_pos_combo.addItems(BRAND_POSITIONS.keys())
        self._brand_pos_combo.setCurrentText("Random")
        r_b.addWidget(self._brand_pos_combo)
        r_b.addSpacing(12)
        r_b.addWidget(QLabel("Margin:"))
        self._brand_margin_pct_spin = QDoubleSpinBox()
        self._brand_margin_pct_spin.setDecimals(1)
        self._brand_margin_pct_spin.setSingleStep(0.5)
        self._brand_margin_pct_spin.setRange(0.0, 20.0)
        self._brand_margin_pct_spin.setValue(2.0)
        self._brand_margin_pct_spin.setFixedWidth(70)
        r_b.addWidget(self._brand_margin_pct_spin)
        r_b.addWidget(QLabel("%"))
        r_b.addStretch()
        v.addLayout(r_b)

        self._refresh_profiles_ui()
        return w

    def _sep_label(self, text):
        l = QLabel(text)
        l.setStyleSheet("color:#a0a8ff;font-size:11px;font-weight:600;margin-top:4px;")
        return l

    def _refresh_preview(self):
        """Render a subtitle preview using QPainter into the preview label."""
        if not self._preview_lbl:
            return

        lbl_w = max(340, self._preview_lbl.width() or 460)
        lbl_h = max(190, self._preview_lbl.height() or 190)

        ratio_label = (
            self._preview_ratio_combo.currentText()
            if self._preview_ratio_combo
            else PREVIEW_ASPECT_AUTO
        )
        ratio = PREVIEW_ASPECTS.get(ratio_label, None)
        src = None
        src_w = src_h = 0
        if ratio is None:
            src = self._get_current_source()
            if src:
                src_w, src_h = _get_video_size(src)
                ratio = (src_w / src_h) if src_w > 0 and src_h > 0 else (16 / 9)
            else:
                ratio = 16 / 9

        if self._preview_meta_lbl:
            if PREVIEW_ASPECTS.get(ratio_label, None) is None:
                if src and src_w > 0 and src_h > 0:
                    self._preview_meta_lbl.setText(
                        f"Source: {Path(src).name} | {src_w}x{src_h} | Auto ratio {ratio:.3f}"
                    )
                else:
                    self._preview_meta_lbl.setText(
                        "Source not found — Auto fallback to 16:9"
                    )
            else:
                self._preview_meta_lbl.setText(
                    f"Manual ratio: {ratio_label} ({ratio:.3f})"
                )

        # Fit a video rectangle inside the preview canvas based on selected aspect ratio
        avail_w = max(10, lbl_w - 20)
        avail_h = max(10, lbl_h - 20)
        if (avail_w / avail_h) > ratio:
            video_h = avail_h
            video_w = int(video_h * ratio)
        else:
            video_w = avail_w
            video_h = int(video_w / ratio)
        vx = (lbl_w - video_w) // 2
        vy = (lbl_h - video_h) // 2

        font_pct = self._font_pct_spin.value() if self._font_pct_spin else 2.0
        # Scale font size proportionally to preview render size (min 4px for visibility)
        font_size_px = max(4, int(video_h * font_pct / 100))

        text = (
            self._preview_text_edit.text() if self._preview_text_edit else ""
        ) or "Sample Subtitle Text"
        font_color_name = (
            self._color_combo.currentText() if self._color_combo else "white"
        )
        outline_color_name = (
            self._outline_combo.currentText() if self._outline_combo else "black"
        )
        bg_color_name = (
            self._bg_color_combo.currentText() if self._bg_color_combo else "black"
        )
        outline_w = self._outline_width_spin.value() if self._outline_width_spin else 2
        shadow_px = self._shadow_spin.value() if self._shadow_spin else 0
        bg_style = BG_BOX_STYLES.get(
            (
                self._bg_box_combo.currentText()
                if self._bg_box_combo
                else "Semi-transparent box"
            ),
            "semi",
        )
        bg_opacity = (
            self._bg_opacity_spin.value() if self._bg_opacity_spin else 50
        ) / 100.0
        align = SUB_POSITIONS.get(
            (
                self._pos_combo.currentText()
                if self._pos_combo
                else "Bottom center (default)"
            ),
            2,
        )
        margin_v = self._margin_v_spin.value() if self._margin_v_spin else 6
        bold = self._bold_chk.isChecked() if self._bold_chk else False
        italic = self._italic_chk.isChecked() if self._italic_chk else False
        family = (
            self._font_family_combo.currentText()
            if self._font_family_combo
            else "Arial"
        )

        # Color mapping
        _COLOR_MAP = {
            "white": QColor(255, 255, 255),
            "yellow": QColor(255, 255, 0),
            "cyan": QColor(0, 255, 255),
            "green": QColor(0, 200, 80),
            "red": QColor(255, 60, 60),
            "black": QColor(0, 0, 0),
            "blue": QColor(70, 120, 255),
            "purple": QColor(170, 70, 220),
            "orange": QColor(255, 165, 40),
            "gray": QColor(140, 140, 140),
        }
        fg_color = _COLOR_MAP.get(font_color_name, QColor(255, 255, 255))
        oc = (
            _COLOR_MAP.get(outline_color_name, QColor(0, 0, 0))
            if outline_color_name != "none"
            else None
        )
        bg_base = _COLOR_MAP.get(bg_color_name, QColor(0, 0, 0))

        img = QImage(lbl_w, lbl_h, QImage.Format.Format_ARGB32)
        img.fill(QColor(20, 20, 20))

        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # Draw a simulated video frame area for chosen ratio
        painter.fillRect(vx, vy, video_w, video_h, QColor(36, 48, 66))
        painter.setPen(QPen(QColor(110, 130, 155), 1))
        painter.drawRect(vx, vy, video_w, video_h)

        font = QFont(family, font_size_px)
        font.setBold(bold)
        font.setItalic(italic)
        painter.setFont(font)

        fm = QFontMetrics(font)
        text_w = fm.horizontalAdvance(text)
        text_h = fm.height()
        if align in (1,):
            x = vx + 12
        elif align in (3,):
            x = vx + video_w - text_w - 12
        else:
            x = vx + (video_w - text_w) // 2

        if align in (8,):
            y = vy + text_h + max(4, margin_v)
        elif align in (5,):
            y = vy + (video_h + text_h) // 2
        else:
            y = vy + video_h - max(4, margin_v)

        # Background box
        if bg_style != "none":
            pad = max(3, outline_w + 4)
            box_alpha = int(bg_opacity * 255)
            if bg_style == "opaque":
                box_alpha = 255
            box_color = QColor(
                bg_base.red(), bg_base.green(), bg_base.blue(), box_alpha
            )
            # Use font metrics around the baseline so text sits centered in the box.
            text_top = y - fm.ascent()
            text_bottom = y + fm.descent()
            box_y = text_top - pad
            box_h = (text_bottom - text_top) + (2 * pad)
            painter.fillRect(x - pad, box_y, text_w + 2 * pad, box_h, box_color)

        # Shadow
        if shadow_px > 0:
            painter.setPen(QColor(0, 0, 0, 160))
            painter.drawText(x + shadow_px, y + shadow_px, text)

        # Outline (draw text in outline color at N offsets)
        if oc and outline_w > 0:
            painter.setPen(QPen(oc, 1))
            for dx in range(-outline_w, outline_w + 1):
                for dy in range(-outline_w, outline_w + 1):
                    if dx == 0 and dy == 0:
                        continue
                    painter.setPen(oc)
                    painter.drawText(x + dx, y + dy, text)

        # Main text
        painter.setPen(fg_color)
        painter.drawText(x, y, text)
        painter.end()

        self._preview_lbl.setPixmap(QPixmap.fromImage(img))

    def apply_config(self, config: dict) -> None:
        if not config:
            return
        _POS_BY_VAL = {v: k for k, v in SUB_POSITIONS.items()}
        _BRAND_BY_VAL = {v: k for k, v in BRAND_POSITIONS.items()}
        _BG_BY_VAL = {v: k for k, v in BG_BOX_STYLES.items()}
        if config.get("mode") == "hard" and self._radio_hard:
            self._radio_hard.setChecked(True)
        elif config.get("mode") == "soft" and self._radio_soft:
            self._radio_soft.setChecked(True)
        if self._font_pct_spin and config.get("font_pct") is not None:
            self._font_pct_spin.setValue(float(config["font_pct"]))
        if self._color_combo and config.get("font_color"):
            self._color_combo.setCurrentText(config["font_color"])
        if self._outline_combo:
            oc = config.get("outline_color", "black")
            self._outline_combo.setCurrentText(oc if oc else "none")
        if self._outline_width_spin and config.get("outline_width") is not None:
            self._outline_width_spin.setValue(int(config["outline_width"]))
        if self._shadow_spin and config.get("shadow") is not None:
            self._shadow_spin.setValue(int(config["shadow"]))
        if self._font_family_combo and config.get("font_family"):
            self._font_family_combo.setCurrentText(config["font_family"])
        if self._bold_chk and config.get("bold") is not None:
            self._bold_chk.setChecked(bool(config["bold"]))
        if self._italic_chk and config.get("italic") is not None:
            self._italic_chk.setChecked(bool(config["italic"]))
        if self._bg_box_combo and config.get("bg_style"):
            lbl = _BG_BY_VAL.get(config["bg_style"], "Semi-transparent box")
            self._bg_box_combo.setCurrentText(lbl)
        if self._bg_color_combo and config.get("bg_color"):
            self._bg_color_combo.setCurrentText(str(config["bg_color"]))
        if self._bg_opacity_spin and config.get("bg_opacity") is not None:
            self._bg_opacity_spin.setValue(int(config["bg_opacity"]))
        if self._pos_combo and config.get("alignment") is not None:
            label = _POS_BY_VAL.get(config["alignment"], "Bottom center (default)")
            self._pos_combo.setCurrentText(label)
        if self._margin_v_spin and config.get("margin_v") is not None:
            self._margin_v_spin.setValue(int(config["margin_v"]))
        if self._preview_ratio_combo:
            aspect = str(config.get("preview_aspect") or PREVIEW_ASPECT_AUTO)
            if aspect not in PREVIEW_ASPECTS:
                aspect = PREVIEW_ASPECT_AUTO
            self._preview_ratio_combo.setCurrentText(aspect)
        if self._crf_spin and config.get("crf") is not None:
            self._crf_spin.setValue(int(config["crf"]))
        if self._preset_combo and config.get("preset"):
            self._preset_combo.setCurrentText(str(config["preset"]))
        # delogo
        dl = config.get("delogo")
        if self._delogo_chk:
            self._delogo_chk.setChecked(bool(dl and dl.get("enabled")))
        if dl and isinstance(dl, dict):
            for attr, key in (
                ("_delogo_x_spin", "x"),
                ("_delogo_y_spin", "y"),
                ("_delogo_w_spin", "w"),
                ("_delogo_h_spin", "h"),
            ):
                spin = getattr(self, attr, None)
                if spin and dl.get(key) is not None:
                    spin.setValue(int(dl[key]))
        # brand
        if self._brand_enable_chk and config.get("brand_enabled") is not None:
            self._brand_enable_chk.setChecked(bool(config["brand_enabled"]))
        if self._brand_name_edit and config.get("brand_name") is not None:
            self._brand_name_edit.setText(config["brand_name"])
        if self._brand_avatar_edit and config.get("brand_avatar") is not None:
            self._brand_avatar_edit.setText(config["brand_avatar"])
        if self._brand_avatar_pct_spin and config.get("brand_avatar_pct") is not None:
            self._brand_avatar_pct_spin.setValue(float(config["brand_avatar_pct"]))
        if self._brand_opacity_spin and config.get("brand_opacity") is not None:
            self._brand_opacity_spin.setValue(int(config["brand_opacity"]))
        if self._brand_pos_combo and config.get("brand_pos"):
            label = _BRAND_BY_VAL.get(config["brand_pos"], "Random")
            self._brand_pos_combo.setCurrentText(label)
        if self._brand_margin_pct_spin and config.get("brand_margin_pct") is not None:
            self._brand_margin_pct_spin.setValue(float(config["brand_margin_pct"]))
        if self._brand_name_pct_spin and config.get("brand_name_pct") is not None:
            self._brand_name_pct_spin.setValue(float(config["brand_name_pct"]))

    def collect_config(self):
        outline = self._outline_combo.currentText() if self._outline_combo else "black"
        bg_style = BG_BOX_STYLES.get(
            (
                self._bg_box_combo.currentText()
                if self._bg_box_combo
                else "Semi-transparent box"
            ),
            "semi",
        )

        delogo_cfg = None
        if self._delogo_chk and self._delogo_chk.isChecked():
            delogo_cfg = {
                "enabled": True,
                "x": self._delogo_x_spin.value() if self._delogo_x_spin else 0,
                "y": self._delogo_y_spin.value() if self._delogo_y_spin else 0,
                "w": self._delogo_w_spin.value() if self._delogo_w_spin else 400,
                "h": self._delogo_h_spin.value() if self._delogo_h_spin else 60,
            }

        return {
            "mode": (
                "hard"
                if (self._radio_hard and self._radio_hard.isChecked())
                else "soft"
            ),
            "font_pct": self._font_pct_spin.value() if self._font_pct_spin else 2.0,
            "font_family": (
                self._font_family_combo.currentText()
                if self._font_family_combo
                else "Arial"
            ),
            "bold": self._bold_chk.isChecked() if self._bold_chk else False,
            "italic": self._italic_chk.isChecked() if self._italic_chk else False,
            "font_color": (
                self._color_combo.currentText() if self._color_combo else "white"
            ),
            "outline_color": "" if outline == "none" else outline,
            "outline_width": (
                self._outline_width_spin.value() if self._outline_width_spin else 2
            ),
            "shadow": self._shadow_spin.value() if self._shadow_spin else 0,
            "bg_style": bg_style,
            "bg_color": (
                self._bg_color_combo.currentText() if self._bg_color_combo else "black"
            ),
            "bg_opacity": (
                self._bg_opacity_spin.value() if self._bg_opacity_spin else 50
            ),
            # legacy compat: bg_box=True when style != none
            "bg_box": bg_style != "none",
            "alignment": (
                SUB_POSITIONS[self._pos_combo.currentText()] if self._pos_combo else 2
            ),
            "margin_v": self._margin_v_spin.value() if self._margin_v_spin else 6,
            "preview_aspect": (
                self._preview_ratio_combo.currentText()
                if self._preview_ratio_combo
                else PREVIEW_ASPECT_AUTO
            ),
            "crf": self._crf_spin.value() if self._crf_spin else DEFAULT_CRF,
            "preset": (
                self._preset_combo.currentText()
                if self._preset_combo
                else DEFAULT_PRESET
            ),
            "delogo": delogo_cfg,
            "brand_enabled": (
                self._brand_enable_chk.isChecked() if self._brand_enable_chk else True
            ),
            "brand_name": (
                self._brand_name_edit.text().strip() if self._brand_name_edit else ""
            ),
            "brand_avatar": (
                self._brand_avatar_edit.text().strip()
                if self._brand_avatar_edit
                else ""
            ),
            "brand_avatar_pct": (
                self._brand_avatar_pct_spin.value()
                if self._brand_avatar_pct_spin
                else 9.0
            ),
            "brand_opacity": (
                self._brand_opacity_spin.value() if self._brand_opacity_spin else 30
            ),
            "brand_pos": BRAND_POSITIONS.get(
                (
                    self._brand_pos_combo.currentText()
                    if self._brand_pos_combo
                    else "Random"
                ),
                "random",
            ),
            "brand_margin_pct": (
                self._brand_margin_pct_spin.value()
                if self._brand_margin_pct_spin
                else 2.0
            ),
            "brand_name_pct": (
                self._brand_name_pct_spin.value() if self._brand_name_pct_spin else 2.0
            ),
        }


# ── FFmpeg command builders ───────────────────────────────────────────────────


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
    font_family="Arial",
    bold=False,
    italic=False,
    font_color="white",
    outline_color="black",
    outline_width=2,
    shadow=0,
    bg_style="semi",
    bg_color="black",
    bg_opacity=50,
    alignment=2,
    margin_v=6,
    bg_box=True,
    video_w=1920,
    video_h=1080,
    crf=DEFAULT_CRF,
    preset=DEFAULT_PRESET,
    delogo=None,
    branding=None,
):
    escaped = srt.replace("\\", "/").replace(":", "\\:")

    # Resolve bg from new bg_style field
    use_bg = bg_style != "none"
    # UI opacity: 0..100 means transparent..opaque.
    # ASS alpha is inverted: 00=opaque, FF=transparent.
    bg_opacity = max(0.0, min(100.0, float(bg_opacity)))
    bg_alpha_hex = f"{int((100.0 - bg_opacity) / 100.0 * 255):02X}"

    outline_val = outline_width if outline_color and outline_color != "none" else 0
    outline_str = (
        f"Outline={outline_val},OutlineColour=&H00{_bgr(outline_color)},"
        if outline_val > 0
        else "Outline=0,"
    )

    bold_val = 1 if bold else 0
    italic_val = 1 if italic else 0
    font_name_str = f"Fontname={font_family}," if font_family else ""

    if use_bg:
        force_style = (
            f"{font_name_str}"
            f"FontSize={font_size},Bold={bold_val},Italic={italic_val},"
            f"PrimaryColour=&H00{_bgr(font_color)},"
            f"{outline_str}"
            # BorderStyle=4 keeps subtitle box color from BackColour while preserving text outline.
            f"Shadow={shadow},BorderStyle=4,"
            f"BackColour=&H{bg_alpha_hex}{_bgr(bg_color)},"
            f"Alignment={alignment},MarginV={margin_v}"
        )
    else:
        force_style = (
            f"{font_name_str}"
            f"FontSize={font_size},Bold={bold_val},Italic={italic_val},"
            f"PrimaryColour=&H00{_bgr(font_color)},"
            f"{outline_str}"
            f"Shadow={shadow},Alignment={alignment},MarginV={margin_v}"
        )

    sub_filter = f"subtitles='{escaped}':force_style='{force_style}'"

    # ── Chain delogo BEFORE subtitles ──────────────────────────────────────
    # Order matters: remove old sub first, then burn new one on clean frame.
    if delogo and delogo.get("enabled"):
        dx, dy = delogo["x"], delogo["y"]
        dw, dh = delogo["w"], delogo["h"]
        # Defensive clamp — values may come from user spinboxes without validation
        dx = max(0, min(dx, video_w - 1))
        dy = max(0, min(dy, video_h - 1))
        dw = max(3, min(dw, video_w - dx))
        dh = max(3, min(dh, video_h - dy))
        if dw >= 3 and dh >= 3:
            vf_base = f"{_delogo_filter(dx, dy, dw, dh)},{sub_filter}"
        else:
            vf_base = sub_filter  # skip delogo if region invalid
    else:
        vf_base = sub_filter

    # No branding — simple case
    if not branding or not branding.get("enabled"):
        return [
            "ffmpeg",
            "-y",
            "-i",
            video,
            "-vf",
            vf_base,
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(crf),
            "-c:a",
            "copy",
            out,
        ]

    # With branding
    name = _escape_drawtext_text(branding.get("name", "").strip())
    avatar = branding.get("avatar", "").strip()
    avatar_exists = bool(avatar) and Path(avatar).exists()

    opacity = max(0.0, min(1.0, float(branding.get("opacity", 30)) / 100.0))
    avatar_w = max(24, int(video_w * float(branding.get("avatar_pct", 9.0)) / 100.0))
    name_size = max(12, int(video_h * float(branding.get("name_pct", 2.0)) / 100.0))
    margin = max(
        0, int(min(video_w, video_h) * float(branding.get("margin_pct", 2.0)) / 100.0)
    )
    pos = branding.get("pos", "random")
    gap = max(6, int(name_size * 0.35))
    est_text_h = int(name_size * 1.4)

    use_random_movement = pos == "random"

    if use_random_movement:
        x_span_overlay = max(0, video_w - avatar_w - 2 * margin)
        y_span_overlay = max(0, video_h - avatar_w - est_text_h - gap - 2 * margin)
        x_span_text = max(0, video_w - avatar_w - 2 * margin)
        y_span_text = max(0, video_h - avatar_w - est_text_h - gap - 2 * margin)
        x_avatar_overlay = f"{margin}+({x_span_overlay})*(0.5+0.5*sin(t/6))"
        y_avatar_overlay = f"{margin}+({y_span_overlay})*(0.5+0.5*cos(t/7))"
        x_avatar_text = f"{margin}+({x_span_text})*(0.5+0.5*sin(t/6))"
        y_avatar_text = f"{margin}+({y_span_text})*(0.5+0.5*cos(t/7))"
        y_text_name = f"({y_avatar_text})+{avatar_w}+{gap}"
    else:
        if pos == "top_right":
            x_avatar_overlay = f"W-overlay_w-{margin}"
            y_avatar_overlay = margin
            x_avatar_text = f"W-{avatar_w}-{margin}"
            y_avatar_text = margin
        elif pos == "bottom_left":
            x_avatar_overlay = str(margin)
            y_avatar_overlay = max(0, video_h - avatar_w - est_text_h - gap - margin)
            x_avatar_text = str(margin)
            y_avatar_text = max(0, video_h - avatar_w - est_text_h - gap - margin)
        elif pos == "bottom_right":
            x_avatar_overlay = f"W-overlay_w-{margin}"
            y_avatar_overlay = max(0, video_h - avatar_w - est_text_h - gap - margin)
            x_avatar_text = f"W-{avatar_w}-{margin}"
            y_avatar_text = max(0, video_h - avatar_w - est_text_h - gap - margin)
        else:
            x_avatar_overlay = str(margin)
            y_avatar_overlay = margin
            x_avatar_text = str(margin)
            y_avatar_text = margin
        y_text_name = f"{y_avatar_text}+{avatar_w}+{gap}"

    text_width_approx = max(50, len(name) * name_size * 0.5)
    center_offset = (avatar_w - int(text_width_approx)) / 2

    # vf_base already includes delogo+sub chain
    filters = [f"[0:v]{vf_base}[sub]"]
    map_label = "sub"

    if avatar_exists:
        filters.append(
            f"[1:v]scale={avatar_w}:-1,format=rgba,colorchannelmixer=aa={opacity:.3f}[logo]"
        )
        filters.append(
            f"[sub][logo]overlay=x={x_avatar_overlay}:y={y_avatar_overlay}[vlogo]"
        )
        map_label = "vlogo"

    if name:
        filters.append(
            f"[{map_label}]drawtext=text='{name}':"
            f"fontcolor=white@{opacity:.3f}:fontsize={name_size}:"
            f"box=1:boxcolor=black@0.45:boxborderw=8:"
            f"x=({x_avatar_text})+{center_offset}:y={y_text_name}[vout]"
        )
        map_label = "vout"

    cmd = ["ffmpeg", "-y", "-i", video]
    if avatar_exists:
        cmd += ["-i", avatar]
    cmd += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        f"[{map_label}]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-c:a",
        "copy",
        out,
    ]
    return cmd
