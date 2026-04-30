"""
SubtitleEditor — widget hiển thị Original (read-only) + Translated (editable).

Khi user edit text trong Translated panel và nhấn Save (hoặc Ctrl+S),
widget tự parse lại các segment, ghi đè step2_translated.json và
step2_translated.srt trong session folder.

Usage:
    editor = SubtitleEditor()
    editor.load_session(session)   # load từ Session object
    editor.clear()                 # reset

Tích hợp:
    - MainWindow: thay thế self._trans_edit bằng SubtitleEditor
    - MultiSessionWindow: dùng trong preview panel
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from PyQt6.QtCore import QEvent, QRect, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QImage,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
    from PyQt6.QtMultimediaWidgets import QVideoWidget
except Exception:
    QAudioOutput = None
    QMediaPlayer = None
    QVideoSink = None
    QVideoWidget = None


_COLOR_MAP = {
    "white": "#FFFFFF",
    "yellow": "#FFFF00",
    "cyan": "#00FFFF",
    "black": "#000000",
    "red": "#FF0000",
    "green": "#00FF00",
    "blue": "#0000FF",
    "purple": "#800080",
    "orange": "#FFA500",
    "gray": "#808080",
}


def _named_color(name: str, alpha: int = 255) -> QColor:
    c = QColor(_COLOR_MAP.get((name or "white").lower(), "#FFFFFF"))
    c.setAlpha(max(0, min(255, int(alpha))))
    return c


def _style_alignment_flags(alignment: int):
    base = Qt.AlignmentFlag.AlignVCenter
    if alignment == 1:
        return Qt.AlignmentFlag.AlignLeft | base
    if alignment == 3:
        return Qt.AlignmentFlag.AlignRight | base
    return Qt.AlignmentFlag.AlignHCenter | base


def _draw_overlay_block(
    painter: QPainter,
    rect: QRect,
    text: str,
    font: QFont,
    fg: QColor,
    outline: QColor | None,
    outline_width: int,
    shadow: int,
    bg_style: str,
    bg_color: QColor,
    align_flags,
):
    if not text.strip():
        return

    flags = align_flags | Qt.TextFlag.TextWordWrap
    painter.setFont(font)
    metrics = painter.fontMetrics()
    text_rect = metrics.boundingRect(rect, flags, text)

    if bg_style != "none":
        pad_x = max(10, outline_width * 4)
        pad_y = max(6, outline_width * 3)
        bg_rect = text_rect.adjusted(-pad_x, -pad_y, pad_x, pad_y)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg_color)
        painter.drawRoundedRect(bg_rect, 8, 8)

    if shadow > 0:
        painter.setPen(QColor(0, 0, 0, 150))
        painter.drawText(rect.translated(shadow, shadow), flags, text)

    if outline and outline_width > 0:
        painter.setPen(QPen(outline, max(1, outline_width)))
        for dx in range(-outline_width, outline_width + 1):
            for dy in range(-outline_width, outline_width + 1):
                if dx == 0 and dy == 0:
                    continue
                painter.drawText(rect.translated(dx, dy), flags, text)

    painter.setPen(fg)
    painter.drawText(rect, flags, text)


class _StudioOverlayWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._title_text = ""
        self._subtitle_text = ""
        self._style_config: dict = {}
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")

    def set_payload(self, title_text: str, subtitle_text: str, style_config: dict):
        self._title_text = title_text or ""
        self._subtitle_text = subtitle_text or ""
        self._style_config = dict(style_config or {})
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        cfg = self._style_config or {}
        fg = _named_color(cfg.get("font_color", "white"))
        outline_name = cfg.get("outline_color") or ""
        outline = _named_color(outline_name) if outline_name else None
        outline_width = int(cfg.get("outline_width", 2) or 0)
        shadow = int(cfg.get("shadow", 0) or 0)
        bg_style = str(cfg.get("bg_style", "semi") or "semi")
        bg_opacity = int(cfg.get("bg_opacity", 50) or 50)
        alpha = (
            255
            if bg_style == "opaque"
            else int(max(0, min(100, bg_opacity)) * 255 / 100)
        )
        bg = _named_color(cfg.get("bg_color", "black"), alpha)
        alignment = int(cfg.get("alignment", 2) or 2)
        margin_v = int(cfg.get("margin_v", 6) or 6)

        title_font = QFont(cfg.get("font_family", "Arial") or "Arial")
        title_font.setBold(bool(cfg.get("bold", False)))
        title_font.setItalic(bool(cfg.get("italic", False)))
        title_font.setPixelSize(
            max(14, int(self.height() * float(cfg.get("font_pct", 2.0) or 2.0) / 140.0))
        )
        subtitle_font = QFont(title_font)
        subtitle_font.setPixelSize(
            max(16, int(self.height() * float(cfg.get("font_pct", 2.0) or 2.0) / 100.0))
        )

        title_rect = QRect(
            24, 14, max(120, self.width() - 48), int(self.height() * 0.18)
        )
        _draw_overlay_block(
            painter,
            title_rect,
            self._title_text,
            title_font,
            fg,
            outline,
            max(1, outline_width),
            shadow,
            bg_style,
            bg,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
        )

        if alignment == 8:
            subtitle_rect = QRect(
                24,
                24 + margin_v,
                max(120, self.width() - 48),
                int(self.height() * 0.26),
            )
        elif alignment == 5:
            subtitle_rect = QRect(
                24,
                int(self.height() * 0.38),
                max(120, self.width() - 48),
                int(self.height() * 0.24),
            )
        else:
            subtitle_rect = QRect(
                24,
                int(self.height() * 0.68) - margin_v,
                max(120, self.width() - 48),
                int(self.height() * 0.22),
            )

        _draw_overlay_block(
            painter,
            subtitle_rect,
            self._subtitle_text,
            subtitle_font,
            fg,
            outline,
            outline_width,
            shadow,
            bg_style,
            bg,
            _style_alignment_flags(alignment),
        )
        painter.end()


def _srt_time(s: float) -> str:
    h, r = divmod(int(s), 3600)
    m, sec = divmod(r, 60)
    ms = int((s - int(s)) * 1000)
    return f"{h:02}:{m:02}:{sec:02},{ms:03}"


def _write_srt(segments: list[dict], path: str):
    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(
            f"{i}\n"
            f"{_srt_time(seg['start'])} --> {_srt_time(seg['end'])}\n"
            f"{seg['translated'].strip()}\n"
        )
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def _ffprobe_duration_seconds(path: str) -> float:
    if not path or not Path(path).exists():
        return 0.0
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
        out = subprocess.check_output(cmd, text=True).strip()
        return max(0.0, float(out))
    except Exception:
        return 0.0


def _parse_translated_block(text: str) -> list[dict]:
    """
    Parse Translated panel (SRT-style) về list of dicts.

    Format:
        1
        [0.0s-1.28s] original text
        translated text

        2
        [1.28s-2.84s] original text
        translated text here
    """
    segments = []
    blocks = re.split(r"\n\s*\n", text.strip())
    for block in blocks:
        lines = [l for l in block.strip().splitlines() if l.strip()]
        if len(lines) < 2:
            continue
        # Find timestamp line
        ts_idx = None
        for i, line in enumerate(lines):
            if re.match(r"^\[[\d.]+s[–\-][\d.]+s\]", line.strip()):
                ts_idx = i
                break
        if ts_idx is None:
            continue
        ts_line = lines[ts_idx].strip()
        m = re.match(r"^\[([\d.]+)s[–\-]([\d.]+)s\]\s*(.*)", ts_line)
        if not m:
            continue
        start = float(m.group(1))
        end = float(m.group(2))
        original = m.group(3).strip()
        translated_lines = lines[ts_idx + 1 :]
        translated = " ".join(l.strip() for l in translated_lines if l.strip())
        if not translated:
            continue
        segments.append(
            {"start": start, "end": end, "original": original, "translated": translated}
        )
    return segments


class SubtitleEditor(QWidget):
    """
    Side-by-side Original (read-only) + Translated (editable) panels.
    Emits saved(session_folder) after a successful save.
    """

    saved = pyqtSignal(str)  # emits session folder path
    mode_changed = pyqtSignal(str)  # emits "default" | "studio"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._session = None
        self._step3 = None
        self._source_file = ""
        self._segments: list[dict] = []
        self._active_idx = -1
        self._duration_ms = 0
        self._timeline_dragging = False
        self._has_realtime_player = False
        self._current_video_frame = QPixmap()
        self._studio_tmp_dir = Path(tempfile.gettempdir()) / "subsync_studio_preview"
        self._studio_tmp_dir.mkdir(parents=True, exist_ok=True)
        self._mode = "default"
        self._dirty = False
        self._studio_dirty = False
        self._setup_ui()

    def set_step3_bridge(self, step3):
        """Attach BurnStep instance so Studio controls can sync live style."""
        self._step3 = step3
        self._sync_studio_choices_from_step3()
        self._apply_studio_to_step3()

    def set_source_file(self, source_file: str | None):
        self._source_file = str(source_file or "")
        if self._has_realtime_player and self._player:
            try:
                self._player.stop()
                self._player.setSource(
                    QUrl.fromLocalFile(self._source_file)
                    if self._source_file and Path(self._source_file).exists()
                    else QUrl()
                )
                self._player.setPosition(0)
                self._btn_play_pause.setText("▶ Play")
            except Exception:
                pass
        self._duration_ms = int(_ffprobe_duration_seconds(self._source_file) * 1000)
        if hasattr(self, "_studio_timeline") and self._studio_timeline:
            self._studio_timeline.setMaximum(max(0, self._duration_ms))
        self._update_timeline_label()
        self._render_studio_preview(force=True)

    def set_mode(self, mode: str):
        """Public API for parent windows to switch editor mode."""
        self._set_mode(mode)

    def current_mode(self) -> str:
        return self._mode

    # ── Public API ────────────────────────────────────────────────────────────

    def load_session(self, session) -> bool:
        """
        Load translated segments from a Session object.
        Returns True if step2 data was found, False otherwise.
        """
        self._session = session
        self.set_source_file(session.source_file)
        self._dirty = False
        self._segments = []
        self._active_idx = -1
        self._orig_edit.clear()
        self._trans_edit.clear()
        self._update_title()

        if not session.step1_done:
            self._orig_edit.setPlaceholderText("(Step 1 not done yet)")
        else:
            try:
                tr = session.load_transcript()
                self._orig_edit.setPlainText(
                    "\n".join(f"[{s.start}s–{s.end}s]  {s.text}" for s in tr.segments)
                )
            except Exception as e:
                self._orig_edit.setPlainText(f"(Cannot load transcript: {e})")

        if not session.step2_done:
            self._load_studio_from_session(session)
            self._trans_edit.setPlaceholderText(
                "(Step 2 not done yet — run Translate first)"
            )
            self._btn_save.setEnabled(False)
            self._reload_studio_segments()
            return False

        try:
            segs = session.load_translated()
            lines = []
            for i, s in enumerate(segs, 1):
                lines += [
                    str(i),
                    f"[{s.start}s–{s.end}s] {s.original}",
                    s.translated,
                    "",
                ]
            self._trans_edit.setPlainText("\n".join(lines))
            self._segments = [
                {
                    "start": float(s.start),
                    "end": float(s.end),
                    "original": s.original,
                    "translated": s.translated,
                }
                for s in segs
            ]
            self._trans_edit.setPlaceholderText("")
            self._btn_save.setEnabled(True)
            self._dirty = False
            self._load_studio_from_session(session)
            self._reload_studio_segments()
            self._update_title()
            return True
        except Exception as e:
            self._trans_edit.setPlainText(f"(Cannot load translation: {e})")
            self._btn_save.setEnabled(False)
            return False

    def load_from_text(self, orig_text: str, trans_text: str):
        """Load raw text (used by MainWindow after step runs)."""
        self._session = None
        self._dirty = False
        self._orig_edit.setPlainText(orig_text)
        self._trans_edit.setPlainText(trans_text)
        self._btn_save.setEnabled(False)
        self._studio_dirty = False
        self._segments = _parse_translated_block(trans_text)
        self._reload_studio_segments()
        self._update_title()

    def set_session_for_save(self, session):
        """Call this after load_from_text to enable saving."""
        self._session = session
        self._btn_save.setEnabled(session is not None and session.step2_done)
        self._update_title()

    def clear(self):
        self._session = None
        self._dirty = False
        self._segments = []
        self._active_idx = -1
        self._orig_edit.clear()
        self._trans_edit.clear()
        self._btn_save.setEnabled(False)
        self._studio_dirty = False
        self._reload_studio_segments()
        self._update_title()

    def get_translated_text(self) -> str:
        return self._trans_edit.toPlainText()

    def set_orig_placeholder(self, text: str):
        self._orig_edit.setPlaceholderText(text)

    def set_trans_placeholder(self, text: str):
        self._trans_edit.setPlaceholderText(text)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # Header bar
        hdr = QHBoxLayout()
        self._title_lbl = QLabel("Subtitles")
        self._title_lbl.setStyleSheet("color:#a0a8ff;font-size:11px;font-weight:600;")
        hdr.addWidget(self._title_lbl)
        hdr.addSpacing(8)

        self._btn_mode_default = QPushButton("Default")
        self._btn_mode_default.setCheckable(True)
        self._btn_mode_default.setChecked(True)
        self._btn_mode_default.setFixedHeight(24)
        self._btn_mode_default.clicked.connect(lambda: self._set_mode("default"))
        hdr.addWidget(self._btn_mode_default)

        self._btn_mode_studio = QPushButton("Studio")
        self._btn_mode_studio.setCheckable(True)
        self._btn_mode_studio.setFixedHeight(24)
        self._btn_mode_studio.clicked.connect(lambda: self._set_mode("studio"))
        hdr.addWidget(self._btn_mode_studio)
        hdr.addStretch()

        self._dirty_lbl = QLabel("")
        self._dirty_lbl.setStyleSheet("color:#ffaa55;font-size:10px;")
        hdr.addWidget(self._dirty_lbl)

        self._btn_save = QPushButton("💾  Save")
        self._btn_save.setFixedHeight(26)
        self._btn_save.setStyleSheet(
            "QPushButton{background:#1a3a2a;color:#5dca8e;border:1px solid #2a6a4a;"
            "font-weight:bold;border-radius:5px;padding:2px 12px;font-size:12px;}"
            "QPushButton:hover{background:#2a5a3a;border-color:#5dca8e;}"
            "QPushButton:disabled{color:#444;background:#1a1a2e;border-color:#252540;}"
        )
        self._btn_save.setEnabled(False)
        self._btn_save.clicked.connect(self._save)
        self._btn_save.setToolTip(
            "Save edits to step2_translated.json + .srt  (Ctrl+S)"
        )
        hdr.addWidget(self._btn_save)

        root.addLayout(hdr)

        self._studio_wrap = QWidget()
        studio_v = QVBoxLayout(self._studio_wrap)
        studio_v.setContentsMargins(0, 0, 0, 0)
        studio_v.setSpacing(4)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Title:"))
        self._studio_title_edit = QLineEdit()
        self._studio_title_edit.setPlaceholderText("Session title for this output…")
        self._studio_title_edit.textChanged.connect(self._on_studio_changed)
        r1.addWidget(self._studio_title_edit)
        studio_v.addLayout(r1)

        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Font:"))
        self._studio_font_combo = QComboBox()
        self._studio_font_combo.setMinimumWidth(130)
        self._studio_font_combo.currentTextChanged.connect(self._on_studio_changed)
        r2.addWidget(self._studio_font_combo)
        r2.addSpacing(6)
        r2.addWidget(QLabel("Size %:"))
        self._studio_font_pct = QDoubleSpinBox()
        self._studio_font_pct.setDecimals(1)
        self._studio_font_pct.setSingleStep(0.5)
        self._studio_font_pct.setRange(0.5, 15.0)
        self._studio_font_pct.setValue(2.0)
        self._studio_font_pct.valueChanged.connect(self._on_studio_changed)
        r2.addWidget(self._studio_font_pct)
        r2.addSpacing(6)
        r2.addWidget(QLabel("Position:"))
        self._studio_pos_combo = QComboBox()
        self._studio_pos_combo.setMinimumWidth(160)
        self._studio_pos_combo.currentTextChanged.connect(self._on_studio_changed)
        r2.addWidget(self._studio_pos_combo)
        r2.addStretch()
        self._btn_apply_studio = QPushButton("Apply style")
        self._btn_apply_studio.setFixedHeight(24)
        self._btn_apply_studio.clicked.connect(self._apply_studio_to_step3)
        r2.addWidget(self._btn_apply_studio)
        self._btn_save_studio = QPushButton("Save studio")
        self._btn_save_studio.setFixedHeight(24)
        self._btn_save_studio.clicked.connect(self._save_studio_only)
        r2.addWidget(self._btn_save_studio)
        studio_v.addLayout(r2)

        r3 = QHBoxLayout()
        self._studio_bold_chk = QCheckBox("Bold")
        self._studio_bold_chk.toggled.connect(self._on_studio_changed)
        r3.addWidget(self._studio_bold_chk)
        self._studio_italic_chk = QCheckBox("Italic")
        self._studio_italic_chk.toggled.connect(self._on_studio_changed)
        r3.addWidget(self._studio_italic_chk)
        r3.addSpacing(8)
        r3.addWidget(QLabel("Color:"))
        self._studio_color_combo = QComboBox()
        self._studio_color_combo.currentTextChanged.connect(self._on_studio_changed)
        r3.addWidget(self._studio_color_combo)
        r3.addSpacing(8)
        r3.addWidget(QLabel("Outline:"))
        self._studio_outline_combo = QComboBox()
        self._studio_outline_combo.currentTextChanged.connect(self._on_studio_changed)
        r3.addWidget(self._studio_outline_combo)
        r3.addSpacing(4)
        r3.addWidget(QLabel("Width:"))
        self._studio_outline_width_spin = QSpinBox()
        self._studio_outline_width_spin.setRange(0, 8)
        self._studio_outline_width_spin.valueChanged.connect(self._on_studio_changed)
        r3.addWidget(self._studio_outline_width_spin)
        r3.addSpacing(4)
        r3.addWidget(QLabel("Shadow:"))
        self._studio_shadow_spin = QSpinBox()
        self._studio_shadow_spin.setRange(0, 5)
        self._studio_shadow_spin.valueChanged.connect(self._on_studio_changed)
        r3.addWidget(self._studio_shadow_spin)
        r3.addStretch()
        studio_v.addLayout(r3)

        r4 = QHBoxLayout()
        r4.addWidget(QLabel("Background:"))
        self._studio_bg_style_combo = QComboBox()
        self._studio_bg_style_combo.currentTextChanged.connect(self._on_studio_changed)
        r4.addWidget(self._studio_bg_style_combo)
        r4.addSpacing(6)
        r4.addWidget(QLabel("BG color:"))
        self._studio_bg_color_combo = QComboBox()
        self._studio_bg_color_combo.currentTextChanged.connect(self._on_studio_changed)
        r4.addWidget(self._studio_bg_color_combo)
        r4.addSpacing(4)
        r4.addWidget(QLabel("Opacity:"))
        self._studio_bg_opacity_spin = QSpinBox()
        self._studio_bg_opacity_spin.setRange(0, 100)
        self._studio_bg_opacity_spin.valueChanged.connect(self._on_studio_changed)
        r4.addWidget(self._studio_bg_opacity_spin)
        r4.addWidget(QLabel("%"))
        r4.addSpacing(8)
        r4.addWidget(QLabel("Margin V:"))
        self._studio_margin_v_spin = QSpinBox()
        self._studio_margin_v_spin.setRange(0, 200)
        self._studio_margin_v_spin.valueChanged.connect(self._on_studio_changed)
        r4.addWidget(self._studio_margin_v_spin)
        r4.addWidget(QLabel("px"))
        r4.addStretch()
        studio_v.addLayout(r4)

        self._studio_hint_lbl = QLabel(
            "Studio mode: edit title + font + position and sync to session + Step 3."
        )
        self._studio_hint_lbl.setStyleSheet("color:#666;font-size:10px;")
        studio_v.addWidget(self._studio_hint_lbl)

        # Studio timeline editor: left preview+timeline, right segments list+editor
        studio_split = QSplitter(Qt.Orientation.Horizontal)

        studio_left = QWidget()
        studio_left_v = QVBoxLayout(studio_left)
        studio_left_v.setContentsMargins(0, 0, 0, 0)
        studio_left_v.setSpacing(4)

        if QMediaPlayer and QVideoSink:
            self._has_realtime_player = True
            self._player = QMediaPlayer(self)
            self._audio = QAudioOutput(self) if QAudioOutput else None
            if self._audio:
                self._audio.setVolume(0.8)
                self._player.setAudioOutput(self._audio)

            self._studio_video_host = QFrame()
            self._studio_video_host.setMinimumHeight(230)
            self._studio_video_host.setStyleSheet(
                "background:#111;border:1px solid #333;border-radius:4px;"
            )
            self._studio_video_host.installEventFilter(self)
            host_v = QVBoxLayout(self._studio_video_host)
            host_v.setContentsMargins(0, 0, 0, 0)
            host_v.setSpacing(0)

            self._studio_video = QLabel("No source video", self._studio_video_host)
            self._studio_video.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._studio_video.setStyleSheet("background:transparent;color:#666;")
            host_v.addWidget(self._studio_video)

            self._video_sink = QVideoSink(self)
            self._video_sink.videoFrameChanged.connect(self._on_video_frame_changed)
            self._player.setVideoOutput(self._video_sink)

            self._studio_overlay_lbl = None

            self._player.positionChanged.connect(self._on_player_position_changed)
            self._player.durationChanged.connect(self._on_player_duration_changed)
            self._player.playbackStateChanged.connect(self._on_playback_state_changed)
            studio_left_v.addWidget(self._studio_video_host)
        else:
            self._player = None
            self._audio = None
            self._video_sink = None
            self._studio_video = None
            self._studio_video_host = None
            self._studio_overlay_lbl = None
            self._studio_video_lbl = QLabel("No source video")
            self._studio_video_lbl.setMinimumHeight(230)
            self._studio_video_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._studio_video_lbl.setStyleSheet(
                "background:#111;border:1px solid #333;border-radius:4px;color:#666;"
            )
            studio_left_v.addWidget(self._studio_video_lbl)

        pr = QHBoxLayout()
        self._btn_play_pause = QPushButton("▶ Play")
        self._btn_play_pause.setFixedHeight(24)
        self._btn_play_pause.clicked.connect(self._toggle_play_pause)
        pr.addWidget(self._btn_play_pause)
        self._btn_to_prev = QPushButton("◀ Prev")
        self._btn_to_prev.setFixedHeight(24)
        self._btn_to_prev.clicked.connect(self._jump_prev_segment)
        pr.addWidget(self._btn_to_prev)
        self._btn_to_next = QPushButton("Next ▶")
        self._btn_to_next.setFixedHeight(24)
        self._btn_to_next.clicked.connect(self._jump_next_segment)
        pr.addWidget(self._btn_to_next)
        pr.addStretch()
        studio_left_v.addLayout(pr)

        tl = QHBoxLayout()
        self._studio_timeline = QSlider(Qt.Orientation.Horizontal)
        self._studio_timeline.setRange(0, 0)
        self._studio_timeline.sliderPressed.connect(self._on_timeline_pressed)
        self._studio_timeline.sliderReleased.connect(self._on_timeline_released)
        self._studio_timeline.valueChanged.connect(self._on_timeline_changed)
        tl.addWidget(self._studio_timeline, stretch=1)
        self._studio_time_lbl = QLabel("00:00.000 / 00:00.000")
        self._studio_time_lbl.setStyleSheet("color:#777;font-size:10px;")
        tl.addWidget(self._studio_time_lbl)
        studio_left_v.addLayout(tl)

        studio_split.addWidget(studio_left)

        studio_right = QWidget()
        studio_right_v = QVBoxLayout(studio_right)
        studio_right_v.setContentsMargins(0, 0, 0, 0)
        studio_right_v.setSpacing(4)
        studio_right_v.addWidget(QLabel("Timeline segments"))

        self._studio_segment_list = QListWidget()
        self._studio_segment_list.itemClicked.connect(self._on_segment_clicked)
        self._studio_segment_list.setStyleSheet(
            "QListWidget{background:#0e0e1e;border:1px solid #2d2d4e;border-radius:6px;}"
            "QListWidget::item{padding:6px;border-bottom:1px solid #1a1a30;}"
        )
        studio_right_v.addWidget(self._studio_segment_list, stretch=1)

        studio_right_v.addWidget(QLabel("Selected segment text"))
        self._studio_seg_edit = QTextEdit()
        self._studio_seg_edit.setFixedHeight(68)
        self._studio_seg_edit.setPlaceholderText(
            "Select a segment to edit translated text…"
        )
        studio_right_v.addWidget(self._studio_seg_edit)

        edit_row = QHBoxLayout()
        self._btn_update_segment = QPushButton("Update segment")
        self._btn_update_segment.clicked.connect(self._update_selected_segment)
        edit_row.addWidget(self._btn_update_segment)
        edit_row.addStretch()
        studio_right_v.addLayout(edit_row)

        studio_split.addWidget(studio_right)
        studio_split.setStretchFactor(0, 2)
        studio_split.setStretchFactor(1, 1)
        studio_v.addWidget(studio_split, stretch=1)

        self._studio_preview_timer = QTimer(self)
        self._studio_preview_timer.setSingleShot(True)
        self._studio_preview_timer.setInterval(120)
        self._studio_preview_timer.timeout.connect(self._render_studio_preview)

        self._studio_wrap.setVisible(False)
        root.addWidget(self._studio_wrap)

        # Side-by-side panels
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._default_splitter = splitter

        # Original (read-only)
        orig_w = QWidget()
        orig_v = QVBoxLayout(orig_w)
        orig_v.setContentsMargins(0, 0, 0, 0)
        orig_v.setSpacing(2)
        orig_lbl = QLabel("Original")
        orig_lbl.setStyleSheet("color:#666;font-size:10px;font-weight:600;")
        orig_v.addWidget(orig_lbl)
        self._orig_edit = QTextEdit()
        self._orig_edit.setReadOnly(True)
        self._orig_edit.setPlaceholderText("Original transcript…")
        self._orig_edit.setStyleSheet(
            "background:#0a0a1a;border:1px solid #1e1e38;border-radius:5px;"
            "padding:6px;color:#b0b0b0;font-family:'SF Mono','Consolas',monospace;font-size:11px;"
        )
        orig_v.addWidget(self._orig_edit, stretch=1)
        splitter.addWidget(orig_w)

        # Translated (editable)
        trans_w = QWidget()
        trans_v = QVBoxLayout(trans_w)
        trans_v.setContentsMargins(0, 0, 0, 0)
        trans_v.setSpacing(2)
        trans_hdr = QHBoxLayout()
        trans_lbl = QLabel("Translated  (editable)")
        trans_lbl.setStyleSheet("color:#5dca8e;font-size:10px;font-weight:600;")
        trans_hdr.addWidget(trans_lbl)
        hint = QLabel("Edit → lines  ·  Ctrl+S to save")
        hint.setStyleSheet("color:#444;font-size:10px;")
        trans_hdr.addStretch()
        trans_hdr.addWidget(hint)
        trans_v.addLayout(trans_hdr)

        self._trans_edit = QTextEdit()
        self._trans_edit.setPlaceholderText("Translated subtitles…")
        self._trans_edit.setStyleSheet(
            "background:#0c0c1f;border:1px solid #2a3a5a;border-radius:5px;"
            "padding:6px;color:#e0e0e0;font-family:'SF Mono','Consolas',monospace;font-size:11px;"
        )
        self._trans_edit.textChanged.connect(self._on_text_changed)
        trans_v.addWidget(self._trans_edit, stretch=1)
        splitter.addWidget(trans_w)

        root.addWidget(splitter, stretch=1)

        # Ctrl+S shortcut
        shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        shortcut.activated.connect(self._save)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _on_text_changed(self):
        if not self._dirty:
            self._dirty = True
            self._update_title()

    def _on_studio_changed(self):
        if not self._studio_dirty:
            self._studio_dirty = True
            self._update_title()
        self._apply_studio_to_step3()

    def _set_mode(self, mode: str):
        new_mode = "studio" if mode == "studio" else "default"
        old_mode = self._mode
        self._mode = new_mode
        is_studio = self._mode == "studio"
        self._btn_mode_default.setChecked(not is_studio)
        self._btn_mode_studio.setChecked(is_studio)
        self._studio_wrap.setVisible(is_studio)
        self._default_splitter.setVisible(not is_studio)
        if self._has_realtime_player and self._player and not is_studio:
            self._player.pause()
            self._btn_play_pause.setText("▶ Play")
        if is_studio:
            self._reload_studio_segments()
            self._render_studio_preview(force=True)
        if old_mode != new_mode:
            self.mode_changed.emit(new_mode)

    def _studio_payload(self) -> dict:
        cfg = {}
        if self._step3 is not None and hasattr(self._step3, "collect_config"):
            try:
                cfg = dict(self._step3.collect_config() or {})
            except Exception:
                cfg = {}
        if not cfg:
            cfg = {
                "font_family": self._studio_font_combo.currentText().strip(),
                "font_pct": float(self._studio_font_pct.value()),
                "bold": self._studio_bold_chk.isChecked(),
                "italic": self._studio_italic_chk.isChecked(),
                "font_color": self._studio_color_combo.currentText().strip() or "white",
                "outline_color": self._studio_outline_combo.currentText().strip()
                or "black",
                "outline_width": int(self._studio_outline_width_spin.value()),
                "shadow": int(self._studio_shadow_spin.value()),
                "bg_style": self._studio_bg_style_combo.currentText().strip() or "semi",
                "bg_color": self._studio_bg_color_combo.currentText().strip()
                or "black",
                "bg_opacity": int(self._studio_bg_opacity_spin.value()),
                "alignment": 2,
                "margin_v": int(self._studio_margin_v_spin.value()),
            }
        cfg["title"] = self._studio_title_edit.text().strip()
        cfg["position"] = self._studio_pos_combo.currentText().strip()
        return cfg

    def _sync_studio_choices_from_step3(self):
        if self._step3 is None:
            if self._studio_font_combo.count() == 0:
                self._studio_font_combo.addItems(["Arial", "Tahoma", "Verdana"])
            if self._studio_pos_combo.count() == 0:
                self._studio_pos_combo.addItems(
                    ["Bottom center (default)", "Top center", "Middle center"]
                )
            if self._studio_color_combo.count() == 0:
                self._studio_color_combo.addItems(
                    ["white", "yellow", "cyan", "green", "red", "black"]
                )
            if self._studio_outline_combo.count() == 0:
                self._studio_outline_combo.addItems(["black", "white", "none"])
            if self._studio_bg_style_combo.count() == 0:
                self._studio_bg_style_combo.addItems(
                    ["None", "Semi-transparent box", "Opaque box"]
                )
            if self._studio_bg_color_combo.count() == 0:
                self._studio_bg_color_combo.addItems(
                    [
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
                )
            return

        ff = getattr(self._step3, "_font_family_combo", None)
        if ff and self._studio_font_combo.count() == 0:
            self._studio_font_combo.addItems(
                [ff.itemText(i) for i in range(ff.count())]
            )
        pos = getattr(self._step3, "_pos_combo", None)
        if pos and self._studio_pos_combo.count() == 0:
            self._studio_pos_combo.addItems(
                [pos.itemText(i) for i in range(pos.count())]
            )
        color = getattr(self._step3, "_color_combo", None)
        if color and self._studio_color_combo.count() == 0:
            self._studio_color_combo.addItems(
                [color.itemText(i) for i in range(color.count())]
            )
        outline = getattr(self._step3, "_outline_combo", None)
        if outline and self._studio_outline_combo.count() == 0:
            self._studio_outline_combo.addItems(
                [outline.itemText(i) for i in range(outline.count())]
            )
        bg_style = getattr(self._step3, "_bg_box_combo", None)
        if bg_style and self._studio_bg_style_combo.count() == 0:
            self._studio_bg_style_combo.addItems(
                [bg_style.itemText(i) for i in range(bg_style.count())]
            )
        bg_color = getattr(self._step3, "_bg_color_combo", None)
        if bg_color and self._studio_bg_color_combo.count() == 0:
            self._studio_bg_color_combo.addItems(
                [bg_color.itemText(i) for i in range(bg_color.count())]
            )

    def _sync_studio_controls_from_step3(self):
        if self._step3 is None:
            return
        ff = getattr(self._step3, "_font_family_combo", None)
        if ff and self._studio_font_combo.count():
            self._studio_font_combo.setCurrentText(ff.currentText())
        fs = getattr(self._step3, "_font_pct_spin", None)
        if fs:
            self._studio_font_pct.setValue(float(fs.value()))
        pos = getattr(self._step3, "_pos_combo", None)
        if pos and self._studio_pos_combo.count():
            self._studio_pos_combo.setCurrentText(pos.currentText())
        bold = getattr(self._step3, "_bold_chk", None)
        if bold:
            self._studio_bold_chk.setChecked(bool(bold.isChecked()))
        italic = getattr(self._step3, "_italic_chk", None)
        if italic:
            self._studio_italic_chk.setChecked(bool(italic.isChecked()))
        color = getattr(self._step3, "_color_combo", None)
        if color and self._studio_color_combo.count():
            self._studio_color_combo.setCurrentText(color.currentText())
        outline = getattr(self._step3, "_outline_combo", None)
        if outline and self._studio_outline_combo.count():
            self._studio_outline_combo.setCurrentText(outline.currentText())
        outline_w = getattr(self._step3, "_outline_width_spin", None)
        if outline_w:
            self._studio_outline_width_spin.setValue(int(outline_w.value()))
        shadow = getattr(self._step3, "_shadow_spin", None)
        if shadow:
            self._studio_shadow_spin.setValue(int(shadow.value()))
        bg_style = getattr(self._step3, "_bg_box_combo", None)
        if bg_style and self._studio_bg_style_combo.count():
            self._studio_bg_style_combo.setCurrentText(bg_style.currentText())
        bg_color = getattr(self._step3, "_bg_color_combo", None)
        if bg_color and self._studio_bg_color_combo.count():
            self._studio_bg_color_combo.setCurrentText(bg_color.currentText())
        bg_opacity = getattr(self._step3, "_bg_opacity_spin", None)
        if bg_opacity:
            self._studio_bg_opacity_spin.setValue(int(bg_opacity.value()))
        margin_v = getattr(self._step3, "_margin_v_spin", None)
        if margin_v:
            self._studio_margin_v_spin.setValue(int(margin_v.value()))

    def _apply_studio_to_step3(self):
        if self._step3 is None:
            return
        try:
            ff = getattr(self._step3, "_font_family_combo", None)
            if ff and self._studio_font_combo.currentText():
                ff.setCurrentText(self._studio_font_combo.currentText())
            fs = getattr(self._step3, "_font_pct_spin", None)
            if fs:
                fs.setValue(float(self._studio_font_pct.value()))
            pos = getattr(self._step3, "_pos_combo", None)
            if pos and self._studio_pos_combo.currentText():
                pos.setCurrentText(self._studio_pos_combo.currentText())
            bold = getattr(self._step3, "_bold_chk", None)
            if bold:
                bold.setChecked(bool(self._studio_bold_chk.isChecked()))
            italic = getattr(self._step3, "_italic_chk", None)
            if italic:
                italic.setChecked(bool(self._studio_italic_chk.isChecked()))
            color = getattr(self._step3, "_color_combo", None)
            if color and self._studio_color_combo.currentText():
                color.setCurrentText(self._studio_color_combo.currentText())
            outline = getattr(self._step3, "_outline_combo", None)
            if outline and self._studio_outline_combo.currentText():
                outline.setCurrentText(self._studio_outline_combo.currentText())
            outline_w = getattr(self._step3, "_outline_width_spin", None)
            if outline_w:
                outline_w.setValue(int(self._studio_outline_width_spin.value()))
            shadow = getattr(self._step3, "_shadow_spin", None)
            if shadow:
                shadow.setValue(int(self._studio_shadow_spin.value()))
            bg_style = getattr(self._step3, "_bg_box_combo", None)
            if bg_style and self._studio_bg_style_combo.currentText():
                bg_style.setCurrentText(self._studio_bg_style_combo.currentText())
            bg_color = getattr(self._step3, "_bg_color_combo", None)
            if bg_color and self._studio_bg_color_combo.currentText():
                bg_color.setCurrentText(self._studio_bg_color_combo.currentText())
            bg_opacity = getattr(self._step3, "_bg_opacity_spin", None)
            if bg_opacity:
                bg_opacity.setValue(int(self._studio_bg_opacity_spin.value()))
            margin_v = getattr(self._step3, "_margin_v_spin", None)
            if margin_v:
                margin_v.setValue(int(self._studio_margin_v_spin.value()))
            refresh = getattr(self._step3, "_refresh_preview", None)
            if callable(refresh):
                refresh()
        except Exception:
            pass
        self._update_live_overlay()
        self._render_studio_preview(force=True)

    def _paint_overlay_on_pixmap(self, base_pixmap: QPixmap) -> QPixmap:
        if base_pixmap.isNull():
            return base_pixmap
        draw = QPixmap(base_pixmap)
        painter = QPainter(draw)
        overlay = _StudioOverlayWidget()
        overlay.resize(draw.size())
        txt = ""
        if 0 <= self._active_idx < len(self._segments):
            txt = self._segments[self._active_idx].get("translated", "")
        overlay.set_payload(
            self._studio_title_edit.text().strip(), txt, self._studio_payload()
        )
        overlay.render(painter)
        painter.end()
        return draw

    def _present_realtime_frame(self):
        if not self._has_realtime_player or not self._studio_video:
            return
        pix = self._current_video_frame
        if pix.isNull():
            self._studio_video.setText("No source video")
            return
        pix = self._paint_overlay_on_pixmap(pix)
        target = self._studio_video.size()
        if pix.width() > target.width() or pix.height() > target.height():
            scaled = pix.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        else:
            scaled = pix
        self._studio_video.setPixmap(scaled)
        self._studio_video.setText("")

    def _on_video_frame_changed(self, frame):
        try:
            image = frame.toImage()
        except Exception:
            image = None
        if image is None or image.isNull():
            return
        image = image.convertToFormat(QImage.Format.Format_ARGB32)
        self._current_video_frame = QPixmap.fromImage(image)
        self._present_realtime_frame()

    def _load_studio_from_session(self, session):
        self._sync_studio_choices_from_step3()
        studio = {}
        try:
            studio = (
                session.load_subtitle_studio()
                if hasattr(session, "load_subtitle_studio")
                else {}
            )
        except Exception:
            studio = {}

        self._studio_title_edit.setText(studio.get("title", session.title or ""))
        if studio and self._step3 is not None and hasattr(self._step3, "apply_config"):
            try:
                self._step3.apply_config(studio)
            except Exception:
                pass
        self._sync_studio_controls_from_step3()
        self._studio_dirty = False
        self._apply_studio_to_step3()

    def _format_ms(self, ms: int) -> str:
        s = max(0, ms) / 1000.0
        m = int(s // 60)
        ss = int(s % 60)
        ms_part = int((s - int(s)) * 1000)
        return f"{m:02}:{ss:02}.{ms_part:03}"

    def _update_timeline_label(self):
        cur = self._studio_timeline.value() if hasattr(self, "_studio_timeline") else 0
        self._studio_time_lbl.setText(
            f"{self._format_ms(cur)} / {self._format_ms(self._duration_ms)}"
        )

    def _on_player_duration_changed(self, dur: int):
        if dur and dur > 0:
            self._duration_ms = int(dur)
            self._studio_timeline.setMaximum(int(dur))
            self._update_timeline_label()

    def _on_playback_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._btn_play_pause.setText("⏸ Pause")
        else:
            self._btn_play_pause.setText("▶ Play")

    def _on_player_position_changed(self, pos: int):
        if self._timeline_dragging:
            return
        self._studio_timeline.blockSignals(True)
        self._studio_timeline.setValue(int(pos))
        self._studio_timeline.blockSignals(False)
        self._update_timeline_label()
        self._sync_active_segment_from_timeline()
        self._update_live_overlay()

    def _on_timeline_pressed(self):
        self._timeline_dragging = True

    def _on_timeline_released(self):
        self._timeline_dragging = False
        pos = self._studio_timeline.value()
        if self._has_realtime_player and self._player:
            self._player.setPosition(int(pos))
        self._update_timeline_label()
        self._sync_active_segment_from_timeline()
        if not self._has_realtime_player:
            self._studio_preview_timer.start()

    def _toggle_play_pause(self):
        if not self._has_realtime_player or not self._player:
            QMessageBox.information(
                self,
                "Studio Player",
                "Realtime player unavailable on this environment. Using frame preview mode.",
            )
            return
        if not self._source_file or not Path(self._source_file).exists():
            QMessageBox.warning(
                self, "Studio Player", "Source video not found for this session."
            )
            return
        if self._player.source().isEmpty():
            self._player.setSource(QUrl.fromLocalFile(self._source_file))
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _jump_prev_segment(self):
        if not self._segments:
            return
        sec = self._studio_timeline.value() / 1000.0
        prev_idx = -1
        for i, s in enumerate(self._segments):
            if s["start"] < sec - 0.05:
                prev_idx = i
        if prev_idx >= 0:
            self._seek_to_segment(prev_idx)

    def _jump_next_segment(self):
        if not self._segments:
            return
        sec = self._studio_timeline.value() / 1000.0
        for i, s in enumerate(self._segments):
            if s["start"] > sec + 0.05:
                self._seek_to_segment(i)
                return

    def _seek_to_segment(self, idx: int):
        if idx < 0 or idx >= len(self._segments):
            return
        self._active_idx = idx
        start_ms = int(float(self._segments[idx]["start"]) * 1000)
        end_ms = int(float(self._segments[idx]["end"]) * 1000)
        ms = start_ms + 30
        if end_ms > start_ms:
            ms = min(ms, end_ms - 1)
        if self._has_realtime_player and self._player:
            self._player.setPosition(ms)
        self._studio_timeline.setValue(ms)
        self._sync_active_segment_from_timeline()
        self._update_live_overlay()

    def _update_live_overlay(self):
        if not self._has_realtime_player or not self._studio_video_host:
            return
        self._present_realtime_frame()

    def eventFilter(self, obj, event):
        if (
            obj is getattr(self, "_studio_video_host", None)
            and event.type() == QEvent.Type.Resize
        ):
            self._update_live_overlay()
        return super().eventFilter(obj, event)

    def _segments_to_text(self) -> str:
        lines = []
        for i, s in enumerate(self._segments, 1):
            lines += [
                str(i),
                f"[{s['start']}s–{s['end']}s] {s.get('original', '')}",
                s.get("translated", ""),
                "",
            ]
        return "\n".join(lines)

    def _reload_studio_segments(self):
        if not hasattr(self, "_studio_segment_list"):
            return
        self._studio_segment_list.clear()
        for idx, s in enumerate(self._segments):
            it = QListWidgetItem(
                f"[{s['start']:.2f}s - {s['end']:.2f}s]  {s.get('translated','')}"
            )
            it.setData(Qt.ItemDataRole.UserRole, idx)
            self._studio_segment_list.addItem(it)

    def _find_segment_idx_at(self, sec: float) -> int:
        for i, s in enumerate(self._segments):
            start = float(s["start"])
            end = float(s["end"])
            if i == len(self._segments) - 1:
                if start <= sec <= end:
                    return i
            elif start <= sec < end:
                return i
        return -1

    def _sync_active_segment_from_timeline(self):
        sec = (
            self._studio_timeline.value() if hasattr(self, "_studio_timeline") else 0
        ) / 1000.0
        idx = self._find_segment_idx_at(sec)
        if idx == self._active_idx:
            return
        self._active_idx = idx
        if idx < 0:
            self._studio_seg_edit.blockSignals(True)
            self._studio_seg_edit.clear()
            self._studio_seg_edit.blockSignals(False)
            return
        item = self._studio_segment_list.item(idx)
        if item:
            self._studio_segment_list.blockSignals(True)
            self._studio_segment_list.setCurrentItem(item)
            self._studio_segment_list.blockSignals(False)
        self._studio_seg_edit.blockSignals(True)
        self._studio_seg_edit.setPlainText(self._segments[idx].get("translated", ""))
        self._studio_seg_edit.blockSignals(False)

    def _on_timeline_changed(self, _value: int):
        self._update_timeline_label()
        self._sync_active_segment_from_timeline()
        if not self._has_realtime_player:
            self._studio_preview_timer.start()

    def _on_segment_clicked(self, item: QListWidgetItem):
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is None or not (0 <= idx < len(self._segments)):
            return
        self._active_idx = int(idx)
        seg = self._segments[self._active_idx]
        self._studio_seg_edit.setPlainText(seg.get("translated", ""))
        self._seek_to_segment(self._active_idx)
        if not self._has_realtime_player:
            self._render_studio_preview(force=True)

    def _update_selected_segment(self):
        if self._active_idx < 0 or self._active_idx >= len(self._segments):
            QMessageBox.information(self, "No segment", "Select a segment first.")
            return
        self._segments[self._active_idx][
            "translated"
        ] = self._studio_seg_edit.toPlainText().strip()
        self._dirty = True
        self._reload_studio_segments()
        self._trans_edit.blockSignals(True)
        self._trans_edit.setPlainText(self._segments_to_text())
        self._trans_edit.blockSignals(False)
        self._update_title()
        self._render_studio_preview(force=True)

    def _render_studio_preview(self, force: bool = False):
        if self._has_realtime_player:
            return
        if not hasattr(self, "_studio_video_lbl"):
            return
        if not self._source_file or not Path(self._source_file).exists():
            self._studio_video_lbl.setText("No source video")
            return

        sec = (
            self._studio_timeline.value() if hasattr(self, "_studio_timeline") else 0
        ) / 1000.0
        frame_path = self._studio_tmp_dir / "frame.jpg"
        try:
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                f"{sec:.3f}",
                "-i",
                self._source_file,
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(frame_path),
            ]
            subprocess.run(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
            )
        except Exception:
            self._studio_video_lbl.setText("Cannot extract frame (ffmpeg)")
            return

        if not frame_path.exists():
            self._studio_video_lbl.setText("Cannot render frame")
            return

        pix = QPixmap(str(frame_path))
        if pix.isNull():
            self._studio_video_lbl.setText("Cannot load frame")
            return

        draw = QPixmap(pix)
        draw = self._paint_overlay_on_pixmap(draw)
        scaled = draw.scaled(
            self._studio_video_lbl.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._studio_video_lbl.setPixmap(scaled)

    def _save_studio_only(self):
        if self._session is None:
            QMessageBox.warning(self, "No session", "Load a session first.")
            return
        payload = self._studio_payload()
        try:
            self._session.save_info(
                payload.get("title", ""), self._session.description or ""
            )
            if hasattr(self._session, "save_subtitle_studio"):
                self._session.save_subtitle_studio(payload)
        except Exception as e:
            QMessageBox.critical(
                self, "Save failed", f"Could not save studio settings:\n{e}"
            )
            return
        self._apply_studio_to_step3()
        self._studio_dirty = False
        self._update_title()
        self._dirty_lbl.setText("✅ Studio saved")
        self.saved.emit(str(self._session.folder))

    def _update_title(self):
        if self._session:
            name = self._session.folder.name
            self._title_lbl.setText(f"Subtitles — {name}")
        else:
            self._title_lbl.setText("Subtitles")

        if self._dirty:
            self._dirty_lbl.setText("● unsaved")
            self._btn_save.setEnabled(self._session is not None)
        elif self._studio_dirty:
            self._dirty_lbl.setText("● studio unsaved")
        else:
            self._dirty_lbl.setText("")

    def _save(self):
        if self._session is None:
            QMessageBox.warning(self, "No session", "Load a session first.")
            return

        text = self._trans_edit.toPlainText()
        if self._mode == "studio" and self._segments:
            text = self._segments_to_text()
            self._trans_edit.blockSignals(True)
            self._trans_edit.setPlainText(text)
            self._trans_edit.blockSignals(False)
        segments = _parse_translated_block(text)

        if not segments:
            QMessageBox.warning(
                self,
                "Parse error",
                "Could not parse subtitle blocks.\n\n"
                "Make sure each block has the format:\n"
                "[start–end]\n"
                "  original\n"
                "  → translated",
            )
            return

        # Write JSON
        json_path = self._session.step2_json
        srt_path = self._session.step2_srt

        try:
            json_path.write_text(
                json.dumps(segments, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _write_srt(segments, str(srt_path))
            # Keep studio settings and title in session metadata.
            payload = self._studio_payload()
            self._session.save_info(
                payload.get("title", ""), self._session.description or ""
            )
            if hasattr(self._session, "save_subtitle_studio"):
                self._session.save_subtitle_studio(payload)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", f"Could not write files:\n{e}")
            return

        self._dirty = False
        self._studio_dirty = False
        self._segments = segments
        self._reload_studio_segments()
        self._apply_studio_to_step3()
        self._update_title()
        self._dirty_lbl.setText(f"✅ Saved {len(segments)} segments")
        self.saved.emit(str(self._session.folder))
