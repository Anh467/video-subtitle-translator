"""MultiSessionWindow — run pipeline steps across multiple sessions sequentially.

Mở như một cửa sổ riêng biệt từ MainWindow.
Dùng chung step instances (config) từ MainWindow nhưng không đụng vào session đơn.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.session import Session
from ui.widgets.session_info_editor import SessionInfoEditor
from ui.widgets.step_card import StepCard
from ui.widgets.subtitle_editor import SubtitleEditor

# ── Session list panel ────────────────────────────────────────────────────────


class SessionListPanel(QWidget):
    """Left panel: list of sessions với checkbox chọn để chạy."""

    # Emits session dict when user clicks a row (for preview)
    session_clicked = pyqtSignal(dict)
    # Emits after a new session is created
    session_added = pyqtSignal()

    STATUS_ICONS = {
        "idle": "⬜",
        "queued": "🔵",
        "running": "⏳",
        "done": "✅",
        "error": "❌",
        "skipped": "⏭️",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._base_dir = ""
        self._sessions: list[dict] = []
        self._session_status: dict[str, str] = {}  # folder → overall status
        self._session_step_status: dict[str, dict] = {}  # folder → {step_id: status}
        self._sort_by = "time"
        self._sort_order = "desc"
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        # Header
        hdr = QHBoxLayout()
        lbl = QLabel("Sessions to process")
        lbl.setStyleSheet("color:#a0a8ff;font-weight:600;font-size:12px;")
        hdr.addWidget(lbl)
        hdr.addSpacing(6)

        self._sort_by_combo = QComboBox()
        self._sort_by_combo.addItems(["Time", "Video"])
        self._sort_by_combo.setCurrentText("Time")
        self._sort_by_combo.setToolTip(
            "Sort by session modified time or source video name"
        )
        self._sort_by_combo.setFixedHeight(24)
        self._sort_by_combo.currentTextChanged.connect(self._on_sort_changed)
        hdr.addWidget(self._sort_by_combo)

        self._sort_order_combo = QComboBox()
        self._sort_order_combo.addItems(["Desc", "Asc"])
        self._sort_order_combo.setCurrentText("Desc")
        self._sort_order_combo.setToolTip("Sort order")
        self._sort_order_combo.setFixedHeight(24)
        self._sort_order_combo.currentTextChanged.connect(self._on_sort_changed)
        hdr.addWidget(self._sort_order_combo)

        hdr.addStretch()

        btn_all = QPushButton("All")
        btn_all.setFixedHeight(24)
        btn_all.setFixedWidth(38)
        btn_all.setStyleSheet("font-size:11px;padding:2px 6px;")
        btn_all.clicked.connect(self._select_all)
        hdr.addWidget(btn_all)

        btn_none = QPushButton("None")
        btn_none.setFixedHeight(24)
        btn_none.setFixedWidth(42)
        btn_none.setStyleSheet("font-size:11px;padding:2px 6px;")
        btn_none.clicked.connect(self._deselect_all)
        hdr.addWidget(btn_none)

        btn_refresh = QPushButton("↻")
        btn_refresh.setFixedWidth(28)
        btn_refresh.setFixedHeight(24)
        btn_refresh.setToolTip("Refresh session list")
        btn_refresh.clicked.connect(self.refresh)
        hdr.addWidget(btn_refresh)

        self._btn_add = QPushButton("＋")
        self._btn_add.setFixedWidth(28)
        self._btn_add.setFixedHeight(24)
        self._btn_add.setToolTip("Add new session from video/audio file")
        self._btn_add.setStyleSheet(
            "QPushButton{background:#1a3a2a;color:#5dca8e;border:1px solid #2a6a4a;"
            "font-size:14px;font-weight:bold;border-radius:4px;padding:0;}"
            "QPushButton:hover{background:#2a5a3a;border-color:#5dca8e;}"
        )
        self._btn_add.clicked.connect(self._add_session)
        hdr.addWidget(self._btn_add)

        root.addLayout(hdr)

        # List
        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget{background:#0e0e1e;border:1px solid #2d2d4e;border-radius:6px;}"
            "QListWidget::item{padding:0;border-bottom:1px solid #1a1a30;}"
            "QListWidget::item:hover{background:#14142a;}"
        )
        root.addWidget(self._list, stretch=1)

        self._list.currentItemChanged.connect(self._on_current_changed)

        legend = QLabel("⬜ idle  🔵 queued  ⏳ running  ✅ done  ❌ error  ⏭️ skipped")
        legend.setStyleSheet("color:#444;font-size:10px;")
        root.addWidget(legend)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_base_dir(self, base_dir: str):
        self._base_dir = base_dir
        self.refresh()

    def refresh(self):
        if not self._base_dir:
            return
        self._sessions = Session.list_sessions(self._base_dir)
        self._apply_sort()
        self._rebuild_list(preserve_checked=True)

    def get_selected_sessions(self) -> list[dict]:
        result = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            w = self._list.itemWidget(item)
            if w is None:
                continue
            chk = w.findChild(QCheckBox)
            if chk and chk.isChecked():
                idx = item.data(Qt.ItemDataRole.UserRole)
                if idx is not None and 0 <= idx < len(self._sessions):
                    result.append(self._sessions[idx])
        return result

    def set_session_status(self, folder: str, status: str, step_id: str = ""):
        self._session_status[folder] = status
        if step_id:
            self._session_step_status.setdefault(folder, {})[step_id] = status
        self._update_row(folder)

    def reset_all_status(self):
        self._session_status.clear()
        self._session_step_status.clear()
        self._rebuild_list(preserve_checked=True)

    def mark_queued(self, folders: list[str]):
        for f in folders:
            self._session_status[f] = "queued"
            self._update_row(f)

    # ── Internal ─────────────────────────────────────────────────────────────

    def _get_checked_folders(self) -> set[str]:
        """Snapshot which folders are currently checked."""
        checked = set()
        for i in range(self._list.count()):
            item = self._list.item(i)
            w = self._list.itemWidget(item)
            if not w:
                continue
            chk = w.findChild(QCheckBox)
            if chk and chk.isChecked():
                idx = item.data(Qt.ItemDataRole.UserRole)
                if idx is not None and 0 <= idx < len(self._sessions):
                    checked.add(self._sessions[idx]["folder"])
        return checked

    def _rebuild_list(self, preserve_checked: bool = False):
        checked_folders = self._get_checked_folders() if preserve_checked else set()
        self._list.clear()
        if not self._sessions:
            item = QListWidgetItem("  (No sessions — choose base folder)")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(item)
            return
        for idx, s in enumerate(self._sessions):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, idx)
            item.setSizeHint(QSize(0, 76))
            w = self._make_row(s, idx)
            if preserve_checked:
                chk = w.findChild(QCheckBox)
                if chk:
                    chk.setChecked(s["folder"] in checked_folders)
            self._list.addItem(item)
            self._list.setItemWidget(item, w)

    def _make_row(self, s: dict, idx: int) -> QWidget:
        container = QWidget()
        container.setStyleSheet("background:transparent;")
        h = QHBoxLayout(container)
        h.setContentsMargins(6, 4, 6, 4)
        h.setSpacing(6)

        chk = QCheckBox()
        chk.setChecked(False)
        chk.setFixedWidth(20)
        h.addWidget(chk)

        status = self._session_status.get(s["folder"], "idle")
        icon_lbl = QLabel(self.STATUS_ICONS.get(status, "⬜"))
        icon_lbl.setFixedWidth(22)
        icon_lbl.setStyleSheet("font-size:14px;")
        h.addWidget(icon_lbl)

        # Thumbnail (small, if exists)
        thumb_path = s.get("thumbnail", "")
        if thumb_path and __import__("pathlib").Path(thumb_path).exists():
            from PyQt6.QtGui import QPixmap

            thumb_lbl = QLabel()
            thumb_lbl.setFixedSize(48, 27)
            pix = QPixmap(thumb_path).scaled(
                48,
                27,
                __import__(
                    "PyQt6.QtCore", fromlist=["Qt"]
                ).Qt.AspectRatioMode.KeepAspectRatio,
                __import__(
                    "PyQt6.QtCore", fromlist=["Qt"]
                ).Qt.TransformationMode.SmoothTransformation,
            )
            thumb_lbl.setPixmap(pix)
            thumb_lbl.setStyleSheet(
                "border:1px solid #2a3a5a;border-radius:3px;background:#0a0a1a;"
            )
            h.addWidget(thumb_lbl)

        info_w = QWidget()
        info_w.setStyleSheet("background:transparent;")
        info_v = QVBoxLayout(info_w)
        info_v.setContentsMargins(0, 0, 0, 0)
        info_v.setSpacing(1)

        src = Path(s["source_file"]).name if s["source_file"] else "unknown.mp4"
        display_name = s.get("title", "").strip() or s["name"]

        # Line 1: source filename
        name_lbl = QLabel(src)
        name_lbl.setStyleSheet("color:#e0e0e0;font-size:12px;font-weight:600;")
        name_lbl.setToolTip(f"Session: {display_name} ({s['name']})")
        info_v.addWidget(name_lbl)

        done_str = " ".join(s["done_steps"]) if s["done_steps"] else "—"
        dt = datetime.fromtimestamp(s["mtime"]).strftime("%m-%d %H:%M")
        folder_hint = f"  📁 {display_name}" if display_name else ""

        # Line 2: progress + size + completion time
        detail = QLabel(f"✅ {done_str}   💾 {s['size_mb']}MB   🕐 {dt}{folder_hint}")
        detail.setStyleSheet("color:#666;font-size:10px;")
        info_v.addWidget(detail)

        # Per-step live status row
        step_status = self._session_step_status.get(s["folder"], {})
        if step_status:
            STEP_ICONS = {
                "step1_transcribe": "①",
                "step2_translate": "②",
                "step3_burn": "③",
                "step4_separate": "④",
                "step5_tts": "⑤",
                "step6_add_voice": "⑥",
                "step7_publish_info": "⑦",
            }
            parts = []
            for sid, icon in STEP_ICONS.items():
                st = step_status.get(sid)
                if st == "done":
                    parts.append(f"✅{icon}")
                elif st == "running":
                    parts.append(f"⏳{icon}")
                elif st == "error":
                    parts.append(f"❌{icon}")
            if parts:
                steps_row = QLabel("  ".join(parts))
                steps_row.setStyleSheet("color:#a0a8ff;font-size:10px;")
                info_v.addWidget(steps_row)

        h.addWidget(info_w, stretch=1)

        btn_open = QPushButton("📂")
        btn_open.setFixedSize(26, 22)
        btn_open.setToolTip("Open this session folder")
        btn_open.setStyleSheet(
            "QPushButton{background:#1a2a3a;color:#60c8ff;border:1px solid #2a4a6a;"
            "border-radius:4px;padding:0;font-size:11px;}"
            "QPushButton:hover{background:#2a4a6a;}"
        )
        btn_open.clicked.connect(
            lambda _=False, folder=s["folder"]: self._open_session_folder(folder)
        )
        h.addWidget(btn_open)
        return container

    def _update_row(self, folder: str):
        for i in range(self._list.count()):
            item = self._list.item(i)
            idx = item.data(Qt.ItemDataRole.UserRole)
            if idx is None or idx >= len(self._sessions):
                continue
            if self._sessions[idx]["folder"] != folder:
                continue
            old_w = self._list.itemWidget(item)
            was_checked = False
            if old_w:
                chk = old_w.findChild(QCheckBox)
                if chk:
                    was_checked = chk.isChecked()
            new_w = self._make_row(self._sessions[idx], idx)
            chk = new_w.findChild(QCheckBox)
            if chk:
                chk.setChecked(was_checked)
            item.setSizeHint(QSize(0, 76))
            self._list.setItemWidget(item, new_w)
            break

    def _add_session(self):
        """Open file picker, create new Session, refresh list, auto-select new row."""
        if not self._base_dir:
            from PyQt6.QtWidgets import QMessageBox

            QMessageBox.warning(
                self,
                "No folder",
                "Base folder not set.\nChoose a session base folder in the main window first.",
            )
            return

        from PyQt6.QtWidgets import QFileDialog

        SUPPORTED = (
            "*.mp4 *.mov *.avi *.mkv *.webm *.flv *.wmv "
            "*.mp3 *.wav *.m4a *.flac *.ogg *.aac *.wma"
        )
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select video/audio files", "", f"Media ({SUPPORTED})"
        )
        if not paths:
            return

        created = []
        for p in paths:
            try:
                sess = Session(self._base_dir, p)
                created.append(sess.folder.name)
            except Exception as e:
                from PyQt6.QtWidgets import QMessageBox

                QMessageBox.warning(
                    self, "Error", f"Cannot create session for:\n{p}\n\n{e}"
                )

        if created:
            self.refresh()
            self.session_added.emit()
            # Auto-select + highlight the first newly created session
            for i in range(self._list.count()):
                item = self._list.item(i)
                idx = item.data(Qt.ItemDataRole.UserRole)
                if idx is not None and self._sessions[idx]["name"] == created[0]:
                    self._list.setCurrentItem(item)
                    break

    def _on_current_changed(self, current, _previous):
        if current is None:
            return
        idx = current.data(Qt.ItemDataRole.UserRole)
        if idx is not None and 0 <= idx < len(self._sessions):
            self.session_clicked.emit(self._sessions[idx])

    def _on_sort_changed(self, _text: str):
        self._sort_by = (
            "video" if self._sort_by_combo.currentText().lower() == "video" else "time"
        )
        self._sort_order = (
            "asc" if self._sort_order_combo.currentText().lower() == "asc" else "desc"
        )
        self._apply_sort()
        self._rebuild_list(preserve_checked=True)

    def _apply_sort(self):
        reverse = self._sort_order == "desc"
        if self._sort_by == "video":
            self._sessions.sort(
                key=lambda s: Path(s.get("source_file") or "").name.lower(),
                reverse=reverse,
            )
        else:
            self._sessions.sort(key=lambda s: s.get("mtime", 0), reverse=reverse)

    def _open_session_folder(self, folder: str):
        import os
        import subprocess
        import sys

        try:
            if sys.platform == "darwin":
                subprocess.run(["open", folder], check=False)
            elif sys.platform == "win32":
                os.startfile(folder)
            else:
                subprocess.run(["xdg-open", folder], check=False)
        except Exception:
            QMessageBox.warning(self, "Open folder", f"Cannot open folder:\n{folder}")

    def _select_all(self):
        for i in range(self._list.count()):
            w = self._list.itemWidget(self._list.item(i))
            if w:
                chk = w.findChild(QCheckBox)
                if chk:
                    chk.setChecked(True)

    def _deselect_all(self):
        for i in range(self._list.count()):
            w = self._list.itemWidget(self._list.item(i))
            if w:
                chk = w.findChild(QCheckBox)
                if chk:
                    chk.setChecked(False)


# ── Multi-Session Window ──────────────────────────────────────────────────────


class MultiSessionWindow(QMainWindow):
    """
    Standalone window để xử lý nhiều session tuần tự.

    Nhận steps[] và base_dir từ MainWindow — dùng chung config,
    không tạo session mới, không đụng vào single-session state.
    """

    def __init__(self, steps: list, base_dir: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("SubSync — Multi-Session Runner")
        self.setMinimumSize(1200, 780)
        self.setStyleSheet(parent.styleSheet() if parent else "")

        self._steps = steps
        self._base_dir = base_dir
        self._pool = QThreadPool.globalInstance()

        # Queue state
        self._job_queue: list[tuple[dict, object]] = []  # (sess_data, step)
        self._worker = None
        self._stop_requested = False
        self._current_session: Session | None = None
        self._multi_started_at = 0.0
        self._multi_total_jobs = 0
        self._multi_done_jobs = 0
        self._multi_total_sessions = 0
        self._multi_failed_sessions: set[str] = set()
        self._multi_session_stats: dict[str, dict] = {}

        # Cards — create fresh cards that wrap the SAME step instances
        # so config edits here and in main window stay in sync
        self._cards: list[StepCard] = []

        self._setup_ui()
        if base_dir:
            self._session_panel.set_base_dir(base_dir)
        # Autofill API keys into newly created step widgets
        self._autofill_keys()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        cw = QWidget()
        self.setCentralWidget(cw)
        root = QVBoxLayout(cw)
        root.setContentsMargins(16, 12, 16, 10)
        root.setSpacing(10)

        # Title
        title_row = QHBoxLayout()
        t = QLabel("Multi-Session Runner")
        t.setStyleSheet(
            "font-size:22px;font-weight:700;color:#c084fc;letter-spacing:1px;"
        )
        sub = QLabel("Process multiple sessions sequentially with the same step config")
        sub.setStyleSheet("font-size:11px;color:#555;margin-left:12px;margin-top:6px;")
        title_row.addWidget(t)
        title_row.addWidget(sub)
        title_row.addSpacing(10)

        self._btn_editor_default = QPushButton("Default")
        self._btn_editor_default.setCheckable(True)
        self._btn_editor_default.setChecked(True)
        self._btn_editor_default.setFixedHeight(26)
        self._btn_editor_default.clicked.connect(
            lambda: self._set_editor_mode("default")
        )
        title_row.addWidget(self._btn_editor_default)

        self._btn_editor_studio = QPushButton("Studio")
        self._btn_editor_studio.setCheckable(True)
        self._btn_editor_studio.setFixedHeight(26)
        self._btn_editor_studio.clicked.connect(lambda: self._set_editor_mode("studio"))
        title_row.addWidget(self._btn_editor_studio)
        title_row.addStretch()
        root.addLayout(title_row)

        # ── Top area: session list (left) + preview (right) ───────────────
        self._top_split = QSplitter(Qt.Orientation.Horizontal)

        # Left: session list only
        self._session_panel = SessionListPanel()
        self._session_panel.session_clicked.connect(self._on_session_clicked)
        self._session_panel.setMinimumWidth(340)
        self._session_panel.setMaximumWidth(500)
        self._top_split.addWidget(self._session_panel)

        # Right: session info editor (top) + subtitle editor (bottom)
        self._right_split = QSplitter(Qt.Orientation.Vertical)

        self._info_editor = SessionInfoEditor()
        self._info_editor.setMaximumHeight(130)
        self._right_split.addWidget(self._info_editor)

        self._subtitle_editor = SubtitleEditor()
        self._preview_title = self._subtitle_editor._title_lbl
        self._subtitle_editor.saved.connect(self._on_subtitle_editor_saved)
        self._subtitle_editor.mode_changed.connect(self._on_editor_mode_changed)
        for step in self._steps:
            if getattr(step, "STEP_ID", "") == "step3_burn":
                self._subtitle_editor.set_step3_bridge(step)
                break
        self._right_split.addWidget(self._subtitle_editor)

        self._right_split.setStretchFactor(0, 0)
        self._right_split.setStretchFactor(1, 1)
        self._top_split.addWidget(self._right_split)
        self._top_split.setStretchFactor(0, 0)
        self._top_split.setStretchFactor(1, 1)
        root.addWidget(self._top_split, stretch=1)

        # ── Bottom: step cards full width ──────────────────────────────────
        cards_container = QWidget()
        cards_h = QHBoxLayout(cards_container)
        cards_h.setSpacing(12)
        cards_h.setContentsMargins(4, 4, 4, 4)

        for step in self._steps:
            card = StepCard(step)
            self._cards.append(card)
            cards_h.addWidget(card)
        cards_h.addStretch()

        cards_container.adjustSize()
        cards_container.setMinimumWidth(cards_container.sizeHint().width())

        self._cards_scroll = QScrollArea()
        self._cards_scroll.setWidgetResizable(False)
        self._cards_scroll.setWidget(cards_container)
        self._cards_scroll.setFixedHeight(220)
        self._cards_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._cards_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._cards_scroll.setStyleSheet(
            "QScrollBar:horizontal{height:8px;background:#111828;border-radius:4px;}"
            "QScrollBar::handle:horizontal{background:#3d3d6e;border-radius:4px;}"
            "QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0;}"
        )
        root.addWidget(self._cards_scroll)

        # ── Run controls ───────────────────────────────────────────────────
        self._run_ctrl = QFrame()
        self._run_ctrl.setStyleSheet(
            "QFrame{background:#111828;border:1px solid #2d2d4e;border-radius:8px;}"
        )
        ctrl_h = QHBoxLayout(self._run_ctrl)
        ctrl_h.setContentsMargins(12, 8, 12, 8)
        ctrl_h.setSpacing(8)

        self._btn_run_selected = QPushButton("▶  Run Selected Sessions")
        self._btn_run_selected.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #6c63ff,stop:1 #a855f7);color:white;font-weight:bold;font-size:14px;"
            "border:none;border-radius:7px;padding:9px 24px;}"
            "QPushButton:hover{background:#5a52d5;}"
            "QPushButton:disabled{background:#2a2a4a;color:#555;}"
        )
        self._btn_run_selected.clicked.connect(self._run_selected)
        ctrl_h.addWidget(self._btn_run_selected)

        self._btn_run_all_sessions = QPushButton("▶▶  Run All Sessions")
        self._btn_run_all_sessions.setStyleSheet(
            "QPushButton{background:#1a3a2a;color:#5dca8e;border:1px solid #2a6a4a;"
            "font-weight:bold;font-size:13px;border-radius:7px;padding:9px 20px;}"
            "QPushButton:hover{background:#2a5a3a;}"
            "QPushButton:disabled{color:#444;background:#1a2a1a;border-color:#252530;}"
        )
        self._btn_run_all_sessions.clicked.connect(self._run_all_sessions)
        ctrl_h.addWidget(self._btn_run_all_sessions)

        self._btn_stop = QPushButton("⏹  Stop After Current")
        self._btn_stop.setStyleSheet(
            "QPushButton{background:#2a1a0a;color:#ffaa55;border:1px solid #6e4a1a;"
            "font-weight:bold;border-radius:6px;padding:8px 16px;}"
            "QPushButton:hover{background:#4a2a0a;border-color:#ffaa55;}"
            "QPushButton:disabled{color:#554422;border-color:#3a2a1a;}"
        )
        self._btn_stop.setEnabled(False)
        self._btn_stop.setVisible(False)
        self._btn_stop.clicked.connect(self._request_stop)
        ctrl_h.addWidget(self._btn_stop)

        self._btn_cancel = QPushButton("✕  Cancel Job")
        self._btn_cancel.setStyleSheet(
            "QPushButton{background:#3a1a1a;color:#ff7070;border:1px solid #6e2d2d;"
            "font-weight:bold;padding:7px 14px;border-radius:6px;}"
            "QPushButton:hover{background:#5a2020;}"
        )
        self._btn_cancel.setVisible(False)
        self._btn_cancel.clicked.connect(self._cancel_job)
        ctrl_h.addWidget(self._btn_cancel)

        ctrl_h.addSpacing(8)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        self._progress.setFixedWidth(160)
        ctrl_h.addWidget(self._progress)

        self._queue_lbl = QLabel("")
        self._queue_lbl.setStyleSheet("color:#ffaa55;font-size:12px;font-weight:600;")
        ctrl_h.addWidget(self._queue_lbl)

        self._prog_lbl = QLabel("")
        self._prog_lbl.setStyleSheet("color:#888;font-size:11px;")
        ctrl_h.addWidget(self._prog_lbl)
        ctrl_h.addStretch()
        root.addWidget(self._run_ctrl)

        # ── Log panel ──────────────────────────────────────────────────────
        self._log_wrap = QWidget()
        log_v = QVBoxLayout(self._log_wrap)
        log_v.setContentsMargins(0, 0, 0, 0)
        log_v.setSpacing(3)
        log_lbl = QLabel("Log")
        log_lbl.setStyleSheet("color:#a0a8ff;font-size:11px;font-weight:600;")
        log_v.addWidget(log_lbl)
        self._log_edit = QTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setMaximumHeight(160)
        self._log_edit.setPlaceholderText("Multi-session pipeline log…")
        self._log_edit.setStyleSheet(
            "background:#0f0f23;border:1px solid #2d2d4e;border-radius:6px;"
            "padding:8px;color:#d0d0d0;font-family:'SF Mono','Consolas',monospace;font-size:12px;"
        )
        log_v.addWidget(self._log_edit)
        root.addWidget(self._log_wrap)

        self._status_bar = QStatusBar()
        self._status_bar.setStyleSheet(
            "background:#0f0f23;border-top:1px solid #2d2d4e;color:#666;"
        )
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready — select sessions and steps, then Run")

    def _set_editor_mode(self, mode: str):
        if hasattr(self, "_subtitle_editor") and self._subtitle_editor:
            self._subtitle_editor.set_mode(mode)
        is_studio = mode == "studio"
        self._session_panel.setVisible(not is_studio)
        self._info_editor.setVisible(not is_studio)
        self._cards_scroll.setVisible(not is_studio)
        self._run_ctrl.setVisible(not is_studio)
        self._log_wrap.setVisible(not is_studio)
        self._btn_editor_default.setChecked(not is_studio)
        self._btn_editor_studio.setChecked(is_studio)
        if is_studio:
            self._top_split.setSizes([0, 1])
            self._right_split.setSizes([0, 1])

    def _on_editor_mode_changed(self, mode: str):
        self._set_editor_mode(mode)

    # ── Public: called by MainWindow when base_dir changes ────────────────────

    def update_base_dir(self, base_dir: str):
        self._base_dir = base_dir
        self._session_panel.set_base_dir(base_dir)
        self._autofill_keys()

    def _autofill_keys(self):
        """Push API keys from manager into step widgets.
        Called on init and whenever base_dir changes.
        Step cards create new widgets via build_config_widget() which clears
        previous values — this restores them from the persistent manager.
        """
        try:
            from core.api_keys import get_manager
            from core.pipeline.selection import (
                translate_key_candidates,
                tts_backend_from_label,
                tts_key_candidates,
            )

            mgr = get_manager()
            service_keys = mgr.to_dict_by_service()

            for step in self._steps:
                sid = getattr(step, "STEP_ID", "")

                # Step 1: Whisper API key
                if sid == "step1_transcribe":
                    if hasattr(step, "_api_key_edit") and step._api_key_edit:
                        key = service_keys.get("openai", "")
                        if key:
                            step._api_key_edit.blockSignals(True)
                            step._api_key_edit.setText(key)
                            step._api_key_edit.blockSignals(False)

                # Step 2: translate API key
                elif sid == "step2_translate":
                    if (
                        hasattr(step, "_api_edit")
                        and step._api_edit
                        and hasattr(step, "_backend_combo")
                        and step._backend_combo
                    ):
                        backend_text = step._backend_combo.currentText().lower()
                        bk = (
                            "gemini"
                            if "gemini" in backend_text
                            else "openai" if "openai" in backend_text else "google"
                        )
                        for svc in translate_key_candidates(bk):
                            key = service_keys.get(svc, "")
                            if key:
                                step._api_edit.blockSignals(True)
                                step._api_edit.setText(key)
                                step._api_edit.blockSignals(False)
                                break

                # Step 5: TTS API key
                elif sid == "step5_tts":
                    if (
                        hasattr(step, "_api_edit")
                        and step._api_edit
                        and hasattr(step, "_backend_combo")
                        and step._backend_combo
                    ):
                        bk = tts_backend_from_label(step._backend_combo.currentText())
                        for svc in tts_key_candidates(bk):
                            key = service_keys.get(svc, "")
                            if key:
                                step._api_edit.blockSignals(True)
                                step._api_edit.setText(key)
                                step._api_edit.blockSignals(False)
                                if hasattr(step, "_selected_api_key"):
                                    step._selected_api_key = key.strip()
                                break

                # Step 7: Publish Info (Gemini API key)
                elif sid == "step7_publish_info":
                    if hasattr(step, "_api_edit") and step._api_edit:
                        key = service_keys.get("gemini", "")
                        if key:
                            step._api_edit.blockSignals(True)
                            step._api_edit.setText(key)
                            step._api_edit.blockSignals(False)
                            if hasattr(step, "_selected_api_key"):
                                step._selected_api_key = key.strip()
        except Exception:
            pass  # Never block UI due to autofill failure

    # ── Session preview ───────────────────────────────────────────────────────

    def _small_lbl(self, text: str) -> QLabel:
        l = QLabel(text)
        l.setStyleSheet("color:#666;font-size:10px;font-weight:600;")
        return l

    def _set_step3_source_file(self, source_file: str | None):
        if hasattr(self, "_subtitle_editor") and self._subtitle_editor:
            self._subtitle_editor.set_source_file(source_file)
        for step in self._steps:
            if getattr(step, "STEP_ID", "") != "step3_burn":
                continue
            setter = getattr(step, "set_source_file", None)
            if callable(setter):
                setter(source_file)
            break

    def _apply_session_studio_to_step3(self, session: Session):
        """Load per-session studio style and push into Step 3 controls."""
        try:
            studio = session.load_subtitle_studio()
        except Exception:
            studio = {}
        if not studio:
            return

        for step in self._steps:
            if getattr(step, "STEP_ID", "") != "step3_burn":
                continue
            ff = getattr(step, "_font_family_combo", None)
            if ff and studio.get("font_family"):
                ff.setCurrentText(str(studio.get("font_family")))
            fs = getattr(step, "_font_pct_spin", None)
            if fs and studio.get("font_pct") is not None:
                fs.setValue(float(studio.get("font_pct")))
            pos = getattr(step, "_pos_combo", None)
            if pos and studio.get("position"):
                pos.setCurrentText(str(studio.get("position")))
            refresh = getattr(step, "_refresh_preview", None)
            if callable(refresh):
                refresh()
            break

    def _on_session_clicked(self, sess_data: dict):
        """Load session into info editor, subtitle editor + restore step card statuses."""
        folder = sess_data["folder"]
        try:
            session = Session.load(folder)
        except Exception as e:
            self._subtitle_editor.clear()
            self._info_editor.clear()
            self._subtitle_editor.set_orig_placeholder(f"Cannot load session:\n{e}")
            return

        # Load info editor
        self._info_editor.load_session(session)
        self._set_step3_source_file(session.source_file)

        # Load subtitle preview + editor
        self._subtitle_editor.load_session(session)

        # Update TTS char count for clicked session
        for step in self._steps:
            if hasattr(step, "update_char_count"):
                step.update_char_count(session)

        # Restore step card statuses
        done_steps = session.done_steps()
        for step, card in zip(self._steps, self._cards):
            card.reset()
            if step.STEP_ID in done_steps:
                out_path = self._step_output_path(step, session)
                card.set_status("✅ Done (saved)", "loaded", out_path)
            else:
                card.set_status("Waiting…", "idle")

    def _on_subtitle_editor_saved(self, folder: str):
        """Reload active session and refresh session list row after studio/text save."""
        try:
            session = Session.load(folder)
        except Exception:
            return
        self._current_session = session
        self._info_editor.load_session(session)
        self._session_panel.refresh()

    # ── Run logic ─────────────────────────────────────────────────────────────

    def _run_selected(self):
        sessions = self._session_panel.get_selected_sessions()
        if not sessions:
            QMessageBox.warning(self, "Nothing selected", "Check at least one session.")
            return
        self._start_queue(sessions)

    def _run_all_sessions(self):
        # Run ALL sessions regardless of checkbox state — do NOT call _select_all()
        all_sessions = self._session_panel._sessions
        if not all_sessions:
            QMessageBox.information(
                self, "No sessions", "No sessions found in base folder."
            )
            return
        self._start_queue(all_sessions)

    def _start_queue(self, sessions: list[dict]):
        if self._worker is not None:
            QMessageBox.warning(self, "Busy", "A job is already running.")
            return

        enabled_steps = [s for s, c in zip(self._steps, self._cards) if c.is_enabled()]
        if not enabled_steps:
            QMessageBox.information(self, "Nothing to run", "Enable at least one step.")
            return

        # Build flat sequential job list
        self._job_queue = [
            (sess_data, step) for sess_data in sessions for step in enabled_steps
        ]

        total = len(self._job_queue)
        self._multi_started_at = time.perf_counter()
        self._multi_total_jobs = total
        self._multi_done_jobs = 0
        self._multi_total_sessions = len(sessions)
        self._multi_failed_sessions = set()
        self._multi_session_stats = {
            s["folder"]: {
                "name": s["name"],
                "total_steps": len(enabled_steps),
                "done_steps": 0,
                "failed_steps": 0,
                "start_at": 0.0,
                "end_at": 0.0,
            }
            for s in sessions
        }
        self._stop_requested = False

        self._log_edit.clear()
        self._session_panel.reset_all_status()

        self._log(
            f"🚀 Multi-session queue: {len(sessions)} sessions × "
            f"{len(enabled_steps)} steps = {total} jobs"
        )
        self._log("   Sessions: " + ",  ".join(s["name"] for s in sessions))
        self._log("   Steps:    " + "  →  ".join(s.LABEL for s in enabled_steps))

        # Mark all as queued
        self._session_panel.mark_queued([s["folder"] for s in sessions])

        self._set_running(True)
        self._run_next()

    def _run_next(self):
        if self._stop_requested:
            self._stop_requested = False
            remaining = len(self._job_queue)
            elapsed = time.perf_counter() - self._multi_started_at
            processed_sessions = sum(
                1
                for st in self._multi_session_stats.values()
                if st.get("start_at", 0) > 0
            )
            actions_per_min = (
                (self._multi_done_jobs * 60.0 / elapsed) if elapsed > 0 else 0.0
            )
            sessions_per_min = (
                (processed_sessions * 60.0 / elapsed) if elapsed > 0 else 0.0
            )
            self._job_queue.clear()
            self._set_running(False)
            self._queue_lbl.setText("")
            self._log(f"⏹  Stopped — {remaining} job(s) skipped.")
            self._log(
                f"📊 Multi summary: sessions={processed_sessions}/{self._multi_total_sessions} processed, "
                f"actions={self._multi_done_jobs}/{self._multi_total_jobs}, elapsed={elapsed:.2f}s, "
                f"throughput={actions_per_min:.2f} actions/min, {sessions_per_min:.2f} sessions/min"
            )
            return

        if not self._job_queue:
            self._set_running(False)
            self._queue_lbl.setText("")
            elapsed = time.perf_counter() - self._multi_started_at
            ok_sessions = self._multi_total_sessions - len(self._multi_failed_sessions)
            actions_per_min = (
                (self._multi_done_jobs * 60.0 / elapsed) if elapsed > 0 else 0.0
            )
            sessions_per_min = (
                (self._multi_total_sessions * 60.0 / elapsed) if elapsed > 0 else 0.0
            )
            self._log("🎉 All sessions processed!")
            self._log(
                f"📊 Multi summary: sessions ok={ok_sessions}, failed={len(self._multi_failed_sessions)}, "
                f"total={self._multi_total_sessions}; actions done={self._multi_done_jobs}/{self._multi_total_jobs}; elapsed={elapsed:.2f}s; "
                f"throughput={actions_per_min:.2f} actions/min, {sessions_per_min:.2f} sessions/min"
            )
            for folder, st in self._multi_session_stats.items():
                sess_elapsed = (st["end_at"] or time.perf_counter()) - (
                    st["start_at"] or self._multi_started_at
                )
                session_apm = (
                    (st["done_steps"] * 60.0 / sess_elapsed)
                    if sess_elapsed > 0
                    else 0.0
                )
                self._log(
                    f"   • [{st['name']}] actions={st['done_steps']}/{st['total_steps']} "
                    f"failed_steps={st['failed_steps']} time={sess_elapsed:.2f}s throughput={session_apm:.2f} actions/min"
                )
            self._status_bar.showMessage("✅ All sessions complete!")
            self._session_panel.refresh()
            return

        sess_data, step = self._job_queue[0]
        folder = sess_data["folder"]
        remaining = len(self._job_queue)

        # Load session object
        try:
            session = Session.load(folder)
        except Exception as e:
            self._log(f"❌ Cannot load session [{sess_data['name']}]: {e}")
            self._job_queue.pop(0)
            self._run_next()
            return

        self._current_session = session
        self._set_step3_source_file(session.source_file)
        self._apply_session_studio_to_step3(session)
        st = self._multi_session_stats.get(folder)
        if st and not st["start_at"]:
            st["start_at"] = time.perf_counter()

        # Update UI
        self._session_panel.set_session_status(folder, "running", step.STEP_ID)
        self._queue_lbl.setText(
            f"[{sess_data['name']}]  {step.LABEL}  ({remaining} left)"
        )
        self._log(f"\n▶  [{sess_data['name']}]  {step.LABEL}")

        # Update base dir for steps with set_base_dir (e.g. BurnStep channel profiles)
        for s in self._steps:
            setter = getattr(s, "set_base_dir", None)
            if callable(setter):
                setter(self._base_dir)

        # Ensure API keys are filled before collecting config
        self._autofill_keys()

        config = step.collect_config()
        card = self._card_for(step)
        card.set_status("▶ Running…", "running")
        card.set_running(True)
        self._progress.setVisible(True)

        worker = step.make_worker(session, config)
        self._worker = worker
        worker.signals.progress.connect(self._log)
        worker.signals.finished.connect(
            lambda r, s=step, sd=sess_data: self._on_done(s, sd, r)
        )
        worker.signals.error.connect(
            lambda e, s=step, sd=sess_data: self._on_error(s, sd, e)
        )
        worker.signals.cancelled.connect(
            lambda s=step, sd=sess_data: self._on_cancelled(s, sd)
        )
        self._pool.start(worker)

    def _on_done(self, step, sess_data: dict, result):
        self._worker = None
        folder = sess_data["folder"]
        self._multi_done_jobs += 1
        st = self._multi_session_stats.get(folder)
        if st:
            st["done_steps"] += 1

        card = self._card_for(step)
        card.set_running(False)
        out_path = result if isinstance(result, str) else ""
        if not out_path and self._current_session:
            out_path = self._step_output_path(step, self._current_session)
        card.set_status("✅ Done", "done", out_path)

        self._session_panel.set_session_status(folder, "done", step.STEP_ID)

        if step.STEP_ID == "step7_publish_info":
            try:
                self._current_session = Session.load(folder)
                self._info_editor.load_session(self._current_session)
                self._session_panel.refresh()
            except Exception:
                pass

        # Check if this session has no more jobs after this one
        more_for_session = any(sd["folder"] == folder for sd, _ in self._job_queue[1:])
        if not more_for_session:
            self._session_panel.set_session_status(folder, "done")
            st = self._multi_session_stats.get(folder)
            if st:
                st["end_at"] = time.perf_counter()
                sess_elapsed = st["end_at"] - st["start_at"]
                session_apm = (
                    (st["done_steps"] * 60.0 / sess_elapsed)
                    if sess_elapsed > 0
                    else 0.0
                )
                self._log(
                    f"✅ Session [{sess_data['name']}] complete — actions {st['done_steps']}/{st['total_steps']} in {sess_elapsed:.2f}s | throughput={session_apm:.2f} actions/min"
                )
            else:
                self._log(f"✅ Session [{sess_data['name']}] complete")

        elapsed = time.perf_counter() - self._multi_started_at
        actions_per_min = (
            (self._multi_done_jobs * 60.0 / elapsed) if elapsed > 0 else 0.0
        )
        self._log(
            f"📈 Multi progress: actions {self._multi_done_jobs}/{self._multi_total_jobs} | avg throughput={actions_per_min:.2f} actions/min"
        )

        if self._job_queue and self._job_queue[0][1] is step:
            self._job_queue.pop(0)

        self._run_next()

    def _on_error(self, step, sess_data: dict, msg: str):
        self._worker = None
        folder = sess_data["folder"]
        self._multi_failed_sessions.add(folder)
        st = self._multi_session_stats.get(folder)
        if st:
            st["failed_steps"] += 1
            st["end_at"] = time.perf_counter()

        card = self._card_for(step)
        card.set_running(False)
        card.set_status("❌ Error", "error")

        self._session_panel.set_session_status(folder, "error", step.STEP_ID)
        self._log(f"❌ [{sess_data['name']}] [{step.LABEL}]: {msg}")
        if st:
            sess_elapsed = st["end_at"] - (st["start_at"] or st["end_at"])
            session_apm = (
                (st["done_steps"] * 60.0 / sess_elapsed) if sess_elapsed > 0 else 0.0
            )
            self._log(
                f"📊 Session [{sess_data['name']}] failed — actions {st['done_steps']}/{st['total_steps']} in {sess_elapsed:.2f}s | throughput={session_apm:.2f} actions/min"
            )

        # Skip remaining jobs for this session, continue with others
        self._job_queue = [
            (sd, s) for sd, s in self._job_queue if sd["folder"] != folder
        ]
        self._log(f"⏭️  Skipping remaining steps for [{sess_data['name']}]")
        self._run_next()

    def _on_cancelled(self, step, sess_data: dict):
        self._worker = None
        self._set_running(False)
        self._job_queue.clear()
        self._queue_lbl.setText("")
        card = self._card_for(step)
        card.set_running(False)
        card.set_status("🚫 Cancelled", "idle")
        self._session_panel.set_session_status(sess_data["folder"], "idle")
        self._log("🚫 Queue cancelled.")

    def _request_stop(self):
        self._stop_requested = True
        self._btn_stop.setEnabled(False)
        self._btn_stop.setText("Stopping…")
        self._log("⏹  Stop after current job — waiting…")

    def _cancel_job(self):
        if self._worker and hasattr(self._worker, "cancel"):
            self._worker.cancel()
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.setText("Cancelling…")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_running(self, running: bool):
        self._btn_run_selected.setEnabled(not running)
        self._btn_run_all_sessions.setEnabled(not running)
        self._btn_stop.setVisible(running)
        self._btn_stop.setEnabled(running)
        self._btn_stop.setText("⏹  Stop After Current")
        self._btn_cancel.setVisible(running)
        self._btn_cancel.setEnabled(True)
        self._btn_cancel.setText("✕  Cancel Job")
        self._progress.setVisible(running)
        if not running:
            self._prog_lbl.setText("")

    def _card_for(self, step) -> StepCard:
        return self._cards[self._steps.index(step)]

    def _step_output_path(self, step, session) -> str:
        attr = {
            "step1_transcribe": "step1_json",
            "step2_translate": "step2_srt",
            "step3_burn": "step3_video",
            "step4_separate": "step4_vocals",
            "step5_tts": "step5_tts",
            "step6_add_voice": "step6_video",
            "step7_publish_info": "step7_info",
        }.get(step.STEP_ID, "")
        if attr:
            p = getattr(session, attr, None)
            if p and Path(str(p)).exists():
                return str(p)
        return ""

    def _log(self, msg: str):
        self._log_edit.append(msg)
        self._prog_lbl.setText(msg.strip()[:80])
        self._status_bar.showMessage(msg.strip()[:120])
        from core.log_file import FileLogger

        FileLogger.get().write(msg)
