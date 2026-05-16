"""Optional Step 7: CJK detection, zh→vi via callback, PaddleOCR thumbnail overlay."""

from __future__ import annotations

import re
import shutil
import json
import os
import subprocess
import sys
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


def normalize_ocr_cjk(text: str) -> str:
    """
    PaddleOCR sometimes yields CJK with spaces between characters.
    Normalize by removing spaces between consecutive CJK chars, while keeping
    normal word spacing for non-CJK text.
    """
    t = (text or "").strip()
    if not t:
        return ""
    # Collapse multiple spaces first
    t = re.sub(r"\s+", " ", t)
    # Remove spaces between CJK chars: "西 游" -> "西游"
    out = []
    prev_cjk = False
    for ch in t:
        is_cjk = has_cjk(ch)
        if ch == " " and prev_cjk:
            # skip; might be between CJK chars
            continue
        out.append(ch)
        prev_cjk = is_cjk
    t2 = "".join(out)
    # If we accidentally removed a needed space, keep a final cleanup
    return re.sub(r"\s+", " ", t2).strip()


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
        "Translate the following Chinese/mixed metadata into fluent Vietnamese.\n"
        "Keep proper nouns consistent with common Vietnamese romanization where applicable.\n"
        "Preserve #hashtags and @handles unchanged.\n"
        "Natural phrasing — not literal word-by-word; output Vietnamese only, no quotes or notes.\n\n"
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
    context: str | None = None,
    translate_fn: Callable[[str], str],
    log,
) -> list[str]:
    if not lines:
        return []
    src_lines = [normalize_ocr_cjk(t) for t in lines]
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(src_lines))

    ctx = (context or "").strip()
    ctx_block = f"CONTEXT (Vietnamese or bilingual): {ctx}\n\n" if ctx else ""

    def _parse(raw: str) -> list[str]:
        out_lines: list[str] = []
        for m in re.finditer(
            r"^\s*\d+\.\s*(.+)$", raw or "", re.MULTILINE | re.UNICODE
        ):
            chunk = m.group(1).strip().split("\n")[0].strip()
            out_lines.append(chunk)
        while len(out_lines) < len(src_lines):
            out_lines.append("")
        return out_lines[: len(src_lines)]

    def _quality_ok(v: str) -> bool:
        v = (v or "").strip()
        if not v:
            return False
        if has_cjk(v):
            return False
        # avoid nonsense 1-2 chars
        return len(v) >= 2

    prompts = [
        (
            "You are a professional Chinese→Vietnamese translator for on-image thumbnail text.\n"
            "Use CONTEXT to disambiguate names, relationships, and wordplay.\n"
            "Translate each numbered line to natural Vietnamese (correct diacritics).\n"
            "Rules:\n"
            "- Keep numbering exactly.\n"
            "- Match story tone from CONTEXT; do NOT invent unrelated events.\n"
            "- Preserve proper nouns consistently.\n"
            "- Episode markers (第一集 / 第1集) → e.g. 'Tập 1'.\n"
            "- Concise readable length for overlay (often 2–10 words).\n"
            "- Output ONLY the numbered list.\n\n"
            f"{ctx_block}"
            f"{numbered}"
        ),
        (
            "Faithfully translate numbered Chinese lines to Vietnamese using CONTEXT for meaning.\n"
            "Avoid literal Han-Viet calques when a idiomatic Vietnamese phrase fits.\n"
            "Numbered lines only:\n\n"
            f"{ctx_block}"
            f"{numbered}"
        ),
    ]

    last_raw = ""
    for attempt, prompt in enumerate(prompts, start=1):
        try:
            last_raw = translate_fn(prompt) or ""
            parsed = _parse(last_raw)
            merged: list[str] = []
            for i, vi in enumerate(parsed):
                vi = (vi or "").strip()
                if not _quality_ok(vi):
                    merged.append(src_lines[i])
                else:
                    merged.append(re.sub(r"\s+", " ", vi))
            # If at least half lines look good, accept.
            good = sum(1 for x in merged if _quality_ok(x))
            if good >= max(1, len(src_lines) // 2):
                return merged
        except Exception as e:
            if log:
                log(f"⚠️  Batch OCR line translate failed (attempt {attempt}): {e}")
            continue
    return src_lines


def repaint_thumbnail_remove_zh_overlay(
    path_in: str,
    path_out: str,
    *,
    context: str | None = None,
    ocr_python_exe: str | None = None,
    translate_fn: Callable[[str], str],
    log,
) -> bool:
    try:
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        if log:
            log(
                "⚠️  Thiếu numpy/PIL — bỏ qua OCR zh→vi."
            )
        return False

    def _run_external_paddleocr(image_path: str) -> list[tuple[list, str]]:
        exe = (ocr_python_exe or os.environ.get("SUBSYNC_OCR_PYTHON") or "").strip()
        if not exe:
            return []
        if not Path(exe).exists():
            return []
        script = Path(__file__).with_name("paddleocr_extract.py")
        if not script.is_file():
            return []
        try:
            cp = subprocess.run(
                [exe, str(script), image_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=240,
            )
        except Exception:
            return []
        if cp.returncode != 0:
            if log:
                err = (cp.stderr or "").strip().splitlines()[-1:]  # last line
                log(f"⚠️  External PaddleOCR failed (code {cp.returncode}): {err[0] if err else ''}")
            return []
        try:
            obj = json.loads(cp.stdout or "{}")
            rows = obj.get("entries") or []
        except Exception:
            return []
        out: list[tuple[list, str]] = []
        for it in rows:
            if not isinstance(it, dict):
                continue
            box = it.get("box")
            tx = it.get("text")
            if not isinstance(box, list) or not isinstance(tx, str):
                continue
            if not has_cjk(tx):
                continue
            out.append((box, normalize_ocr_cjk(tx.strip())))
        return out

    if not Path(path_in).exists():
        return False

    try:
        img = Image.open(path_in).convert("RGB")
    except Exception as e:
        if log:
            log(f"⚠️  Không mở được ảnh OCR: {e}")
        return False

    # Improve OCR recall: run OCR on an upscaled image (boxes later scaled back).
    ocr_scale = 2.0
    w0, h0 = img.size
    if w0 >= 40 and h0 >= 40:
        try:
            up = img.resize((int(w0 * ocr_scale), int(h0 * ocr_scale)))
        except Exception:
            up = img
            ocr_scale = 1.0
    else:
        up = img
        ocr_scale = 1.0

    # Optional best-quality background cleanup (inpaint). If OpenCV missing,
    # fallback to rectangle fill approach.
    try:
        import cv2  # type: ignore

        have_cv2 = True
    except Exception:
        cv2 = None
        have_cv2 = False

    # Prefer in-process PaddleOCR when available; otherwise use external OCR python env.
    entries: list[tuple[list, str]] = []
    np_img = np.array(img)
    np_up = np.array(up)
    try:
        from paddleocr import PaddleOCR  # type: ignore

        try:
            ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
        except TypeError:
            ocr = PaddleOCR(use_angle_cls=True, lang="ch")
        result = ocr.ocr(np_up, cls=True)

        first = result[0] if isinstance(result, (list, tuple)) and result else result
        rows = first if isinstance(first, list) else list(first or [])
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
            if ocr_scale != 1.0 and isinstance(box, (list, tuple)) and box:
                try:
                    box = [[float(p[0]) / ocr_scale, float(p[1]) / ocr_scale] for p in box]
                except Exception:
                    pass
            entries.append((box, normalize_ocr_cjk(tx.strip())))
    except Exception as e:
        # PaddleOCR not available or broken in this Python (e.g. NumPy ABI mismatch on 3.13)
        if log:
            log(f"⚠️  PaddleOCR in-process unavailable: {str(e)[:160]}")
        entries = _run_external_paddleocr(path_in)

    if not entries:
        shutil.copy2(path_in, path_out)
        if log:
            log("📝 OCR: không có chữ Trung — giữ ảnh gốc")
        return True

    zh_lines = [t for _, t in entries]
    vi_lines = translate_numbered_lines(
        zh_lines, context=context, translate_fn=translate_fn, log=log
    )

    # Inpaint all CJK boxes at once for cleaner background.
    if have_cv2 and cv2 is not None:
        try:
            mask = np.zeros((img.height, img.width), dtype=np.uint8)
            for box, _zh in entries:
                x0, y0, x1, y1 = _quad_to_xyxy(box)
                # More aggressive padding to reduce "box miss" artifacts.
                pad = max(10, (y1 - y0) // 4)
                x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
                x1, y1 = min(img.width - 1, x1 + pad), min(img.height - 1, y1 + pad)
                mask[y0 : y1 + 1, x0 : x1 + 1] = 255

            bgr = cv2.cvtColor(np_img, cv2.COLOR_RGB2BGR)
            # Telea tends to look smoother on text removals.
            inpainted = cv2.inpaint(bgr, mask, 5, cv2.INPAINT_TELEA)
            np_img2 = cv2.cvtColor(inpainted, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(np_img2)
            np_img = np_img2
            if log:
                log("🧽 OCR: removed Chinese text via OpenCV inpaint")
        except Exception as e:
            if log:
                log(f"⚠️  OpenCV inpaint failed, fallback to rectangle fill: {e}")

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
        pad = max(10, (y1 - y0) // 5)
        x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
        x1, y1 = min(img.width - 1, x1 + pad), min(img.height - 1, y1 + pad)
        if not have_cv2:
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
