"""
Run PaddleOCR in a separate Python environment (py310/py311) and emit JSON to stdout.

This exists to support running the main app on Python 3.13 where Paddle/PaddleOCR wheels
may not be compatible with NumPy 2.x.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _has_cjk(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    for ch in t:
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0x3000 <= o <= 0x303F:
            return True
    return False


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python paddleocr_extract.py <image_path>", file=sys.stderr)
        return 2
    img_path = Path(argv[1])
    if not img_path.is_file():
        print(f"Image not found: {img_path}", file=sys.stderr)
        return 3

    try:
        import numpy as np
        from paddleocr import PaddleOCR
        from PIL import Image
    except Exception as e:
        print(f"IMPORT_ERROR: {e}", file=sys.stderr)
        return 4

    img = Image.open(img_path).convert("RGB")
    np_img = np.array(img)
    try:
        try:
            ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
        except TypeError:
            ocr = PaddleOCR(use_angle_cls=True, lang="ch")
        result = ocr.ocr(np_img, cls=True)
    except Exception as e:
        print(f"OCR_ERROR: {e}", file=sys.stderr)
        return 5

    first = result[0] if isinstance(result, (list, tuple)) and result else result
    rows = first if isinstance(first, list) else list(first or [])

    out = []
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
        if not _has_cjk(tx):
            continue
        out.append({"box": box, "text": tx.strip()})

    sys.stdout.write(json.dumps({"entries": out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

