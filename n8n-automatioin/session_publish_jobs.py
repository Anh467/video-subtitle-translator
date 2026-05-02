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

# Values written into exported_publish_job.json "status".
EXPORT_STATUS_COMPLETED = "completed"
EXPORT_STATUS_FAILED = "failed"
EXPORT_STATUS_PARTIAL = "partial"
EXPORT_STATUS_PENDING = "pending_n8n"


def load_export_marker(path: Path | str) -> dict | None:
    p = Path(path)
    marker = (
        (p / EXPORTED_MARKER).resolve(strict=False)
        if p.name != EXPORTED_MARKER
        else p.resolve(strict=False)
    )
    if not marker.exists():
        return None
    try:
        raw = marker.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def export_marker_should_block_export(session_path: Path | str) -> tuple[bool, str]:
    """Whether the next export_scan should omit this folder (unless --include-exported).

    Skips mainly **completed** uploads (terminal success). **pending_n8n** is **not**
    skipped: exporter/Python may succeed while n8n fails—those sessions stay in the
    next batch.

    Legacy markers (**no ``status`` key**) still block to avoid surprise duplicate
    bursts from old installs.
    """
    sp = Path(session_path).resolve(strict=False)
    marker_path = sp / EXPORTED_MARKER
    if not marker_path.exists():
        return False, ""

    data = load_export_marker(sp)
    if data is None:
        return True, "export_marker_present_invalid_json_skipped"

    status = data.get("status")

    # Legacy markers (no status) — behave like before (do not re-export).
    if status is None:
        return True, "export_marker_legacy_no_status"

    status_s = str(status).strip()

    if status_s == EXPORT_STATUS_FAILED:
        return False, ""

    if status_s == EXPORT_STATUS_COMPLETED:
        return True, "export_marker_completed"

    if status_s == EXPORT_STATUS_PARTIAL:
        return True, "export_marker_partial_use_include_exported"

    if status_s != EXPORT_STATUS_PENDING:
        return True, f"export_marker_unknown_status_{status_s}"

    # Queued-to-n8n but not verified posted — include again unless user fixed marker.
    return False, ""


def refresh_export_marker_status(session_folder: str | Path) -> None:
    """Sync exported_publish_job.json status with posted_<platform>.json files."""
    session_path = Path(session_folder).resolve(strict=False)
    data = load_export_marker(session_path)
    if data is None:
        return

    targets = tuple(data.get("platforms") or ())
    if not targets:
        targets = ("youtube", "facebook")

    posted: list[str] = []
    for p in targets:
        pk = str(p).strip().lower()
        fname = POST_MARKERS.get(pk)
        if fname and (session_path / fname).exists():
            posted.append(pk)

    prev_status = data.get("status")

    # Do not downgrade an explicit terminal failure edited by operators.
    if prev_status == EXPORT_STATUS_FAILED:
        return

    if len(posted) >= len(targets):
        new_status = EXPORT_STATUS_COMPLETED
    elif not posted:
        new_status = EXPORT_STATUS_PENDING
    else:
        new_status = EXPORT_STATUS_PARTIAL

    if new_status == prev_status == EXPORT_STATUS_COMPLETED:
        return

    data["status"] = new_status
    now = datetime.now(timezone.utc).isoformat()
    data["marker_updated_at"] = now
    if new_status == EXPORT_STATUS_COMPLETED:
        data["completed_at"] = now

    marker_path = session_path / EXPORTED_MARKER
    marker_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

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


def _as_n8n_path(path: str | Path) -> str:
    """Absolute path with forward slashes for n8n Read/Write (Docker allow-list)."""
    if not path or not str(path).strip():
        return ""
    return Path(path).expanduser().resolve(strict=False).as_posix()


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
    scheduled_publish_unix: int
    ready: bool
    missing: list[str]
    youtube_posted: bool
    facebook_posted: bool
    youtube_remote_id: str
    facebook_remote_id: str
    exported: bool
    export_marker_status: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_post_marker_remote_id(session_path: Path, platform_key: str) -> str:
    """Best-effort remote_id from posted_<platform>.json (for n8n skip branches)."""
    fname = POST_MARKERS.get(platform_key)
    if not fname:
        return ""
    path = session_path / fname
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return ""
        return str(data.get("remote_id") or "").strip()
    except (json.JSONDecodeError, OSError, TypeError):
        return ""


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

    # Newest matching video anywhere under session (layouts that omit result/ prefixes).
    pool_all: list[Path] = []
    skip_dirs = frozenset({".git", "__pycache__", "node_modules"})
    for path in session_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(session_dir)
        except ValueError:
            continue
        if skip_dirs.intersection(rel.parts):
            continue
        if path.suffix.lower() in RESULT_VIDEO_SUFFIXES:
            pool_all.append(path)
    if pool_all:
        return str(max(pool_all, key=lambda p: p.stat().st_mtime))

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

    skip_dirs = frozenset({".git", "__pycache__", "node_modules"})
    fallback: list[Path] = []
    for path in session_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(session_dir)
        except ValueError:
            continue
        if skip_dirs.intersection(rel.parts):
            continue
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            fallback.append(path)
    if fallback:
        newest = max(
            {p.resolve(): p for p in fallback}.values(),
            key=lambda item: item.stat().st_mtime,
        )
        return str(newest)

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


_PUBLISH_FIELD_PLACEHOLDERS = frozenset(
    {"unknown", "n/a", "null", "none", "(unknown)", "[unknown]", "undefined"}
)


def _effective_publish_field(info: dict, meta: dict, key: str) -> str:
    """Prefer step7_publish_info.json, then session.json; skip obvious placeholders."""
    for block in (info, meta):
        raw = str(block.get(key, "") or "").strip()
        if not raw:
            continue
        low = raw.lower().strip()
        compact = "".join(low.split())
        if low in _PUBLISH_FIELD_PLACEHOLDERS or compact in _PUBLISH_FIELD_PLACEHOLDERS:
            continue
        return raw
    return ""


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
    title = _effective_publish_field(info, meta, "title")
    description = _effective_publish_field(info, meta, "description")
    hashtags = _normalize_hashtags(info.get("hashtags", meta.get("hashtags", "")))
    if not hashtags:
        hashtags = _extract_hashtags_from_text(title) or _extract_hashtags_from_text(
            description
        )

    published_at = _extract_published_at(meta, session_path.name)

    exp_marker_file = session_path / EXPORTED_MARKER
    exported = exp_marker_file.exists()
    if not exported:
        export_marker_status: str | None = None
    else:
        em_data = load_export_marker(session_path)
        if em_data is None:
            export_marker_status = "legacy_invalid_json"
        elif em_data.get("status") is None:
            export_marker_status = "legacy_no_status_field"
        else:
            export_marker_status = str(em_data.get("status"))

    missing = []
    if not video_path:
        missing.append("video")
    if not title:
        missing.append("title")
    if not description:
        missing.append("description")
    if not thumbnail_path:
        missing.append("thumbnail")

    yt_marker = session_path / POST_MARKERS["youtube"]
    fb_marker = session_path / POST_MARKERS["facebook"]
    youtube_posted_flag = yt_marker.exists()
    facebook_posted_flag = fb_marker.exists()

    session_resolved = session_path.resolve(strict=False)
    return PublishJob(
        session_folder=session_resolved.as_posix(),
        session_name=session_path.name,
        source_file=str(meta.get("source_file", "") or ""),
        publish_info_path=info_path.resolve(strict=False).as_posix()
        if info_path.exists()
        else "",
        session_meta_path=meta_path.resolve(strict=False).as_posix(),
        video_path=_as_n8n_path(video_path),
        thumbnail_path=_as_n8n_path(thumbnail_path),
        title=title,
        description=description,
        hashtags=hashtags,
        published_at=published_at,
        scheduled_at="",
        scheduled_publish_unix=0,
        ready=not missing,
        missing=missing,
        youtube_posted=youtube_posted_flag,
        facebook_posted=facebook_posted_flag,
        youtube_remote_id=_read_post_marker_remote_id(session_path, "youtube"),
        facebook_remote_id=_read_post_marker_remote_id(session_path, "facebook"),
        exported=exported,
        export_marker_status=export_marker_status,
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
        if not include_exported:
            block, block_detail = export_marker_should_block_export(item)
            if block:
                msg = f"[skip] export marker blocks rescan ({EXPORTED_MARKER}"
                if block_detail:
                    msg += f", reason={block_detail}"
                msg += ")"
                _debug(f"[skip] {item}: {msg}")
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
