"""Facebook Page resumable video upload (Graph API) — urllib only."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from core.publish.cancelled import PublishCancelled

DEFAULT_GRAPH_VIDEO_HOST = "graph-video.facebook.com"
DEFAULT_GRAPH_HOST = "graph.facebook.com"
DEFAULT_CHUNK_MB = 4
# Page video lên lịch — Meta: thường 10 phút … 30 ngày so với thời điểm request (finish phase).
FB_SCHEDULE_MIN_LEAD_SEC = 600
FB_SCHEDULE_MAX_LEAD_SEC = 30 * 86400


def _read_json(resp: Any) -> dict[str, Any]:
    raw = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(f"Facebook API returned non-JSON ({resp.status}): {raw[:800]}")


def _post_urlencoded(
    base_url: str, token: str, fields: dict[str, str]
) -> dict[str, Any]:
    q = urllib.parse.urlencode({"access_token": token})
    url = f"{base_url}?{q}"
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return _read_json(resp)
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
            data = json.loads(err_body)
        except Exception:
            data = {"raw": err_body[:1200], "status": e.code}
        if isinstance(data, dict) and "error" not in data:
            data = {"error": data, "status": e.code}
        elif isinstance(data, dict):
            data.setdefault("status", e.code)
        raise RuntimeError(json.dumps(data, ensure_ascii=False)[:4000]) from None


def _post_multipart_chunk(
    base_url: str,
    token: str,
    fields: dict[str, str],
    chunk: bytes,
) -> dict[str, Any]:
    boundary = f"----SubSyncFb{uuid.uuid4().hex}"
    crlf = b"\r\n"
    parts: list[bytes] = []

    for key, value in fields.items():
        parts.append(f"--{boundary}".encode() + crlf)
        parts.append(
            f'Content-Disposition: form-data; name="{key}"'.encode() + crlf + crlf
        )
        parts.append(str(value).encode() + crlf)

    parts.append(f"--{boundary}".encode() + crlf)
    parts.append(
        b'Content-Disposition: form-data; name="video_file_chunk"; filename="chunk.bin"'
        + crlf
    )
    parts.append(b"Content-Type: application/octet-stream" + crlf + crlf)
    parts.append(chunk + crlf)
    parts.append(f"--{boundary}--".encode() + crlf)

    body = b"".join(parts)
    q = urllib.parse.urlencode({"access_token": token})
    url = f"{base_url}?{q}"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return _read_json(resp)
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
            data = json.loads(err_body)
        except Exception:
            data = {"raw": err_body[:1200], "status": e.code}
        raise RuntimeError(json.dumps(data, ensure_ascii=False)[:4000]) from None


def _post_multipart_file(
    base_url: str,
    token: str,
    fields: dict[str, str],
    *,
    file_field: str,
    filename: str,
    content_type: str,
    file_bytes: bytes,
) -> dict[str, Any]:
    boundary = f"----SubSyncFbFile{uuid.uuid4().hex}"
    crlf = b"\r\n"
    parts: list[bytes] = []

    for key, value in fields.items():
        parts.append(f"--{boundary}".encode() + crlf)
        parts.append(
            f'Content-Disposition: form-data; name="{key}"'.encode() + crlf + crlf
        )
        parts.append(str(value).encode() + crlf)

    parts.append(f"--{boundary}".encode() + crlf)
    parts.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"'.encode()
        + crlf
    )
    parts.append(f"Content-Type: {content_type}".encode() + crlf + crlf)
    parts.append(file_bytes + crlf)
    parts.append(f"--{boundary}--".encode() + crlf)

    body = b"".join(parts)
    q = urllib.parse.urlencode({"access_token": token})
    url = f"{base_url}?{q}"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return _read_json(resp)
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
            data = json.loads(err_body)
        except Exception:
            data = {"raw": err_body[:1200], "status": e.code}
        raise RuntimeError(json.dumps(data, ensure_ascii=False)[:4000]) from None


def set_video_thumbnail(
    *,
    video_id: str,
    page_access_token: str,
    thumbnail_path: str,
    graph_version: str = "v21.0",
    on_progress: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """
    Best-effort set preferred thumbnail for a Page video:
      POST /{video_id}/thumbnails?access_token=...  (multipart form: source=@file, is_preferred=true)
    """
    from pathlib import Path

    def _p(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    def _abort() -> None:
        if is_cancelled and is_cancelled():
            raise PublishCancelled()

    vid = (video_id or "").strip()
    if not vid:
        raise RuntimeError("Missing video_id for /thumbnails")
    token = page_access_token.strip()
    if not token:
        raise RuntimeError("Missing page_access_token for /thumbnails")
    p = Path(thumbnail_path)
    if not p.is_file():
        raise RuntimeError(f"Thumbnail not found: {p}")

    ver = graph_version.strip().lstrip("/")
    if not ver.startswith("v"):
        ver = "v21.0"
    base_url = f"https://{DEFAULT_GRAPH_HOST}/{ver}/{vid}/thumbnails"

    _abort()
    img = p.read_bytes()
    _abort()
    _p("Facebook: đang set thumbnail…")
    return _post_multipart_file(
        base_url,
        token,
        {"is_preferred": "true"},
        file_field="source",
        filename=p.name,
        content_type="application/octet-stream",
        file_bytes=img,
    )


def _page_videos_base(version: str, page_id: str, host: str) -> str:
    h = host.strip()
    if not h.startswith("http"):
        h = f"https://{h}"
    return f"{h.rstrip('/')}/{version}/{page_id}/videos"


def _host_from_start(data: dict[str, Any]) -> str:
    u = data.get("upload_domain")
    if not u:
        return DEFAULT_GRAPH_VIDEO_HOST
    u = str(u).strip()
    if u.startswith("https://") or u.startswith("http://"):
        from urllib.parse import urlparse

        netloc = urlparse(u).netloc
        return netloc or DEFAULT_GRAPH_VIDEO_HOST
    return u


def upload_page_video(
    *,
    video_path: str,
    page_id: str,
    page_access_token: str,
    title: str,
    description: str,
    graph_version: str = "v21.0",
    chunk_mb: int = DEFAULT_CHUNK_MB,
    publish_immediately: bool = True,
    scheduled_publish_unix: int | None = None,
    on_progress: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """
    Resumable upload. If publish_immediately, video goes live when finish succeeds.
    Otherwise scheduled_publish_unix must be a future Unix timestamp (Graph rules).
    """
    import os
    from pathlib import Path

    token = page_access_token.strip()
    if not token:
        raise RuntimeError("Missing page_access_token")
    pid = page_id.strip()
    if not pid:
        raise RuntimeError("Missing page_id")

    path = Path(video_path).resolve()
    if not path.is_file():
        raise RuntimeError(f"Video file not found: {path}")
    file_size = path.stat().st_size
    if file_size <= 0:
        raise RuntimeError("Video file is empty")

    def _p(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    def _abort() -> None:
        if is_cancelled and is_cancelled():
            raise PublishCancelled()

    chunk_size = max(1_048_576, min(chunk_mb * 1024 * 1024, 50 * 1024 * 1024))
    ver = graph_version.strip().lstrip("/")
    if not ver.startswith("v"):
        ver = "v21.0"

    _abort()
    _p(f"Facebook: bắt đầu upload có thể tiếp tục ({file_size // 1_048_576} MB gần đúng)…")
    start_url = _page_videos_base(ver, pid, DEFAULT_GRAPH_VIDEO_HOST)
    start_body = {"upload_phase": "start", "file_size": str(file_size)}
    data = _post_urlencoded(start_url, token, start_body)
    if "error" in data:
        raise RuntimeError(json.dumps(data, ensure_ascii=False)[:4000])

    session_id = data.get("upload_session_id")
    if not session_id:
        raise RuntimeError(json.dumps(data, ensure_ascii=False)[:2000])

    host = _host_from_start(data)
    transfer_base = _page_videos_base(ver, pid, host)

    next_offset = int(data.get("start_offset", 0))
    max_steps = (file_size // max(1, chunk_size)) + 20
    step = 0
    total_chunks = max(1, (file_size + chunk_size - 1) // chunk_size)

    with open(path, "rb") as f:
        while next_offset < file_size:
            _abort()
            step += 1
            if step > max_steps:
                raise RuntimeError("Too many transfer steps — check Graph responses")
            at = next_offset
            to_read = min(chunk_size, file_size - next_offset)
            f.seek(next_offset)
            chunk = f.read(to_read)
            if len(chunk) != to_read:
                raise RuntimeError("Short read from video file")

            tr_fields = {
                "upload_phase": "transfer",
                "upload_session_id": str(session_id),
                "start_offset": str(next_offset),
            }
            tr = _post_multipart_chunk(transfer_base, token, tr_fields, chunk)
            if "error" in tr:
                raise RuntimeError(json.dumps(tr, ensure_ascii=False)[:4000])

            if "start_offset" in tr:
                next_offset = int(tr["start_offset"])
            else:
                next_offset += len(chunk)
            if next_offset == at:
                raise RuntimeError("Upload offset did not advance after transfer")

            pct = min(100, int(100 * next_offset / file_size)) if file_size else 100
            if step == 1 or step % 3 == 0 or next_offset >= file_size:
                _p(f"Facebook: đang gửi video ~{pct}% (chunk {step}/{total_chunks}+)…")

    _abort()
    _p("Facebook: hoàn tất upload, đang xuất bản / lên lịch…")
    finish_fields: dict[str, str] = {
        "upload_phase": "finish",
        "upload_session_id": str(session_id),
        "title": title or "",
        "description": description or "",
    }
    if publish_immediately:
        finish_fields["published"] = "true"
    else:
        if scheduled_publish_unix is None:
            raise RuntimeError("scheduled_publish_unix required when not immediate")
        st0 = int(scheduled_publish_unix)
        now_u = int(time.time())
        earliest = now_u + FB_SCHEDULE_MIN_LEAD_SEC
        latest = now_u + FB_SCHEDULE_MAX_LEAD_SEC
        st = st0
        if st < now_u:
            raise RuntimeError(
                "Facebook (#100) thời gian lên lịch không hợp lệ: mốc đã chọn **đã qua** "
                f"(Unix đích {st0} < hiện tại {now_u}). Upload lâu hoặc lịch cũ trong session.json — "
                "mở «Đăng đa nền tảng» → Lên lịch → chọn «Bắt đầu» xa hơn, hoặc «Đăng ngay»."
            )
        if st < earliest:
            # Meta tính buffer từ lúc POST finish — upload lâu có thể làm mốc cũ «quá gần».
            st = earliest + 90
            _p(
                f"Facebook: giờ đăng dự kiến quá gần sau khi upload — "
                f"tự lùi lịch +{(st - st0) // 60} phút để đạt tối thiểu ~{FB_SCHEDULE_MIN_LEAD_SEC // 60} phút."
            )
        if st > latest:
            raise RuntimeError(
                f"Facebook: thời điểm lên lịch quá xa (Meta giới hạn ~30 ngày; Unix ≤ {latest})."
            )
        finish_fields["published"] = "false"
        finish_fields["scheduled_publish_time"] = str(st)
        finish_fields["unpublished_content_type"] = "SCHEDULED"

    finish_url = _page_videos_base(ver, pid, host)
    done = _post_urlencoded(finish_url, token, finish_fields)
    if "error" in done:
        raise RuntimeError(json.dumps(done, ensure_ascii=False)[:4000])

    # Meta đôi khi trả {"success": true} (đặc biệt lên lịch) mà không có id/video_id.
    raw_id = done.get("id") or done.get("video_id") or done.get("post_id")
    vid = str(raw_id).strip() if raw_id is not None else ""
    if not vid and done.get("success") is True:
        return {
            "id": "",
            "upload_session_id": str(session_id),
            "response": done,
        }
    if not vid:
        raise RuntimeError(
            "Facebook finish: thiếu id/video_id và không có success=true — "
            + json.dumps(done, ensure_ascii=False)[:2000]
        )

    return {
        "id": vid,
        "upload_session_id": str(session_id),
        "response": done,
    }
