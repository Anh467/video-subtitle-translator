"""Subtitle region removal helpers (delogo / auto band detection)."""

import os
import statistics
import subprocess
import tempfile
from pathlib import Path

from PIL import Image


def escape_drawtext_text(text: str) -> str:
    return (
        (text or "")
        .replace("\\", r"\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("%", r"\%")
        .replace(",", r"\,")
    )


def delogo_filter(x: int, y: int, w: int, h: int, enable_expr: str | None = None) -> str:
    """
    FFmpeg delogo filter — interpolates border pixels to fill the region.

    x, y = top-left corner of subtitle region
    w, h = width and height of region
    show=0 means don't show debug border
    Optional ``enable_expr``: timeline expression (typically OR of ``between(t,a,b)``).
    When absent, delogo applies to every frame.
    """
    s = f"delogo=x={x}:y={y}:w={w}:h={h}:show=0"
    if enable_expr:
        s += f":enable={enable_expr}"
    return s


def cue_ranges_from_translated_segments(
    segments: list | tuple,
    *,
    pad_start: float = 0.20,
    pad_end: float = 0.35,
    gap_merge_sec: float = 0.50,
    video_duration_sec: float | None = None,
) -> list[tuple[float, float]]:
    """Merge Step 2 cues into FFmpeg ``enable=between()`` windows."""
    if not segments:
        return []
    raw: list[tuple[float, float]] = []
    for s in segments:
        try:
            a = float(getattr(s, "start"))
            b = float(getattr(s, "end"))
        except Exception:
            continue
        if b <= a:
            continue
        st = max(0.0, a - pad_start)
        en = max(st + 1e-3, b + pad_end)
        raw.append((st, en))
    if not raw:
        return []
    raw.sort(key=lambda x: x[0])
    merged: list[tuple[float, float]] = []
    cs, ce = raw[0]
    for st, en in raw[1:]:
        if st <= ce + gap_merge_sec:
            ce = max(ce, en)
        else:
            merged.append((cs, ce))
            cs, ce = st, en
    merged.append((cs, ce))
    if video_duration_sec is not None and video_duration_sec > 0.1:
        vd = float(video_duration_sec)
        clipped: list[tuple[float, float]] = []
        for lo, hi in merged:
            if lo >= vd:
                continue
            hi = min(hi, vd)
            if hi > lo + 1e-4:
                clipped.append((lo, hi))
        merged = clipped
    return merged


def _merge_intervals_gap(
    intervals: list[tuple[float, float]], gap: float
) -> list[tuple[float, float]]:
    if not intervals:
        return []
    intervals = sorted(intervals)
    out: list[tuple[float, float]] = []
    cs, ce = intervals[0]
    for st, en in intervals[1:]:
        if st <= ce + gap:
            ce = max(ce, en)
        else:
            out.append((cs, ce))
            cs, ce = st, en
    out.append((cs, ce))
    return out


def compress_delogo_ranges_if_needed(
    intervals: list[tuple[float, float]],
    *,
    max_ranges: int = 170,
    max_expr_len: int = 26000,
) -> list[tuple[float, float]]:
    """Merge cues until FFmpeg CLI ``enable`` string stays manageable."""
    if not intervals:
        return []
    intervals = sorted(intervals)
    expr = delogo_timeline_enable_expr(intervals)
    dur_span = intervals[-1][1] - intervals[0][0]
    gap = max(0.48, dur_span / max(len(intervals), 1) * 0.08)
    passes = 0
    while (
        expr is None or len(expr) > max_expr_len or len(intervals) > max_ranges
    ) and gap < 1_000_000.0 and passes < 80:
        prev_n = len(intervals)
        gap *= 1.65
        intervals = _merge_intervals_gap(intervals, gap)
        expr = delogo_timeline_enable_expr(intervals)
        passes += 1
        if len(intervals) == prev_n and gap > dur_span * 4 + 900.0:
            break
    return intervals


def delogo_timeline_enable_expr(intervals: list[tuple[float, float]]) -> str | None:
    parts: list[str] = []
    for lo, hi in intervals:
        if hi <= lo:
            continue
        parts.append(f"between(t\\,{lo:.4f}\\,{hi:.4f})")
    if not parts:
        return None
    return "+".join(parts)


def default_bottom_subtitle_band(video_w: int, video_h: int) -> tuple[int, int, int, int]:
    """
    Typical hard-burned subtitles: wide strip near bottom centre.
    Use when user leaves the legacy (0,0,400,60) preset or detection fails.
    """
    vw, vh = max(320, video_w), max(180, video_h)
    side = max(6, int(vw * 0.022))
    bottom_pad = max(6, int(vh * 0.012))
    strip_h = max(48, min(int(vh * 0.145), max(76, int(vh * 0.10))))
    strip_h = min(strip_h, max(64, vh // 7))
    w_box = max(96, vw - 2 * side)
    x = side
    y = max(0, vh - strip_h - bottom_pad)
    return x, y, w_box, strip_h


def _row_variance_samples(gray: Image.Image, step: int = 8) -> list[float]:
    w, h = gray.size
    if w < 16 or h < 4:
        return []
    out: list[float] = []
    for yy in range(h):
        samples = []
        for xx in range(0, w, step):
            samples.append(float(gray.getpixel((xx, yy))))
        if len(samples) < 4:
            out.append(0.0)
            continue
        m = sum(samples) / len(samples)
        v = sum((p - m) * (p - m) for p in samples) / len(samples)
        out.append(v)
    return out


def _band_from_variance_rows(vars_per_row: list[float]) -> tuple[int, int] | None:
    if len(vars_per_row) < 8:
        return None

    med = statistics.median(vars_per_row)
    mad = statistics.median([abs(v - med) for v in vars_per_row]) + 1e-6
    thresh = med + max(120.0, 3.5 * mad)

    highs = [i for i, v in enumerate(vars_per_row) if v >= thresh]
    if not highs:
        return None

    # Prefer bands in lower half of the analyzed strip (where subs usually sit).
    mid = len(vars_per_row) // 2
    lower_highs = [i for i in highs if i >= mid - 4]
    if len(lower_highs) >= 2:
        highs = lower_highs

    y0, y1 = min(highs), max(highs) + 1
    pad = max(6, (y1 - y0) // 4 + 4)
    y0 = max(0, y0 - pad)
    y1 = min(len(vars_per_row), y1 + pad)
    band_h = y1 - y0
    if band_h < 12 or band_h > len(vars_per_row) * 11 // 20:
        return None
    return y0, y1


def _analyze_strip_png(
    png_path: str, crop_y0: int, vw: int, vh: int
) -> tuple[tuple[int, int, int, int], float] | None:
    try:
        im = Image.open(png_path).convert("RGB")
    except Exception:
        return None
    strip_h = im.height
    if strip_h < 20:
        return None
    gray = im.convert("L")
    rvar = _row_variance_samples(gray, step=max(6, vw // 200))
    band = _band_from_variance_rows(rvar)
    if not band:
        return None
    y0_rel, y1_rel = band
    strip_top = crop_y0
    full_y = strip_top + y0_rel
    band_px = y1_rel - y0_rel
    cap_band = max(44, min(int(vh * 0.172), strip_h - 2))
    full_h_px = max(36, min(band_px, cap_band, vh - full_y))

    mx = max(6, int(vw * 0.02))
    # Full width bar so wide Chinese lines are covered.
    box = (mx, full_y, max(64, vw - 2 * mx), full_h_px)

    # Score: how much "text-like" variance stands out vs rest of strip.
    try:
        med_all = statistics.median(rvar) if rvar else 0.0
        med_band = statistics.median(rvar[y0_rel:y1_rel]) if (y1_rel > y0_rel) else 0.0
        score = float(med_band - med_all)
    except Exception:
        score = 0.0
    return box, score


def _extract_bottom_strip_frame(
    video_path: str, at_sec: float, crop_y0: int, vw: int, vh: int, out_png: str
) -> bool:
    ch = vh - crop_y0
    if ch < 40:
        return False
    r = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{max(0.0, at_sec):.2f}",
            "-i",
            video_path,
            "-vframes",
            "1",
            "-vf",
            f"crop={vw}:{ch}:0:{crop_y0}",
            out_png,
        ],
        capture_output=True,
    )
    return r.returncode == 0 and Path(out_png).exists() and Path(out_png).stat().st_size > 0


def auto_detect_sub_region(
    video_path: str, video_w: int, video_h: int
) -> tuple[int, int, int, int] | None:
    """
    Auto-detect subtitle band: sample multiple strips (mid + bottom) at several
    timestamps and pick high-contrast text rows (works for Chinese / coloured hard subs).

    Falls back to ``default_bottom_subtitle_band`` if analysis fails.
    """
    vw, vh = max(320, video_w), max(180, video_h)
    # Candidate ROIs. Many apps place foreign hard-subs at bottom, but some sources
    # place them around mid-frame. We test several y0 values.
    crop_y0_candidates = [
        int(vh * 0.32),  # mid-ish
        int(vh * 0.44),
        int(vh * 0.52),  # default bottom half
        int(vh * 0.62),
    ]
    crop_y0_candidates = [max(0, min(vh - 60, y0)) for y0 in crop_y0_candidates]

    times: list[float] = []
    if Path(video_path).exists():
        try:
            dur = _probe_duration_sec(video_path)
            if dur and dur > 1.0:
                for t in (1.0, 3.0, 8.0, 15.0, 25.0, 45.0, 90.0, dur * 0.35, dur * 0.55):
                    if 0.5 < t < dur - 0.5:
                        times.append(t)
        except Exception:
            pass
    if not times:
        times = [3.0, 10.0, 20.0]

    best: tuple[tuple[int, int, int, int], float] | None = None

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    tmp_path = tmp.name
    try:
        for at in sorted(set(round(t, 2) for t in times))[:12]:
            for crop_y0 in crop_y0_candidates:
                if not _extract_bottom_strip_frame(
                    video_path, at, crop_y0, vw, vh, tmp_path
                ):
                    continue
                analyzed = _analyze_strip_png(tmp_path, crop_y0, vw, vh)
                if not analyzed:
                    continue
                (x, y, rw, rh), score = analyzed
                if rh < 28 or rw < vw // 4:
                    continue
                # Prefer higher score; slight bias to lower placements (typical subtitle region)
                # but allow mid-frame if it is more text-like.
                y_norm = y / max(1.0, float(vh))
                score2 = float(score + (y_norm * 8.0))
                if best is None or score2 > best[1]:
                    best = ((x, y, rw, rh), score2)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if best:
        (x, y, rw, rh), _score = best
        cap_rh = max(56, min(int(vh * 0.22), max(108, vh // 9)))
        rh = max(34, min(int(rh), cap_rh))
        pad = 6
        x = max(0, x - pad)
        y = max(0, y - pad)
        rw = max(48, min(rw + 2 * pad, vw - x))
        rh = max(34, min(rh + 2 * pad, vh - y, cap_rh + 2 * pad))
        return x, y, rw, rh

    return default_bottom_subtitle_band(vw, vh)


def _probe_duration_sec(video_path: str) -> float | None:
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video_path,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if r.returncode != 0:
        return None
    try:
        return float((r.stdout or "").strip())
    except ValueError:
        return None


def probe_video_duration_sec(video_path: str) -> float | None:
    """Duration in seconds via ffprobe."""
    return _probe_duration_sec(video_path)


def looks_like_stale_top_left_preset(
    x: int, y: int, w: int, h: int, video_w: int, video_h: int
) -> bool:
    """UI factory defaults — top-left strip; triggers auto bottom band before burn."""
    _ = video_w, video_h
    return (x, y, w, h) == (0, 0, 400, 60)
