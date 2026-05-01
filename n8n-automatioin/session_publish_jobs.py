from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

POST_MARKERS = {
    "youtube": "posted_youtube.json",
    "facebook": "posted_facebook.json",
}

EXPORTED_MARKER = "exported_publish_job.json"

DEFAULT_THUMBNAIL_PATTERNS = (
    "thumbnail.*",
    "thumb.*",
    "*thumbnail*.*",
    "*thumb*.*",
    "cover.*",
    "poster.*",
)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

RESULT_VIDEO_SUFFIXES = frozenset(
    {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".ts", ".m2ts"}
)


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
    exported: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_video(session_dir: Path) -> str:
    result_dir = session_dir / "result"
    if result_dir.exists():
        pool: list[Path] = []
        for pattern in ("result_output_*.*", "step6_output_*.*"):
            pool.extend(result_dir.rglob(pattern))
        for path in result_dir.rglob("*"):
            if path.is_file() and path.suffix.lower() in RESULT_VIDEO_SUFFIXES:
                pool.append(path)
        uniq = list({p.resolve(): p for p in pool}.values())
        if uniq:
            newest = max(uniq, key=lambda p: p.stat().st_mtime)
            return str(newest)

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


def _find_thumbnail(
    session_dir: Path,
    thumbnail_patterns: tuple[str, ...] | None = None,
) -> str:
    patterns = tuple(thumbnail_patterns or DEFAULT_THUMBNAIL_PATTERNS)
    candidates: list[Path] = []

    search_dirs = [session_dir]
    result_dir = session_dir / "result"
    if result_dir.exists():
        search_dirs.append(result_dir)

    for folder in search_dirs:
        for pattern in patterns:
            hits = set(folder.glob(pattern)) | set(folder.rglob(pattern))
            for candidate in hits:
                if not candidate.is_file():
                    continue
                if candidate.suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                candidates.append(candidate)

    # Keep newest candidate if multiple files match configured patterns.
    candidates = sorted(
        {p.resolve(): p for p in candidates}.values(),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return str(candidates[0])
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


def build_publish_job(
    session_dir: str | Path,
    thumbnail_patterns: tuple[str, ...] | None = None,
) -> PublishJob | None:
    session_path = Path(session_dir)
    meta_path = session_path / "session.json"
    info_path = session_path / "step7_publish_info.json"

    if not meta_path.exists():
        return None

    meta = _load_json(meta_path)
    info = _load_json(info_path) if info_path.exists() else {}

    video_path = _find_video(session_path)
    thumbnail_path = _find_thumbnail(
        session_path,
        thumbnail_patterns=thumbnail_patterns,
    )
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
        exported=(session_path / EXPORTED_MARKER).exists(),
    )


def scan_publish_jobs(
    base_dir: str | Path,
    platforms: tuple[str, ...] = ("youtube", "facebook"),
    include_incomplete: bool = False,
    include_posted: bool = False,
    include_exported: bool = False,
    recursive: bool = True,
    debug: bool = False,
    thumbnail_patterns: tuple[str, ...] | None = None,
    audit: list[tuple[str, str]] | None = None,
) -> list[PublishJob]:
    root = Path(base_dir)
    if not root.exists():
        raise FileNotFoundError(f"Base dir not found: {root}")

    def _debug(message: str) -> None:
        if debug:
            print(message, file=sys.stderr)

    if recursive:
        session_dirs = sorted(
            {path.parent for path in root.rglob("session.json")},
            key=lambda path: str(path).lower(),
        )
        _debug(f"[debug] recursive scan found {len(session_dirs)} session folders")
    else:
        session_dirs = sorted(
            [path for path in root.iterdir() if path.is_dir()],
            key=lambda path: path.name.lower(),
        )
        _debug(f"[debug] non-recursive scan found {len(session_dirs)} direct folders")

    def _audit(row: tuple[str, str]) -> None:
        if audit is not None:
            audit.append(row)

    jobs: list[PublishJob] = []
    for item in session_dirs:
        folder = str(item)
        job = build_publish_job(item, thumbnail_patterns=thumbnail_patterns)
        if job is None:
            msg = "[skip] missing session.json"
            _debug(f"[skip] {item}: missing session.json")
            _audit((folder, msg))
            continue
        if not include_incomplete and not job.ready:
            detail = f"incomplete: {', '.join(job.missing)}"
            _debug(f"[skip] {item}: incomplete metadata/assets ({', '.join(job.missing)})")
            _audit((folder, f"[skip] {detail}"))
            continue
        if not include_posted and platforms:
            already_posted = True
            posted_platforms: list[str] = []
            for platform in platforms:
                if platform == "youtube" and job.youtube_posted:
                    posted_platforms.append("youtube")
                if platform == "youtube" and not job.youtube_posted:
                    already_posted = False
                if platform == "facebook" and job.facebook_posted:
                    posted_platforms.append("facebook")
                if platform == "facebook" and not job.facebook_posted:
                    already_posted = False
            if already_posted:
                _debug(
                    f"[skip] {item}: already posted for selected platforms "
                    f"({', '.join(posted_platforms)})"
                )
                _audit(
                    (
                        folder,
                        f"[skip] already posted ({', '.join(posted_platforms)})",
                    )
                )
                continue
        if not include_exported and job.exported:
            msg = f"[skip] already exported ({EXPORTED_MARKER})"
            _debug(f"[skip] {item}: already exported ({EXPORTED_MARKER})")
            _audit((folder, msg))
            continue
        _debug(f"[keep] {item}: queued for export")
        _audit((folder, "[included]"))
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


def write_export_marker(
    session_folder: str | Path,
    payload: dict,
) -> str:
    session_path = Path(session_folder)
    if not session_path.exists():
        raise FileNotFoundError(f"Session folder not found: {session_path}")

    marker_path = session_path / EXPORTED_MARKER
    marker_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(marker_path)
