"""Formats, languages, Whisper pricing for Step 1."""

SUPPORTED_AUDIO = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma"}
SUPPORTED_VIDEO = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv"}
SUPPORTED_FORMATS = SUPPORTED_AUDIO | SUPPORTED_VIDEO
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large"]
WHISPER_API_COST_PER_MINUTE = 0.006

LANGUAGES = {
    "Auto detect": None,
    "Vietnamese": "vi",
    "English": "en",
    "Japanese": "ja",
    "Korean": "ko",
    "Chinese": "zh",
    "French": "fr",
    "German": "de",
    "Spanish": "es",
    "Thai": "th",
    "Indonesian": "id",
}
