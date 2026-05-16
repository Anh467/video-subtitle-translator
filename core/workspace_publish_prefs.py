"""Workspace-level defaults for multi-session publish (schedule hint, batch cursor)."""

from __future__ import annotations

import json
from pathlib import Path

PREFS_FILENAME = ".subsync_workspace_publish.json"


def prefs_path(base_dir: str) -> Path:
    return Path(base_dir) / PREFS_FILENAME


def load_workspace_publish_prefs(base_dir: str) -> dict:
    """
    Return merged prefs dict:
      last_max_scheduled_unix: int — max scheduled_unix seen when saving plans (local tz timestamps)
      batch_cursor: int — index into published_at-sorted selection for «continue N» mode
      limit_sessions_enabled: bool — dialog default
      limit_sessions_count: int — default batch size
      scope_mode: 'only_missing_success' | 'all'
      schedule_mode: 'scheduled' | 'immediate'
      interval_hours: int
    """
    empty = {
        "last_max_scheduled_unix": 0,
        "batch_cursor": 0,
        "limit_sessions_enabled": True,
        "limit_sessions_count": 10,
        "scope_mode": "only_missing_success",
        "schedule_mode": "scheduled",
        "interval_hours": 24,
    }
    if not (base_dir or "").strip():
        return dict(empty)
    p = prefs_path(base_dir)
    if not p.is_file():
        return dict(empty)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return dict(empty)
    if not isinstance(raw, dict):
        return dict(empty)
    out = dict(empty)
    for k in empty:
        if k in raw:
            out[k] = raw[k]
    out["last_max_scheduled_unix"] = int(out["last_max_scheduled_unix"] or 0)
    out["batch_cursor"] = max(0, int(out["batch_cursor"] or 0))
    out["limit_sessions_count"] = max(1, min(500, int(out["limit_sessions_count"] or 10)))
    out["interval_hours"] = max(1, min(168, int(out["interval_hours"] or 24)))
    sm = str(out.get("schedule_mode") or "scheduled")
    out["schedule_mode"] = sm if sm in ("scheduled", "immediate") else "scheduled"
    sc = str(out.get("scope_mode") or "only_missing_success")
    out["scope_mode"] = sc if sc in ("only_missing_success", "all") else "only_missing_success"
    out["limit_sessions_enabled"] = bool(out.get("limit_sessions_enabled", True))
    return out


def save_workspace_publish_prefs(base_dir: str, data: dict) -> None:
    if not (base_dir or "").strip():
        return
    cur = load_workspace_publish_prefs(base_dir)
    cur.update(data)
    cur["last_max_scheduled_unix"] = int(cur.get("last_max_scheduled_unix") or 0)
    cur["batch_cursor"] = max(0, int(cur.get("batch_cursor") or 0))
    cur["limit_sessions_count"] = max(1, min(500, int(cur.get("limit_sessions_count") or 10)))
    cur["interval_hours"] = max(1, min(168, int(cur.get("interval_hours") or 24)))
    sm = str(cur.get("schedule_mode") or "scheduled")
    cur["schedule_mode"] = sm if sm in ("scheduled", "immediate") else "scheduled"
    sc = str(cur.get("scope_mode") or "only_missing_success")
    cur["scope_mode"] = sc if sc in ("only_missing_success", "all") else "only_missing_success"
    out = {
        "version": 1,
        **cur,
    }
    prefs_path(base_dir).write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
