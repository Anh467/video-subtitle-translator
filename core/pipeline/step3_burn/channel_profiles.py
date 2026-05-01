"""Workspace-scoped channel brand profiles (avatar + name)."""

import shutil
from pathlib import Path

from core.pipeline.step3_burn.constants import CHANNEL_PROFILE_ASSETS

def profiles_root(base_dir: str) -> Path:
    return Path(base_dir) / CHANNEL_PROFILE_ASSETS


def safe_profile_dir_name(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (name or "").strip())
    return safe.strip("_") or "channel"


def find_avatar_in_dir(profile_dir: Path) -> Path | None:
    exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    candidates = [
        p for p in profile_dir.iterdir() if p.is_file() and p.suffix.lower() in exts
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda p: (0 if p.stem.lower() == "avatar" else 1, p.name.lower())
    )
    return candidates[0]


def load_channel_profiles(base_dir: str) -> dict:
    if not base_dir:
        return {}
    root = profiles_root(base_dir)
    if not root.exists():
        return {}
    profiles = {}
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir():
            continue
        name_file = entry / "channel_name.txt"
        if name_file.exists():
            display_name = name_file.read_text(
                encoding="utf-8", errors="ignore"
            ).strip()
        else:
            display_name = entry.name
        if not display_name:
            continue
        avatar_file = find_avatar_in_dir(entry)
        profiles[display_name] = {
            "avatar": str(avatar_file) if avatar_file else "",
            "folder": str(entry),
        }
    return profiles


def store_profile_image(base_dir: str, src_path: str, profile_name: str) -> str:
    root = profiles_root(base_dir)
    root.mkdir(parents=True, exist_ok=True)
    profile_dir = root / safe_profile_dir_name(profile_name)
    profile_dir.mkdir(parents=True, exist_ok=True)
    src = Path(src_path)
    ext = src.suffix.lower() or ".png"
    dst = profile_dir / f"avatar{ext}"
    for old in profile_dir.glob("avatar.*"):
        if old.resolve() != dst.resolve():
            old.unlink(missing_ok=True)
    shutil.copy2(src, dst)
    (profile_dir / "channel_name.txt").write_text(
        profile_name.strip(), encoding="utf-8"
    )
    return str(dst)
