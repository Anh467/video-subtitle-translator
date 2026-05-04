#!/usr/bin/env python3
"""CLI: lấy YouTube refresh token (localhost OAuth). In ra stdout dòng cuối = token."""

from __future__ import annotations

import argparse
import sys

# Cho phép chạy từ thư mục project: python scripts/youtube_oauth_refresh_token.py
if __name__ == "__main__" and __package__ is None:
    from pathlib import Path

    _root = Path(__file__).resolve().parents[1]
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))


def main() -> int:
    p = argparse.ArgumentParser(
        description="OAuth localhost — in refresh_token ra stdout (dòng cuối)."
    )
    p.add_argument("--client-id", required=True)
    p.add_argument("--client-secret", required=True)
    p.add_argument(
        "--redirect-uri",
        default="",
        help="Phải trùng ký tự với Authorized redirect URIs (Web client) trên GCP.",
    )
    p.add_argument(
        "--port",
        type=int,
        default=None,
        help="Viết tắt: http://127.0.0.1:PORT/ (không dùng cùng lúc với --redirect-uri).",
    )
    p.add_argument(
        "--login-hint",
        default="",
        help="Tuỳ chọn: gợi ý địa chỉ Gmail (vẫn có thể chọn tài khoản khác).",
    )
    args = p.parse_args()

    from core.publish.youtube_oauth_local import run_local_oauth

    ru_cli = (args.redirect_uri or "").strip()
    if ru_cli and args.port is not None:
        print("Chỉ dùng --redirect-uri hoặc --port, không dùng cả hai.", file=sys.stderr)
        return 1
    if args.port is not None:
        ru = f"http://127.0.0.1:{int(args.port)}/"
    else:
        ru = ru_cli

    try:
        tok = run_local_oauth(
            args.client_id,
            args.client_secret,
            redirect_uri=ru or None,
            login_hint=(args.login_hint or "").strip() or None,
        )
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1
    print(tok, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
