"""Subtitle text / SRT parse helpers used by ``SubtitleEditor``."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from core.ffmpeg_utils import ffprobe_executable


def format_srt_timestamp(s: float) -> str:
    h, r = divmod(int(s), 3600)
    m, sec = divmod(r, 60)
    ms = int((s - int(s)) * 1000)
    return f"{h:02}:{m:02}:{sec:02},{ms:03}"


def write_srt_from_segment_dicts(segments: list[dict], path: str) -> None:
    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(
            f"{i}\n"
            f"{format_srt_timestamp(seg['start'])} --> {format_srt_timestamp(seg['end'])}\n"
            f"{seg['translated'].strip()}\n"
        )
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def media_duration_seconds_ffprobe(path: str) -> float:
    if not path or not Path(path).exists():
        return 0.0
    try:
        cmd = [
            ffprobe_executable(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
        out = subprocess.check_output(cmd, text=True).strip()
        return max(0.0, float(out))
    except Exception:
        return 0.0


def parse_translated_panel_text(text: str) -> list[dict]:
    """
    Parse Translated panel (SRT-style) về list of dicts.

    Format:
        1
        [0.0s-1.28s] original text
        translated text
    """
    segments: list[dict] = []
    blocks = re.split(r"\n\s*\n", text.strip())
    for block in blocks:
        lines = [ln for ln in block.strip().splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        ts_idx = None
        for i, line in enumerate(lines):
            if re.match(r"^\[[\d.]+s[–\-][\d.]+s\]", line.strip()):
                ts_idx = i
                break
        if ts_idx is None:
            continue
        ts_line = lines[ts_idx].strip()
        m = re.match(r"^\[([\d.]+)s[–\-]([\d.]+)s\]\s*(.*)", ts_line)
        if not m:
            continue
        start = float(m.group(1))
        end = float(m.group(2))
        original = m.group(3).strip()
        translated_lines = lines[ts_idx + 1 :]
        translated = " ".join(ln.strip() for ln in translated_lines if ln.strip())
        if not translated:
            continue
        segments.append(
            {"start": start, "end": end, "original": original, "translated": translated}
        )
    return segments
