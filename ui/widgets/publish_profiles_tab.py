"""Tab: publish profiles (Facebook / YouTube / TikTok credentials per workspace)."""

from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
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
from core.publish.youtube_oauth_local import DEFAULT_REDIRECT_URI


class _YoutubeOAuthWorker(QObject):
    """Chạy OAuth localhost trong thread — không block UI."""

    finished = pyqtSignal(str, str)  # refresh_token hoặc "", error hoặc ""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        login_hint: str,
    ):
        super().__init__()
        self._cid = client_id
        self._cs = client_secret
        self._ru = redirect_uri
        self._lh = login_hint

    def run(self):
        try:
            from core.publish.youtube_oauth_local import run_local_oauth

            tok = run_local_oauth(
                self._cid,
                self._cs,
                redirect_uri=self._ru or None,
                login_hint=self._lh or None,
            )
            self.finished.emit(tok, "")
        except Exception as e:
            self.finished.emit("", str(e))


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
        self._yt_redirect = QLineEdit()
        self._yt_redirect.setPlaceholderText(
            "Phải trùng ký tự với Authorized redirect URIs trên GCP"
        )
        self._yt_login_hint = QLineEdit()
        self._yt_login_hint.setPlaceholderText("Tuỳ chọn — gợi ý Gmail (vẫn có thể đổi tài khoản)")
        self._yt_refresh = QLineEdit()
        self._yt_refresh.setEchoMode(QLineEdit.EchoMode.Password)
        self._yt_refresh.setPlaceholderText(
            "Không có trên GCP — dán token sau OAuth (OAuth Playground / script / n8n)"
        )
        self._yt_refresh.setToolTip(
            "Google Cloud Console chỉ có Client ID + Client secret.\n"
            "Refresh token chỉ xuất hiện sau khi bạn đăng nhập và cấp quyền một lần "
            "(vd. https://developers.google.com/oauthplayground/ với scope youtube.upload).\n"
            "Dán chuỗi refresh_token vào đây để app đổi lấy access_token mỗi lần upload."
        )
        self._tt_token = QLineEdit()
        self._tt_token.setEchoMode(QLineEdit.EchoMode.Password)
        self._tt_open = QLineEdit()
        self._loaded_row: int = -1
        self._oauth_thread: QThread | None = None
        self._oauth_worker: _YoutubeOAuthWorker | None = None
        self._btn_yt_oauth: QPushButton | None = None
        self._setup_ui()
        self.reload_from_disk()

    def set_base_dir(self, base_dir: str):
        self._base_dir = base_dir or ""
        self.reload_from_disk()

    def reload_from_disk(self):
        self._save_form_into_profile_row(self._loaded_row)
        st = load_profiles(self._base_dir)
        self._profiles = list(st.get("profiles") or [])
        self._last_profile_id = str(st.get("last_profile_id") or "")
        self._loaded_row = -1
        self._rebuild_list(select_id=self._last_profile_id or None)

    def persist_to_disk(self):
        """Flush form and write JSON."""
        self._save_form_into_profile_row(self._loaded_row)
        save_profiles(
            self._base_dir,
            {"profiles": self._profiles, "last_profile_id": self._last_profile_id},
        )

    def profiles_for_publish_dialog(self) -> tuple[list[dict], str]:
        """Return (profiles, last_id) — flush first."""
        self._save_form_into_profile_row(self._loaded_row)
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

        yt = QGroupBox("YouTube (OAuth)")
        yf = QFormLayout(yt)
        yt_hint = QLabel(
            "OAuth dùng đúng chuỗi <b>Redirect URI</b> bên dưới — copy dán <b>y hệt</b> vào "
            "Google Cloud → OAuth client (Web) → Authorized redirect URIs "
            "(http ≠ https; <b>localhost</b> ≠ <b>127.0.0.1</b>; có/không <b>/</b> cuối là khác nhau). "
            "Trình duyệt sẽ hiện <b>chọn tài khoản</b> Google; dùng ẩn danh nếu cần Gmail khác."
        )
        yt_hint.setWordWrap(True)
        yt_hint.setStyleSheet("color:#888;font-size:10px;")
        yf.addRow(yt_hint)
        yf.addRow("Client ID:", self._yt_cid)
        yf.addRow("Client secret:", self._yt_secret)
        yf.addRow("Redirect URI (localhost):", self._yt_redirect)
        yf.addRow("Gợi ý Gmail (tuỳ chọn):", self._yt_login_hint)
        self._btn_yt_oauth = QPushButton("🔐 Lấy refresh token (mở trình duyệt)")
        self._btn_yt_oauth.setToolTip(
            "Mở Google (có bước chọn tài khoản), callback về đúng Redirect URI — "
            "URI đó phải có trong GCP."
        )
        self._btn_yt_oauth.clicked.connect(self._on_youtube_oauth_clicked)
        yf.addRow(self._btn_yt_oauth)
        self._chk_yt_show_refresh = QCheckBox(
            "Tôi cần upload lên YouTube — hiện ô refresh token (bắt buộc khi upload)"
        )
        self._chk_yt_show_refresh.setChecked(False)
        self._chk_yt_show_refresh.toggled.connect(self._on_yt_refresh_visibility)
        yf.addRow(self._chk_yt_show_refresh)
        self._yt_refresh_wrap = QWidget()
        yrf = QFormLayout(self._yt_refresh_wrap)
        yrf.setContentsMargins(0, 0, 0, 0)
        yrf.addRow("Refresh token:", self._yt_refresh)
        self._yt_refresh_wrap.setVisible(False)
        yf.addRow(self._yt_refresh_wrap)
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

    def _on_youtube_oauth_clicked(self):
        cid = self._yt_cid.text().strip()
        csec = self._yt_secret.text().strip()
        if not cid or not csec:
            QMessageBox.warning(
                self,
                "YouTube OAuth",
                "Nhập Client ID và Client secret (từ Google Cloud) trước.",
            )
            return
        ru = self._yt_redirect.text().strip() or DEFAULT_REDIRECT_URI
        ans = QMessageBox.question(
            self,
            "YouTube OAuth",
            "Sẽ mở trình duyệt để đăng nhập Google (có thể chọn đúng Gmail).\n\n"
            "Trên Google Cloud → OAuth client (Web) → Authorized redirect URIs "
            "phải có **đúng** dòng sau (copy từ ô Redirect URI):\n"
            f"  {ru}\n\n"
            "Tiếp tục?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        if self._oauth_thread is not None and self._oauth_thread.isRunning():
            return
        if self._btn_yt_oauth:
            self._btn_yt_oauth.setEnabled(False)
        self._oauth_thread = QThread(self)
        self._oauth_worker = _YoutubeOAuthWorker(
            cid,
            csec,
            self._yt_redirect.text().strip(),
            self._yt_login_hint.text().strip(),
        )
        self._oauth_worker.moveToThread(self._oauth_thread)
        self._oauth_thread.started.connect(self._oauth_worker.run)
        self._oauth_worker.finished.connect(self._on_youtube_oauth_finished)
        self._oauth_worker.finished.connect(self._oauth_thread.quit)
        self._oauth_worker.finished.connect(self._oauth_worker.deleteLater)
        self._oauth_thread.finished.connect(self._oauth_thread.deleteLater)
        self._oauth_thread.start()

    def _on_youtube_oauth_finished(self, tok: str, err: str):
        if self._btn_yt_oauth:
            self._btn_yt_oauth.setEnabled(True)
        self._oauth_thread = None
        self._oauth_worker = None
        if err:
            QMessageBox.warning(self, "YouTube OAuth", err[:4000])
            return
        self._yt_refresh.setText(tok)
        self._chk_yt_show_refresh.blockSignals(True)
        self._chk_yt_show_refresh.setChecked(True)
        self._chk_yt_show_refresh.blockSignals(False)
        self._yt_refresh_wrap.setVisible(True)
        QMessageBox.information(
            self,
            "YouTube OAuth",
            "Đã điền refresh token. Nhớ bấm Save ở cuối hộp thoại để ghi file profile.",
        )

    def _on_yt_refresh_visibility(self, checked: bool):
        self._yt_refresh_wrap.setVisible(checked)

    def _rebuild_list(self, select_id: str | None = None):
        self._list.blockSignals(True)
        self._list.clear()
        for pr in self._profiles:
            it = QListWidgetItem(pr.get("name") or "(no name)")
            it.setData(Qt.ItemDataRole.UserRole, pr.get("id"))
            self._list.addItem(it)
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
            self._loaded_row = row
        else:
            self._clear_form()
            self._loaded_row = -1
        self._list.blockSignals(False)

    def _clear_form(self):
        self._name_edit.clear()
        self._fb_page.clear()
        self._fb_token.clear()
        self._yt_cid.clear()
        self._yt_secret.clear()
        self._yt_redirect.setText(DEFAULT_REDIRECT_URI)
        self._yt_login_hint.clear()
        self._yt_refresh.clear()
        self._tt_token.clear()
        self._tt_open.clear()
        self._chk_yt_show_refresh.setChecked(False)

    def _sync_yt_refresh_checkbox_from_profile(self):
        """Hiện ô refresh token nếu profile đã có refresh token (đã từng cấu hình)."""
        has_rt = bool(self._yt_refresh.text().strip())
        self._chk_yt_show_refresh.blockSignals(True)
        self._chk_yt_show_refresh.setChecked(has_rt)
        self._chk_yt_show_refresh.blockSignals(False)
        self._yt_refresh_wrap.setVisible(has_rt)

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
        self._yt_redirect.setText(str(yt.get("redirect_uri") or DEFAULT_REDIRECT_URI))
        self._yt_login_hint.setText(str(yt.get("oauth_login_hint") or ""))
        self._yt_refresh.setText(str(yt.get("refresh_token") or ""))
        self._tt_token.setText(str(tt.get("access_token") or ""))
        self._tt_open.setText(str(tt.get("open_id") or ""))
        self._sync_yt_refresh_checkbox_from_profile()

    def _save_form_into_profile_row(self, row: int):
        """Ghi nội dung form vào self._profiles[row] (không phụ thuộc currentRow của list)."""
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
        yt["redirect_uri"] = self._yt_redirect.text().strip() or DEFAULT_REDIRECT_URI
        yt["oauth_login_hint"] = self._yt_login_hint.text().strip()
        yt["refresh_token"] = self._yt_refresh.text().strip()
        tt["access_token"] = self._tt_token.text().strip()
        tt["open_id"] = self._tt_open.text().strip()
        it = self._list.item(row)
        if it:
            it.setText(pr["name"])
        self.data_changed.emit()

    def _flush_form_to_current_profile(self):
        self._save_form_into_profile_row(self._list.currentRow())

    def _on_row_changed(self, row: int):
        if row < 0:
            return
        self._save_form_into_profile_row(self._loaded_row)
        if row < len(self._profiles):
            pid = str(self._profiles[row].get("id") or "")
            self._last_profile_id = pid
            self._loaded_row = row
            self._load_form_from_profile(self._profiles[row])

    def _on_name_edited(self, _t: str):
        row = self._list.currentRow()
        if row >= 0 and row < self._list.count():
            it = self._list.item(row)
            if it:
                it.setText(self._name_edit.text().strip() or "Profile")

    def _add_profile(self):
        self._save_form_into_profile_row(self._loaded_row)
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
        self._save_form_into_profile_row(self._loaded_row)
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
        self._loaded_row = -1
        self._last_profile_id = (
            str(self._profiles[0].get("id")) if self._profiles else ""
        )
        self._rebuild_list(select_id=self._last_profile_id or None)
        self.data_changed.emit()
