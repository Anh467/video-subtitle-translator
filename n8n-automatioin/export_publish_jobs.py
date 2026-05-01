from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from session_publish_jobs import (
    EXPORT_STATUS_PENDING,
    scan_publish_jobs,
    write_export_marker,
)


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
        "--include-exported",
        action="store_true",
        help="Include sessions already marked as exported by this script",
    )
    parser.add_argument(
        "--non-recursive",
        action="store_true",
        help="Scan only direct child folders of base_dir",
    )
    parser.add_argument(
        "--thumbnail-pattern",
        action="append",
        dest="thumbnail_patterns",
        help=(
            "Glob pattern used to find thumbnail files. " "Can be passed multiple times"
        ),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print debug reasons for skipped/kept session folders to stderr",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help=(
            "After scan, print one line per candidate folder on stderr "
            "(folder path + included or skip reason)"
        ),
    )
    parser.add_argument(
        "--no-mark-exported",
        action="store_true",
        help="Do not write per-session exported marker file",
    )
    parser.add_argument(
        "--info-file",
        default="export_publish_jobs_info.json",
        help="Filename for run summary info under base_dir",
    )
    parser.add_argument(
        "--json-out",
        help="Optional path to save the JSON payload in addition to stdout",
    )
    parser.add_argument(
        "--schedule-interval-hours",
        type=float,
        default=4.0,
        help="Interval in hours between scheduled publish jobs",
    )
    parser.add_argument(
        "--schedule-start",
        default="",
        help="Optional ISO-8601 start datetime for the first scheduled post",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print output JSON",
    )
    return parser.parse_args()


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def main() -> int:
    args = parse_args()
    platforms = tuple(args.platforms or ["youtube", "facebook"])
    audit_rows: list[tuple[str, str]] | None = [] if args.audit else None
    jobs = scan_publish_jobs(
        base_dir=args.base_dir,
        platforms=platforms,
        include_incomplete=args.include_incomplete,
        include_posted=args.include_posted,
        include_exported=args.include_exported,
        recursive=not args.non_recursive,
        debug=args.debug,
        thumbnail_patterns=tuple(args.thumbnail_patterns or []),
        audit=audit_rows,
    )
    if audit_rows is not None:
        for folder, outcome in audit_rows:
            print(f"{outcome}\t{folder}", file=sys.stderr)

    schedule_start = None
    if args.schedule_start:
        schedule_start = _parse_datetime(args.schedule_start)
    if schedule_start is None and jobs:
        schedule_start = _parse_datetime(jobs[0].published_at)
    if schedule_start is None:
        schedule_start = datetime.now(timezone.utc)
    if schedule_start.tzinfo is None:
        schedule_start = schedule_start.replace(tzinfo=timezone.utc)

    min_start = datetime.now(timezone.utc) + timedelta(minutes=11)
    if schedule_start < min_start:
        schedule_start = min_start

    interval = timedelta(hours=args.schedule_interval_hours)
    for index, job in enumerate(jobs):
        slot = schedule_start + interval * index
        if slot.tzinfo is None:
            slot = slot.replace(tzinfo=timezone.utc)
        job.scheduled_at = slot.isoformat()
        job.scheduled_publish_unix = int(slot.timestamp())

    marker_paths: list[str] = []
    if not args.no_mark_exported:
        exported_at = datetime.now(timezone.utc).isoformat()
        for job in jobs:
            marker_payload = {
                "status": EXPORT_STATUS_PENDING,
                "exported_at": exported_at,
                "scheduled_at": job.scheduled_at,
                "platforms": list(platforms),
                "session_name": job.session_name,
                "session_folder": job.session_folder,
            }
            marker_paths.append(write_export_marker(job.session_folder, marker_payload))

    payload = {
        "base_dir": str(Path(args.base_dir)),
        "platforms": list(platforms),
        "count": len(jobs),
        "schedule_interval_hours": args.schedule_interval_hours,
        "schedule_start": schedule_start.isoformat(),
        "mark_exported": not args.no_mark_exported,
        "jobs": [job.to_dict() for job in jobs],
    }

    info_payload = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "base_dir": str(Path(args.base_dir)),
        "platforms": list(platforms),
        "count": len(jobs),
        "mark_exported": not args.no_mark_exported,
        "marker_paths": marker_paths,
        "jobs": [
            {
                "session_name": job.session_name,
                "session_folder": job.session_folder,
                "scheduled_at": job.scheduled_at,
                "exported": job.exported,
                "export_marker_status": job.export_marker_status,
            }
            for job in jobs
        ],
    }

    info_path = Path(args.base_dir) / args.info_file
    info_path.write_text(
        json.dumps(info_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    payload["info_file"] = str(info_path)
    text = json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None)
    if args.json_out:
        Path(args.json_out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
