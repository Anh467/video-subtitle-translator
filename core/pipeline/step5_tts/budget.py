"""TTS cost table and translated-character estimates (USD/VND hints)."""

from __future__ import annotations

# Approximate cost per 1M chars (USD) for reference display
COST_PER_1M = {
    "fpt": 0.00,
    "zalo": 0.00,
    "gtts": 0.00,
    "google_cloud_tts": 4.00,
    "openai_tts": 15.00,
    "elevenlabs": 30.00,
}


def count_translated_chars(session) -> tuple[int, str]:
    """Return (character count, UI label text)."""
    try:
        if not session.step2_done:
            return 0, "No translated script yet"
        segs = session.load_translated()
        total = sum(len(s.translated.strip()) for s in segs)
        return total, f"{total:,} characters  ({len(segs)} segments)"
    except Exception as e:
        return 0, f"Cannot read script: {e}"


def format_tts_cost_estimate(char_count: int, backend_key: str) -> str:
    if char_count == 0:
        return ""
    cost_per_1m = COST_PER_1M.get(backend_key, 0)
    if cost_per_1m == 0:
        return "Free"
    usd = char_count / 1_000_000 * cost_per_1m
    vnd = usd * 25_000
    if vnd < 1:
        return f"~${usd:.4f} USD"
    return f"~${usd:.3f} USD  (~{vnd:,.0f} VNĐ)"
