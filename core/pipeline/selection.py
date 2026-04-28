"""Shared selection and mapping helpers for backend/model/API-key logic."""

from __future__ import annotations

TRANSLATE_BACKEND_BY_INDEX = {0: "gemini", 1: "google", 2: "openai"}
VERIFY_BACKEND_BY_INDEX = {0: "none", 1: "ollama", 2: "gemini", 3: "openai"}

TTS_BACKEND_LABEL_TO_KEY = {
    "FPT AI TTS (free ⭐ VI)": "fpt",
    "Zalo AI TTS (free VI)": "zalo",
    "gTTS (Google, free)": "gtts",
    "OpenAI TTS (natural)": "openai_tts",
    "ElevenLabs (best+emotion)": "elevenlabs",
}


def translate_backend_from_index(index: int) -> str:
    return TRANSLATE_BACKEND_BY_INDEX.get(index, "gemini")


def verify_backend_from_index(index: int) -> str:
    return VERIFY_BACKEND_BY_INDEX.get(index, "none")


def ollama_model_from_combo_text(text: str) -> str:
    # Format examples: "qwen2   — description", "llama3"
    return text.split("—")[0].strip().split()[0].strip() if text else "qwen2"


def tts_backend_from_label(label: str) -> str:
    if label == "All backends (batch run)":
        return "all"
    return TTS_BACKEND_LABEL_TO_KEY.get(label, "gtts")


def expand_tts_backends(backend_key: str) -> list[str]:
    if backend_key == "all":
        return list(TTS_BACKEND_LABEL_TO_KEY.values())
    return [backend_key]


def translate_key_candidates(backend_key: str) -> list[str]:
    if backend_key == "gemini":
        return ["gemini"]
    if backend_key == "openai":
        return ["openai"]
    return ["gemini", "openai"]


def tts_key_candidates(backend_key: str) -> list[str]:
    if backend_key == "fpt":
        return ["fpt"]
    if backend_key == "zalo":
        return ["zalo"]
    if backend_key == "elevenlabs":
        return ["elevenlabs"]
    if backend_key == "openai_tts":
        return ["openai"]
    if backend_key == "all":
        return ["fpt", "zalo", "elevenlabs", "openai"]
    return []
