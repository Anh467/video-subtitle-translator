#!/usr/bin/env python3
"""
Chunked (resumable) Page video upload for Meta Graph API — same bytes as source file, no re-encode.

Uses POST .../{page-id}/videos with upload_phase start | transfer | finish (see Page Videos edge).

Requires a Page access token with pages_manage_posts (and related video permissions).
Token (first match wins):

1. Env ``FACEBOOK_PAGE_ACCESS_TOKEN``
2. Env ``FACEBOOK_PAGE_TOKEN_FILE`` → path to a one-line token file (Docker secrets)
3. CLI ``--token-file /path/to/file``
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

DEFAULT_GRAPH_VIDEO_HOST = "graph-video.facebook.com"
DEFAULT_CHUNK_MB = 4


def _resolve_page_access_token(args: argparse.Namespace) -> str:
    """Same Page token as n8n Facebook Graph credential; avoid logging this value."""
    t = os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", "").strip()
    if t:
        return t
    path = os.environ.get("FACEBOOK_PAGE_TOKEN_FILE", "").strip()
    if path:
        p = Path(path)
        if p.is_file():
            return p.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    tf = getattr(args, "token_file", None) or ""
    tf = str(tf).strip()
    if tf:
        p = Path(tf)
        if p.is_file():
            return p.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    return ""


def _read_json(resp: Any) -> dict[str, Any]:
    raw = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise SystemExit(f"Facebook API returned non-JSON ({resp.status}): {raw[:800]}")


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
        raise SystemExit(json.dumps(data, ensure_ascii=False)[:4000]) from None


def _post_multipart_chunk(
    base_url: str,
    token: str,
    fields: dict[str, str],
    chunk: bytes,
) -> dict[str, Any]:
    boundary = f"----n8nFbChunk{uuid.uuid4().hex}"
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
        raise SystemExit(json.dumps(data, ensure_ascii=False)[:4000]) from None


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


def _die_error(data: dict[str, Any]) -> None:
    if "error" in data:
        raise SystemExit(json.dumps(data, ensure_ascii=False)[:4000])


def main() -> int:
    p = argparse.ArgumentParser(
        description="Facebook Page resumable video upload (chunked)."
    )
    p.add_argument("--video", required=True, help="Path to local video file (mp4).")
    p.add_argument("--page-id", required=True, dest="page_id")
    p.add_argument("--graph-version", default="v25.0", dest="graph_version")
    p.add_argument("--chunk-mb", type=int, default=DEFAULT_CHUNK_MB, dest="chunk_mb")
    p.add_argument(
        "--meta-b64",
        required=True,
        dest="meta_b64",
        help="Base64 of JSON: {title, description, scheduled_publish_unix}",
    )
    p.add_argument(
        "--token-file",
        default="",
        dest="token_file",
        help="Optional file with one line: Page access token (alternative to env).",
    )
    args = p.parse_args()

    token = _resolve_page_access_token(args)
    if not token:
        print(
            "Thiếu Page access token. Một trong các cách: "
            "biến môi trường FACEBOOK_PAGE_ACCESS_TOKEN; "
            "hoặc FACEBOOK_PAGE_TOKEN_FILE=/path/to/secret; "
            "hoặc --token-file /path (cùng token credential Facebook Graph trong n8n). "
            "Với Docker: thêm vào docker-compose environment hoặc file .env — xem docker-compose.n8n.example.yml.",
            file=sys.stderr,
        )
        return 1

    try:
        raw_meta = base64.b64decode(args.meta_b64.strip())
        meta = json.loads(raw_meta.decode("utf-8"))
    except Exception as e:
        print(f"meta-b64 không hợp lệ: {e}", file=sys.stderr)
        return 1

    title = str(meta.get("title") or "")
    description = str(meta.get("description") or "")
    try:
        scheduled_unix = int(meta["scheduled_publish_unix"])
    except (KeyError, TypeError, ValueError):
        print("meta cần scheduled_publish_unix (số Unix)", file=sys.stderr)
        return 1

    path = os.path.abspath(os.path.normpath(args.video))
    if not os.path.isfile(path):
        print(f"Không tìm thấy file video: {path}", file=sys.stderr)
        return 1

    file_size = os.path.getsize(path)
    if file_size <= 0:
        print("File video rỗng.", file=sys.stderr)
        return 1

    chunk_size = max(1_048_576, min(args.chunk_mb * 1024 * 1024, 50 * 1024 * 1024))
    ver = args.graph_version.strip().lstrip("/")
    if not ver.startswith("v"):
        ver = "v25.0"

    start_url = _page_videos_base(ver, args.page_id, DEFAULT_GRAPH_VIDEO_HOST)
    start_body = {
        "upload_phase": "start",
        "file_size": str(file_size),
    }
    data = _post_urlencoded(start_url, token, start_body)
    _die_error(data)

    session_id = data.get("upload_session_id")
    if not session_id:
        raise SystemExit(json.dumps(data, ensure_ascii=False)[:2000])

    host = _host_from_start(data)
    transfer_base = _page_videos_base(ver, args.page_id, host)

    next_offset = int(data.get("start_offset", 0))
    max_steps = (file_size // max(1, chunk_size)) + 20
    step = 0

    with open(path, "rb") as f:
        while next_offset < file_size:
            step += 1
            if step > max_steps:
                print(
                    "Quá nhiều vòng transfer — kiểm tra phản hồi Graph.",
                    file=sys.stderr,
                )
                return 1
            at = next_offset
            to_read = min(chunk_size, file_size - next_offset)
            f.seek(next_offset)
            chunk = f.read(to_read)
            if len(chunk) != to_read:
                print("Đọc file không đủ byte.", file=sys.stderr)
                return 1

            tr_fields = {
                "upload_phase": "transfer",
                "upload_session_id": str(session_id),
                "start_offset": str(next_offset),
            }
            tr = _post_multipart_chunk(transfer_base, token, tr_fields, chunk)
            _die_error(tr)

            if "start_offset" in tr:
                next_offset = int(tr["start_offset"])
            else:
                next_offset += len(chunk)
            if next_offset == at:
                print("Offset không tiến sau transfer — dừng.", file=sys.stderr)
                return 1

    finish_fields = {
        "upload_phase": "finish",
        "upload_session_id": str(session_id),
        "title": title,
        "description": description,
        "published": "false",
        "scheduled_publish_time": str(scheduled_unix),
        "unpublished_content_type": "SCHEDULED",
    }
    finish_url = _page_videos_base(ver, args.page_id, host)
    done = _post_urlencoded(finish_url, token, finish_fields)
    _die_error(done)

    vid = done.get("id") or done.get("video_id")
    if not vid:
        raise SystemExit(json.dumps(done, ensure_ascii=False)[:2000])

    out = {"id": str(vid), "upload_session_id": str(session_id)}
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
