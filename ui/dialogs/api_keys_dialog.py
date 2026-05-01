"""Dialog để nhập và quản lý API keys — auto-save vào .subsync_keys"""

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)


class ApiKeysDialog(QDialog):
    """Dialog để nhập và quản lý API keys — auto-save vào .subsync_keys"""

    def __init__(self, base_dir: str = "", parent=None):
        super().__init__(parent)
        self.base_dir = base_dir
        self.setWindowTitle("🔑  API Keys Manager")
        self.setMinimumSize(540, 480)
        self.setStyleSheet(parent.styleSheet() if parent else "")
        self._edits: dict = {}
        self._setup_ui()
        self._load()

    def _setup_ui(self):
        from core.api_keys import ENV_FILE, KNOWN_KEYS

        v = QVBoxLayout(self)
        v.setSpacing(10)

        hdr = QLabel("API Keys — lưu vào file .subsync_keys trong session folder")
        hdr.setStyleSheet("color:#a0a8ff;font-size:12px;")
        v.addWidget(hdr)

        if self.base_dir:
            path_lbl = QLabel(f"📁 {self.base_dir}/{ENV_FILE}")
            path_lbl.setStyleSheet("color:#555;font-size:10px;font-family:monospace;")
            v.addWidget(path_lbl)

        for env_key, meta in KNOWN_KEYS.items():
            row = QHBoxLayout()
            lbl = QLabel(f"{meta['label']}:")
            lbl.setFixedWidth(160)
            lbl.setStyleSheet("color:#a0c0ff;font-size:12px;")
            row.addWidget(lbl)
            edit = QLineEdit()
            edit.setPlaceholderText(f"{env_key}=...")
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            row.addWidget(edit)

            btn_show = QPushButton("👁")
            btn_show.setFixedWidth(32)
            btn_show.setCheckable(True)
            btn_show.setStyleSheet("QPushButton{padding:2px;}")
            btn_show.toggled.connect(
                lambda checked, e=edit: e.setEchoMode(
                    QLineEdit.EchoMode.Normal
                    if checked
                    else QLineEdit.EchoMode.Password
                )
            )
            row.addWidget(btn_show)
            v.addLayout(row)
            self._edits[env_key] = edit

        info = QLabel(
            "💡 Keys được lưu vào file <b>.subsync_keys</b> trong session folder.<br>"
            "App tự động load khi bạn chọn folder.<br>"
            "<b>Không share file này!</b> Thêm vào <code>.gitignore</code> nếu dùng git."
        )
        info.setStyleSheet("color:#666;font-size:11px;padding:8px;")
        info.setWordWrap(True)
        v.addWidget(info)

        v.addStretch()

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    def _load(self):
        from core.api_keys import get_manager

        mgr = get_manager()
        for env_key, edit in self._edits.items():
            v = mgr.get(env_key, "")
            if v:
                edit.setText(v)

    def _save(self):
        from core.api_keys import get_manager

        mgr = get_manager()
        for env_key, edit in self._edits.items():
            v = edit.text().strip()
            mgr.set(env_key, v)
        self.accept()
