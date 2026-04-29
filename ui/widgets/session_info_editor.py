"""
SessionInfoEditor — compact widget to view/edit session title, description,
and thumbnail image.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QFileDialog,
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
    saved = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._session = None
        self._dirty = False
        self._thumb_path = ""
        self._setup_ui()

    def load_session(self, session):
        self._session = session
        self._dirty = False
        self._title_edit.setText(session.title or "")
        self._desc_edit.setPlainText(session.description or "")
        self._folder_lbl.setText(str(session.folder.name))
        self._thumb_path = session.thumbnail or ""
        self._refresh_thumbnail()
        self._update_state()

    def clear(self):
        self._session = None
        self._dirty = False
        self._thumb_path = ""
        self._title_edit.clear()
        self._desc_edit.clear()
        self._folder_lbl.setText("—")
        self._refresh_thumbnail()
        self._update_state()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # Header
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
        self._btn_save = QPushButton("Save info")
        self._btn_save.setFixedHeight(26)
        self._btn_save.setStyleSheet(
            "QPushButton{background:#1a3a2a;color:#5dca8e;border:1px solid #2a6a4a;"
            "font-weight:bold;border-radius:5px;padding:2px 12px;font-size:12px;}"
            "QPushButton:hover{background:#2a5a3a;}"
            "QPushButton:disabled{color:#444;background:#1a1a2e;border-color:#252540;}"
        )
        self._btn_save.setEnabled(False)
        self._btn_save.setToolTip("Save title + description  (Ctrl+Shift+S)")
        self._btn_save.clicked.connect(self._save)
        hdr.addWidget(self._btn_save)
        root.addLayout(hdr)

        # Thumbnail (left) + Title/Notes (right)
        top = QHBoxLayout()
        top.setSpacing(10)

        # Thumbnail column
        tcol = QVBoxLayout()
        tcol.setSpacing(3)
        self._thumb_preview = QLabel("no image")
        self._thumb_preview.setFixedSize(96, 54)
        self._thumb_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_preview.setStyleSheet(
            "background:#0a0a1a;border:1px solid #2a3a5a;border-radius:5px;color:#444;font-size:9px;"
        )
        tcol.addWidget(self._thumb_preview)
        tbtns = QHBoxLayout()
        tbtns.setSpacing(3)
        btn_up = QPushButton("Upload")
        btn_up.setFixedHeight(22)
        btn_up.setStyleSheet(
            "QPushButton{background:#1a2a3a;color:#60aaff;border:1px solid #2a4a6a;"
            "border-radius:4px;padding:1px 8px;font-size:10px;}"
            "QPushButton:hover{background:#2a4a6a;}"
        )
        btn_up.clicked.connect(self._upload_thumbnail)
        tbtns.addWidget(btn_up)
        btn_clr = QPushButton("✕")
        btn_clr.setFixedSize(22, 22)
        btn_clr.setStyleSheet(
            "QPushButton{background:#3a1a1a;color:#ff7070;border:1px solid #6e2d2d;"
            "border-radius:4px;font-size:10px;}"
            "QPushButton:hover{background:#5a2020;}"
        )
        btn_clr.setToolTip("Remove thumbnail")
        btn_clr.clicked.connect(self._clear_thumbnail)
        tbtns.addWidget(btn_clr)
        tcol.addLayout(tbtns)
        top.addLayout(tcol)

        # Title + Notes column
        icol = QVBoxLayout()
        icol.setSpacing(4)

        tr = QHBoxLayout()
        tl = QLabel("Title:")
        tl.setFixedWidth(44)
        tl.setStyleSheet("color:#888;font-size:11px;")
        tr.addWidget(tl)
        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("Short title for this session…")
        self._title_edit.setStyleSheet(
            "background:#111828;border:1px solid #2a3a5a;border-radius:5px;"
            "padding:4px 8px;color:#e0e0e0;font-size:12px;"
        )
        self._title_edit.textChanged.connect(self._on_changed)
        tr.addWidget(self._title_edit)
        icol.addLayout(tr)

        nr = QHBoxLayout()
        nl = QLabel("Notes:")
        nl.setFixedWidth(44)
        nl.setAlignment(Qt.AlignmentFlag.AlignTop)
        nl.setStyleSheet("color:#888;font-size:11px;margin-top:3px;")
        nr.addWidget(nl)
        self._desc_edit = QTextEdit()
        self._desc_edit.setPlaceholderText("Notes, context, or description…")
        self._desc_edit.setFixedHeight(52)
        self._desc_edit.setStyleSheet(
            "background:#111828;border:1px solid #2a3a5a;border-radius:5px;"
            "padding:4px 8px;color:#e0e0e0;font-size:11px;"
        )
        self._desc_edit.textChanged.connect(self._on_changed)
        nr.addWidget(self._desc_edit)
        icol.addLayout(nr)

        top.addLayout(icol)
        root.addLayout(top)

        shortcut = QShortcut(QKeySequence("Ctrl+Shift+S"), self)
        shortcut.activated.connect(self._save)

    # ── Thumbnail ─────────────────────────────────────────────────────────────

    def _upload_thumbnail(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select thumbnail image",
            "",
            "Images (*.jpg *.jpeg *.png *.webp *.bmp)",
        )
        if not path:
            return
        if self._session is None:
            QMessageBox.warning(self, "No session", "Load a session first.")
            return
        try:
            saved = self._session.save_thumbnail(path)
            self._thumb_path = saved
            self._refresh_thumbnail()
            self._dirty_lbl.setText("Thumbnail saved")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _clear_thumbnail(self):
        if not self._thumb_path:
            return
        import os

        try:
            if os.path.exists(self._thumb_path):
                os.unlink(self._thumb_path)
        except Exception:
            pass
        self._thumb_path = ""
        self._refresh_thumbnail()
        self._dirty_lbl.setText("Thumbnail removed")

    def _refresh_thumbnail(self):
        if self._thumb_path and Path(self._thumb_path).exists():
            pix = QPixmap(self._thumb_path).scaled(
                96,
                54,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._thumb_preview.setPixmap(pix)
            self._thumb_preview.setText("")
            self._thumb_preview.setStyleSheet(
                "background:#0a0a1a;border:1px solid #3a5a3a;border-radius:5px;"
            )
        else:
            self._thumb_preview.clear()
            self._thumb_preview.setText("no image")
            self._thumb_preview.setStyleSheet(
                "background:#0a0a1a;border:1px solid #2a3a5a;border-radius:5px;"
                "color:#444;font-size:9px;"
            )

    # ── Internals ─────────────────────────────────────────────────────────────

    def _on_changed(self):
        if not self._dirty:
            self._dirty = True
            self._update_state()

    def _update_state(self):
        self._btn_save.setEnabled(self._session is not None and self._dirty)
        self._dirty_lbl.setText("● unsaved" if self._dirty else "")

    def _save(self):
        if self._session is None:
            return
        title = self._title_edit.text().strip()
        description = self._desc_edit.toPlainText().strip()
        try:
            self._session.save_info(title, description)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return
        self._dirty = False
        self._update_state()
        self._dirty_lbl.setText("Saved")
        self.saved.emit(str(self._session.folder))
