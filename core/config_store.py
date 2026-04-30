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

# Keys whose values should NOT be persisted (sensitive / session-specific)
_SKIP_KEYS = {"api_key", "tts_path", "thumb_bg_path"}


def _sanitize(cfg: dict) -> dict:
    """Remove keys we should never persist (API keys, transient paths)."""
    return {k: v for k, v in cfg.items() if k not in _SKIP_KEYS}


def save_step_configs(base_dir: str | Path, steps: list) -> None:
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

    out_path = root / CONFIG_FILE
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_step_configs(base_dir: str | Path, steps: list) -> bool:
    """Read saved configs and call apply_config on each step. Returns True if file existed."""
    root = Path(base_dir)
    cfg_path = root / CONFIG_FILE
    if not cfg_path.exists():
        return False

    try:
        data: dict = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return False

    for step in steps:
        step_id = getattr(step, "STEP_ID", "")
        if step_id and step_id in data:
            try:
                step.apply_config(data[step_id])
            except Exception:
                pass

    return True
