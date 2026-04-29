"""Step 7 — generate publish info (title, description, hashtags, thumbnail)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.pipeline.base import BaseStep, CancelledError

STOP_WORDS = {
    "la",
    "va",
    "voi",
    "cua",
    "cho",
    "mot",
    "nhung",
    "trong",
    "khi",
    "duoc",
    "dang",
    "se",
    "da",
    "tu",
    "den",
    "tai",
    "nay",
    "kia",
    "day",
    "ban",
    "toi",
    "anh",
    "chi",
    "em",
    "ong",
    "ba",
    "co",
    "khong",
    "rat",
    "hon",
    "bi",
    "ve",
    "do",
    "nhu",
    "roi",
    "noi",
    "chuyen",
    "video",
    "minh",
    "nay",
    "do",
    "roi",
    "thi",
    "nua",
    "sau",
    "truoc",
    "day",
    "kia",
    "qua",
    "rat",
    "van",
    "dang",
    "duoi",
    "tren",
    "hay",
    "nhieu",
    "it",
    "mot",
    "nhung",
}


class PublishInfoStep(BaseStep):
    STEP_ID = "step7_publish_info"
    LABEL = "⑦ Publish Info"
    COLOR = "#17576f"
    ENABLED_BY_DEFAULT = False

    def __init__(self):
        self._gen_backend_combo = None
        self._ollama_model_combo = None
        self._style_combo = None
        self._max_tags_spin = None
        self._thumb_mode_combo = None
        self._thumb_at_spin = None
        self._overwrite_chk = None

    def run(self, session, config, log, cancel):
        if not session.step2_done:
            raise RuntimeError(
                "Step 2 translated script not found. Run Translate first."
            )

        segments = session.load_translated()
        if not segments:
            raise RuntimeError("No translated segments to generate publish info.")

        if cancel.is_set():
            raise CancelledError()

        style = config.get("style", "story")
        gen_backend = config.get("gen_backend", "ollama")
        ollama_model = config.get("ollama_model", "qwen2")
        max_tags = int(config.get("max_tags", 8) or 8)
        thumb_mode = config.get("thumb_mode", "keep")
        thumb_at_sec = float(config.get("thumb_at_sec", 12.0) or 12.0)
        overwrite = bool(config.get("overwrite", False))

        lines = [s.translated.strip() for s in segments if s.translated.strip()]
        script_text = " ".join(lines)

        hashtags = self._build_hashtags(script_text, max_tags=max_tags)

        if gen_backend == "ollama":
            title, description = self._generate_title_description_ollama(
                lines=lines,
                hashtags=hashtags,
                style=style,
                model=ollama_model,
                log=log,
            )
        else:
            title = self._build_title(lines, style=style)
            description = self._build_description(lines, hashtags)

        log(f"🧠 Generated title: {title}")
        log(f"🏷️  Hashtags: {' '.join(hashtags)}")

        old_title = (session.title or "").strip()
        old_desc = (session.description or "").strip()
        if overwrite or not old_title:
            final_title = title
        else:
            final_title = old_title
        if overwrite or not old_desc:
            final_desc = description
        else:
            final_desc = old_desc

        session.save_info(final_title, final_desc)
        log("✅ Saved title + description to session.json")

        thumb_saved = ""
        if thumb_mode == "auto" or (
            thumb_mode == "auto_if_missing" and not session.thumbnail
        ):
            thumb_saved = self._generate_thumbnail(
                session,
                at_sec=thumb_at_sec,
                hook_title=final_title or title,
                lines=lines,
                style=style,
                gen_backend=gen_backend,
                ollama_model=ollama_model,
                log=log,
            )
            if thumb_saved:
                log(f"🖼️  Saved thumbnail: {Path(thumb_saved).name}")
        elif thumb_mode == "keep":
            log("🖼️  Keep current thumbnail")

        marker = {
            "title": final_title,
            "description": final_desc,
            "hashtags": hashtags,
            "thumbnail": session.thumbnail,
            "generated_thumbnail": thumb_saved,
            "style": style,
            "max_tags": max_tags,
        }
        session.step7_info.write_text(
            json.dumps(marker, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log(f"💾 Publish info marker: {session.step7_info.name}")
        return str(session.step7_info)

    def _norm_token(self, token: str) -> str:
        token = (token or "").lower().strip()
        token = unicodedata.normalize("NFKD", token)
        token = "".join(c for c in token if not unicodedata.combining(c))
        token = re.sub(r"[^a-z0-9_]", "", token)
        return token

    def _build_title(self, lines: list[str], style: str = "story") -> str:
        head = " ".join(lines[:2]).strip() if lines else ""
        head = re.sub(r"\s+", " ", head)
        if not head:
            return "Cau Chuyen Moi Hom Nay"

        base = head[:68].strip(" .,!?:;-")
        if style == "dramatic":
            return f"{base} | Dien Bien Bat Ngo"[:78]
        if style == "short":
            return base[:56]
        return base[:72]

    def _build_description(self, lines: list[str], hashtags: list[str]) -> str:
        def _clean(s: str, limit: int) -> str:
            s = re.sub(r"\s+", " ", (s or "").strip())
            s = s[:limit].strip(" .,!?:;-")
            return s

        l1 = _clean(lines[0] if len(lines) > 0 else "", 160)
        l2 = _clean(lines[len(lines) // 2] if len(lines) > 2 else "", 180)
        l3 = _clean(lines[-1] if len(lines) > 3 else "", 140)

        parts = []
        if l1:
            parts.append(f"{l1}!")
        if l2:
            parts.append(f"Tu dien bien ban dau den cao trao: {l2}.")
        if l3:
            parts.append(f"Ket cuc se nghieng ve ai: {l3}?")

        if not parts:
            parts.append("Tran chien sinh ton khoc liet nhat dang dien ra!")

        parts.append("Xem den cuoi de chung kien ke song sot cuoi cung!")
        body = "\n".join(parts)
        tag_line = "\n\n" + " ".join(hashtags) if hashtags else ""
        return body + tag_line

    def _extract_json_object(self, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except Exception:
            pass
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return {}
        try:
            return json.loads(m.group(0))
        except Exception:
            return {}

    def _ollama_generate(self, prompt: str, model: str = "qwen2") -> str:
        payload = json.dumps(
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.65,
                    "num_predict": 700,
                },
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=150) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return (data.get("response") or "").strip()

    def _generate_title_description_ollama(
        self,
        lines: list[str],
        hashtags: list[str],
        style: str,
        model: str,
        log,
    ) -> tuple[str, str]:
        excerpt = "\n".join(lines[:80])
        excerpt = excerpt[:9000]
        hashtag_line = " ".join(hashtags[:10])

        style_hint = {
            "dramatic": "tone dramatic, high tension",
            "short": "tone concise, direct",
            "story": "tone storytelling, emotional",
        }.get(style, "tone storytelling")

        prompt = (
            "You are a Vietnamese YouTube content strategist.\n"
            "Write a highly clickable but truthful title and a strong description in Vietnamese.\n"
            "Do NOT write generic boilerplate. Keep it specific, emotional, and vivid.\n"
            f"Style: {style_hint}.\n\n"
            "Rules:\n"
            "- Title: 50-78 chars, natural Vietnamese, no misleading claims.\n"
            "- Description: 4-6 short lines, storytelling flow: hook -> escalation -> suspense question -> CTA.\n"
            "- Keep content consistent with provided script.\n"
            "- Avoid phrases like: 'Noi dung duoc trich...', 'toi uu de dang dang tai'.\n"
            "- Sound like a real creator writing teaser text.\n"
            "- Include hashtags at the end of description if provided.\n"
            "- Output JSON only with keys: title, description\n\n"
            f"SCRIPT:\n{excerpt}\n\n"
            f"SUGGESTED_HASHTAGS: {hashtag_line}\n"
        )

        try:
            log(f"🤖 Generating title/description via Ollama ({model})...")
            raw = self._ollama_generate(prompt, model=model)
            obj = self._extract_json_object(raw)
            title = str(obj.get("title", "")).strip()
            desc = str(obj.get("description", "")).strip()

            if not title or not desc:
                raise RuntimeError("Ollama JSON missing title/description")

            bad_markers = (
                "Noi dung duoc trich",
                "toi uu de dang dang tai",
                "nội dung được trích",
                "tối ưu để đăng tải",
            )
            if len(desc) < 140 or any(m.lower() in desc.lower() for m in bad_markers):
                raise RuntimeError("Ollama description quality too low")

            if hashtags:
                hline = " ".join(hashtags)
                if hline not in desc:
                    desc = f"{desc}\n\n{hline}"

            return title[:90], desc
        except Exception as e:
            log(f"⚠️  Ollama generation failed, fallback to rule-based: {e}")
            title = self._build_title(lines, style=style)
            desc = self._build_description(lines, hashtags)
            return title, desc

    def _build_hashtags(self, script_text: str, max_tags: int = 8) -> list[str]:
        raw_words = re.findall(r"[A-Za-zÀ-ỹà-ỹ0-9_]{2,}", script_text.lower())
        tokens = []
        for w in raw_words:
            t = self._norm_token(w)
            if len(t) < 3:
                continue
            if t in STOP_WORDS:
                continue
            if t.isdigit():
                continue
            tokens.append(t)

        if not tokens:
            return ["#tomtat", "#giaitri", "#truyenchu"][:max_tags]

        uni = Counter(tokens)
        bi = Counter()
        for i in range(len(tokens) - 1):
            a, b = tokens[i], tokens[i + 1]
            if a in STOP_WORDS or b in STOP_WORDS:
                continue
            if len(a) < 3 or len(b) < 3:
                continue
            bi[f"{a}{b}"] += 1

        candidates: list[tuple[str, float]] = []
        for w, n in uni.items():
            # Score longer and repeated words higher.
            score = n * 1.0 + min(len(w), 14) * 0.08
            candidates.append((w, score))
        for w, n in bi.items():
            if len(w) > 24:
                continue
            score = n * 1.6 + min(len(w), 18) * 0.07
            candidates.append((w, score))

        # Domain priors to avoid useless generic hashtags.
        txt = " ".join(tokens)
        seed = []
        if any(k in txt for k in ("cua", "tom", "ca", "be", "nuoi")):
            seed += ["nuoicua", "aquarium", "dongvat"]
        if any(k in txt for k in ("chien", "dau", "pk", "doi")):
            seed += ["animalbattle"]
        if any(k in txt for k in ("review", "tomtat")):
            seed += ["review", "tomtat"]

        for s in seed:
            candidates.append((s, 10.0))

        candidates.sort(key=lambda x: x[1], reverse=True)
        tags = []
        seen = set()
        for word, _score in candidates:
            t = self._norm_token(word)
            if len(t) < 3 or t in STOP_WORDS:
                continue
            if t in seen:
                continue
            seen.add(t)
            tags.append(f"#{t}")
            if len(tags) >= max_tags:
                break

        if not tags:
            return ["#tomtat", "#giaitri", "#truyenchu"][:max_tags]
        return tags

    def _pick_hook_text(self, hook_title: str, lines: list[str], style: str) -> str:
        base = (hook_title or "").strip()
        if not base and lines:
            base = lines[0].strip()
        base = re.sub(r"\s+", " ", base)
        base = base[:64].strip(" .,!?:;-")

        if style == "dramatic":
            prefix = "SUC THAT GAY SOC"
        elif style == "short":
            prefix = "BAN KHONG NGO TOI"
        else:
            prefix = "CU LAT NGOAN MUC"

        if not base:
            return prefix

        # Keep text short and punchy for thumbnail readability.
        if len(base) > 34:
            base = base[:34].rstrip() + "..."
        return f"{prefix}: {base}"

    def _generate_hook_text_ollama(
        self,
        hook_title: str,
        lines: list[str],
        style: str,
        model: str,
        log,
    ) -> str:
        seed = " ".join((lines or [])[:6])[:1200]
        style_hint = {
            "dramatic": "dramatic",
            "short": "very short",
            "story": "storytelling",
        }.get(style, "storytelling")
        prompt = (
            "You create Vietnamese thumbnail hooks.\n"
            "Write ONE short uppercase hook line for a YouTube thumbnail.\n"
            f"Style: {style_hint}.\n"
            "Rules:\n"
            "- Max 7 words\n"
            "- Punchy, emotional, no clickbait lies\n"
            "- No hashtag, no emoji, no quotes\n"
            "- Output plain text only\n\n"
            f"VIDEO_CONTEXT: {hook_title}\n"
            f"SCRIPT_SAMPLE: {seed}\n"
        )
        try:
            raw = self._ollama_generate(prompt, model=model)
            txt = re.sub(r"\s+", " ", (raw or "").strip())
            txt = re.sub(r"[^A-Za-z0-9À-ỹà-ỹ\s:!?-]", "", txt)
            if not txt:
                raise RuntimeError("empty hook")
            words = txt.split()
            if len(words) > 7:
                txt = " ".join(words[:7])
            return txt.upper()
        except Exception as e:
            log(f"⚠️  Ollama hook fallback: {e}")
            return self._pick_hook_text(hook_title, lines, style)

    def _escape_drawtext(self, text: str) -> str:
        return (
            (text or "")
            .replace("\\", r"\\")
            .replace(":", r"\:")
            .replace("'", r"\'")
            .replace("%", r"\%")
            .replace(",", r"\,")
        )

    def _extract_representative_frame(
        self, src: str, at_sec: float, out_img: str, log
    ) -> bool:
        # Deterministic + content-aware: analyze a short window and pick representative frame.
        start = max(0.0, at_sec - 6.0)
        analyze_sec = 12.0
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start:.2f}",
            "-t",
            f"{analyze_sec:.2f}",
            "-i",
            src,
            "-vf",
            "thumbnail=120,scale=1280:-2",
            "-frames:v",
            "1",
            "-q:v",
            "2",
            out_img,
        ]
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if (
            r.returncode == 0
            and Path(out_img).exists()
            and Path(out_img).stat().st_size > 0
        ):
            return True

        # Fallback: exact timestamp frame.
        cmd2 = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{max(0.0, at_sec):.2f}",
            "-i",
            src,
            "-frames:v",
            "1",
            "-q:v",
            "2",
            out_img,
        ]
        r2 = subprocess.run(
            cmd2,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if r2.returncode != 0:
            log("⚠️  ffmpeg thumbnail extract failed")
            return False
        return Path(out_img).exists() and Path(out_img).stat().st_size > 0

    def _render_edited_thumbnail(
        self, src_img: str, out_img: str, hook_text: str, log
    ) -> bool:
        try:
            img = Image.open(src_img).convert("RGB")
            w, h = img.size

            # Slight pop for thumbnail look.
            img = ImageEnhance.Contrast(img).enhance(1.08)
            img = ImageEnhance.Color(img).enhance(1.18)

            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            y0 = int(h * 0.70)
            draw.rectangle([(0, y0), (w, h)], fill=(0, 0, 0, 128))

            font_paths = [
                "C:/Windows/Fonts/arialbd.ttf",
                "C:/Windows/Fonts/arial.ttf",
            ]
            font = None
            target_size = max(28, int(h * 0.060))
            for fp in font_paths:
                if Path(fp).exists():
                    try:
                        font = ImageFont.truetype(fp, target_size)
                        break
                    except Exception:
                        continue
            if font is None:
                font = ImageFont.load_default()

            text = (hook_text or "").strip()
            if not text:
                text = "BAT NGO CHUA TUNG THAY"

            # Wrap text to fit width.
            max_w = int(w * 0.92)
            words = text.split()
            lines = []
            cur = ""
            for wd in words:
                test = (cur + " " + wd).strip()
                bw = draw.textbbox((0, 0), test, font=font)[2]
                if bw <= max_w or not cur:
                    cur = test
                else:
                    lines.append(cur)
                    cur = wd
            if cur:
                lines.append(cur)
            lines = lines[:2]

            line_h = draw.textbbox((0, 0), "Ay", font=font)[3] + 6
            total_h = len(lines) * line_h
            y_text = max(y0 + 8, int(h * 0.83 - total_h / 2))

            for ln in lines:
                bb = draw.textbbox((0, 0), ln, font=font)
                tw = bb[2] - bb[0]
                x = int((w - tw) / 2)
                # Stroke-like outline by drawing multiple shadows.
                for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2)]:
                    draw.text((x + dx, y_text + dy), ln, font=font, fill=(0, 0, 0, 220))
                draw.text((x, y_text), ln, font=font, fill=(255, 255, 255, 255))
                y_text += line_h

            out = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
            out.save(out_img, format="JPEG", quality=92)
            return Path(out_img).exists() and Path(out_img).stat().st_size > 0
        except Exception as e:
            log(f"⚠️  thumbnail text overlay failed: {e}")
            return False

    def _generate_thumbnail(
        self,
        session,
        at_sec: float,
        hook_title: str,
        lines: list[str],
        style: str,
        gen_backend: str,
        ollama_model: str,
        log,
    ) -> str:
        src = session.latest_video() or session.source_file
        if not src or not Path(src).exists():
            log("⚠️  Cannot generate thumbnail: source video not found")
            return ""

        raw_frame = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        raw_frame.close()
        edited = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        edited.close()
        if gen_backend == "ollama":
            hook = self._generate_hook_text_ollama(
                hook_title=hook_title,
                lines=lines,
                style=style,
                model=ollama_model,
                log=log,
            )
        else:
            hook = self._pick_hook_text(hook_title, lines, style)
        try:
            if not self._extract_representative_frame(src, at_sec, raw_frame.name, log):
                return ""
            log(f"🎯 Thumbnail hook text: {hook}")
            if self._render_edited_thumbnail(raw_frame.name, edited.name, hook, log):
                return session.save_thumbnail(edited.name)
            return session.save_thumbnail(raw_frame.name)
        finally:
            if os.path.exists(raw_frame.name):
                os.unlink(raw_frame.name)
            if os.path.exists(edited.name):
                os.unlink(edited.name)

    def build_config_widget(self, parent=None):
        w = QWidget(parent)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        rg = QHBoxLayout()
        rg.addWidget(QLabel("Generator:"))
        self._gen_backend_combo = QComboBox()
        self._gen_backend_combo.addItems(
            [
                "Ollama (better quality, slower)",
                "Rule-based (fast)",
            ]
        )
        self._gen_backend_combo.setCurrentIndex(0)
        rg.addWidget(self._gen_backend_combo)
        rg.addStretch()
        v.addLayout(rg)

        rm = QHBoxLayout()
        rm.addWidget(QLabel("Ollama model:"))
        self._ollama_model_combo = QComboBox()
        self._ollama_model_combo.addItems(
            [
                "qwen2",
                "llama3",
                "llama3.1",
                "mistral",
                "gemma2",
            ]
        )
        self._ollama_model_combo.setCurrentText("qwen2")
        rm.addWidget(self._ollama_model_combo)
        rm.addStretch()
        v.addLayout(rm)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Title style:"))
        self._style_combo = QComboBox()
        self._style_combo.addItems(
            [
                "Story (balanced)",
                "Dramatic (hook)",
                "Short (compact)",
            ]
        )
        r1.addWidget(self._style_combo)
        r1.addStretch()
        v.addLayout(r1)

        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Hashtags:"))
        self._max_tags_spin = QSpinBox()
        self._max_tags_spin.setRange(3, 20)
        self._max_tags_spin.setValue(8)
        self._max_tags_spin.setFixedWidth(62)
        r2.addWidget(self._max_tags_spin)
        r2.addWidget(QLabel("tags"))
        r2.addStretch()
        v.addLayout(r2)

        r3 = QHBoxLayout()
        r3.addWidget(QLabel("Thumbnail:"))
        self._thumb_mode_combo = QComboBox()
        self._thumb_mode_combo.addItems(
            [
                "Keep current thumbnail",
                "Auto if missing",
                "Always auto-generate",
            ]
        )
        self._thumb_mode_combo.setCurrentIndex(1)
        r3.addWidget(self._thumb_mode_combo)
        r3.addStretch()
        v.addLayout(r3)

        r4 = QHBoxLayout()
        r4.addWidget(QLabel("Thumb time:"))
        self._thumb_at_spin = QDoubleSpinBox()
        self._thumb_at_spin.setRange(0.0, 36000.0)
        self._thumb_at_spin.setDecimals(1)
        self._thumb_at_spin.setSingleStep(1.0)
        self._thumb_at_spin.setValue(12.0)
        self._thumb_at_spin.setFixedWidth(80)
        r4.addWidget(self._thumb_at_spin)
        r4.addWidget(QLabel("sec"))
        r4.addStretch()
        v.addLayout(r4)

        self._overwrite_chk = QCheckBox("Overwrite existing title/description")
        self._overwrite_chk.setChecked(False)
        v.addWidget(self._overwrite_chk)

        hint = QLabel(
            "Generate metadata + edited thumbnail (hook text overlay), then save to session.json."
        )
        hint.setStyleSheet("color:#666;font-size:10px;")
        hint.setWordWrap(True)
        v.addWidget(hint)
        return w

    def collect_config(self):
        style_key = {
            "Story (balanced)": "story",
            "Dramatic (hook)": "dramatic",
            "Short (compact)": "short",
        }.get(self._style_combo.currentText() if self._style_combo else "", "story")

        thumb_mode = {
            "Keep current thumbnail": "keep",
            "Auto if missing": "auto_if_missing",
            "Always auto-generate": "auto",
        }.get(
            self._thumb_mode_combo.currentText() if self._thumb_mode_combo else "",
            "auto_if_missing",
        )

        gen_backend = "ollama"
        if (
            self._gen_backend_combo
            and "Rule-based" in self._gen_backend_combo.currentText()
        ):
            gen_backend = "rule"

        return {
            "gen_backend": gen_backend,
            "ollama_model": (
                self._ollama_model_combo.currentText()
                if self._ollama_model_combo
                else "qwen2"
            ),
            "style": style_key,
            "max_tags": self._max_tags_spin.value() if self._max_tags_spin else 8,
            "thumb_mode": thumb_mode,
            "thumb_at_sec": (
                self._thumb_at_spin.value() if self._thumb_at_spin else 12.0
            ),
            "overwrite": (
                self._overwrite_chk.isChecked() if self._overwrite_chk else False
            ),
        }
