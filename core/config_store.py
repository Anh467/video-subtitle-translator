"""
Workspace-scoped step config persistence.

Saves and loads UI config for all pipeline steps per base folder.

File: <base_dir>/.subsync_step_configs.json
Format: { "step_id": { ...config dict... }, ... }

Usage:
    from core.config_store import save_step_configs, load_step_configs
    save_step_configs(base_dir, steps)
    load_step_configs(base_dir, steps)
"""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_FILE = ".subsync_step_configs.json"
META_KEY = "__meta__"

# Keys whose values should NOT be persisted (sensitive / session-specific)
_SKIP_KEYS = {"api_key", "tts_path", "thumb_bg_path"}


def _sanitize(cfg: dict) -> dict:
    """Remove keys we should never persist (API keys, transient paths)."""
    out: dict = {}
    for k, v in cfg.items():
        if k in _SKIP_KEYS:
            continue
        if k == "delogo" and isinstance(v, dict):
            v = {a: b for a, b in v.items() if a != "enable_expr"}
        out[k] = v
    return out


def _collect_step_enabled(cards: list | None) -> dict[str, bool]:
    enabled: dict[str, bool] = {}
    if not cards:
        return enabled
    for card in cards:
        step = getattr(card, "step", None)
        step_id = getattr(step, "STEP_ID", "") if step else ""
        if not step_id:
            continue
        is_enabled = getattr(card, "is_enabled", None)
        if callable(is_enabled):
            enabled[step_id] = bool(is_enabled())
    return enabled


def _apply_step_enabled(cards: list | None, enabled_map: dict) -> None:
    if not cards or not enabled_map:
        return
    for card in cards:
        step = getattr(card, "step", None)
        step_id = getattr(step, "STEP_ID", "") if step else ""
        if not step_id or step_id not in enabled_map:
            continue
        chk = getattr(card, "_enable_chk", None)
        if chk is None:
            continue
        chk.blockSignals(True)
        chk.setChecked(bool(enabled_map[step_id]))
        chk.blockSignals(False)
        toggle = getattr(card, "_on_toggle", None)
        if callable(toggle):
            toggle()


def save_step_configs(
    base_dir: str | Path, steps: list, cards: list | None = None
) -> None:
    """Collect current config from each step and write to <base_dir>/.subsync_step_configs.json"""
    root = Path(base_dir)
    if not root.exists():
        return

    data: dict = {}
    for step in steps:
        step_id = getattr(step, "STEP_ID", "")
        if not step_id:
            continue
        try:
            cfg = step.collect_config()
            data[step_id] = _sanitize(cfg)
        except Exception:
            pass  # widget might not be built yet

    enabled = _collect_step_enabled(cards)
    if enabled:
        data[META_KEY] = {"step_enabled": enabled}

    out_path = root / CONFIG_FILE
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_step_configs(
    base_dir: str | Path, steps: list, cards: list | None = None
) -> bool:
    """Read saved configs and call apply_config on each step. Returns True if file existed."""
    root = Path(base_dir)
    cfg_path = root / CONFIG_FILE
    if not cfg_path.exists():
        return False

    try:
        data: dict = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return False

    meta = data.pop(META_KEY, None) or {}
    enabled_map = meta.get("step_enabled") if isinstance(meta, dict) else {}
    _apply_step_enabled(cards, enabled_map)

    for step in steps:
        step_id = getattr(step, "STEP_ID", "")
        if step_id and step_id in data:
            try:
                step.apply_config(data[step_id])
            except Exception:
                pass

    return True
