"""Session list sidebar for Multi-Session window."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.session import Session

# session_listing stores progress as circled digits, not step IDs
STEP_LABEL_TO_ID = {
    "Step 1": "step1_transcribe",
    "Step 2": "step2_translate",
    "Step 3": "step3_burn",
    "Step 4": "step4_separate",
    "Step 5": "step5_tts",
    "Step 6": "step6_add_voice",
    "Step 7": "step7_publish_info",
}
STEP_ID_TO_MARKER = {
    "step1_transcribe": "①",
    "step2_translate": "②",
    "step3_burn": "③",
    "step4_separate": "④",
    "step5_tts": "⑤",
    "step6_add_voice": "⑥",
    "step7_publish_info": "⑦",
}


class SessionListPanel(QWidget):
    """Left panel: list of sessions với checkbox chọn để chạy."""

    # Emits session dict when user clicks a row (for preview)
    session_clicked = pyqtSignal(dict)
    # Emits after a new session is created
    session_added = pyqtSignal()
    # Emits when selected session checkboxes change
    selection_changed = pyqtSignal()

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

        self._btn_import = QPushButton("↓")
        self._btn_import.setFixedWidth(28)
        self._btn_import.setFixedHeight(24)
        self._btn_import.setToolTip(
            "Import workspace folders as sessions for multi-session processing"
        )
        self._btn_import.setStyleSheet(
            "QPushButton{background:#2a2a4a;color:#80b6ff;border:1px solid #2a4a7a;"
            "font-size:14px;font-weight:bold;border-radius:4px;padding:0;}"
            "QPushButton:hover{background:#3a3a5a;border-color:#80b6ff;}"
        )
        self._btn_import.clicked.connect(self._import_sessions)
        hdr.addWidget(self._btn_import)

        root.addLayout(hdr)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)

        st_lbl = QLabel("Status:")
        st_lbl.setStyleSheet("color:#a0a8ff;font-size:11px;font-weight:600;")
        filter_row.addWidget(st_lbl)

        self._status_filter_combo = QComboBox()
        self._status_filter_combo.addItems(["All", "Done", "Not done"])
        self._status_filter_combo.setFixedHeight(24)
        self._status_filter_combo.setFixedWidth(88)
        self._status_filter_combo.setToolTip(
            "All rows: Done = Step 6 finished (⑥). Per step: Done = that step marker is in done list."
        )
        filter_row.addWidget(self._status_filter_combo)

        step_lbl = QLabel("Step:")
        step_lbl.setStyleSheet("color:#a0a8ff;font-size:11px;font-weight:600;")
        filter_row.addWidget(step_lbl)

        self._step_filter_combo = QComboBox()
        self._step_filter_combo.addItems(
            ["All", "Step 1", "Step 2", "Step 3", "Step 4", "Step 5", "Step 6", "Step 7"]
        )
        self._step_filter_combo.setFixedHeight(24)
        self._step_filter_combo.setFixedWidth(100)
        self._step_filter_combo.setToolTip("Which step to test for Done / Not done")
        filter_row.addWidget(self._step_filter_combo)

        apply_btn = QPushButton("Apply filter")
        apply_btn.setFixedHeight(24)
        apply_btn.setFixedWidth(82)
        apply_btn.setStyleSheet("font-size:11px;padding:2px 8px;")
        apply_btn.setToolTip("Check only rows matching Status + Step below")
        apply_btn.clicked.connect(self._select_filtered)
        filter_row.addWidget(apply_btn)

        select_all_btn = QPushButton("Select all")
        select_all_btn.setFixedHeight(24)
        select_all_btn.setFixedWidth(76)
        select_all_btn.setStyleSheet("font-size:11px;padding:2px 8px;")
        select_all_btn.setToolTip("Reset Status & Step to All, then tick every session")
        select_all_btn.clicked.connect(self._filter_select_all_flat)
        filter_row.addWidget(select_all_btn)

        filter_row.addStretch()
        root.addLayout(filter_row)

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
        self.selection_changed.emit()

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
        chk.stateChanged.connect(self._emit_selection_changed)
        h.addWidget(chk)

        status = self._session_status.get(s["folder"], "idle")
        icon_lbl = QLabel(self.STATUS_ICONS.get(status, "⬜"))
        icon_lbl.setFixedWidth(22)
        icon_lbl.setStyleSheet("font-size:14px;")
        h.addWidget(icon_lbl)

        # Thumbnail (small, if exists)
        thumb_path = s.get("thumbnail", "")
        if thumb_path and Path(thumb_path).exists():
            thumb_lbl = QLabel()
            thumb_lbl.setFixedSize(48, 27)
            pix = QPixmap(thumb_path).scaled(
                48,
                27,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
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

    def _import_sessions(self):
        if not self._base_dir:
            QMessageBox.warning(
                self,
                "No folder",
                "Base folder not set.\nChoose a session base folder in the main window first.",
            )
            return

        root_folder = QFileDialog.getExistingDirectory(
            self,
            "Select workspace root folder to import",
            "",
        )
        if not root_folder:
            return

        created = Session.import_sessions_from_workspace(self._base_dir, root_folder)
        if created:
            self.refresh()
            self.session_added.emit()
            QMessageBox.information(
                self,
                "Import complete",
                f"Imported {len(created)} session(s) from workspace.\n\n"
                f"Created {len(created)} new session folder(s) in the base directory.",
            )
            return

        QMessageBox.information(
            self,
            "Nothing imported",
            "No valid video folders were found in the selected workspace root.",
        )

    def _on_current_changed(self, current, _previous):
        if current is None:
            return
        idx = current.data(Qt.ItemDataRole.UserRole)
        if idx is not None and 0 <= idx < len(self._sessions):
            self.session_clicked.emit(self._sessions[idx])

    def _emit_selection_changed(self, _state: int):
        self.selection_changed.emit()

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
        self.selection_changed.emit()

    def _deselect_all(self):
        for i in range(self._list.count()):
            w = self._list.itemWidget(self._list.item(i))
            if w:
                chk = w.findChild(QCheckBox)
                if chk:
                    chk.setChecked(False)
        self.selection_changed.emit()

    def _marker_for_step_id(self, step_id: str) -> str:
        return STEP_ID_TO_MARKER.get(step_id, "")

    def _step_done(self, session: dict, step_id: str | None) -> bool:
        """True if session list data shows the step finished (uses ①…⑦ markers)."""
        markers = session.get("done_steps") or []
        if not step_id:
            # "Whole session" done = final mixed video (Step 6)
            return "⑥" in markers
        m = self._marker_for_step_id(step_id)
        return bool(m) and m in markers

    def _filter_select_all_flat(self):
        """Reset filters to All / All and tick every row."""
        self._status_filter_combo.blockSignals(True)
        self._step_filter_combo.blockSignals(True)
        self._status_filter_combo.setCurrentText("All")
        self._step_filter_combo.setCurrentText("All")
        self._status_filter_combo.blockSignals(False)
        self._step_filter_combo.blockSignals(False)
        self._select_all()

    def _select_filtered(self):
        step_name = self._step_filter_combo.currentText()
        status_raw = self._status_filter_combo.currentText().strip().lower()
        # Accept EN + common VN phrasing
        if status_raw in ("not done", "notdone", "chưa xong", "chua xong"):
            status_name = "not done"
        elif status_raw == "done" or status_raw == "xong":
            status_name = "done"
        else:
            status_name = "all"

        selected_step_id = (
            None if step_name == "All" else STEP_LABEL_TO_ID.get(step_name)
        )

        for i in range(self._list.count()):
            item = self._list.item(i)
            w = self._list.itemWidget(item)
            if not w:
                continue
            chk = w.findChild(QCheckBox)
            if not chk:
                continue
            idx = item.data(Qt.ItemDataRole.UserRole)
            if idx is None or idx >= len(self._sessions):
                continue
            session = self._sessions[idx]
            step_finished = self._step_done(session, selected_step_id)

            if status_name == "all":
                chk.setChecked(True)
            elif status_name == "done":
                chk.setChecked(step_finished)
            else:
                chk.setChecked(not step_finished)

        self.selection_changed.emit()
