from __future__ import annotations

"""Append one JSON line (JSONL) for n8n workflows. Safe on Windows paths."""

import argparse
import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Append a UTF-8 JSONL log row.")
    parser.add_argument("log_file", help="Log file path")
    parser.add_argument("event", help="Short event name, e.g. youtube_ok")
    parser.add_argument("--level", default="INFO", help="DEBUG|INFO|WARN|ERROR")
    parser.add_argument("--session", default="", help="session_folder for context")
    parser.add_argument("--message", default="", help="Free-text detail")
    parser.add_argument(
        "--payload", default="", help="Merge this JSON string into row['data']"
    )
    parser.add_argument(
        "--payload-b64",
        default="",
        help="Base64(utf-8 JSON object) merged into row['data']",
    )
    args, unknown = parser.parse_known_args()
    if unknown:
        extra = " ".join(unknown).strip()
        if extra:
            args.message = (args.message + " " + extra).strip()

    row: dict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": args.level,
        "event": args.event,
        "session_folder": args.session or None,
        "message": args.message or None,
    }
    if args.payload_b64.strip():
        raw = base64.b64decode(args.payload_b64).decode("utf-8")
        row["data"] = json.loads(raw)
    elif args.payload.strip():
        row["data"] = json.loads(args.payload)
    path = Path(args.log_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        raise SystemExit(1)
