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

from core.ffmpeg_utils import ffmpeg_executable
from PyQt6.QtCore import QEvent, QRect, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PyQt6.QtWidgets import (
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
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QVideoWidget
except Exception:
    QAudioOutput = None
    QMediaPlayer = None
    QVideoWidget = None

from ui.widgets.subtitle_editor_io import (
    media_duration_seconds_ffprobe,
    parse_translated_panel_text,
    write_srt_from_segment_dicts,
)


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
        self._studio_tmp_dir = Path(tempfile.gettempdir()) / "subsync_studio_preview"
        self._studio_tmp_dir.mkdir(parents=True, exist_ok=True)
        self._mode = "default"
        self._dirty = False
        self._studio_dirty = False
        self._autosave_pending = False
        self._segment_apply_timer = QTimer(self)
        self._segment_apply_timer.setSingleShot(True)
        self._segment_apply_timer.setInterval(350)
        self._segment_apply_timer.timeout.connect(self._apply_selected_segment)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(900)
        self._autosave_timer.timeout.connect(self._autosave)
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
        self._duration_ms = int(media_duration_seconds_ffprobe(self._source_file) * 1000)
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
        self._segments = parse_translated_panel_text(trans_text)
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

        if QMediaPlayer and QVideoWidget:
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

            self._studio_video = QVideoWidget(self._studio_video_host)
            self._player.setVideoOutput(self._studio_video)
            host_v.addWidget(self._studio_video)

            self._studio_overlay_lbl = QLabel("", self._studio_video_host)
            self._studio_overlay_lbl.setAlignment(
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
            )
            self._studio_overlay_lbl.setWordWrap(True)
            self._studio_overlay_lbl.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
            )
            self._studio_overlay_lbl.setStyleSheet(
                "color:white;font-weight:600;"
                "background:rgba(0,0,0,80);"
                "padding:6px;border-radius:6px;"
            )
            self._studio_overlay_lbl.hide()

            self._player.positionChanged.connect(self._on_player_position_changed)
            self._player.durationChanged.connect(self._on_player_duration_changed)
            self._player.playbackStateChanged.connect(self._on_playback_state_changed)
            studio_left_v.addWidget(self._studio_video_host)
        else:
            self._player = None
            self._audio = None
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
        self._studio_seg_edit.textChanged.connect(self._on_studio_segment_text_changed)
        studio_right_v.addWidget(self._studio_seg_edit)

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
        hint = QLabel("Auto-saves while editing  ·  Ctrl+S to save now")
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
        self._schedule_autosave()

    def _on_studio_segment_text_changed(self):
        self._segment_apply_timer.start()

    def _schedule_autosave(self):
        if self._session is None:
            return
        self._autosave_pending = True
        self._autosave_timer.start()

    def _on_studio_changed(self):
        if not self._studio_dirty:
            self._studio_dirty = True
            self._update_title()
        self._schedule_autosave()

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
        return {
            "title": self._studio_title_edit.text().strip(),
            "font_family": self._studio_font_combo.currentText().strip(),
            "font_pct": float(self._studio_font_pct.value()),
            "position": self._studio_pos_combo.currentText().strip(),
        }

    def _sync_studio_choices_from_step3(self):
        if self._step3 is None:
            if self._studio_font_combo.count() == 0:
                self._studio_font_combo.addItems(["Arial", "Tahoma", "Verdana"])
            if self._studio_pos_combo.count() == 0:
                self._studio_pos_combo.addItems(
                    ["Bottom center (default)", "Top center", "Middle center"]
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
            refresh = getattr(self._step3, "_refresh_preview", None)
            if callable(refresh):
                refresh()
        except Exception:
            pass
        self._update_live_overlay()
        self._render_studio_preview(force=True)

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
        if studio.get("font_family"):
            self._studio_font_combo.setCurrentText(studio.get("font_family"))
        if studio.get("font_pct") is not None:
            self._studio_font_pct.setValue(float(studio.get("font_pct")))
        if studio.get("position"):
            self._studio_pos_combo.setCurrentText(studio.get("position"))
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
        if (
            not self._has_realtime_player
            or not self._studio_overlay_lbl
            or not self._studio_video_host
        ):
            return
        txt = ""
        if 0 <= self._active_idx < len(self._segments):
            txt = self._segments[self._active_idx].get("translated", "")
        if not txt:
            self._studio_overlay_lbl.hide()
            return

        w = max(280, self._studio_video_host.width() - 40)
        h = max(50, int(self._studio_video_host.height() * 0.22))
        x = 20
        pos = (self._studio_pos_combo.currentText() or "Bottom center").lower()
        if "top" in pos:
            y = int(self._studio_video_host.height() * 0.06)
        elif "middle" in pos or (
            "center" in pos and "bottom" not in pos and "top" not in pos
        ):
            y = int(self._studio_video_host.height() * 0.40)
        else:
            y = int(self._studio_video_host.height() * 0.74)

        self._studio_overlay_lbl.setGeometry(x, y, w, h)
        f = QFont(self._studio_font_combo.currentText() or "Arial")
        f.setPixelSize(
            max(
                12,
                int(
                    self._studio_video_host.height()
                    * float(self._studio_font_pct.value())
                    / 100.0
                ),
            )
        )
        self._studio_overlay_lbl.setFont(f)
        self._studio_overlay_lbl.setText(txt)
        self._studio_overlay_lbl.show()
        self._studio_overlay_lbl.raise_()

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
        self._flush_studio_segment_edit()
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
        self._flush_studio_segment_edit()
        self._active_idx = int(idx)
        seg = self._segments[self._active_idx]
        self._studio_seg_edit.setPlainText(seg.get("translated", ""))
        self._seek_to_segment(self._active_idx)
        if not self._has_realtime_player:
            self._render_studio_preview(force=True)

    def _flush_studio_segment_edit(self):
        if self._segment_apply_timer.isActive():
            self._segment_apply_timer.stop()
            self._apply_selected_segment()

    def _apply_selected_segment(self):
        if self._active_idx < 0 or self._active_idx >= len(self._segments):
            return
        new_text = self._studio_seg_edit.toPlainText().strip()
        if self._segments[self._active_idx].get("translated", "") == new_text:
            return
        self._segments[self._active_idx]["translated"] = new_text
        self._dirty = True
        self._reload_studio_segments()
        item = self._studio_segment_list.item(self._active_idx)
        if item:
            self._studio_segment_list.blockSignals(True)
            self._studio_segment_list.setCurrentItem(item)
            self._studio_segment_list.blockSignals(False)
        self._trans_edit.blockSignals(True)
        self._trans_edit.setPlainText(self._segments_to_text())
        self._trans_edit.blockSignals(False)
        self._update_title()
        self._update_live_overlay()
        self._render_studio_preview(force=True)
        self._schedule_autosave()

    def _update_selected_segment(self):
        if self._active_idx < 0 or self._active_idx >= len(self._segments):
            QMessageBox.information(self, "No segment", "Select a segment first.")
            return
        self._apply_selected_segment()

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
                ffmpeg_executable(),
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
        p = QPainter(draw)
        txt = ""
        if 0 <= self._active_idx < len(self._segments):
            txt = self._segments[self._active_idx].get("translated", "")
        if txt:
            rect = draw.rect()
            f = QFont(self._studio_font_combo.currentText() or "Arial")
            f.setPixelSize(
                max(
                    12,
                    int(rect.height() * float(self._studio_font_pct.value()) / 100.0),
                )
            )
            p.setFont(f)
            pos = (self._studio_pos_combo.currentText() or "Bottom center").lower()
            if "top" in pos:
                y_rect = QRect(
                    20,
                    int(rect.height() * 0.06),
                    rect.width() - 40,
                    int(rect.height() * 0.28),
                )
            elif (
                "middle" in pos
                or "center" in pos
                and "bottom" not in pos
                and "top" not in pos
            ):
                y_rect = QRect(
                    20,
                    int(rect.height() * 0.36),
                    rect.width() - 40,
                    int(rect.height() * 0.28),
                )
            else:
                y_rect = QRect(
                    20,
                    int(rect.height() * 0.72),
                    rect.width() - 40,
                    int(rect.height() * 0.24),
                )

            p.setPen(QPen(QColor("black"), 4))
            p.drawText(
                y_rect,
                Qt.AlignmentFlag.AlignHCenter
                | Qt.AlignmentFlag.AlignVCenter
                | Qt.TextFlag.TextWordWrap,
                txt,
            )
            p.setPen(QColor("white"))
            p.drawText(
                y_rect,
                Qt.AlignmentFlag.AlignHCenter
                | Qt.AlignmentFlag.AlignVCenter
                | Qt.TextFlag.TextWordWrap,
                txt,
            )

        p.end()
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

    def _autosave(self):
        if self._session is None or not self._autosave_pending:
            return
        if not self._dirty and not self._studio_dirty:
            self._autosave_pending = False
            return
        self._save(silent=True)

    def _save(self, silent: bool = False):
        if self._session is None:
            if not silent:
                QMessageBox.warning(self, "No session", "Load a session first.")
            return

        if self._mode == "studio":
            self._flush_studio_segment_edit()

        text = self._trans_edit.toPlainText()
        if self._mode == "studio" and self._segments:
            text = self._segments_to_text()
            self._trans_edit.blockSignals(True)
            self._trans_edit.setPlainText(text)
            self._trans_edit.blockSignals(False)
        segments = parse_translated_panel_text(text)

        if not segments:
            if not silent:
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
            write_srt_from_segment_dicts(segments, str(srt_path))
            # Keep studio settings and title in session metadata.
            payload = self._studio_payload()
            self._session.save_info(
                payload.get("title", ""), self._session.description or ""
            )
            if hasattr(self._session, "save_subtitle_studio"):
                self._session.save_subtitle_studio(payload)
        except Exception as e:
            if not silent:
                QMessageBox.critical(self, "Save failed", f"Could not write files:\n{e}")
            return

        self._dirty = False
        self._studio_dirty = False
        self._autosave_pending = False
        self._segments = segments
        self._reload_studio_segments()
        self._apply_studio_to_step3()
        self._update_title()
        if silent:
            self._dirty_lbl.setText(f"✅ Auto-saved {len(segments)} segments")
        else:
            self._dirty_lbl.setText(f"✅ Saved {len(segments)} segments")
        self.saved.emit(str(self._session.folder))
