from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from automation.session_publish_jobs import write_publish_marker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write a posted_youtube.json or posted_facebook.json marker."
    )
    parser.add_argument("session_folder", help="Path to one session folder")
    parser.add_argument(
        "--platform",
        required=True,
        choices=("youtube", "facebook"),
        help="Target platform",
    )
    parser.add_argument("--remote-id", default="", help="Remote video/post id")
    parser.add_argument("--url", default="", help="Published URL")
    parser.add_argument(
        "--scheduled-at",
        default="",
        help="Platform schedule time in ISO-8601 or platform native format",
    )
    parser.add_argument(
        "--status",
        default="scheduled",
        help="Marker status, e.g. scheduled/uploaded/published",
    )
    parser.add_argument(
        "--extra-json",
        default="",
        help="Optional raw JSON string merged into the marker payload",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = {
        "platform": args.platform,
        "status": args.status,
        "remote_id": args.remote_id,
        "url": args.url,
        "scheduled_at": args.scheduled_at,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if args.extra_json:
        payload.update(json.loads(args.extra_json))
    marker_path = write_publish_marker(args.session_folder, args.platform, payload)
    print(
        json.dumps(
            {"ok": True, "marker_path": marker_path, "payload": payload},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
