"""TTS UI constants (backend labels, gTTS languages)."""

from core.pipeline.selection import TTS_BACKEND_LABEL_TO_KEY

TTS_BACKENDS = dict(TTS_BACKEND_LABEL_TO_KEY)
GTTS_LANGS = {
    "Vietnamese": "vi",
    "English": "en",
    "Japanese": "ja",
    "Korean": "ko",
    "Chinese": "zh-CN",
    "French": "fr",
    "German": "de",
    "Spanish": "es",
}
