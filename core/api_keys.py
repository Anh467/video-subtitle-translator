"""
ApiKeyManager — load/save API keys từ file .env trong session folder hoặc base dir.

File .env format:
  GEMINI_API_KEY=AIzaSy...
  OPENAI_API_KEY=sk-...
  FPT_API_KEY=xxxxx
  ZALO_API_KEY=xxxxx
  ELEVENLABS_API_KEY=xxxxx
"""

import os
from pathlib import Path

ENV_FILE = ".subsync_keys"  # tên file chứa keys

KNOWN_KEYS = {
    "GEMINI_API_KEY": {"label": "Gemini API Key", "service": "gemini"},
    "OPENAI_API_KEY": {"label": "OpenAI API Key", "service": "openai"},
    "FPT_API_KEY": {"label": "FPT AI TTS Key", "service": "fpt"},
    "ZALO_API_KEY": {"label": "Zalo AI TTS Key", "service": "zalo"},
    "ELEVENLABS_API_KEY": {"label": "ElevenLabs Key", "service": "elevenlabs"},
}


class ApiKeyManager:
    def __init__(self, base_dir: str = ""):
        self._base_dir = base_dir
        self._keys: dict = {}
        if base_dir:
            self.load(base_dir)

    def load(self, base_dir: str):
        """Load keys từ <base_dir>/.subsync_keys"""
        self._base_dir = base_dir
        path = Path(base_dir) / ENV_FILE
        self._keys = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, _, v = line.partition("=")
                    k = k.strip()
                    v = v.strip()
                    if k and v:
                        self._keys[k] = v
        # Also load from environment (lower priority)
        for k in KNOWN_KEYS:
            if k not in self._keys and os.environ.get(k):
                self._keys[k] = os.environ[k]

    def save(self, base_dir: str = ""):
        """Save keys to <base_dir>/.subsync_keys"""
        d = base_dir or self._base_dir
        if not d:
            return
        path = Path(d) / ENV_FILE
        lines = [
            "# SubSync API Keys — auto-saved",
            "# Không share file này với người khác!\n",
        ]
        for k, meta in KNOWN_KEYS.items():
            v = self._keys.get(k, "")
            lines.append(f"# {meta['label']}")
            lines.append(f"{k}={v}\n")
        path.write_text("\n".join(lines), encoding="utf-8")

    def get(self, key: str, default: str = "") -> str:
        return self._keys.get(key, default)

    def set(self, key: str, value: str):
        self._keys[key] = value

    def get_all(self) -> dict:
        return dict(self._keys)

    def to_dict_by_service(self) -> dict:
        """Return {service: api_key} mapping."""
        result = {}
        for k, meta in KNOWN_KEYS.items():
            v = self._keys.get(k, "")
            if v:
                result[meta["service"]] = v
        return result


# Global singleton — shared across all steps
_manager = ApiKeyManager()


def get_manager() -> ApiKeyManager:
    return _manager


def load_keys(base_dir: str):
    _manager.load(base_dir)


def save_keys(base_dir: str):
    _manager.save(base_dir)


def get_key(service_or_env: str) -> str:
    """Get key by service name or env var name."""
    # Try env var name directly
    if service_or_env in KNOWN_KEYS:
        return _manager.get(service_or_env)
    # Try by service name
    for k, meta in KNOWN_KEYS.items():
        if meta["service"] == service_or_env:
            return _manager.get(k)
    return ""
