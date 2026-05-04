"""Session folder discovery — keeps ``Session.list_sessions()`` implementation out of ``session.py``."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


_PLATFORM_LABELS = {
    "facebook": "Facebook",
    "youtube": "YouTube",
    "tiktok": "TikTok",
}


def summarize_publish_plan(meta: dict) -> dict:
    """
    Đọc publish_plan trong session.json — phục vụ UI Multi-Session.
    Trả về:
      publish_done_platforms: tên nền tảng đã có ít nhất một job status=done
      publish_incomplete_hint: chuỗi ngắn cho tooltip (platform còn job nhưng chưa done)
    """
    plan = meta.get("publish_plan")
    if not isinstance(plan, list) or not plan:
        return {"publish_done_platforms": [], "publish_incomplete_hint": ""}

    by_pl: dict[str, set[str]] = {}
    for j in plan:
        if not isinstance(j, dict):
            continue
        pl = str(j.get("platform") or "").strip().lower()
        if not pl:
            continue
        st = str(j.get("status") or "").strip().lower() or "pending"
        by_pl.setdefault(pl, set()).add(st)

    done_names: list[str] = []
    incomplete_bits: list[str] = []
    for pl in ("facebook", "youtube", "tiktok"):
        sts = by_pl.get(pl)
        if not sts:
            continue
        label = _PLATFORM_LABELS.get(pl, pl)
        if "done" in sts:
            done_names.append(label)
        else:
            incomplete_bits.append(f"{label}: {','.join(sorted(sts))}")

    hint = " · ".join(incomplete_bits[:4])
    if len(incomplete_bits) > 4:
        hint += "…"
    return {
        "publish_done_platforms": done_names,
        "publish_incomplete_hint": hint,
    }


def published_at_epoch_seconds(value: str | None) -> float:
    """
    Parse ``session.json`` ``published_at`` for stable sorting.

    Naive ISO strings use :func:`datetime.timestamp` (local timezone).
    Missing or unparseable values return ``+inf`` so ascending sorts put them last.
    """
    s = (value or "").strip()
    if not s:
        return float("inf")
    try:
        s2 = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        return float(dt.timestamp())
    except ValueError:
        pass
    try:
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
            return float(dt.timestamp())
    except ValueError:
        pass
    return float("inf")


def _dir_tree_size_bytes(path: Path) -> int:
    """Recursive byte size via ``os.walk`` (fewer temporary ``Path`` objects than ``rglob``)."""
    total = 0
    root = os.fspath(path)
    try:
        for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    except OSError:
        pass
    return total


def list_sessions(base_dir: str) -> list[dict]:
    """List all sessions in base_dir, sorted by folder name (case-insensitive)."""
    base = Path(base_dir)
    if not base.exists():
        return []
    sessions = []
    for d in sorted(base.iterdir(), key=lambda x: x.name.lower()):
        if not d.is_dir():
            continue
        meta_path = d / "session.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            done = []
            if (d / "step1_transcript.json").exists():
                done.append("①")
            if (d / "step2_translated.json").exists():
                done.append("②")
            if any(d.glob("step3_output.*")):
                done.append("③")
            if (d / "step4_vocals.mp3").exists():
                done.append("④")
            if (d / "step5_tts.mp3").exists():
                done.append("⑤")
            result_dir = d / "result"
            has_step6 = (
                any(d.glob("step6_output.*"))
                or any(d.glob("result_output.*"))
                or (result_dir.exists() and any(result_dir.glob("step6_output_*.*")))
                or (result_dir.exists() and any(result_dir.glob("result_output_*.*")))
            )
            if has_step6 or any(d.glob("step5_output.*")):
                done.append("⑥")
            if (d / "step7_publish_info.json").exists():
                done.append("⑦")

            size = _dir_tree_size_bytes(d)
            thumb = ""
            for ext in (".jpg", ".jpeg", ".png", ".webp"):
                tp = d / f"thumbnail{ext}"
                if tp.exists():
                    thumb = str(tp)
                    break
            pub = summarize_publish_plan(meta)
            sessions.append(
                {
                    "folder": str(d),
                    "name": d.name,
                    "source_file": meta.get("source_file", ""),
                    "title": meta.get("title", ""),
                    "description": meta.get("description", ""),
                    "published_at": meta.get("published_at", ""),
                    "thumb_background": meta.get("thumb_background", ""),
                    "thumbnail": thumb,
                    "done_steps": done,
                    "size_mb": round(size / 1024 / 1024, 1),
                    "mtime": d.stat().st_mtime,
                    "publish_done_platforms": pub["publish_done_platforms"],
                    "publish_incomplete_hint": pub["publish_incomplete_hint"],
                }
            )
        except Exception:
            continue
    return sessions
