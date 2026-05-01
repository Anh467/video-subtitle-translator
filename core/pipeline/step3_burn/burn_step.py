"""Step 3 — burn / attach subtitles (UI + pipeline run)."""

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
from core.pipeline.step3_burn.channel_profiles import load_channel_profiles, store_profile_image
from core.pipeline.step3_burn.constants import (
    ASS_PLAYRES_Y,
    BG_BOX_STYLES,
    BG_COLORS,
    BRAND_POSITIONS,
    CRF_RANGE,
    DEFAULT_CRF,
    DEFAULT_PRESET,
    FONT_COLORS,
    FONT_FAMILIES,
    OUTLINE_COLORS,
    PRESET_OPTIONS,
    PREVIEW_ASPECTS,
    PREVIEW_ASPECT_AUTO,
    SUB_POSITIONS,
)
from core.pipeline.step3_burn.delogo import auto_detect_sub_region
from core.pipeline.step3_burn.ffmpeg_burn import hard_burn_cmd, soft_sub_cmd
from core.pipeline.step3_burn.srt_writer import write_srt
from core.pipeline.step3_burn.video_probe import get_video_size

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
        self._profiles = load_channel_profiles(self._base_dir)
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
        store_profile_image(self._base_dir, avatar, name)
        self._profiles = load_channel_profiles(self._base_dir)
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

        w, h = get_video_size(src)
        result = auto_detect_sub_region(src, w, h)
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

        w, h = get_video_size(input_video)
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
                cmd = soft_sub_cmd(input_video, tmp.name, out)
            else:
                cmd = hard_burn_cmd(
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
                src_w, src_h = get_video_size(src)
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

