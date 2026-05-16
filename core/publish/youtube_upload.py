"""YouTube Data API v3 — refresh token + simple upload (urllib only)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from core.publish.cancelled import PublishCancelled


def _post_form(url: str, fields: dict[str, str]) -> dict[str, Any]:
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw)


def refresh_access_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    *,
    is_cancelled: Callable[[], bool] | None = None,
) -> str:
    if is_cancelled and is_cancelled():
        raise PublishCancelled()
    data = _post_form(
        "https://oauth2.googleapis.com/token",
        {
            "client_id": client_id.strip(),
            "client_secret": client_secret.strip(),
            "refresh_token": refresh_token.strip(),
            "grant_type": "refresh_token",
        },
    )
    err = data.get("error")
    if err:
        raise RuntimeError(json.dumps(data, ensure_ascii=False)[:2000])
    at = data.get("access_token")
    if not at:
        raise RuntimeError("No access_token in OAuth response")
    return str(at)


def upload_video_simple(
    *,
    access_token: str,
    video_path: str,
    title: str,
    description: str,
    made_for_kids: bool,
    privacy_status: str = "public",
    publish_at_rfc3339: str | None = None,
    on_progress: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """
    multipart/related upload (metadata JSON + video bytes). OK for moderate sizes.
    """
    import mimetypes
    import uuid
    from pathlib import Path

    def _p(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    def _abort() -> None:
        if is_cancelled and is_cancelled():
            raise PublishCancelled()

    boundary = f"subsync_yt_{uuid.uuid4().hex}"
    crlf = b"\r\n"
    p = Path(video_path)
    if not p.is_file():
        raise RuntimeError(f"Video not found: {p}")
    mime = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
    priv = privacy_status
    status_body: dict[str, Any] = {
        "privacyStatus": priv,
        "selfDeclaredMadeForKids": bool(made_for_kids),
    }
    if publish_at_rfc3339:
        status_body["publishAt"] = publish_at_rfc3339.strip()
        status_body["privacyStatus"] = "private"
    snippet = {
        "snippet": {
            "title": (title or "Untitled")[:100],
            "description": description or "",
            "categoryId": "22",
        },
        "status": status_body,
    }
    meta_json = json.dumps(snippet).encode("utf-8")

    _abort()
    _p("YouTube: đang đọc file video vào bộ nhớ…")
    video_bytes = p.read_bytes()
    _abort()
    _p(f"YouTube: đang upload multipart (~{len(video_bytes) // 1_048_576} MB) — có thể vài phút…")
    parts: list[bytes] = []
    parts.append(f"--{boundary}".encode() + crlf)
    parts.append(b"Content-Type: application/json; charset=UTF-8" + crlf + crlf)
    parts.append(meta_json + crlf)

    parts.append(f"--{boundary}".encode() + crlf)
    parts.append(f"Content-Type: {mime}".encode() + crlf + crlf)
    parts.append(video_bytes + crlf)
    parts.append(f"--{boundary}--".encode() + crlf)
    body = b"".join(parts)

    q = urllib.parse.urlencode({"uploadType": "multipart", "part": "snippet,status"})
    url = f"https://www.googleapis.com/upload/youtube/v3/videos?{q}"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token.strip()}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=3600) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            _p("YouTube: upload xong, đang xử lý phản hồi…")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(err)
        except Exception:
            data = {"raw": err[:2000], "status": e.code}
        raise RuntimeError(json.dumps(data, ensure_ascii=False)[:4000]) from None


def set_thumbnail(
    *,
    access_token: str,
    video_id: str,
    thumbnail_path: str,
    on_progress: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """
    Call YouTube thumbnails.set (simple upload) to apply a custom thumbnail.
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
        raise RuntimeError("Missing video_id for thumbnails.set")
    p = Path(thumbnail_path)
    if not p.is_file():
        raise RuntimeError(f"Thumbnail not found: {p}")

    _abort()
    img = p.read_bytes()
    _abort()
    _p(f"YouTube: đang set thumbnail (~{len(img) // 1024} KB)…")

    q = urllib.parse.urlencode({"uploadType": "media", "videoId": vid})
    url = f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set?{q}"
    req = urllib.request.Request(
        url,
        data=img,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token.strip()}",
            "Content-Type": "application/octet-stream",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(err)
        except Exception:
            data = {"raw": err[:2000], "status": e.code}
        raise RuntimeError(json.dumps(data, ensure_ascii=False)[:4000]) from None
