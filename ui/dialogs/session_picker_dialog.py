"""Dialog hiển thị danh sách sessions, cho phép chọn hoặc clear."""

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from core.session import Session


class SessionPickerDialog(QDialog):
    """Dialog hiển thị danh sách sessions, cho phép chọn hoặc clear."""

    def __init__(self, base_dir: str, parent=None):
        super().__init__(parent)
        self.base_dir = base_dir
        self.selected_folder = None
        self.setWindowTitle("📁  Session Manager")
        self.setMinimumSize(700, 480)
        self.setStyleSheet(parent.styleSheet() if parent else "")
        self._sessions: list = []
        self._setup_ui()
        self._load_sessions()

    def _setup_ui(self):
        v = QVBoxLayout(self)
        v.setSpacing(10)

        hdr = QLabel("Select a previous session to resume, or start a new one.")
        hdr.setStyleSheet("color:#a0a8ff;font-size:12px;")
        v.addWidget(hdr)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(self._accept)
        v.addWidget(self._list, stretch=1)

        self._info_lbl = QLabel("")
        self._info_lbl.setStyleSheet(
            "color:#888;font-size:11px;font-family:'Consolas','SF Mono',monospace;"
        )
        self._info_lbl.setWordWrap(True)
        v.addWidget(self._info_lbl)
        self._list.currentItemChanged.connect(self._on_select)

        btn_row = QHBoxLayout()

        self._btn_resume = QPushButton("▶  Resume Selected")
        self._btn_resume.setStyleSheet(
            "QPushButton{background:#1a4a2a;color:#5dca8e;border:1px solid #2a6a3a;"
            "font-weight:bold;border-radius:6px;padding:8px 18px;}"
            "QPushButton:hover{background:#2a6a3a;}"
            "QPushButton:disabled{color:#444;background:#1e1e38;}"
        )
        self._btn_resume.setEnabled(False)
        self._btn_resume.clicked.connect(self._accept)
        btn_row.addWidget(self._btn_resume)

        btn_new = QPushButton("✨  New Session")
        btn_new.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #6c63ff,stop:1 #a855f7);color:white;font-weight:bold;"
            "border:none;border-radius:6px;padding:8px 18px;}"
            "QPushButton:hover{background:#5a52d5;}"
        )
        btn_new.clicked.connect(self.reject)
        btn_row.addWidget(btn_new)

        btn_row.addStretch()

        self._btn_clear = QPushButton("🗑️  Clear Session")
        self._btn_clear.setStyleSheet(
            "QPushButton{background:#3a1a1a;color:#ff7070;border:1px solid #6e2d2d;"
            "border-radius:6px;padding:8px 14px;}"
            "QPushButton:hover{background:#5a2020;}"
            "QPushButton:disabled{color:#444;background:#1e1e38;}"
        )
        self._btn_clear.setEnabled(False)
        self._btn_clear.clicked.connect(self._clear_selected)
        btn_row.addWidget(self._btn_clear)

        self._btn_clear_all = QPushButton("🗑️  Clear All")
        self._btn_clear_all.setStyleSheet(
            "QPushButton{background:#3a1a1a;color:#ff7070;border:1px solid #6e2d2d;"
            "border-radius:6px;padding:8px 14px;}"
            "QPushButton:hover{background:#5a2020;}"
        )
        self._btn_clear_all.clicked.connect(self._clear_all)
        btn_row.addWidget(self._btn_clear_all)

        v.addLayout(btn_row)

    def _load_sessions(self):
        self._sessions = Session.list_sessions(self.base_dir)
        self._list.clear()
        if not self._sessions:
            item = QListWidgetItem("  (No previous sessions found)")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(item)
            return

        for s in self._sessions:
            dt = datetime.fromtimestamp(s["mtime"]).strftime("%Y-%m-%d %H:%M")
            done = " ".join(s["done_steps"]) if s["done_steps"] else "—"
            src = Path(s["source_file"]).name if s["source_file"] else "unknown"
            display_name = s.get("title", "").strip() or s["name"]
            folder_sub = f"  ({s['name']})" if display_name != s["name"] else ""
            text = f"  {display_name}{folder_sub}\n  📄 {src}  |  ✅ {done}  |  💾 {s['size_mb']} MB  |  🕐 {dt}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, s)
            self._list.addItem(item)

    def _on_select(self, item):
        if item is None:
            return
        s = item.data(Qt.ItemDataRole.UserRole)
        if s is None:
            self._info_lbl.setText("")
            self._btn_resume.setEnabled(False)
            self._btn_clear.setEnabled(False)
            return
        self._btn_resume.setEnabled(True)
        self._btn_clear.setEnabled(True)
        done = s["done_steps"]
        src = s["source_file"]
        info = f"📁 {s['folder']}\n📄 Source: {src}\n✅ Completed: {' '.join(done) if done else 'None'}"
        if not Path(src).exists():
            info += "\n⚠️  Source file no longer exists at original path"
        self._info_lbl.setText(info)

    def _accept(self):
        item = self._list.currentItem()
        if item is None:
            return
        s = item.data(Qt.ItemDataRole.UserRole)
        if s:
            self.selected_folder = s["folder"]
            self.accept()

    def _clear_selected(self):
        item = self._list.currentItem()
        if item is None:
            return
        s = item.data(Qt.ItemDataRole.UserRole)
        if s is None:
            return
        reply = QMessageBox.question(
            self,
            "Clear Session",
            f"Delete all files in:\n{s['name']}?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            Session.clear_session(s["folder"])
            self._load_sessions()

    def _clear_all(self):
        reply = QMessageBox.question(
            self,
            "Clear All Sessions",
            f"Delete ALL sessions in:\n{self.base_dir}?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            for s in self._sessions:
                Session.clear_session(s["folder"])
            self._load_sessions()
