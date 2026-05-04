"""Dispatch publish to one platform."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from core.publish.cancelled import PublishCancelled
from core.publish.facebook_upload import upload_page_video
from core.publish.youtube_upload import refresh_access_token, upload_video_simple


def _iso_local_to_rfc3339_z(iso_local: str) -> str:
    """Local / naive ISO → UTC RFC3339 for YouTube publishAt."""
    s = (iso_local or "").strip().replace("Z", "")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return iso_local
    if dt.tzinfo is None:
        return datetime.fromtimestamp(dt.timestamp(), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_publish(
    *,
    platform: str,
    credentials: dict[str, Any],
    video_path: str,
    thumbnail_path: str,
    title: str,
    description: str,
    youtube_made_for_kids: bool,
    publish_immediately: bool,
    scheduled_publish_unix: int | None,
    scheduled_at_iso: str = "",
    on_progress: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """
    Returns dict with keys: ok (bool), platform, message (str), detail (optional dict).
    thumbnail_path may be unused per platform (YouTube can use thumbnails.set in a follow-up).
    """
    pl = platform.strip().lower()
    try:
        if pl == "facebook":
            c = credentials or {}
            r = upload_page_video(
                video_path=video_path,
                page_id=str(c.get("page_id") or ""),
                page_access_token=str(c.get("page_access_token") or ""),
                title=title,
                description=description,
                publish_immediately=publish_immediately,
                scheduled_publish_unix=scheduled_publish_unix,
                on_progress=on_progress,
                is_cancelled=is_cancelled,
            )
            rid = str(r.get("id") or "").strip()
            if rid:
                fb_msg = f"Facebook upload OK — video id {rid}"
            else:
                fb_msg = (
                    "Facebook upload OK — Meta trả success (không có id trong finish); "
                    "thường gặp khi lên lịch — kiểm tra video/Page trên Facebook."
                )
            return {
                "ok": True,
                "platform": pl,
                "message": fb_msg,
                "detail": r,
            }
        if pl == "youtube":
            c = credentials or {}
            if on_progress:
                on_progress("YouTube: làm mới access token (OAuth)…")
            at = refresh_access_token(
                str(c.get("client_id") or ""),
                str(c.get("client_secret") or ""),
                str(c.get("refresh_token") or ""),
                is_cancelled=is_cancelled,
            )
            pub_at = None
            if not publish_immediately and scheduled_at_iso:
                pub_at = _iso_local_to_rfc3339_z(scheduled_at_iso)
            r = upload_video_simple(
                access_token=at,
                video_path=video_path,
                title=title,
                description=description,
                made_for_kids=youtube_made_for_kids,
                privacy_status="public",
                publish_at_rfc3339=pub_at,
                on_progress=on_progress,
                is_cancelled=is_cancelled,
            )
            vid = r.get("id", "")
            msg = f"YouTube upload OK — id {vid}"
            if thumbnail_path:
                msg += " (thumbnail: set manually in Studio if needed)"
            return {"ok": True, "platform": pl, "message": msg, "detail": r}
        if pl == "tiktok":
            return {
                "ok": False,
                "platform": pl,
                "message": (
                    "TikTok Content Posting API chưa được tích hợp trong SubSync. "
                    "Điền token trong profile để sẵn sàng cho bản sau; hiện tại bỏ qua."
                ),
                "detail": {},
            }
        return {
            "ok": False,
            "platform": pl,
            "message": f"Unknown platform: {pl}",
            "detail": {},
        }
    except PublishCancelled:
        return {
            "ok": False,
            "cancelled": True,
            "platform": pl,
            "message": "Đã hủy bởi người dùng",
            "detail": {},
        }
    except Exception as e:
        return {
            "ok": False,
            "platform": pl,
            "message": str(e)[:4000],
            "detail": {"error": str(e)},
        }
