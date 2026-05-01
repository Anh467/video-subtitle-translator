"""Subtitle region removal helpers (delogo / cropdetect)."""

import os
import re
import subprocess
import tempfile

def escape_drawtext_text(text: str) -> str:
    return (
        (text or "")
        .replace("\\", r"\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("%", r"\%")
        .replace(",", r"\,")
    )


# ── Delogo: build the removal filter string ───────────────────────────────────


def delogo_filter(x: int, y: int, w: int, h: int) -> str:
    """
    FFmpeg delogo filter — interpolates border pixels to fill the region.
    Much better than blur because it reconstructs background rather than smearing.

    x, y = top-left corner of subtitle region
    w, h = width and height of region
    show=0 means don't show debug border
    """
    return f"delogo=x={x}:y={y}:w={w}:h={h}:show=0"


def auto_detect_sub_region(
    video_path: str, video_w: int, video_h: int
) -> tuple[int, int, int, int] | None:
    """
    Auto-detect subtitle region using ffprobe + a heuristic:
    sample frame at 10s, run ffmpeg cropdetect on bottom 25% of frame.
    Returns (x, y, w, h) or None if detection fails.

    This is a best-effort heuristic — user-defined region is more reliable.
    """
    try:
        bottom_y = int(video_h * 0.72)
        crop_h = int(video_h * 0.25)

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()

        # Extract frame at 10s
        r = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                "10",
                "-i",
                video_path,
                "-vframes",
                "1",
                "-vf",
                f"crop={video_w}:{crop_h}:0:{bottom_y}",
                tmp.name,
            ],
            capture_output=True,
        )
        if r.returncode != 0:
            return None

        # Run cropdetect on the cropped bottom strip
        r2 = subprocess.run(
            ["ffmpeg", "-i", tmp.name, "-vf", "cropdetect=24:2:0", "-f", "null", "-"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        os.unlink(tmp.name)

        # Parse cropdetect output: crop=W:H:X:Y

        matches = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r2.stderr)
        if not matches:
            return None

        # Take the most common crop result
        cw, ch, cx, cy = map(int, matches[-1])
        # Translate cy back to full-video coordinates
        full_y = bottom_y + cy
        # Add padding
        pad = 4
        return (
            max(0, cx - pad),
            max(0, full_y - pad),
            min(cw + pad * 2, video_w),
            min(ch + pad * 2, video_h - full_y),
        )

    except Exception:
        return None
