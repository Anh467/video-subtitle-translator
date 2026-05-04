"""Multi-session: chọn profile, platform, lịch đăng — trả payload cho runner."""

from __future__ import annotations

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
        self._setup_ui()
        self._reload_profiles_combo()

    def _setup_ui(self):
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
        self._rad_scope_all.setChecked(True)
        sv.addWidget(self._rad_scope_all)
        sv.addWidget(self._rad_scope_missing)
        root.addWidget(scope)

        self._chk_yt_not_kids = QCheckBox(
            "YouTube: không phải nội dung dành cho trẻ em (selfDeclaredMadeForKids=false)"
        )
        self._chk_yt_not_kids.setChecked(True)
        root.addWidget(self._chk_yt_not_kids)

        timing = QGroupBox("Thời điểm đăng")
        tv = QVBoxLayout(timing)
        self._rad_immediate = QRadioButton("Đăng ngay (tất cả platform cùng lúc)")
        self._rad_scheduled = QRadioButton("Lên lịch: từ thời điểm bắt đầu, mỗi platform cách nhau một khoảng")
        self._rad_immediate.setChecked(True)
        self._rad_immediate.toggled.connect(self._update_schedule_widgets)
        self._rad_scheduled.toggled.connect(self._update_schedule_widgets)
        tv.addWidget(self._rad_immediate)
        tv.addWidget(self._rad_scheduled)
        form = QFormLayout()
        self._dt_start = QDateTimeEdit(QDateTime.currentDateTime())
        self._dt_start.setCalendarPopup(True)
        self._dt_start.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._spin_interval = QSpinBox()
        self._spin_interval.setRange(1, 168)
        self._spin_interval.setValue(4)
        self._spin_interval.setSuffix(" giờ")
        form.addRow("Bắt đầu:", self._dt_start)
        form.addRow("Khoảng cách:", self._spin_interval)
        tv.addLayout(form)
        root.addWidget(timing)

        hint = QLabel(
            "Kế hoạch được ghi vào <code>session.json</code> → <code>publish_plan</code>. "
            "«Chỉ phần chưa thành công» bỏ qua platform đã upload OK (status <code>done</code>)."
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

        self.payload = {
            "profile": pr,
            "platforms": platforms,
            "schedule_mode": schedule_mode,
            "start_local": start_local,
            "interval_hours": interval_h,
            "youtube_made_for_kids": youtube_mfk,
            "sessions": list(self._sessions),
            "scope_mode": scope_mode,
        }
        self.accept()
