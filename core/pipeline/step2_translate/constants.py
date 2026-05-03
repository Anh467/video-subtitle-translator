"""Languages and cost table for Step 2."""

LANGUAGES = {
    "Vietnamese": "vi",
    "English": "en",
    "Japanese": "ja",
    "Korean": "ko",
    "Chinese (Simplified)": "zh-CN",
    "French": "fr",
    "German": "de",
    "Spanish": "es",
    "Thai": "th",
    "Indonesian": "id",
}
LANG_NAMES = {v: k for k, v in LANGUAGES.items()}

# Approximate cost per 1M translated characters for Step 2.
TRANSLATION_COST_PER_1M_CHARS = {
    "gemini": 0.0,
    "google": 0.0,
    "openai": 2.0,
    "openai_gpt4o": 2.0,
}

CHUNK_SEP = "|||"  # separator cho Google chunk mode
