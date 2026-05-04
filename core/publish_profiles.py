"""Publish profiles — credentials per platform, stored in workspace .subsync_publish_profiles.json."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

PROFILES_FILENAME = ".subsync_publish_profiles.json"

PLATFORM_ORDER = ("facebook", "youtube", "tiktok")

PLATFORM_LABELS = {
    "facebook": "Facebook (Page)",
    "youtube": "YouTube",
    "tiktok": "TikTok",
}


def profiles_path(base_dir: str) -> Path:
    return Path(base_dir) / PROFILES_FILENAME


def new_profile(name: str = "Profile") -> dict:
    return _empty_profile(name)


def _empty_profile(name: str) -> dict:
    pid = f"pf_{uuid.uuid4().hex[:12]}"
    return {
        "id": pid,
        "name": name.strip() or "Profile",
        "credentials": {
            "facebook": {"page_id": "", "page_access_token": ""},
            "youtube": {
                "client_id": "",
                "client_secret": "",
                "refresh_token": "",
                "redirect_uri": "http://127.0.0.1:8742/",
                "oauth_login_hint": "",
            },
            "tiktok": {"access_token": "", "open_id": ""},
        },
    }


def load_profiles(base_dir: str) -> dict:
    """Return {profiles: [...], last_profile_id: str}."""
    if not base_dir.strip():
        return {"profiles": [], "last_profile_id": ""}
    p = profiles_path(base_dir)
    if not p.is_file():
        return {"profiles": [], "last_profile_id": ""}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"profiles": [], "last_profile_id": ""}
    profiles = data.get("profiles") or []
    if not isinstance(profiles, list):
        profiles = []
    return {
        "profiles": profiles,
        "last_profile_id": str(data.get("last_profile_id") or ""),
    }


def save_profiles(base_dir: str, data: dict) -> None:
    if not base_dir.strip():
        return
    p = profiles_path(base_dir)
    out = {
        "version": 1,
        "last_profile_id": str(data.get("last_profile_id") or ""),
        "profiles": data.get("profiles") or [],
    }
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


def get_profile_by_id(base_dir: str, profile_id: str) -> dict | None:
    st = load_profiles(base_dir)
    for pr in st["profiles"]:
        if str(pr.get("id")) == profile_id:
            return pr
    return None


def platform_configured(platform: str, creds: dict) -> bool:
    c = creds or {}
    if platform == "facebook":
        return bool(
            str(c.get("page_id") or "").strip()
            and str(c.get("page_access_token") or "").strip()
        )
    if platform == "youtube":
        return bool(
            str(c.get("client_id") or "").strip()
            and str(c.get("client_secret") or "").strip()
            and str(c.get("refresh_token") or "").strip()
        )
    if platform == "tiktok":
        return bool(str(c.get("access_token") or "").strip())
    return False


def profile_platforms_ready(profile: dict) -> dict[str, bool]:
    creds_root = profile.get("credentials") or {}
    out: dict[str, bool] = {}
    for pl in PLATFORM_ORDER:
        out[pl] = platform_configured(pl, creds_root.get(pl) or {})
    return out
