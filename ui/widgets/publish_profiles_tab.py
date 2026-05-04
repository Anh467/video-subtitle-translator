"""Tab: publish profiles (Facebook / YouTube / TikTok credentials per workspace)."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core.publish_profiles import (
    load_profiles,
    new_profile,
    save_profiles,
)


class PublishProfilesTab(QWidget):
    """Edit .subsync_publish_profiles.json — multiple named profiles."""

    data_changed = pyqtSignal()

    def __init__(self, base_dir: str = "", parent=None):
        super().__init__(parent)
        self._base_dir = base_dir or ""
        self._profiles: list[dict] = []
        self._last_profile_id = ""
        self._list = QListWidget()
        self._name_edit = QLineEdit()
        self._fb_page = QLineEdit()
        self._fb_token = QLineEdit()
        self._fb_token.setEchoMode(QLineEdit.EchoMode.Password)
        self._yt_cid = QLineEdit()
        self._yt_secret = QLineEdit()
        self._yt_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self._yt_refresh = QLineEdit()
        self._yt_refresh.setEchoMode(QLineEdit.EchoMode.Password)
        self._tt_token = QLineEdit()
        self._tt_token.setEchoMode(QLineEdit.EchoMode.Password)
        self._tt_open = QLineEdit()
        self._setup_ui()
        self.reload_from_disk()

    def set_base_dir(self, base_dir: str):
        self._base_dir = base_dir or ""
        self.reload_from_disk()

    def reload_from_disk(self):
        self._flush_form_to_current_profile()
        st = load_profiles(self._base_dir)
        self._profiles = list(st.get("profiles") or [])
        self._last_profile_id = str(st.get("last_profile_id") or "")
        self._rebuild_list(select_id=self._last_profile_id or None)

    def persist_to_disk(self):
        """Flush form and write JSON."""
        self._flush_form_to_current_profile()
        save_profiles(
            self._base_dir,
            {"profiles": self._profiles, "last_profile_id": self._last_profile_id},
        )

    def profiles_for_publish_dialog(self) -> tuple[list[dict], str]:
        """Return (profiles, last_id) — flush first."""
        self._flush_form_to_current_profile()
        return list(self._profiles), self._last_profile_id

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        info = QLabel(
            "Mỗi <b>profile</b> gom credential đăng Facebook Page, YouTube OAuth, TikTok. "
            "Multi-Session → Đăng đa nền tảng chọn profile ở đây."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#a0a8ff;font-size:11px;")
        root.addWidget(info)

        split = QSplitter()
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self._list.setMinimumWidth(160)
        self._list.currentRowChanged.connect(self._on_row_changed)
        lv.addWidget(self._list)

        bh = QHBoxLayout()
        b_add = QPushButton("＋ Profile")
        b_add.clicked.connect(self._add_profile)
        b_del = QPushButton("✕ Xóa")
        b_del.clicked.connect(self._remove_profile)
        bh.addWidget(b_add)
        bh.addWidget(b_del)
        lv.addLayout(bh)
        split.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(8, 0, 0, 0)
        rv.addWidget(QLabel("Tên profile:"))
        self._name_edit.textEdited.connect(self._on_name_edited)
        rv.addWidget(self._name_edit)

        fb = QGroupBox("Facebook Page")
        ff = QFormLayout(fb)
        ff.addRow("Page ID:", self._fb_page)
        ff.addRow("Page access token:", self._fb_token)
        rv.addWidget(fb)

        yt = QGroupBox("YouTube (OAuth refresh)")
        yf = QFormLayout(yt)
        yf.addRow("Client ID:", self._yt_cid)
        yf.addRow("Client secret:", self._yt_secret)
        yf.addRow("Refresh token:", self._yt_refresh)
        rv.addWidget(yt)

        tt = QGroupBox("TikTok (sắp hỗ trợ)")
        tf = QFormLayout(tt)
        tf.addRow("Access token:", self._tt_token)
        tf.addRow("Open ID (tuỳ API):", self._tt_open)
        rv.addWidget(tt)
        rv.addStretch()
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        root.addWidget(split)

    def _rebuild_list(self, select_id: str | None = None):
        self._list.blockSignals(True)
        self._list.clear()
        for pr in self._profiles:
            it = QListWidgetItem(pr.get("name") or "(no name)")
            it.setData(Qt.ItemDataRole.UserRole, pr.get("id"))
            self._list.addItem(it)
        self._list.blockSignals(False)
        row = 0
        if select_id:
            for i in range(self._list.count()):
                it = self._list.item(i)
                if str(it.data(Qt.ItemDataRole.UserRole)) == select_id:
                    row = i
                    break
        if self._profiles:
            self._list.setCurrentRow(row)
            self._load_form_from_profile(self._profiles[row])
        else:
            self._clear_form()

    def _clear_form(self):
        self._name_edit.clear()
        self._fb_page.clear()
        self._fb_token.clear()
        self._yt_cid.clear()
        self._yt_secret.clear()
        self._yt_refresh.clear()
        self._tt_token.clear()
        self._tt_open.clear()

    def _load_form_from_profile(self, pr: dict):
        self._name_edit.setText(str(pr.get("name") or ""))
        c = pr.get("credentials") or {}
        fb = c.get("facebook") or {}
        yt = c.get("youtube") or {}
        tt = c.get("tiktok") or {}
        self._fb_page.setText(str(fb.get("page_id") or ""))
        self._fb_token.setText(str(fb.get("page_access_token") or ""))
        self._yt_cid.setText(str(yt.get("client_id") or ""))
        self._yt_secret.setText(str(yt.get("client_secret") or ""))
        self._yt_refresh.setText(str(yt.get("refresh_token") or ""))
        self._tt_token.setText(str(tt.get("access_token") or ""))
        self._tt_open.setText(str(tt.get("open_id") or ""))

    def _flush_form_to_current_profile(self):
        row = self._list.currentRow()
        if row < 0 or row >= len(self._profiles):
            return
        pr = self._profiles[row]
        pr["name"] = self._name_edit.text().strip() or pr.get("name") or "Profile"
        c = pr.setdefault("credentials", {})
        fb = c.setdefault("facebook", {})
        yt = c.setdefault("youtube", {})
        tt = c.setdefault("tiktok", {})
        fb["page_id"] = self._fb_page.text().strip()
        fb["page_access_token"] = self._fb_token.text().strip()
        yt["client_id"] = self._yt_cid.text().strip()
        yt["client_secret"] = self._yt_secret.text().strip()
        yt["refresh_token"] = self._yt_refresh.text().strip()
        tt["access_token"] = self._tt_token.text().strip()
        tt["open_id"] = self._tt_open.text().strip()
        it = self._list.item(row)
        if it:
            it.setText(pr["name"])
        self.data_changed.emit()

    def _on_row_changed(self, row: int):
        if row < 0:
            return
        self._flush_form_to_current_profile()
        if row < len(self._profiles):
            pid = str(self._profiles[row].get("id") or "")
            self._last_profile_id = pid
            self._load_form_from_profile(self._profiles[row])

    def _on_name_edited(self, _t: str):
        row = self._list.currentRow()
        if row >= 0 and row < self._list.count():
            it = self._list.item(row)
            if it:
                it.setText(self._name_edit.text().strip() or "Profile")

    def _add_profile(self):
        self._flush_form_to_current_profile()
        n = len(self._profiles) + 1
        pr = new_profile(f"Profile {n}")
        self._profiles.append(pr)
        self._last_profile_id = str(pr["id"])
        self._rebuild_list(select_id=self._last_profile_id)
        self.data_changed.emit()

    def _remove_profile(self):
        row = self._list.currentRow()
        if row < 0 or not self._profiles:
            return
        ans = QMessageBox.question(
            self,
            "Xóa profile",
            "Xóa profile đang chọn?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        self._profiles.pop(row)
        self._last_profile_id = (
            str(self._profiles[0].get("id")) if self._profiles else ""
        )
        self._rebuild_list(select_id=self._last_profile_id or None)
        self.data_changed.emit()
