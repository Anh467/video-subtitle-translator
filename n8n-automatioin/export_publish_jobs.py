from __future__ import annotations

import argparse
import json
from pathlib import Path

from automation.session_publish_jobs import scan_publish_jobs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan session folders and export publish jobs as JSON."
    )
    parser.add_argument("base_dir", help="Folder containing all session subfolders")
    parser.add_argument(
        "--platform",
        action="append",
        choices=("youtube", "facebook"),
        dest="platforms",
        help="Platforms to consider when skipping already-posted sessions",
    )
    parser.add_argument(
        "--include-incomplete",
        action="store_true",
        help="Include sessions missing title/description/video/thumbnail",
    )
    parser.add_argument(
        "--include-posted",
        action="store_true",
        help="Include sessions already marked as posted for selected platforms",
    )
    parser.add_argument(
        "--json-out",
        help="Optional path to save the JSON payload in addition to stdout",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print output JSON",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    platforms = tuple(args.platforms or ["youtube", "facebook"])
    jobs = scan_publish_jobs(
        base_dir=args.base_dir,
        platforms=platforms,
        include_incomplete=args.include_incomplete,
        include_posted=args.include_posted,
    )

    payload = {
        "base_dir": str(Path(args.base_dir)),
        "platforms": list(platforms),
        "count": len(jobs),
        "jobs": [job.to_dict() for job in jobs],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None)
    if args.json_out:
        Path(args.json_out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
