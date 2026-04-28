"""
SessionInfoEditor — compact widget to view/edit session title + description.

Saves to session.json via session.save_info(title, description).
Works in both MainWindow and MultiSessionWindow.

Usage:
    editor = SessionInfoEditor()
    editor.load_session(session)
    editor.clear()
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class SessionInfoEditor(QWidget):
    """Title + description editor bound to a Session object."""

    saved = pyqtSignal(str)  # emits session folder path

    _STYLE_EDIT = (
        "background:#111828;border:1px solid #2a3a5a;border-radius:5px;"
        "padding:4px 8px;color:#e0e0e0;font-size:12px;"
    )
    _STYLE_EDIT_FOCUS = (
        "background:#111828;border:1px solid #6c63ff;border-radius:5px;"
        "padding:4px 8px;color:#e0e0e0;font-size:12px;"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._session = None
        self._dirty = False
        self._setup_ui()

    # ── Public API ────────────────────────────────────────────────────────────

    def load_session(self, session):
        self._session = session
        self._dirty = False
        self._title_edit.setText(session.title or "")
        self._desc_edit.setPlainText(session.description or "")
        self._folder_lbl.setText(str(session.folder.name))
        self._update_state()

    def clear(self):
        self._session = None
        self._dirty = False
        self._title_edit.clear()
        self._desc_edit.clear()
        self._folder_lbl.setText("—")
        self._update_state()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # Header row
        hdr = QHBoxLayout()
        lbl = QLabel("Session info")
        lbl.setStyleSheet("color:#a0a8ff;font-size:11px;font-weight:600;")
        hdr.addWidget(lbl)

        self._folder_lbl = QLabel("—")
        self._folder_lbl.setStyleSheet(
            "color:#555;font-size:10px;font-family:'SF Mono','Consolas',monospace;"
        )
        hdr.addWidget(self._folder_lbl)
        hdr.addStretch()

        self._dirty_lbl = QLabel("")
        self._dirty_lbl.setStyleSheet("color:#ffaa55;font-size:10px;")
        hdr.addWidget(self._dirty_lbl)

        self._btn_save = QPushButton("💾  Save info")
        self._btn_save.setFixedHeight(26)
        self._btn_save.setStyleSheet(
            "QPushButton{background:#1a3a2a;color:#5dca8e;border:1px solid #2a6a4a;"
            "font-weight:bold;border-radius:5px;padding:2px 12px;font-size:12px;}"
            "QPushButton:hover{background:#2a5a3a;border-color:#5dca8e;}"
            "QPushButton:disabled{color:#444;background:#1a1a2e;border-color:#252540;}"
        )
        self._btn_save.setEnabled(False)
        self._btn_save.setToolTip(
            "Save title + description to session.json  (Ctrl+Shift+S)"
        )
        self._btn_save.clicked.connect(self._save)
        hdr.addWidget(self._btn_save)
        root.addLayout(hdr)

        # Title row
        title_row = QHBoxLayout()
        title_lbl = QLabel("Title:")
        title_lbl.setFixedWidth(58)
        title_lbl.setStyleSheet("color:#888;font-size:11px;")
        title_row.addWidget(title_lbl)
        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("Short title for this session…")
        self._title_edit.setStyleSheet(self._STYLE_EDIT)
        self._title_edit.textChanged.connect(self._on_changed)
        title_row.addWidget(self._title_edit)
        root.addLayout(title_row)

        # Description
        desc_row = QHBoxLayout()
        desc_lbl = QLabel("Notes:")
        desc_lbl.setFixedWidth(58)
        desc_lbl.setAlignment(Qt.AlignmentFlag.AlignTop)
        desc_lbl.setStyleSheet("color:#888;font-size:11px;margin-top:4px;")
        desc_row.addWidget(desc_lbl)
        self._desc_edit = QTextEdit()
        self._desc_edit.setPlaceholderText(
            "Notes, context, or description for this session…"
        )
        self._desc_edit.setFixedHeight(64)
        self._desc_edit.setStyleSheet(
            "background:#111828;border:1px solid #2a3a5a;border-radius:5px;"
            "padding:4px 8px;color:#e0e0e0;font-size:11px;"
        )
        self._desc_edit.textChanged.connect(self._on_changed)
        desc_row.addWidget(self._desc_edit)
        root.addLayout(desc_row)

        # Ctrl+Shift+S shortcut
        shortcut = QShortcut(QKeySequence("Ctrl+Shift+S"), self)
        shortcut.activated.connect(self._save)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _on_changed(self):
        if not self._dirty:
            self._dirty = True
            self._update_state()

    def _update_state(self):
        has_session = self._session is not None
        self._btn_save.setEnabled(has_session and self._dirty)
        self._dirty_lbl.setText("● unsaved" if self._dirty else "")

    def _save(self):
        if self._session is None:
            return
        title = self._title_edit.text().strip()
        description = self._desc_edit.toPlainText().strip()
        try:
            self._session.save_info(title, description)
        except Exception as e:
            QMessageBox.critical(
                self, "Save failed", f"Could not save session info:\n{e}"
            )
            return
        self._dirty = False
        self._update_state()
        self._dirty_lbl.setText("✅ Saved")
        self.saved.emit(str(self._session.folder))
