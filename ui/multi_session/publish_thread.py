"""Background thread: run multi-session publish jobs with detailed logging."""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QThread, pyqtSignal

from core.publish.runner import run_publish
from core.session import Session


class MultiPublishThread(QThread):
    log_line = pyqtSignal(str)
    """Ngắn gọn cho thanh trạng thái (job i/tổng + bước hiện tại)."""

    publish_step = pyqtSignal(str)
    publish_job_progress = pyqtSignal(int, int)  # current 1-based, total jobs
    finished_summary = pyqtSignal(int, int)

    def __init__(
        self,
        tasks: list[dict],
        parent=None,
    ):
        """
        tasks: sorted list of dicts with keys:
          session_folder, session_label, job (dict), video_path, thumb_path,
          title, description, profile (full profile dict)
        """
        super().__init__(parent)
        self._tasks = tasks

    def request_cancel(self) -> None:
        """Hủy sau bước hiện tại (Facebook giữa chunk / YouTube giữa đọc file hoặc upload)."""
        self.requestInterruption()

    def run(self):
        ok = 0
        fail = 0
        total = len(self._tasks)

        def on_step(msg: str) -> None:
            self.log_line.emit(f"[PUBLISH][STEP] {msg}")
            self.publish_step.emit(msg)

        for ti, t in enumerate(self._tasks):
            if self.isInterruptionRequested():
                rest = total - ti
                self.log_line.emit(
                    f"[PUBLISH] Đã hủy — bỏ qua {rest} job chưa chạy (vẫn pending trong session.json)."
                )
                break

            folder = t["session_folder"]
            label = t["session_label"]
            job = t["job"]
            vid = t["video_path"]
            thumb = t.get("thumb_path") or ""
            title = t.get("title") or ""
            desc = t.get("description") or ""
            profile = t["profile"]
            jid = str(job.get("id") or "")
            pl = str(job.get("platform") or "")
            timing = str(job.get("timing_mode") or "immediate")
            y_mfk = bool(job.get("youtube_made_for_kids", False))
            sched_iso = str(job.get("scheduled_at") or "")
            sched_unix = job.get("scheduled_unix")
            try:
                su = int(sched_unix) if sched_unix is not None else None
            except (TypeError, ValueError):
                su = None

            creds_root = profile.get("credentials") or {}
            creds = creds_root.get(pl) or {}

            publish_immediately = timing == "immediate"

            self.publish_job_progress.emit(ti, total)
            self.publish_step.emit(f"{pl}: chuẩn bị upload — {label[:40]}")
            self.log_line.emit(
                f"[PUBLISH] Bắt đầu session={label!r} folder={folder} "
                f"platform={pl} job_id={jid} timing={timing} video={vid}"
            )

            try:
                sess = Session.load(folder)
            except Exception as e:
                fail += 1
                self.log_line.emit(
                    f"[PUBLISH][ERROR] Không load session.json: session={label!r} err={e}"
                )
                continue

            if not vid:
                fail += 1
                now = datetime.now().isoformat(timespec="seconds")
                self.log_line.emit(
                    f"[PUBLISH][ERROR] Không có video output (result/step6): session={label!r}"
                )
                if jid:
                    sess.patch_publish_job(
                        jid,
                        status="error",
                        last_error="missing video",
                        executed_at=now,
                        remote_asset_id="",
                        result_message="Thiếu file video để upload",
                    )
                continue

            if self.isInterruptionRequested():
                self.log_line.emit(
                    "[PUBLISH] Đã hủy trước khi upload job này — các job sau chưa chạy."
                )
                break

            res = run_publish(
                platform=pl,
                credentials=creds,
                video_path=vid,
                thumbnail_path=thumb,
                title=title,
                description=desc,
                youtube_made_for_kids=y_mfk,
                publish_immediately=publish_immediately,
                scheduled_publish_unix=su,
                scheduled_at_iso=sched_iso,
                on_progress=on_step,
                is_cancelled=self.isInterruptionRequested,
            )

            now = datetime.now().isoformat(timespec="seconds")
            detail = res.get("detail") if isinstance(res.get("detail"), dict) else {}
            remote_id = ""
            if isinstance(detail, dict):
                remote_id = str(detail.get("id") or detail.get("video_id") or "").strip()
                if not remote_id:
                    resp = detail.get("response")
                    if isinstance(resp, dict):
                        remote_id = str(
                            resp.get("id")
                            or resp.get("video_id")
                            or resp.get("post_id")
                            or ""
                        ).strip()
                if not remote_id and pl == "facebook":
                    sid = detail.get("upload_session_id")
                    if sid:
                        remote_id = f"fb_upload:{sid}"

            if res.get("cancelled"):
                if jid:
                    sess.patch_publish_job(
                        jid,
                        status="cancelled",
                        last_error="cancelled by user",
                        executed_at=now,
                        remote_asset_id=remote_id,
                        result_message="Người dùng hủy trong lúc upload",
                    )
                self.log_line.emit(
                    f"[PUBLISH][CANCEL] session={label!r} platform={pl} — đã dừng; "
                    f"các job còn lại không chạy."
                )
                break

            if res.get("ok"):
                ok += 1
                msg = str(res.get("message") or "OK")
                self.log_line.emit(
                    f"[PUBLISH][OK] session={label!r} platform={pl} — {msg}"
                )
                if jid:
                    sess.patch_publish_job(
                        jid,
                        status="done",
                        last_error="",
                        executed_at=now,
                        remote_asset_id=remote_id,
                        result_message=msg[:2000],
                    )
            else:
                fail += 1
                err = str(res.get("message") or "unknown")
                self.log_line.emit(
                    f"[PUBLISH][FAIL] session={label!r} platform={pl} — {err[:2000]}"
                )
                if jid:
                    sess.patch_publish_job(
                        jid,
                        status="error",
                        last_error=err[:4000],
                        executed_at=now,
                        remote_asset_id=remote_id,
                        result_message=err[:2000],
                    )

        self.log_line.emit(
            f"[PUBLISH] Hoàn tất — thành công={ok} thất bại={fail} (tổng {ok + fail} job đã xử lý)"
        )
        self.publish_step.emit("")
        self.finished_summary.emit(ok, fail)
