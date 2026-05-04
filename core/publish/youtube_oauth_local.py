"""
OAuth 2 localhost — lấy refresh token cho YouTube Data API.

redirect_uri phải trùng **ký tự** một mục trong Google Cloud → OAuth client (Web)
→ Authorized redirect URIs (http/https, localhost vs 127.0.0.1, dấu / cuối đều khác nhau).

Chỉ hỗ trợ http://127.0.0.1:... hoặc http://localhost:... (server tối giản, không TLS).
"""

from __future__ import annotations

import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"

DEFAULT_REDIRECT_URI = "http://127.0.0.1:8742/"


def _exchange_code_for_tokens(
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> dict:
    body = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id.strip(),
            "client_secret": client_secret.strip(),
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            err = json.loads(raw)
        except json.JSONDecodeError:
            err = {"raw": raw[:2000], "status": e.code}
        raise RuntimeError(json.dumps(err, ensure_ascii=False)[:4000]) from None


def _parse_local_http_redirect(redirect_uri: str) -> tuple[str, int, str]:
    """
    Trả về (bind_host, bind_port, expected_path) cho HTTPServer và so khớp path callback.
    expected_path: path trong URI đã strip (chuỗi rỗng → "/" để khớp GET /?code=...).
    """
    ru = (redirect_uri or "").strip()
    if not ru:
        ru = DEFAULT_REDIRECT_URI
    p = urllib.parse.urlparse(ru)
    if p.scheme.lower() != "http":
        raise RuntimeError("Chỉ hỗ trợ redirect dạng http:// (localhost / 127.0.0.1).")
    host = (p.hostname or "").strip().lower()
    if host not in ("127.0.0.1", "localhost"):
        raise RuntimeError(
            "Redirect URI phải dùng hostname 127.0.0.1 hoặc localhost (vd http://127.0.0.1:8742/)."
        )
    if p.port is None:
        raise RuntimeError("Redirect URI cần có cổng tường minh (vd :8742).")
    if p.query:
        raise RuntimeError("Redirect URI không nên chứa query string; chỉ dùng path (vd / hoặc /callback).")
    path = p.path or ""
    if path and not path.startswith("/"):
        path = "/" + path
    expected_path = path if path else "/"
    return (host, int(p.port), expected_path)


def run_local_oauth(
    client_id: str,
    client_secret: str,
    *,
    redirect_uri: str | None = None,
    login_hint: str | None = None,
    max_wait_seconds: int = 180,
) -> str:
    """
    Mở trình duyệt → Google redirect về redirect_uri → đổi code lấy refresh_token.

    - prompt gồm select_account + consent để chọn đúng Gmail và lấy refresh_token.
    - login_hint: gợi ý email (tuỳ chọn), vẫn có thể đổi tài khoản nhờ select_account.
    """
    cid = client_id.strip()
    csec = client_secret.strip()
    if not cid or not csec:
        raise RuntimeError("Thiếu Client ID hoặc Client secret.")

    raw = (redirect_uri or "").strip() or DEFAULT_REDIRECT_URI
    redirect_for_google = raw.split("#", 1)[0].strip()
    bind_host, bind_port, expected_path = _parse_local_http_redirect(redirect_for_google)

    state = secrets.token_urlsafe(16)
    auth_params: dict[str, str] = {
        "client_id": cid,
        "redirect_uri": redirect_for_google,
        "response_type": "code",
        "scope": YOUTUBE_UPLOAD_SCOPE,
        "access_type": "offline",
        "prompt": "select_account consent",
        "state": state,
    }
    lh = (login_hint or "").strip()
    if lh:
        auth_params["login_hint"] = lh
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
        auth_params
    )

    result_holder: dict = {}

    def path_matches(req_path: str) -> bool:
        base = req_path.split("?", 1)[0]
        base = urllib.parse.unquote(base)
        if not base.startswith("/"):
            base = "/" + base if base else "/"
        if not base:
            base = "/"
        return base == expected_path

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            if result_holder.get("code"):
                self._ok_page()
                return
            if not path_matches(self.path):
                self.send_error(404)
                return
            qpart = self.path.split("?", 1)[1] if "?" in self.path else ""
            qs = urllib.parse.parse_qs(qpart)
            if qs.get("error"):
                result_holder["error"] = qs.get("error", [""])[0]
                result_holder["error_description"] = qs.get("error_description", [""])[0]
            elif qs.get("state", [None])[0] != state:
                result_holder["error"] = "state_mismatch"
            elif qs.get("code"):
                result_holder["code"] = qs.get("code", [""])[0]
            self._ok_page()

        def _ok_page(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = (
                "<!DOCTYPE html><html><body style='font-family:sans-serif;padding:24px;'>"
                "<h2>Đăng nhập YouTube — xong</h2>"
                "<p>Bạn có thể đóng tab này và quay lại SubSync.</p></body></html>"
            )
            self.wfile.write(msg.encode("utf-8"))

    server: HTTPServer | None = None
    try:
        try:
            server = HTTPServer((bind_host, bind_port), Handler)
        except OSError as e:
            raise RuntimeError(
                f"Không mở được {bind_host}:{bind_port} ({e}). "
                f"Đổi Redirect URI / cổng hoặc tắt app đang chiếm cổng."
            ) from e
        server.timeout = 1.0
        webbrowser.open(auth_url)
        deadline = time.monotonic() + max_wait_seconds
        while time.monotonic() < deadline:
            server.handle_request()
            if result_holder:
                break
    finally:
        if server is not None:
            try:
                server.server_close()
            except OSError:
                pass

    if not result_holder:
        raise RuntimeError(
            f"Hết thời gian chờ ({max_wait_seconds}s), không nhận được redirect về máy bạn.\n\n"
            "Nếu trên trình duyệt Google báo **redirect_uri_mismatch**:\n"
            "→ Vào Google Cloud → OAuth client (Web) → Authorized redirect URIs\n"
            f"→ Thêm **chính xác** (copy dán): {redirect_for_google}\n\n"
            "Chú ý: http khác https; **localhost** khác **127.0.0.1**; có/không dấu **/** cuối "
            "là các URI khác nhau — phải trùng với ô Redirect URI trong SubSync.\n\n"
            "Để đổi Gmail: chạy OAuth lại (đã bật chọn tài khoản); hoặc cửa sổ ẩn danh."
        )
    if "error" in result_holder:
        err = result_holder.get("error", "")
        desc = result_holder.get("error_description", "")
        extra = ""
        if "redirect_uri" in str(err).lower() or "redirect_uri" in str(desc).lower():
            extra = (
                f"\n\nRedirect URI đang dùng trong SubSync:\n{redirect_for_google}\n"
                "Phải thêm **y hệt** vào GCP (Authorized redirect URIs)."
            )
        raise RuntimeError(f"OAuth lỗi: {err} {desc}{extra}")

    code = result_holder.get("code")
    if not code:
        raise RuntimeError("Không nhận được authorization code từ Google.")

    try:
        data = _exchange_code_for_tokens(cid, csec, code, redirect_for_google)
    except RuntimeError as e:
        msg = str(e)
        if "redirect_uri_mismatch" in msg.lower():
            msg += f"\n\nĐăng ký trên GCP đúng URI:\n{redirect_for_google}"
        raise RuntimeError(msg) from None

    rt = data.get("refresh_token")
    if not rt:
        raise RuntimeError(
            "Google không trả refresh_token. Thử: gỡ quyền app tại "
            "https://myaccount.google.com/permissions rồi chạy lại OAuth."
        )
    return str(rt).strip()
