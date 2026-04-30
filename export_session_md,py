"""
Export all session data in a base folder to a single Markdown file.

Usage:
    python automation/export_sessions_md.py <base_dir> [--out exported.md]

Output file (default: <base_dir>/exported.md) contains one section per session
with: title, description, hashtags, source file, final video, thumbnail,
publish status, creation date, and folder path.

n8n or any other tool can read this file as structured plain text.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

# ── helpers ──────────────────────────────────────────────────────────────────


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _find_video(session_dir: Path) -> str:
    result_dir = session_dir / "result"
    if result_dir.exists():
        candidates = []
        for pattern in ("result_output_*.*", "step6_output_*.*"):
            candidates.extend(result_dir.glob(pattern))
        candidates = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)
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
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return str(candidates[0])
    return ""


def _find_thumbnail(session_dir: Path) -> str:
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        p = session_dir / f"thumbnail{ext}"
        if p.exists():
            return str(p)
    return ""


def _fmt_size(path_str: str) -> str:
    try:
        size = os.path.getsize(path_str)
        if size >= 1_073_741_824:
            return f"{size / 1_073_741_824:.2f} GB"
        if size >= 1_048_576:
            return f"{size / 1_048_576:.2f} MB"
        return f"{size / 1024:.1f} KB"
    except Exception:
        return ""


def _done_steps(session_dir: Path) -> list[str]:
    checks = [
        ("step1_transcript.json", "① Transcribe"),
        ("step2_translated.json", "② Translate"),
        ("step3_output.mp4", "③ Burn Subtitles"),
        ("step4_vocals.mp3", "④ Separate Voice"),
        ("step5_tts.mp3", "⑤ TTS"),
        ("step6_output.mp4", "⑥ Add Voice"),
        ("step7_publish_info.json", "⑦ Publish Info"),
    ]
    done = []
    for filename, label in checks:
        # glob to handle wildcard extensions
        if label == "⑥ Add Voice":
            step6_found = bool(
                list(session_dir.glob("step6_output.*"))
                or list(session_dir.glob("result_output.*"))
                or list((session_dir / "result").glob("step6_output_*.*"))
                or list((session_dir / "result").glob("result_output_*.*"))
            )
            if step6_found:
                done.append(label)
        elif filename.endswith(".mp4"):
            if list(session_dir.glob(filename.replace(".mp4", ".*"))):
                done.append(label)
        elif (session_dir / filename).exists():
            done.append(label)
    return done


# ── per-session renderer ──────────────────────────────────────────────────────


def _render_session(session_dir: Path, index: int) -> str:
    meta = _load_json(session_dir / "session.json")
    info = _load_json(session_dir / "step7_publish_info.json")

    title = str(info.get("title", "") or meta.get("title", "") or "").strip()
    description = str(
        info.get("description", "") or meta.get("description", "") or ""
    ).strip()
    hashtags_raw = info.get("hashtags", [])
    if isinstance(hashtags_raw, list):
        hashtags = " ".join(str(h).strip() for h in hashtags_raw if str(h).strip())
    else:
        hashtags = str(hashtags_raw).strip()

    source_file = str(meta.get("source_file", "") or "").strip()
    created_raw = str(meta.get("created", "") or "").strip()
    folder_name = session_dir.name
    video_path = _find_video(session_dir)
    thumbnail = _find_thumbnail(session_dir)
    done = _done_steps(session_dir)

    yt_posted = (session_dir / "posted_youtube.json").exists()
    fb_posted = (session_dir / "posted_facebook.json").exists()

    display_title = title or folder_name

    lines: list[str] = []
    lines.append(f"## {index}. {display_title}")
    lines.append("")

    # core info table
    lines.append("| Field | Value |")
    lines.append("|---|---|")

    if title:
        lines.append(f"| **Title** | {title} |")
    lines.append(f"| **Folder** | `{folder_name}` |")
    if created_raw:
        lines.append(f"| **Created** | {created_raw} |")
    if source_file:
        size = _fmt_size(source_file)
        src_display = Path(source_file).name
        lines.append(
            f"| **Source** | {src_display}{(' (' + size + ')') if size else ''} |"
        )
    if video_path:
        size = _fmt_size(video_path)
        vid_display = Path(video_path).name
        lines.append(
            f"| **Final Video** | {vid_display}{(' (' + size + ')') if size else ''} |"
        )
    else:
        lines.append("| **Final Video** | _(not yet produced)_ |")
    if thumbnail:
        lines.append(f"| **Thumbnail** | {Path(thumbnail).name} |")
    if done:
        lines.append(f"| **Steps Done** | {', '.join(done)} |")
    lines.append(f"| **YouTube** | {'✅ posted' if yt_posted else '⬜ not posted'} |")
    lines.append(f"| **Facebook** | {'✅ posted' if fb_posted else '⬜ not posted'} |")
    lines.append("")

    if description:
        lines.append("### Description")
        lines.append("")
        lines.append(description)
        lines.append("")

    if hashtags:
        lines.append("### Hashtags")
        lines.append("")
        lines.append(hashtags)
        lines.append("")

    # full paths block (for n8n or scripts that need absolute paths)
    lines.append("### Paths")
    lines.append("")
    lines.append(f"- **Session folder:** `{session_dir}`")
    if source_file:
        lines.append(f"- **Source file:** `{source_file}`")
    if video_path:
        lines.append(f"- **Final video:** `{video_path}`")
    if thumbnail:
        lines.append(f"- **Thumbnail:** `{thumbnail}`")
    lines.append("")
    lines.append("---")
    lines.append("")

    return "\n".join(lines)


# ── main ─────────────────────────────────────────────────────────────────────


def export_markdown(base_dir: str | Path, out_path: str | Path | None = None) -> Path:
    root = Path(base_dir)
    if not root.exists():
        raise FileNotFoundError(f"Base dir not found: {root}")

    if out_path is None:
        out_path = root / "exported.md"
    out_path = Path(out_path)

    session_dirs = sorted(
        [d for d in root.iterdir() if d.is_dir() and (d / "session.json").exists()],
        key=lambda d: d.name.lower(),
    )

    header_lines = [
        "# Session Export",
        "",
        f"> Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"> Base folder: `{root}`  ",
        f"> Total sessions: **{len(session_dirs)}**",
        "",
        "---",
        "",
    ]

    body_parts = [_render_session(d, i + 1) for i, d in enumerate(session_dirs)]

    content = "\n".join(header_lines) + "\n".join(body_parts)
    out_path.write_text(content, encoding="utf-8")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Export session data to a Markdown file."
    )
    parser.add_argument("base_dir", help="Path to the base workspace folder")
    parser.add_argument(
        "--out",
        default=None,
        help="Output .md file path (default: <base_dir>/exported.md)",
    )
    args = parser.parse_args()

    result = export_markdown(args.base_dir, args.out)
    print(f"✅ Exported {result}")
