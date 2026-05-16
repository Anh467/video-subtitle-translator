"""Build and sort publish_plan job entries for session.json."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from core.publish_profiles import PLATFORM_ORDER


def platforms_still_need_upload(
    existing_plan: list | None, selected_platforms: list[str]
) -> list[str]:
    """
    Giữ thứ tự trong selected_platforms, bỏ các platform đã có ít nhất một job status=done.
    Dùng chế độ 'chỉ đăng phần chưa upload thành công'.
    """
    done: set[str] = set()
    for j in existing_plan or []:
        if str(j.get("status", "")).lower() != "done":
            continue
        pl = str(j.get("platform") or "").strip().lower()
        if pl:
            done.add(pl)
    return [p for p in selected_platforms if p not in done]


def _schedule_summary_vi(
    schedule_mode: str,
    start_local: datetime | None,
    interval_hours: int,
    platforms_ordered: list[str],
) -> str:
    pls = ", ".join(platforms_ordered) or "(none)"
    if schedule_mode == "immediate":
        return f"Đăng ngay — thứ tự nền tảng: {pls}"
    st = (
        start_local.strftime("%Y-%m-%d %H:%M")
        if start_local
        else "(không xác định)"
    )
    return (
        f"Lên lịch — bắt đầu {st} (giờ máy), mỗi nền tảng cách nhau {interval_hours} giờ "
        f"theo thứ tự: {pls}"
    )


def enrich_publish_plan_snapshot(
    jobs: list[dict],
    *,
    video_path: str,
    thumbnail_path: str,
    title: str,
    description: str,
    schedule_mode: str,
    interval_hours: int,
    platforms_ordered: list[str],
    start_local: datetime | None,
    publish_scope_mode: str = "all",
) -> list[dict]:
    """Gắn snapshot media + mô tả lịch vào từng job (lưu session.json)."""
    summary = _schedule_summary_vi(
        schedule_mode, start_local, interval_hours, platforms_ordered
    )
    created = datetime.now().isoformat(timespec="seconds")
    iv = int(interval_hours) if schedule_mode == "scheduled" else 0
    media = {
        "video_path": video_path or "",
        "thumbnail_path": thumbnail_path or "",
        "title": title or "",
        "description": description or "",
    }
    for j in jobs:
        j["batch_schedule_mode"] = schedule_mode
        j["interval_hours_between_platforms"] = iv
        j["schedule_summary"] = summary
        j["plan_created_at"] = created
        j["publish_scope_mode"] = publish_scope_mode
        j["media"] = dict(media)
        j.setdefault("remote_asset_id", "")
        j.setdefault("result_message", "")
    return jobs


def mark_all_jobs_skipped(jobs: list[dict], reason: str) -> list[dict]:
    """Đánh dấu toàn bộ job skipped + lý do (ví dụ không có video output)."""
    now = datetime.now().isoformat(timespec="seconds")
    for j in jobs:
        j["status"] = "skipped"
        j["last_error"] = reason
        j["executed_at"] = now
        j["remote_asset_id"] = ""
        j["result_message"] = f"skipped: {reason}"[:2000]
    return jobs


def build_publish_jobs(
    *,
    platforms_checked: list[str],
    profile: dict,
    schedule_mode: str,
    start_local: datetime,
    interval_hours: int,
    youtube_made_for_kids: bool,
) -> list[dict]:
    """
    schedule_mode: 'immediate' | 'scheduled'
    Platforms use fixed order: facebook → youtube → tiktok (only those in platforms_checked).
    """
    ordered = [p for p in PLATFORM_ORDER if p in platforms_checked]
    now = datetime.now()
    jobs: list[dict] = []

    if schedule_mode == "immediate":
        base = now
        for pl in ordered:
            sched = base
            sched_iso = sched.isoformat(timespec="seconds")
            unix = int(sched.timestamp())
            jobs.append(
                _job_dict(
                    pl,
                    profile,
                    sched_iso,
                    unix,
                    "immediate",
                    youtube_made_for_kids,
                )
            )
    else:
        base = start_local
        for i, pl in enumerate(ordered):
            sched = base + timedelta(hours=interval_hours * i)
            sched_iso = sched.isoformat(timespec="seconds")
            unix = int(sched.timestamp())
            jobs.append(
                _job_dict(
                    pl,
                    profile,
                    sched_iso,
                    unix,
                    "scheduled",
                    youtube_made_for_kids,
                )
            )

    return sort_publish_jobs(jobs)


def scheduled_unix_for_platform(
    *,
    anchor_unix: int,
    interval_hours: int,
    platform: str,
) -> int:
    """
    Deterministic scheduled time for a platform.

    NEW RULE (workspace scheduling):
    - One video (one session) has ONE scheduled slot time.
    - All platforms (Facebook/YouTube/...) for that session use the SAME slot.
    - interval_hours is applied between sessions (videos), not between platforms.

    Used to keep scheduling stable across retries / partial success.
    """
    _ = (interval_hours, platform)  # kept for backward-compatible signature
    return int(anchor_unix)


def _job_dict(
    platform: str,
    profile: dict,
    sched_iso: str,
    unix: int,
    timing_mode: str,
    youtube_mfk: bool,
) -> dict:
    return {
        "id": f"job_{uuid.uuid4().hex[:12]}",
        "platform": platform,
        "profile_id": str(profile.get("id") or ""),
        "profile_name": str(profile.get("name") or ""),
        "scheduled_at": sched_iso,
        "scheduled_unix": unix,
        "timing_mode": timing_mode,
        "youtube_made_for_kids": bool(youtube_mfk),
        "status": "pending",
        "last_error": "",
        "executed_at": "",
        "batch_schedule_mode": "",
        "interval_hours_between_platforms": 0,
        "schedule_summary": "",
        "plan_created_at": "",
        "publish_scope_mode": "",
        "media": {},
        "remote_asset_id": "",
        "result_message": "",
    }


def sort_publish_jobs(jobs: list[dict]) -> list[dict]:
    po = {p: i for i, p in enumerate(PLATFORM_ORDER)}

    def key(j: dict):
        return (int(j.get("scheduled_unix") or 0), po.get(str(j.get("platform") or ""), 99))

    return sorted(jobs, key=key)
