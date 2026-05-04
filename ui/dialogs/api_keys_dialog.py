"""Dialog: API keys + publish profiles (multi-platform credentials)."""

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ui.widgets.publish_profiles_tab import PublishProfilesTab


class ApiKeysDialog(QDialog):
    """API keys (.subsync_keys) và publish profiles (.subsync_publish_profiles.json)."""

    def __init__(self, base_dir: str = "", parent=None):
        super().__init__(parent)
        self.base_dir = base_dir
        self.setWindowTitle("🔑  API Keys & Publish profiles")
        self.setMinimumSize(560, 520)
        self.setStyleSheet(parent.styleSheet() if parent else "")
        self._edits: dict = {}
        self._profiles_tab: PublishProfilesTab | None = None
        self._setup_ui()
        self._load()

    def _setup_ui(self):
        from core.api_keys import ENV_FILE, KNOWN_KEYS

        root = QVBoxLayout(self)
        root.setSpacing(8)

        tabs = QTabWidget()
        keys_w = QWidget()
        kv = QVBoxLayout(keys_w)
        kv.setContentsMargins(0, 4, 0, 0)

        hdr = QLabel("API Keys — lưu vào file .subsync_keys trong session folder")
        hdr.setStyleSheet("color:#a0a8ff;font-size:12px;")
        kv.addWidget(hdr)

        if self.base_dir:
            path_lbl = QLabel(f"📁 {self.base_dir}/{ENV_FILE}")
            path_lbl.setStyleSheet("color:#555;font-size:10px;font-family:monospace;")
            kv.addWidget(path_lbl)

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
            kv.addLayout(row)
            self._edits[env_key] = edit

        info = QLabel(
            "💡 Keys được lưu vào file <b>.subsync_keys</b> trong session folder.<br>"
            "App tự động load khi bạn chọn folder.<br>"
            "<b>Không share file này!</b>"
        )
        info.setStyleSheet("color:#666;font-size:11px;padding:8px;")
        info.setWordWrap(True)
        kv.addWidget(info)
        kv.addStretch()

        tabs.addTab(keys_w, "API Keys")

        self._profiles_tab = PublishProfilesTab(self.base_dir, self)
        tabs.addTab(self._profiles_tab, "Publish profiles")

        root.addWidget(tabs)

        path2 = QLabel(
            f"Publish profiles: <code>{self.base_dir or '(chọn base folder)'}/.subsync_publish_profiles.json</code>"
        )
        path2.setWordWrap(True)
        path2.setStyleSheet("color:#555;font-size:10px;")
        root.addWidget(path2)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _load(self):
        from core.api_keys import get_manager

        mgr = get_manager()
        for env_key, edit in self._edits.items():
            v = mgr.get(env_key, "")
            if v:
                edit.setText(v)
        if self._profiles_tab:
            self._profiles_tab.set_base_dir(self.base_dir)

    def _save(self):
        from core.api_keys import get_manager

        mgr = get_manager()
        for env_key, edit in self._edits.items():
            v = edit.text().strip()
            mgr.set(env_key, v)
        if self._profiles_tab and self.base_dir.strip():
            self._profiles_tab.persist_to_disk()
        self.accept()
