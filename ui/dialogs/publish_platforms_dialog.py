"""Multi-session: chọn profile, platform, lịch đăng — trả payload cho runner."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import QDateTime
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
)

from core.publish_profiles import (
    PLATFORM_LABELS,
    PLATFORM_ORDER,
    load_profiles,
    profile_platforms_ready,
)
from core.session_listing import published_at_epoch_seconds
from core.workspace_publish_prefs import (
    load_workspace_publish_prefs,
    save_workspace_publish_prefs,
)


class PublishPlatformsDialog(QDialog):
    def __init__(
        self,
        base_dir: str,
        selected_sessions: list[dict],
        parent=None,
    ):
        super().__init__(parent)
        self.base_dir = base_dir or ""
        self._sessions = list(selected_sessions or [])
        self.payload: dict | None = None
        self.setWindowTitle("Đăng video lên nhiều nền tảng")
        self.setMinimumWidth(560)
        self.setStyleSheet(parent.styleSheet() if parent else "")
        self._profiles: list[dict] = []
        self._wp_prefs_open = load_workspace_publish_prefs(self.base_dir)
        self._sorted_sessions = sorted(
            self._sessions,
            key=lambda s: (
                published_at_epoch_seconds(s.get("published_at")),
                (Path(s["folder"]).name.lower() if s.get("folder") else ""),
            ),
        )
        self._setup_ui()
        self._reload_profiles_combo()

    def _setup_ui(self):
        wp = self._wp_prefs_open
        root = QVBoxLayout(self)
        n = len(self._sessions)
        root.addWidget(
            QLabel(
                f"<b>{n}</b> session đã tick chọn. Video dùng để đăng: file mới nhất trong "
                f"<code>result/</code> (step 6) hoặc fallback source/step3."
            )
        )

        self._profile_combo = QComboBox()
        self._profile_combo.setMinimumWidth(280)
        self._profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        row = QHBoxLayout()
        row.addWidget(QLabel("Profile:"))
        row.addWidget(self._profile_combo, stretch=1)
        root.addLayout(row)

        plat = QGroupBox("Nền tảng")
        pv = QVBoxLayout(plat)
        self._chk: dict[str, QCheckBox] = {}
        for pl in PLATFORM_ORDER:
            cb = QCheckBox(PLATFORM_LABELS.get(pl, pl))
            cb.setChecked(False)
            cb.toggled.connect(self._clamp_fb_start_if_needed)
            self._chk[pl] = cb
            pv.addWidget(cb)
        root.addWidget(plat)

        scope = QGroupBox("Phạm vi đăng (theo session đã tick)")
        sv = QVBoxLayout(scope)
        self._rad_scope_all = QRadioButton(
            "Đăng đầy đủ: tạo kế hoạch mới và chạy hết các nền tảng đã chọn "
            "(ghi đè publish_plan cũ của session đó)."
        )
        self._rad_scope_missing = QRadioButton(
            "Chỉ phần chưa thành công: với mỗi session, chỉ đăng lên nền tảng đã chọn "
            "mà chưa có job status «done» — giữ lịch sử publish_plan, thêm job mới."
        )
        use_missing = str(wp.get("scope_mode") or "only_missing_success") != "all"
        self._rad_scope_missing.setChecked(use_missing)
        self._rad_scope_all.setChecked(not use_missing)
        sv.addWidget(self._rad_scope_all)
        sv.addWidget(self._rad_scope_missing)
        root.addWidget(scope)

        self._chk_yt_not_kids = QCheckBox(
            "YouTube: không phải nội dung dành cho trẻ em (selfDeclaredMadeForKids=false)"
        )
        self._chk_yt_not_kids.setChecked(True)
        root.addWidget(self._chk_yt_not_kids)

        batch = QGroupBox("Tiếp tục theo lượt (workspace)")
        bv = QVBoxLayout(batch)
        self._chk_limit_batch = QCheckBox(
            "Mỗi lần chỉ xử lý tối đa N session tiếp theo "
            "(thứ tự published_at ↑ trong các session đã tick)"
        )
        self._chk_limit_batch.setChecked(bool(wp.get("limit_sessions_enabled", True)))
        row_b = QHBoxLayout()
        row_b.addWidget(self._chk_limit_batch)
        row_b.addWidget(QLabel("N ="))
        self._spin_limit_sessions = QSpinBox()
        self._spin_limit_sessions.setRange(1, 500)
        self._spin_limit_sessions.setValue(int(wp.get("limit_sessions_count") or 10))
        row_b.addWidget(self._spin_limit_sessions)
        self._btn_prev_page = QPushButton("← Trang trước")
        self._btn_prev_page.clicked.connect(lambda: self._nudge_cursor(-1))
        row_b.addWidget(self._btn_prev_page)
        self._btn_next_page = QPushButton("Trang sau →")
        self._btn_next_page.clicked.connect(lambda: self._nudge_cursor(+1))
        row_b.addWidget(self._btn_next_page)
        self._btn_reset_cursor = QPushButton("Đặt lại tiếp tục (từ đầu)")
        self._btn_reset_cursor.setToolTip(
            "Đưa cursor «tiếp tục N» về 0 — lần sau bắt đầu lại từ session đầu trong danh sách đã tick."
        )
        self._btn_reset_cursor.clicked.connect(self._on_reset_batch_cursor)
        row_b.addWidget(self._btn_reset_cursor)
        bv.addLayout(row_b)
        self._lbl_batch_hint = QLabel("")
        self._lbl_batch_hint.setWordWrap(True)
        self._lbl_batch_hint.setStyleSheet("color:#888;font-size:11px;")
        bv.addWidget(self._lbl_batch_hint)
        self._chk_limit_batch.toggled.connect(self._refresh_batch_hint)
        self._spin_limit_sessions.valueChanged.connect(self._refresh_batch_hint)
        root.addWidget(batch)

        timing = QGroupBox("Thời điểm đăng")
        tv = QVBoxLayout(timing)
        self._rad_immediate = QRadioButton("Đăng ngay (tất cả platform cùng lúc)")
        self._rad_scheduled = QRadioButton("Lên lịch: từ thời điểm bắt đầu, mỗi platform cách nhau một khoảng")
        sched_sel = str(wp.get("schedule_mode") or "scheduled") == "scheduled"
        self._rad_scheduled.setChecked(sched_sel)
        self._rad_immediate.setChecked(not sched_sel)

        def _on_timing_toggle():
            self._update_schedule_widgets()
            self._clamp_fb_start_if_needed()

        self._rad_immediate.toggled.connect(_on_timing_toggle)
        self._rad_scheduled.toggled.connect(_on_timing_toggle)
        tv.addWidget(self._rad_immediate)
        tv.addWidget(self._rad_scheduled)
        form = QFormLayout()
        last_u = int(wp.get("last_max_scheduled_unix") or 0)
        iv_open = max(1, min(168, int(wp.get("interval_hours") or 24)))
        if last_u > 0:
            start_hint = datetime.fromtimestamp(last_u) + timedelta(hours=iv_open)
        else:
            start_hint = datetime.now() + timedelta(minutes=15)
        self._dt_start = QDateTimeEdit(
            QDateTime.fromSecsSinceEpoch(int(start_hint.timestamp()))
        )
        self._dt_start.setCalendarPopup(True)
        self._dt_start.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._spin_interval = QSpinBox()
        self._spin_interval.setRange(1, 168)
        self._spin_interval.setValue(iv_open)
        self._spin_interval.setSuffix(" giờ")
        form.addRow("Bắt đầu:", self._dt_start)
        form.addRow("Khoảng cách:", self._spin_interval)
        tv.addLayout(form)
        root.addWidget(timing)

        hint = QLabel(
            "Kế hoạch được ghi vào <code>session.json</code> → <code>publish_plan</code>. "
            "«Chỉ phần chưa thành công» bỏ qua platform đã upload OK (status <code>done</code>). "
            "<b>Facebook lên lịch:</b> giờ đăng phải cách lúc <i>hoàn tất upload</i> ít nhất ~10 phút "
            "(và trong ~30 ngày) — nên chọn «Bắt đầu» đủ xa nếu video lớn."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;font-size:11px;")
        root.addWidget(hint)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Đăng")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        self._update_schedule_widgets()
        self._clamp_fb_start_if_needed()
        self._refresh_batch_hint()

    def _clamp_fb_start_if_needed(self) -> None:
        if not self._rad_scheduled.isChecked():
            return
        fb = self._chk.get("facebook")
        if fb is None or not fb.isEnabled() or not fb.isChecked():
            return
        now = datetime.now()
        min_fb = now + timedelta(minutes=11)
        dt = self._dt_start.dateTime().toPyDateTime()
        if dt < min_fb:
            self._dt_start.setDateTime(
                QDateTime.fromSecsSinceEpoch(int(min_fb.timestamp()))
            )

    def _refresh_batch_hint(self) -> None:
        prefs = load_workspace_publish_prefs(self.base_dir)
        cur = int(prefs.get("batch_cursor") or 0)
        total = len(self._sorted_sessions)
        if total == 0:
            self._lbl_batch_hint.setText("")
            return
        if self._chk_limit_batch.isChecked():
            n = int(self._spin_limit_sessions.value())
            end = min(cur + n, total)
            page = (cur // n) + 1
            pages = max(1, (total + n - 1) // n)
            self._btn_prev_page.setEnabled(page > 1)
            self._btn_next_page.setEnabled(page < pages)
            tail = ""
            if cur >= total:
                tail = (
                    " Cursor đã vượt quá số session đã tick — lần chạy sẽ quay về đầu "
                    "(hoặc báo hết danh sách)."
                )
            self._lbl_batch_hint.setText(
                f"Trang {page}/{pages}: [{cur}:{end}] / {total} session đã tick (published_at ↑).{tail}"
            )
        else:
            self._btn_prev_page.setEnabled(False)
            self._btn_next_page.setEnabled(False)
            self._lbl_batch_hint.setText(
                f"Không giới hạn — xử lý cả {total} session đã tick (cursor không dùng)."
            )

    def _nudge_cursor(self, delta_pages: int) -> None:
        if not self._chk_limit_batch.isChecked():
            return
        n = int(self._spin_limit_sessions.value())
        prefs = load_workspace_publish_prefs(self.base_dir)
        cur = int(prefs.get("batch_cursor") or 0)
        cur2 = max(0, cur + int(delta_pages) * n)
        save_workspace_publish_prefs(self.base_dir, {"batch_cursor": cur2})
        self._refresh_batch_hint()

    def _on_reset_batch_cursor(self) -> None:
        save_workspace_publish_prefs(self.base_dir, {"batch_cursor": 0})
        self._refresh_batch_hint()
        QMessageBox.information(
            self,
            "Đã đặt lại",
            "Cursor «tiếp tục N» đã về 0 — lần đăng sau bắt đầu lại từ session đầu "
            "trong danh sách đã tick.",
        )

    def _update_schedule_widgets(self):
        en = self._rad_scheduled.isChecked()
        self._dt_start.setEnabled(en)
        self._spin_interval.setEnabled(en)

    def _reload_profiles_combo(self):
        self._profile_combo.blockSignals(True)
        self._profile_combo.clear()
        st = load_profiles(self.base_dir)
        self._profiles = list(st.get("profiles") or [])
        last = str(st.get("last_profile_id") or "")
        sel_idx = 0
        for i, pr in enumerate(self._profiles):
            self._profile_combo.addItem(pr.get("name") or "(profile)", pr.get("id"))
            if str(pr.get("id")) == last:
                sel_idx = i
        if self._profiles:
            self._profile_combo.setCurrentIndex(sel_idx)
        self._profile_combo.blockSignals(False)
        self._on_profile_changed()

    def _current_profile(self) -> dict | None:
        i = self._profile_combo.currentIndex()
        if i < 0 or i >= len(self._profiles):
            return None
        return self._profiles[i]

    def _on_profile_changed(self):
        pr = self._current_profile()
        ready = profile_platforms_ready(pr) if pr else {p: False for p in PLATFORM_ORDER}
        for pl, cb in self._chk.items():
            ok = ready.get(pl, False)
            cb.setEnabled(ok)
            if not ok:
                cb.setChecked(False)
                cb.setToolTip("Thiếu thông tin trong profile → mở API Keys Manager → tab Publish profiles")
            else:
                cb.setToolTip("")
        for pl in ("facebook", "youtube"):
            cb = self._chk.get(pl)
            if cb and cb.isEnabled():
                cb.setChecked(True)
        self._clamp_fb_start_if_needed()
        self._refresh_batch_hint()

    def _on_accept(self):
        if not self.base_dir.strip():
            QMessageBox.warning(self, "Thiếu folder", "Chưa có workspace base folder.")
            return
        if not self._profiles:
            QMessageBox.warning(
                self,
                "Chưa có profile",
                "Tạo ít nhất một publish profile trong API Keys Manager → tab Publish profiles.",
            )
            return
        pr = self._current_profile()
        if not pr:
            return
        platforms = [pl for pl, cb in self._chk.items() if cb.isChecked() and cb.isEnabled()]
        if not platforms:
            QMessageBox.warning(self, "Chưa chọn platform", "Tick ít nhất một nền tảng đã cấu hình.")
            return

        schedule_mode = "immediate" if self._rad_immediate.isChecked() else "scheduled"
        start_local = self._dt_start.dateTime().toPyDateTime()
        interval_h = int(self._spin_interval.value())
        youtube_mfk = not self._chk_yt_not_kids.isChecked()
        scope_mode = (
            "only_missing_success"
            if self._rad_scope_missing.isChecked()
            else "all"
        )

        if schedule_mode == "scheduled" and "facebook" in platforms:
            # Meta Page video: scheduled_publish_time phải cách «lúc gọi API» ít nhất ~10 phút
            # (và trong ~30 ngày) — nếu «Bắt đầu» quá gần / quá khử sẽ lỗi #100 invalid.
            now = datetime.now()
            min_fb = now + timedelta(minutes=11)
            if start_local < min_fb:
                QMessageBox.warning(
                    self,
                    "Lịch Facebook",
                    "Khi có **Facebook** và chế độ **Lên lịch**, thời điểm «Bắt đầu» phải **ít nhất "
                    "sau 11 phút** so với giờ máy (Facebook yêu cầu khoảng cách tối thiểu ~10 phút "
                    "trước giờ đăng dự kiến).\n\n"
                    "Chỉnh «Bắt đầu» trễ hơn, hoặc chọn «Đăng ngay».",
                )
                return

        self.payload = {
            "profile": pr,
            "platforms": platforms,
            "schedule_mode": schedule_mode,
            "start_local": start_local,
            "interval_hours": interval_h,
            "youtube_made_for_kids": youtube_mfk,
            "sessions": list(self._sessions),
            "scope_mode": scope_mode,
            "limit_sessions_enabled": self._chk_limit_batch.isChecked(),
            "limit_sessions_count": int(self._spin_limit_sessions.value()),
        }
        self.accept()
