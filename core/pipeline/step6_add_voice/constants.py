"""Mix modes and path helpers for Step 6."""

# TTS stems are often quieter than BGM/original after ffmpeg amix.
TTS_VOLUME_BOOST = 1.35

MIX_MODES = {
    "TTS only (replace original)": "replace",
    "TTS + Background music (Step 4)": "bgm_only",
    "TTS + BGM + Original voice (low vol)": "full_mix",
}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv"}

BACKEND_LABELS = {
    "google_cloud_tts": "Google Cloud TTS",
    "openai_tts": "OpenAI TTS",
    "fpt": "FPT TTS",
    "zalo": "Zalo TTS",
    "gtts": "gTTS",
    "elevenlabs": "ElevenLabs",
}
