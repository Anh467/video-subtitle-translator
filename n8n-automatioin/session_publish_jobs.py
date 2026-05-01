from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

POST_MARKERS = {
    "youtube": "posted_youtube.json",
    "facebook": "posted_facebook.json",
}


@dataclass
class PublishJob:
    session_folder: str
    session_name: str
    source_file: str
    publish_info_path: str
    session_meta_path: str
    video_path: str
    thumbnail_path: str
    title: str
    description: str
    hashtags: list[str]
    published_at: str
    scheduled_at: str
    ready: bool
    missing: list[str]
    youtube_posted: bool
    facebook_posted: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_video(session_dir: Path) -> str:
    result_dir = session_dir / "result"
    if result_dir.exists():
        candidates = []
        for pattern in ("result_output_*.*", "step6_output_*.*"):
            candidates.extend(result_dir.glob(pattern))
        candidates = sorted(
            candidates, key=lambda item: item.stat().st_mtime, reverse=True
        )
        if candidates:
            return str(candidates[0])

    for pattern in (
        "result_output.*",
        "step6_output.*",
        "step5_output.*",
        "step3_output.*",
    ):
        candidates = sorted(
            session_dir.glob(pattern),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return str(candidates[0])
    return ""


def _find_thumbnail(session_dir: Path) -> str:
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        candidate = session_dir / f"thumbnail{ext}"
        if candidate.exists():
            return str(candidate)
    return ""


def _normalize_hashtags(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part for part in value.split() if part.strip()]
    return []


def _extract_published_at(meta: dict, folder_name: str) -> str:
    published_at = str(meta.get("published_at", "") or "").strip()
    if published_at:
        return published_at

    for key in (
        "publishedAt",
        "published_at",
        "publish_date",
        "uploadedAt",
        "upload_date",
        "uploadDate",
        "published",
        "create_time",
    ):
        if key in meta and meta[key] not in (None, "", 0):
            value = meta[key]
            if key == "create_time" and isinstance(value, (int, float)):
                try:
                    return (
                        datetime.utcfromtimestamp(value)
                        .replace(tzinfo=timezone.utc)
                        .isoformat()
                    )
                except Exception:
                    pass
            candidate = str(value).strip()
            if candidate:
                return candidate

    match = re.match(r"^(\d{4}[-_]\d{2}[-_]\d{2})", folder_name)
    if match:
        date_text = match.group(1).replace("_", "-")
        try:
            return datetime.fromisoformat(date_text).date().isoformat()
        except ValueError:
            return date_text

    return ""


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        try:
            dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _extract_hashtags_from_text(text: str) -> list[str]:
    if not isinstance(text, str):
        return []
    return [part for part in text.split() if part.startswith("#")]


def build_publish_job(session_dir: str | Path) -> PublishJob | None:
    session_path = Path(session_dir)
    meta_path = session_path / "session.json"
    info_path = session_path / "step7_publish_info.json"

    if not meta_path.exists():
        return None

    meta = _load_json(meta_path)
    info = _load_json(info_path) if info_path.exists() else {}

    video_path = _find_video(session_path)
    thumbnail_path = _find_thumbnail(session_path)
    title = str(info.get("title", "") or meta.get("title", "") or "").strip()
    description = str(
        info.get("description", "") or meta.get("description", "") or ""
    ).strip()
    hashtags = _normalize_hashtags(info.get("hashtags", meta.get("hashtags", "")))
    if not hashtags:
        hashtags = _extract_hashtags_from_text(title) or _extract_hashtags_from_text(
            description
        )

    published_at = _extract_published_at(meta, session_path.name)

    missing = []
    if not video_path:
        missing.append("video")
    if not title:
        missing.append("title")
    if not description:
        missing.append("description")
    if not thumbnail_path:
        missing.append("thumbnail")

    return PublishJob(
        session_folder=str(session_path),
        session_name=session_path.name,
        source_file=str(meta.get("source_file", "") or ""),
        publish_info_path=str(info_path) if info_path.exists() else "",
        session_meta_path=str(meta_path),
        video_path=video_path,
        thumbnail_path=thumbnail_path,
        title=title,
        description=description,
        hashtags=hashtags,
        published_at=published_at,
        scheduled_at="",
        ready=not missing,
        missing=missing,
        youtube_posted=(session_path / POST_MARKERS["youtube"]).exists(),
        facebook_posted=(session_path / POST_MARKERS["facebook"]).exists(),
    )


def scan_publish_jobs(
    base_dir: str | Path,
    platforms: tuple[str, ...] = ("youtube", "facebook"),
    include_incomplete: bool = False,
    include_posted: bool = False,
) -> list[PublishJob]:
    root = Path(base_dir)
    if not root.exists():
        raise FileNotFoundError(f"Base dir not found: {root}")

    jobs: list[PublishJob] = []
    for item in sorted(root.iterdir(), key=lambda path: path.name.lower()):
        if not item.is_dir():
            continue
        job = build_publish_job(item)
        if job is None:
            continue
        if not include_incomplete and not job.ready:
            continue
        if not include_posted:
            already_posted = True
            for platform in platforms:
                if platform == "youtube" and not job.youtube_posted:
                    already_posted = False
                if platform == "facebook" and not job.facebook_posted:
                    already_posted = False
            if already_posted:
                continue
        jobs.append(job)

    def _job_sort_key(job: PublishJob):
        dt = _parse_datetime(job.published_at)
        return (
            dt if dt is not None else datetime.max.replace(tzinfo=timezone.utc),
            job.session_name.lower(),
        )

    jobs.sort(key=_job_sort_key)
    return jobs


def write_publish_marker(
    session_folder: str | Path,
    platform: str,
    payload: dict,
) -> str:
    if platform not in POST_MARKERS:
        raise ValueError(f"Unsupported platform: {platform}")
    session_path = Path(session_folder)
    if not session_path.exists():
        raise FileNotFoundError(f"Session folder not found: {session_path}")

    marker_path = session_path / POST_MARKERS[platform]
    marker_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(marker_path)
