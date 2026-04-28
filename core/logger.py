"""
ApiLogger — centralized logging wrapper cho tất cả API calls trong SubSync.

Usage:
    from core.logger import ApiLogger
    api = ApiLogger(log_fn)          # log_fn = pipeline log callback

    # HTTP requests với auto-log
    resp = api.post("https://...", headers={...}, data=...)
    resp = api.get("https://...")

    # Context manager cho từng segment
    with api.segment(i, total, text) as ctx:
        ctx.info("doing something")
        ctx.warn("something odd")
        result = do_work()
        ctx.done(f"result: {result}")

    # Timer
    with api.timer("Whisper load") as t:
        model = whisper.load_model("base")
    # → logs: ⏱ Whisper load: 3.21s

    # Step summary
    api.summary(success=50, fail=2, total=52)
"""

import time
import traceback
from contextlib import contextmanager
from typing import Callable

import requests as _requests

# ── ANSI-free emoji log levels ────────────────────────────────────────────────
_ICONS = {
    "info": "   ",
    "ok": "✅ ",
    "warn": "⚠️  ",
    "error": "❌ ",
    "debug": "🔍 ",
    "http": "📡 ",
    "send": "📤 ",
    "recv": "📥 ",
    "poll": "⏳ ",
    "sync": "⏱️  ",
    "gap": "⏸️  ",
    "key": "🔑 ",
    "file": "💾 ",
    "step": "▶  ",
    "done": "🏁 ",
    "progress": "📊 ",
}


class ApiLogger:
    """
    Wraps a log callback with structured logging helpers.
    All HTTP requests go through here → automatic request/response logging.
    """

    def __init__(self, log_fn: Callable[[str], None], prefix: str = ""):
        self._log = log_fn
        self._prefix = prefix
        self._session = _requests.Session()
        self._session.hooks["response"].append(self._on_response)
        self._last_request_t = 0.0

    # ── Basic log levels ─────────────────────────────────────────────────────

    def info(self, msg: str):
        self._log(f"{self._prefix}   {msg}")

    def ok(self, msg: str):
        self._log(f"{self._prefix}✅ {msg}")

    def warn(self, msg: str):
        self._log(f"{self._prefix}⚠️  {msg}")

    def error(self, msg: str):
        self._log(f"{self._prefix}❌ {msg}")

    def debug(self, msg: str):
        self._log(f"{self._prefix}🔍 {msg}")

    def sep(self, char="─", width=38):
        self._log(char * width)

    # ── HTTP wrapper ─────────────────────────────────────────────────────────

    def post(self, url: str, log_body: bool = True, **kwargs) -> _requests.Response:
        """POST with automatic request/response logging."""
        self._log_request("POST", url, kwargs, log_body)
        t = time.perf_counter()
        self._last_request_t = t
        try:
            resp = self._session.post(url, **kwargs)
            self._log_response(resp, time.perf_counter() - t)
            return resp
        except _requests.exceptions.Timeout:
            ms = int((time.perf_counter() - t) * 1000)
            self._log(f"{self._prefix}❌ POST TIMEOUT after {ms}ms — {url}")
            raise
        except _requests.exceptions.ConnectionError as e:
            self._log(f"{self._prefix}❌ POST CONNECTION ERROR — {url}")
            self._log(f"{self._prefix}   {e}")
            raise
        except Exception as e:
            self._log(f"{self._prefix}❌ POST ERROR — {url}: {e}")
            raise

    def get(self, url: str, log_body: bool = False, **kwargs) -> _requests.Response:
        """GET with automatic request/response logging."""
        self._log(f"{self._prefix}📡 GET {_short_url(url)}")
        t = time.perf_counter()
        self._last_request_t = t
        try:
            resp = self._session.get(url, **kwargs)
            ms = int((time.perf_counter() - t) * 1000)
            size = len(resp.content)
            self._log(
                f"{self._prefix}   → HTTP {resp.status_code} | "
                f"{ms}ms | {_fmt_size(size)}"
            )
            if resp.status_code >= 400:
                self._log(f"{self._prefix}   Body: {resp.text[:200]}")
            return resp
        except _requests.exceptions.Timeout:
            ms = int((time.perf_counter() - t) * 1000)
            self._log(f"{self._prefix}❌ GET TIMEOUT after {ms}ms")
            raise
        except Exception as e:
            self._log(f"{self._prefix}❌ GET ERROR: {e}")
            raise

    def _log_request(self, method: str, url: str, kwargs: dict, log_body: bool):
        headers = kwargs.get("headers", {})
        safe_headers = {
            k: (
                v[:8] + "..."
                if k.lower() in ("api_key", "apikey", "authorization", "x-api-key")
                and len(str(v)) > 8
                else v
            )
            for k, v in headers.items()
        }
        self._log(f"{self._prefix}📤 {method} {_short_url(url)}")
        for k, v in safe_headers.items():
            self._log(f"{self._prefix}   Header: {k}: {v}")
        if log_body:
            body = kwargs.get("data") or kwargs.get("json")
            if body:
                body_str = body if isinstance(body, str) else str(body)
                if isinstance(body, (bytes, bytearray)):
                    body_str = body.decode("utf-8", errors="replace")
                preview = body_str[:120] + ("…" if len(body_str) > 120 else "")
                self._log(f"{self._prefix}   Body: {preview} ({len(body_str)} chars)")

    def _log_response(self, resp: _requests.Response, elapsed: float):
        ms = int(elapsed * 1000)
        size = len(resp.content)
        icon = "✅" if resp.status_code < 400 else "❌"
        self._log(
            f"{self._prefix}{icon} HTTP {resp.status_code} | {ms}ms | {_fmt_size(size)}"
        )

        if resp.status_code == 200:
            ct = resp.headers.get("content-type", "")
            if "json" in ct:
                try:
                    j = resp.json()
                    # Log key fields for common APIs
                    for key in (
                        "error",
                        "error_code",
                        "message",
                        "request_id",
                        "async",
                    ):
                        if key in j:
                            self._log(f"{self._prefix}   JSON.{key}: {j[key]}")
                except Exception:
                    pass
        elif resp.status_code == 400:
            self._log(f"{self._prefix}   ⚠️  Bad Request: {resp.text[:300]}")
        elif resp.status_code == 401:
            self._log(f"{self._prefix}   ❌ 401 Unauthorized — API key không hợp lệ!")
        elif resp.status_code == 403:
            self._log(f"{self._prefix}   ❌ 403 Forbidden — Hết quota hoặc bị khóa!")
        elif resp.status_code == 404:
            self._log(f"{self._prefix}   ℹ️  404 Not Found (file chưa ready, sẽ retry)")
        elif resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After", "?")
            self._log(
                f"{self._prefix}   ⚠️  429 Rate Limited — Retry-After: {retry_after}s"
            )
        elif resp.status_code >= 500:
            self._log(
                f"{self._prefix}   ❌ Server Error {resp.status_code}: {resp.text[:200]}"
            )

    def _on_response(self, resp, *args, **kwargs):
        """requests hook — only used for tracking, main logging in get/post."""
        pass

    # ── Context managers ─────────────────────────────────────────────────────

    @contextmanager
    def segment(self, idx: int, total: int, text: str = ""):
        """Context manager for processing one segment."""
        preview = f'"{text[:60]}{"…" if len(text)>60 else ""}"' if text else ""
        self._log(f"{self._prefix}📤 Seg {idx}/{total} {preview}")
        t = time.perf_counter()
        ctx = _SegmentContext(self._log, self._prefix)
        try:
            yield ctx
            ms = int((time.perf_counter() - t) * 1000)
            if not ctx._finished:
                self._log(f"{self._prefix}   ✅ done in {ms}ms")
        except Exception as e:
            ms = int((time.perf_counter() - t) * 1000)
            self._log(f"{self._prefix}   ❌ FAILED after {ms}ms: {e}")
            # Log full traceback for debugging
            tb = traceback.format_exc()
            for line in tb.strip().splitlines()[-5:]:
                self._log(f"{self._prefix}      {line}")
            raise

    @contextmanager
    def timer(self, label: str):
        """Time a block and log duration."""
        self._log(f"{self._prefix}⏳ {label}…")
        t = time.perf_counter()
        try:
            yield
            elapsed = time.perf_counter() - t
            self._log(f"{self._prefix}⏱️  {label}: {_fmt_time(elapsed)}")
        except Exception as e:
            elapsed = time.perf_counter() - t
            self._log(
                f"{self._prefix}❌ {label} FAILED after {_fmt_time(elapsed)}: {e}"
            )
            raise

    @contextmanager
    def step_section(self, label: str):
        """Log a named section with separator."""
        self._log(f"{'─'*38}")
        self._log(f"▶  {label}")
        t = time.perf_counter()
        try:
            yield
            elapsed = time.perf_counter() - t
            self._log(f"✅ {label} complete in {_fmt_time(elapsed)}")
        except Exception as e:
            elapsed = time.perf_counter() - t
            self._log(f"❌ {label} FAILED after {_fmt_time(elapsed)}: {e}")
            raise

    # ── Progress / summary helpers ────────────────────────────────────────────

    def progress(self, current: int, total: int, extra: str = ""):
        pct = int(current / total * 100) if total else 0
        bar = ("█" * (pct // 5)).ljust(20)
        self._log(f"{self._prefix}📊 [{bar}] {current}/{total} ({pct}%) {extra}")

    def summary(self, success: int, fail: int, total: int, label: str = ""):
        tag = f" — {label}" if label else ""
        self._log(
            f"{self._prefix}🏁 Summary{tag}: "
            f"✅ {success} ok | ❌ {fail} failed | 📦 {total} total"
        )
        if fail > 0:
            self._log(
                f"{self._prefix}   ⚠️  {fail}/{total} segments có lỗi "
                f"→ silence trong audio"
            )

    def audio_sync(
        self, seg_idx: int, orig_ms: int, final_ms: int, seg_dur_ms: int, mode: str
    ):
        if orig_ms == final_ms:
            return
        ratio = orig_ms / max(seg_dur_ms, 1)
        self._log(
            f"{self._prefix}⏱️  Seg {seg_idx} sync [{mode}]: "
            f"{orig_ms}ms → {final_ms}ms "
            f"(target={seg_dur_ms}ms, ratio={ratio:.2f}x)"
        )

    def api_key_info(self, service: str, key: str):
        if key:
            masked = key[:8] + "..." + key[-4:] if len(key) > 12 else "***"
            self._log(f"{self._prefix}🔑 {service} key: {masked} ({len(key)} chars)")
        else:
            self._log(f"{self._prefix}❌ {service} key: NOT SET")

    def silence_gap(self, before_seg: int, gap_ms: int):
        if gap_ms > 200:
            self._log(
                f"{self._prefix}⏸️  Gap before seg {before_seg}: {gap_ms}ms silence"
            )


class _SegmentContext:
    def __init__(self, log_fn, prefix):
        self._log = log_fn
        self._prefix = prefix
        self._finished = False

    def info(self, msg: str):
        self._log(f"{self._prefix}   ℹ️  {msg}")

    def ok(self, msg: str):
        self._finished = True
        self._log(f"{self._prefix}   ✅ {msg}")

    def warn(self, msg: str):
        self._log(f"{self._prefix}   ⚠️  {msg}")

    def done(self, msg: str = ""):
        self._finished = True
        self._log(f"{self._prefix}   ✅ {msg}" if msg else f"{self._prefix}   ✅ done")


# ── Utility ───────────────────────────────────────────────────────────────────


def _short_url(url: str, max_len: int = 80) -> str:
    return url if len(url) <= max_len else url[:max_len] + "…"


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024**2:
        return f"{n/1024:.1f}KB"
    return f"{n/1024**2:.1f}MB"


def _fmt_time(s: float) -> str:
    if s < 1:
        return f"{int(s*1000)}ms"
    if s < 60:
        return f"{s:.2f}s"
    return f"{int(s//60)}m {s%60:.1f}s"
