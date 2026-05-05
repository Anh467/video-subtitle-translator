"""Optional Step 7: CJK detection, zh→vi via callback, PaddleOCR thumbnail overlay."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Callable


def has_cjk(text: str) -> bool:
    if not (text or "").strip():
        return False
    for ch in text:
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0x3000 <= o <= 0x303F:
            return True
    return False


def translate_block_zh_to_vi(
    text: str,
    *,
    translate_fn: Callable[[str], str],
    log,
) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    prompt = (
        "Translate the following Chinese (or mixed) social/video metadata into natural Vietnamese.\n"
        "Keep meaning; keep hashtags and @handles as-is if present.\n"
        "Output Vietnamese only, no quotes, no explanation.\n\n"
        f"{t}"
    )
    try:
        out = (translate_fn(prompt) or "").strip()
        return re.sub(r"\s+", " ", out).strip()
    except Exception as e:
        if log:
            log(f"⚠️  Metadata zh→vi translate failed: {e}")
        return t


def _quad_to_xyxy(points) -> tuple[int, int, int, int]:
    xs = [int(float(p[0])) for p in points]
    ys = [int(float(p[1])) for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _sample_border_color(img_rgb, x0: int, y0: int, x1: int, y1: int, pad: int = 6):
    w, h = img_rgb.size
    x0c = max(0, min(x0, w - 1))
    y0c = max(0, min(y0, h - 1))
    x1c = max(0, min(x1, w - 1))
    y1c = max(0, min(y1, h - 1))
    pts = []
    for t in range(pad + 3):
        rr = [(x0c - t, y) for y in range(max(0, y0c - t), min(h, y1c + t))]
        rr += [(x1c + t, y) for y in range(max(0, y0c - t), min(h, y1c + t))]
        rr += [(x, y0c - t) for x in range(max(0, x0c - t), min(w, x1c + t))]
        rr += [(x, y1c + t) for x in range(max(0, x0c - t), min(w, x1c + t))]
        for x, y in rr[:240]:
            if 0 <= x < w and 0 <= y < h:
                pts.append(img_rgb.getpixel((x, y)))
        if len(pts) > 50:
            break
    if not pts:
        return (32, 32, 36)
    r = sum(p[0] for p in pts) // len(pts)
    g = sum(p[1] for p in pts) // len(pts)
    b = sum(p[2] for p in pts) // len(pts)
    return (r, g, b)


def translate_numbered_lines(
    lines: list[str],
    *,
    translate_fn: Callable[[str], str],
    log,
) -> list[str]:
    if not lines:
        return []
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(lines))
    prompt = (
        "Translate each numbered line from Chinese to concise Vietnamese for a video thumbnail.\n"
        "Keep the same numbering. One translation per line. Short phrases preferred.\n"
        "Output format strictly:\n1. ...\n2. ...\n etc.\n\n"
        f"{numbered}"
    )
    try:
        raw = translate_fn(prompt) or ""
        out_lines = []
        for m in re.finditer(
            r"^\s*\d+\.\s*(.+)$", raw, re.MULTILINE | re.UNICODE | re.DOTALL
        ):
            chunk = m.group(1).strip().split("\n")[0].strip()
            out_lines.append(chunk)
        while len(out_lines) < len(lines):
            out_lines.append("")
        return [
            out_lines[i] if i < len(out_lines) and out_lines[i] else lines[i]
            for i in range(len(lines))
        ]
    except Exception as e:
        if log:
            log(f"⚠️  Batch OCR line translate failed: {e}")
        return lines


def repaint_thumbnail_remove_zh_overlay(
    path_in: str,
    path_out: str,
    *,
    translate_fn: Callable[[str], str],
    log,
) -> bool:
    try:
        import numpy as np
        from paddleocr import PaddleOCR
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        if log:
            log(
                "⚠️  PaddleOCR chưa cài — bỏ qua OCR zh→vi. "
                "Xem README: pip install paddlepaddle paddleocr"
            )
        return False

    if not Path(path_in).exists():
        return False

    try:
        img = Image.open(path_in).convert("RGB")
    except Exception as e:
        if log:
            log(f"⚠️  Không mở được ảnh OCR: {e}")
        return False

    np_img = np.array(img)
    try:
        try:
            ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
        except TypeError:
            ocr = PaddleOCR(use_angle_cls=True, lang="ch")
        result = ocr.ocr(np_img, cls=True)
    except Exception as e:
        if log:
            log(f"⚠️  PaddleOCR lỗi: {e}")
        return False

    first = result[0] if isinstance(result, (list, tuple)) and result else result
    if not first:
        shutil.copy2(path_in, path_out)
        if log:
            log("📝 OCR: không có vùng chữ — giữ ảnh gốc")
        return True

    rows = first if isinstance(first, list) else list(first)

    entries: list[tuple[list, str]] = []
    for line in rows:
        if not line or len(line) < 2:
            continue
        box, txpart = line[0], line[1]
        if isinstance(txpart, (list, tuple)) and txpart:
            tx = txpart[0]
        elif isinstance(txpart, str):
            tx = txpart
        else:
            continue
        tx = tx if isinstance(tx, str) else str(tx)
        if not (tx or "").strip():
            continue
        if not has_cjk(tx):
            continue
        entries.append((box, tx.strip()))

    if not entries:
        shutil.copy2(path_in, path_out)
        if log:
            log("📝 OCR: không có chữ Trung — giữ ảnh gốc")
        return True

    zh_lines = [t for _, t in entries]
    vi_lines = translate_numbered_lines(zh_lines, translate_fn=translate_fn, log=log)

    draw = ImageDraw.Draw(img)
    font_path_used = None
    font = None
    for fp in (
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        if Path(fp).exists():
            try:
                font_path_used = fp
                font = ImageFont.truetype(fp, max(14, img.height // 28))
                break
            except Exception:
                continue
    if font is None:
        font = ImageFont.load_default()

    for idx, (box, _zh) in enumerate(entries):
        x0, y0, x1, y1 = _quad_to_xyxy(box)
        pad = max(4, (y1 - y0) // 10)
        x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
        x1, y1 = min(img.width - 1, x1 + pad), min(img.height - 1, y1 + pad)
        fill_rgb = _sample_border_color(img, x0, y0, x1, y1, pad=pad)
        draw.rectangle([x0, y0, x1, y1], fill=fill_rgb)

        vi = vi_lines[idx] if idx < len(vi_lines) else ""
        vi = (vi or "").strip()
        if not vi:
            continue
        fontsize = max(11, min((y1 - y0 + 8) // 2, img.height // 12))
        try:
            nf = (
                ImageFont.truetype(font_path_used, fontsize)
                if font_path_used
                else font
            )
        except Exception:
            nf = font
        max_w = max(28, x1 - x0 - 6)
        words = vi.split()
        lines_tx: list[str] = []
        cur = ""
        for wpx in words:
            test = f"{cur} {wpx}".strip()
            bbox = draw.textbbox((0, 0), test, font=nf)
            if bbox[2] - bbox[0] <= max_w:
                cur = test
            else:
                if cur:
                    lines_tx.append(cur)
                cur = wpx
        if cur:
            lines_tx.append(cur)
        if not lines_tx:
            lines_tx = [vi[:40]]
        try:
            line_h = draw.textbbox((0, 0), "Áy", font=nf)[3] + 3
        except Exception:
            line_h = fontsize + 4
        cy = y0 + max(2, ((y1 - y0) - line_h * len(lines_tx)) // 2)
        for ln in lines_tx[:4]:
            bbox = draw.textbbox((0, 0), ln, font=nf)
            tw = bbox[2] - bbox[0]
            tx_pix = max(x0 + 3, min(x1 - tw - 3, x0 + (x1 - x0 - tw) // 2))
            for dx, dy in [
                (-2, -2),
                (-2, 2),
                (2, -2),
                (2, 2),
                (-2, 0),
                (2, 0),
                (0, -2),
                (0, 2),
            ]:
                draw.text((tx_pix + dx, cy + dy), ln, font=nf, fill=(0, 0, 0))
            draw.text((tx_pix, cy), ln, font=nf, fill=(255, 240, 220))
            cy += line_h
            if cy > y1 - 4:
                break

    try:
        img.save(path_out, format="JPEG", quality=93)
        if log:
            log(f"🖌️  OCR: đã thay ~{len(entries)} vùng chữ Trung → Vi")
        return True
    except Exception as e:
        if log:
            log(f"⚠️  Lưu ảnh OCR thất bại: {e}")
        return False


def copy_if_exists(thumb_path: str | None, dest: str) -> bool:
    if not thumb_path or not Path(thumb_path).is_file():
        return False
    try:
        shutil.copy2(thumb_path, dest)
        return Path(dest).stat().st_size > 0
    except OSError:
        return False
