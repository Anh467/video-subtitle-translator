"""Helpers for converting UI-style log strings to plain file log lines."""


def detect_level(msg: str) -> str:
    """Infer log level from message content."""
    m = msg.strip()
    if any(x in m for x in ("❌", "ERROR", "FAILED", "failed", "Exception")):
        return "ERROR"
    if any(x in m for x in ("⚠️", "WARNING", "warning", "⚠")):
        return "WARN"
    if any(x in m for x in ("✅", "done", "Done", "complete", "Complete")):
        return "OK"
    if any(x in m for x in ("🚀", "▶", "Running", "Starting")):
        return "START"
    if "─" * 5 in m or "=" * 5 in m:
        return "SEP"
    return "INFO"


_REPLACEMENTS = (
    ("🎬", "[VIDEO]"),
    ("🎧", "[AUDIO]"),
    ("📄", "[FILE]"),
    ("✅", "[OK]"),
    ("❌", "[ERR]"),
    ("⚠️", "[WARN]"),
    ("🌏", "[TRANS]"),
    ("🔥", "[BURN]"),
    ("🎵", "[MUSIC]"),
    ("🎙️", "[TTS]"),
    ("📡", "[HTTP]"),
    ("📤", "[SEND]"),
    ("📥", "[RECV]"),
    ("⏳", "[WAIT]"),
    ("⏱️", "[TIME]"),
    ("🔍", "[DEBUG]"),
    ("💾", "[SAVE]"),
    ("📊", "[STAT]"),
    ("🚀", "[START]"),
    ("🎉", "[DONE]"),
    ("🔗", "[CHAIN]"),
    ("💡", "[TIP]"),
    ("🔑", "[KEY]"),
    ("⏸️", "[GAP]"),
    ("📖", "[INFO]"),
    ("🏁", "[END]"),
    ("▶", ">"),
    ("⭐", "*"),
    ("✨", "*"),
    ("→", "->"),
    ("║", "|"),
    ("─", "-"),
    ("═", "="),
)

_KEEP_EXTRA = frozenset("→←↑↓•·…–—")


def strip_emoji(text: str) -> str:
    """Remove decorative emoji / high-plane glyphs for cleaner log files."""
    for emoji, replacement in _REPLACEMENTS:
        text = text.replace(emoji, replacement)
    out_parts: list[str] = []
    for ch in text:
        o = ord(ch)
        if o < 0x2000 or ch in _KEEP_EXTRA:
            out_parts.append(ch)
    return "".join(out_parts)
