"""Step 3 — Burn/attach subtitles into video (ffmpeg).

Improvements:
- Font size auto-scale theo video resolution (% chiều cao video)
- Background box blur đằng sau subtitle
- Giọng TTS khớp đúng subtitle timing
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

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
COLOR_MAP = {
    "white": "FFFFFF",
    "yellow": "00FFFF",
    "cyan": "FFFF00",
    "black": "000000",
    "red": "0000FF",
    "green": "00FF00",
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
        return 1920, 1080  # fallback


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
    # Prefer a normalized avatar file name when available.
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

    # Keep only one avatar file per profile folder.
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
        self._base_dir = ""
        self._profiles = {}

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
        font_pct = config.get("font_pct", 2.0)  # default 2% of height
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
                    branding={
                        "enabled": config.get("brand_enabled", False),
                        "name": config.get("brand_name", ""),
                        "avatar": config.get("brand_avatar", ""),
                        "avatar_pct": config.get("brand_avatar_pct", 12.0),
                        "opacity": config.get("brand_opacity", 30),
                        "pos": config.get("brand_pos", "random"),
                        "margin_pct": config.get("brand_margin_pct", 2.0),
                        "name_pct": config.get("brand_name_pct", 2.4),
                    },
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
        self._font_pct_spin = QDoubleSpinBox()
        self._font_pct_spin.setDecimals(1)
        self._font_pct_spin.setSingleStep(0.5)
        self._font_pct_spin.setRange(0.5, 15)
        self._font_pct_spin.setValue(2)
        self._font_pct_spin.setFixedWidth(60)
        self._font_pct_spin.setToolTip(
            "% chiều cao video\n"
            "720p  → 2% = 14px\n"
            "1080p → 2% = 21px\n"
            "1920p → 2% = 38px\n"
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

        # Channel branding overlay
        v.addWidget(self._sep_label("📌 Channel Branding"))
        self._brand_enable_chk = QCheckBox("Enable channel avatar + name")
        self._brand_enable_chk.setChecked(False)
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
        self._brand_avatar_pct_spin.setValue(12.0)
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
        self._brand_name_pct_spin.setValue(2.4)
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

    def collect_config(self):
        outline = self._outline_combo.currentText() if self._outline_combo else "black"
        return {
            "mode": (
                "hard"
                if (self._radio_hard and self._radio_hard.isChecked())
                else "soft"
            ),
            "font_pct": self._font_pct_spin.value() if self._font_pct_spin else 2.0,
            "font_color": (
                self._color_combo.currentText() if self._color_combo else "white"
            ),
            "outline_color": "" if outline == "none" else outline,
            "alignment": (
                SUB_POSITIONS[self._pos_combo.currentText()] if self._pos_combo else 2
            ),
            "bg_box": self._bg_box_chk.isChecked() if self._bg_box_chk else True,
            "brand_enabled": (
                self._brand_enable_chk.isChecked() if self._brand_enable_chk else False
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
                else 12.0
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
                self._brand_name_pct_spin.value() if self._brand_name_pct_spin else 2.4
            ),
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
    branding=None,
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

    if not branding or not branding.get("enabled"):
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

    name = _escape_drawtext_text(branding.get("name", "").strip())
    avatar = branding.get("avatar", "").strip()
    avatar_exists = bool(avatar) and Path(avatar).exists()

    opacity = max(0.0, min(1.0, float(branding.get("opacity", 30)) / 100.0))
    avatar_w = max(24, int(video_w * float(branding.get("avatar_pct", 12.0)) / 100.0))
    name_size = max(12, int(video_h * float(branding.get("name_pct", 2.4)) / 100.0))
    margin = max(
        0, int(min(video_w, video_h) * float(branding.get("margin_pct", 2.0)) / 100.0)
    )
    pos = branding.get("pos", "random")
    gap = max(6, int(name_size * 0.35))
    est_text_h = int(name_size * 1.4)

    # Determine if we should use animated movement or fixed position
    use_random_movement = pos == "random"

    if use_random_movement:
        # Use comma-free expressions (sin/cos) so FFmpeg parser stays stable.
        # Movement is slow and continuous; text uses the same motion base as avatar.
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
        # Fixed position (static)
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
        else:  # top_left
            x_avatar_overlay = str(margin)
            y_avatar_overlay = margin
            x_avatar_text = str(margin)
            y_avatar_text = margin

        y_text_name = f"{y_avatar_text}+{avatar_w}+{gap}"

    # Tên được centered dựa vào avatar (không từ center screen)
    # Tính x position để center text relative to avatar
    text_width_approx = max(50, len(name) * name_size * 0.5)
    # Center offset = (avatar_width - text_width) / 2
    center_offset = (avatar_w - int(text_width_approx)) / 2

    filters = [f"[0:v]{vf}[sub]"]
    map_label = "sub"

    if avatar_exists:
        filters.append(
            f"[1:v]scale={avatar_w}:-1,format=rgba,colorchannelmixer=aa={opacity:.3f}[logo]"
        )
        # Avatar với movement hoặc fixed position
        filters.append(
            f"[sub][logo]overlay=x={x_avatar_overlay}:y={y_avatar_overlay}[vlogo]"
        )
        map_label = "vlogo"

    if name:
        # Tên nằm dưới avatar và CENTERED relative to avatar position
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
        "-crf",
        "18",
        "-c:a",
        "copy",
        out,
    ]
    return cmd
