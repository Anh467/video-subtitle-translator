"""Publish info pipeline (LLM text + thumbnail).

(Package entry lives in ``core.pipeline.step7_publish``.)"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.api_keys import get_key
from core.pipeline.base import BaseStep, CancelledError
from core.pipeline.step7_publish.stop_words import STOP_WORDS
from core.pipeline.step7_publish.zh_thumb_helpers import (
    copy_if_exists,
    has_cjk,
    repaint_thumbnail_remove_zh_overlay,
    translate_block_zh_to_vi,
)


class PublishInfoStep(BaseStep):
    STEP_ID = "step7_publish_info"
    LABEL = "⑦ Publish Info"
    COLOR = "#17576f"
    ENABLED_BY_DEFAULT = False

    def __init__(self):
        self._gen_backend_combo = None
        self._ollama_model_combo = None
        self._gemini_model_combo = None
        self._api_lbl = None
        self._api_edit = None
        self._selected_api_key = ""
        self._style_combo = None
        self._max_tags_spin = None
        self._thumb_mode_combo = None
        self._thumb_at_spin = None
        self._overwrite_chk = None
        self._thumb_bg_label = None
        self._thumb_bg_preview = None
        self._thumb_bg_path = ""
        self._thumb_base_combo = None
        self._thumb_ocr_chk = None
        self._translate_meta_chk = None
        self._base_dir = ""
        self._ollama_last_failed = False

    def set_base_dir(self, base_dir: str):
        self._base_dir = (base_dir or "").strip()
        self._load_shared_thumb_background()
        self._refresh_thumb_bg_preview()

    def _shared_thumb_background_path(self, base_dir: str | None = None) -> str:
        root = (base_dir or self._base_dir or "").strip()
        if not root:
            return ""
        d = Path(root)
        for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            p = d / f"step7_thumb_foreground{ext}"
            if p.exists():
                return str(p)
        return ""

    def _load_shared_thumb_background(self):
        if self._thumb_bg_path and Path(self._thumb_bg_path).exists():
            return
        p = self._shared_thumb_background_path()
        if p:
            self._thumb_bg_path = p
            if self._thumb_bg_label is not None:
                self._thumb_bg_label.setText(Path(p).name)

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
        # Reset per-run Ollama health flag.
        self._ollama_last_failed = False
        ollama_model = config.get("ollama_model", "qwen2")
        gemini_model = config.get("gemini_model", "gemini-2.0-flash")
        api_key = (config.get("api_key") or "").strip() or None
        max_tags = int(config.get("max_tags", 8) or 8)
        _qm = str(config.get("quality_mode") or "high").strip().lower()
        quality_mode = _qm if _qm in ("high", "balanced") else "high"
        thumb_mode = config.get("thumb_mode", "keep")
        thumb_at_sec = float(config.get("thumb_at_sec", 12.0) or 12.0)
        thumb_base_priority = config.get("thumb_base_priority", "session_first")
        thumb_ocr_zh_vi = bool(config.get("thumb_ocr_zh_vi", True))
        ocr_python_exe = (config.get("ocr_python_exe") or "").strip() or None
        translate_session_zh_meta = bool(config.get("translate_session_zh_meta", True))
        overwrite = bool(config.get("overwrite", False))

        lines = [s.translated.strip() for s in segments if s.translated.strip()]
        script_text = " ".join(lines)
        # High: more lines + optional middle/end anchors so the model tracks arc/characters.
        if quality_mode == "high":
            n = len(lines)
            picks: list[str] = []
            picks.extend(lines[:100])
            if n > 120:
                mid = n // 2
                picks.extend(lines[max(0, mid - 10) : mid + 10])
            if n > 30:
                picks.extend(lines[-20:])
            seen: set[str] = set()
            excerpt_lines: list[str] = []
            for ln in picks:
                t = re.sub(r"\s+", " ", (ln or "").strip())
                if not t or t in seen:
                    continue
                seen.add(t)
                excerpt_lines.append(t)
            script_excerpt_for_llm = "\n".join(excerpt_lines)[:16000]
        else:
            script_excerpt_for_llm = "\n".join(lines[:90])[:10000]

        hashtags = self._build_hashtags(script_text, max_tags=max_tags)

        meta_context = ""
        if translate_session_zh_meta and gen_backend in ("ollama", "gemini"):
            parts: list[str] = []
            st = (session.title or "").strip()
            sd = (session.description or "").strip()

            def _meta_translate_fn(prompt: str) -> str:
                if gen_backend == "ollama":
                    return self._ollama_generate(prompt, model=ollama_model)
                return self._gemini_generate(
                    prompt, model=gemini_model, log=log, api_key=api_key
                )

            if has_cjk(st):
                vi = translate_block_zh_to_vi(
                    st, translate_fn=_meta_translate_fn, log=log
                )
                if vi:
                    parts.append(f"PUBLISHER_TITLE_HINT_VI: {vi}")
            if has_cjk(sd):
                vi_d = translate_block_zh_to_vi(
                    sd, translate_fn=_meta_translate_fn, log=log
                )
                if vi_d:
                    parts.append(f"PUBLISHER_DESCRIPTION_HINT_VI: {vi_d}")
            meta_context = "\n".join(parts)
            if meta_context:
                log("🌐 Đã dịch tiêu đề/mô tả session có chữ Trung → gợi ý cho LLM")

        if gen_backend == "ollama":
            title, description = self._generate_title_description_ollama(
                lines=lines,
                hashtags=hashtags,
                style=style,
                model=ollama_model,
                log=log,
                meta_context=meta_context or None,
                quality_mode=quality_mode,
                excerpt_override=script_excerpt_for_llm,
            )
        elif gen_backend == "gemini":
            title, description = self._generate_title_description_gemini(
                lines=lines,
                hashtags=hashtags,
                style=style,
                model=gemini_model,
                api_key=api_key,
                log=log,
                meta_context=meta_context or None,
                quality_mode=quality_mode,
                excerpt_override=script_excerpt_for_llm,
            )
        else:
            title = self._build_title(lines, style=style)
            description = self._build_description(lines, hashtags)

        thumb_gen_backend = gen_backend
        if gen_backend == "ollama" and self._ollama_last_failed:
            thumb_gen_backend = "rule"
            log("⚙️  Ollama unavailable in this run, thumbnail AI calls disabled.")

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

        # Normalize: keep hashtags at END of description, not inside title.
        def _extract_hashtags(text: str) -> list[str]:
            if not (text or "").strip():
                return []
            return re.findall(r"(#[A-Za-z0-9_À-ỹà-ỹ]+)", text, flags=re.UNICODE)

        def _strip_hashtags(text: str) -> str:
            t = re.sub(r"(?:\s|^)(#[A-Za-z0-9_À-ỹà-ỹ]+)", " ", text, flags=re.UNICODE)
            return re.sub(r"\s+", " ", (t or "").strip())

        tags_seen: list[str] = []
        for src in (final_title, final_desc, " ".join(hashtags)):
            for h in _extract_hashtags(src):
                if h not in tags_seen:
                    tags_seen.append(h)

        final_title = _strip_hashtags(final_title)[:95].strip()
        body_desc = (final_desc or "").strip()
        body_desc = _strip_hashtags(body_desc)
        if lines and len(body_desc) < 260:
            # Make description longer/more informative by adding extra context lines.
            extra = " ".join(lines[:6]).strip()
            extra = re.sub(r"\s+", " ", extra)[:600]
            if extra and extra not in body_desc:
                body_desc = (body_desc + "\n\n" + extra).strip()

        tag_line = " ".join(tags_seen[:15]).strip()
        final_desc = (body_desc + (("\n\n" + tag_line) if tag_line else "")).strip()

        session.save_info(final_title, final_desc)
        log("✅ Saved title + description to session.json")

        thumb_saved = ""
        if thumb_mode == "auto" or (
            thumb_mode == "auto_if_missing" and not session.thumbnail
        ):
            bg_from_config = (config.get("thumb_bg_path") or "").strip()
            if not bg_from_config:
                bg_from_config = self._shared_thumb_background_path(
                    str(session.folder.parent)
                )
            if bg_from_config and Path(bg_from_config).exists():
                try:
                    saved_bg = session.save_thumb_background(bg_from_config)
                    self._thumb_bg_path = saved_bg
                    log(f"🧱 Saved Step 7 background layer: {Path(saved_bg).name}")
                except Exception as e:
                    log(f"⚠️  Cannot save Step 7 background layer: {e}")
            bg_layer = session.thumb_background

            thumb_saved = self._generate_thumbnail(
                session,
                at_sec=thumb_at_sec,
                hook_title=final_title or title,
                segments=segments,
                lines=lines,
                style=style,
                gen_backend=thumb_gen_backend,
                ollama_model=ollama_model,
                gemini_model=gemini_model,
                api_key=api_key,
                foreground_bg=bg_layer,
                log=log,
                thumb_base_priority=thumb_base_priority,
                thumb_ocr_zh_vi=thumb_ocr_zh_vi,
                ocr_python_exe=ocr_python_exe,
                quality_mode=quality_mode,
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
            "thumb_background": session.thumb_background,
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

    def _ollama_generate(
        self,
        prompt: str,
        model: str = "qwen2",
        *,
        num_predict: int = 700,
        temperature: float = 0.45,
    ) -> str:
        if self._ollama_last_failed:
            raise RuntimeError("Ollama temporarily disabled in this run")
        last_err = None
        for attempt in range(1, 4):
            payload = json.dumps(
                {
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        "top_p": 0.9,
                        "num_predict": int(num_predict),
                        "repeat_penalty": 1.1,
                    },
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                "http://localhost:11434/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=135) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    self._ollama_last_failed = False
                    txt = (data.get("response") or "").strip()
                    if not txt:
                        raise RuntimeError("empty response")
                    return txt
            except Exception as e:
                last_err = e
                time.sleep(0.45 * attempt)
        self._ollama_last_failed = True
        raise RuntimeError(f"Ollama failed after retries: {last_err}")

    def _gemini_client(self, api_key: str):
        try:
            from google import genai
        except Exception as e:
            raise RuntimeError(
                "Google GenAI SDK missing. Run: pip install google-genai"
            ) from e
        return genai.Client(api_key=api_key)

    def _gemini_generate(
        self,
        prompt: str,
        model: str,
        log,
        retries: int = 3,
        api_key: str | None = None,
    ) -> str:
        key = (
            (api_key or "").strip()
            or get_key("gemini")
            or os.environ.get("GEMINI_API_KEY", "")
        )
        if not key:
            raise RuntimeError(
                "Gemini API key missing. Add GEMINI_API_KEY in API Keys Manager."
            )
        client = self._gemini_client(key)
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                resp = client.models.generate_content(model=model, contents=prompt)
                txt = (getattr(resp, "text", None) or "").strip()
                if txt:
                    return txt
                raise RuntimeError("empty response")
            except Exception as e:
                last_err = e
                if log:
                    log(f"⚠️  Gemini retry {attempt}/{retries}: {e}")
                time.sleep(0.6 * attempt)
        raise RuntimeError(f"Gemini failed after retries: {last_err}")

    def _generate_title_description_ollama(
        self,
        lines: list[str],
        hashtags: list[str],
        style: str,
        model: str,
        log,
        meta_context: str | None = None,
        quality_mode: str = "balanced",
        excerpt_override: str | None = None,
    ) -> tuple[str, str]:
        excerpt = (excerpt_override or "\n".join(lines[:80]))[:14000]
        hashtag_line = " ".join(hashtags[:10])

        style_hint = {
            "dramatic": "tone dramatic, high tension",
            "short": "tone concise, direct",
            "story": "tone storytelling, emotional",
        }.get(style, "tone storytelling")

        extra = ""
        if (meta_context or "").strip():
            extra = (
                "\nORIGINAL_PUBLISHER_METADATA_TRANSLATED_VI (context only, merge naturally):\n"
                f"{meta_context.strip()}\n"
            )

        quality_hint = (
            "QUALITY HIGH (mandatory):\n"
            "- Use correct Vietnamese spelling with full diacritics (ấ ệ ử …).\n"
            "- Prefer concrete nouns/events from SCRIPT (characters, twists, stakes); do not invent unrelated facts.\n"
            "- Sounds human: varied sentence openings, zero template phrases.\n\n"
            if quality_mode == "high"
            else ""
        )
        if quality_mode == "high":
            desc_rule = (
                "Description: 6-9 lines, Vietnamese; first line strongest hook grounded in SCRIPT; middle lines raise stakes; "
                "one suspense question tied to SCRIPT; subtle CTA (e.g. xem đến cuối — NOT 'đăng ký kênh' spam); "
                "400-950 characters before hashtags.\n"
            )
        else:
            desc_rule = (
                "Description: 4-6 short lines, storytelling flow: hook -> escalation -> suspense question -> CTA.\n"
            )
        prompt = (
            "You are a Vietnamese YouTube strategist. Audience: Vietnamese. Output Vietnamese only.\n"
            "Create truthful, high-CTR metadata that matches SCRIPT (same story/conflict/outcome cues).\n"
            f"Style: {style_hint}.\n\n"
            "Rules:\n"
            "- Title: 52-88 chars, natural Vietnamese, full diacritics, NO hashtag #, NO emoji.\n"
            f"- {desc_rule.strip()}\n"
            "- Do NOT mention 'trích từ script', 'tối ưu SEO', or meta talk about uploading.\n"
            "- If SUGGESTED_HASHTAGS provided, append them once at the very end of description (after a blank line).\n"
            '- Output JSON only: {"title":"...","description":"..."}\n\n'
            f"{quality_hint}"
            f"SCRIPT:\n{excerpt}\n\n"
            f"SUGGESTED_HASHTAGS: {hashtag_line}\n"
            f"{extra}"
        )

        try:
            log(f"🤖 Generating title/description via Ollama ({model})...")
            last_err = None
            for attempt in range(1, 4 if quality_mode == "high" else 2):
                raw = self._ollama_generate(
                    prompt,
                    model=model,
                    num_predict=1100 if quality_mode == "high" else 750,
                    temperature=0.38 if quality_mode == "high" else 0.45,
                )
                obj = self._extract_json_object(raw)
                title = str(obj.get("title", "")).strip()
                desc = str(obj.get("description", "")).strip()

                if not title or not desc:
                    last_err = RuntimeError("Ollama JSON missing title/description")
                    continue

                bad_markers = (
                    "Noi dung duoc trich",
                    "toi uu de dang dang tai",
                    "nội dung được trích",
                    "tối ưu để đăng tải",
                )
                min_desc = 360 if quality_mode == "high" else 140
                if len(desc) < min_desc or any(
                    m.lower() in desc.lower() for m in bad_markers
                ):
                    last_err = RuntimeError("Ollama description quality too low")
                    continue
                if "#" in title:
                    last_err = RuntimeError("Ollama title must not contain hashtags")
                    continue

                if quality_mode == "high" and re.search(r"\b(đăng ký|subscribe)\b", desc, re.IGNORECASE):
                    # Avoid spammy CTA markers in high-quality mode.
                    last_err = RuntimeError("Ollama description includes spammy CTA")
                    continue

                if hashtags:
                    hline = " ".join(hashtags)
                    if hline not in desc:
                        desc = f"{desc}\n\n{hline}"

                return title[:90], desc
            raise RuntimeError(f"Ollama quality retries exhausted: {last_err}")
        except Exception as e:
            log(f"⚠️  Ollama generation failed, fallback to rule-based: {e}")
            title = self._build_title(lines, style=style)
            desc = self._build_description(lines, hashtags)
            return title, desc

    def _generate_title_description_gemini(
        self,
        lines: list[str],
        hashtags: list[str],
        style: str,
        model: str,
        api_key: str | None,
        log,
        meta_context: str | None = None,
        quality_mode: str = "balanced",
        excerpt_override: str | None = None,
    ) -> tuple[str, str]:
        excerpt = (excerpt_override or "\n".join(lines[:90]))[:14000]
        hashtag_line = " ".join(hashtags[:10])
        style_hint = {
            "dramatic": "tone dramatic, high tension",
            "short": "tone concise, direct",
            "story": "tone storytelling, emotional",
        }.get(style, "tone storytelling")
        extra = ""
        if (meta_context or "").strip():
            extra = (
                "\nORIGINAL_PUBLISHER_METADATA_TRANSLATED_VI (context only, merge naturally):\n"
                f"{meta_context.strip()}\n"
            )
        if quality_mode == "high":
            desc_rule_g = (
                "Description: 6-10 lines, 400-1100 chars before hashtags; arc: hook -> stakes -> twist/suspense question -> "
                "soft CTA (xem hết clip); full Vietnamese diacritics; zero filler about SEO/uploading.\n"
            )
        else:
            desc_rule_g = (
                "Description: 5-8 short lines: hook -> escalation -> suspense question -> CTA.\n"
            )
        quality_hint = (
            "QUALITY HIGH: ground every claim in SCRIPT; no invented characters/events.\n"
            if quality_mode == "high"
            else ""
        )
        prompt = (
            "You are a Vietnamese YouTube strategist. Metadata must match SCRIPT (same story beats).\n"
            f"Style: {style_hint}.\n"
            "Rules:\n"
            '- Output JSON only: {"title":"...","description":"..."}\n'
            "- Title: 52-88 chars, Vietnamese with diacritics, NO #hashtag, NO emoji, truthful.\n"
            f"- {desc_rule_g.strip()}\n"
            "- Append SUGGESTED_HASHTAGS once at the very end of description (blank line before tags).\n\n"
            f"{quality_hint}"
            f"SCRIPT:\n{excerpt}\n\n"
            f"SUGGESTED_HASHTAGS: {hashtag_line}\n"
            f"{extra}"
        )
        try:
            log(f"✨ Generating title/description via Gemini ({model})...")
            last_ge = None
            for _gattempt in range(1, 4 if quality_mode == "high" else 2):
                raw = self._gemini_generate(
                    prompt,
                    model=model,
                    log=log,
                    api_key=api_key,
                    retries=6 if quality_mode == "high" else 3,
                )
                obj = self._extract_json_object(raw)
                title = str(obj.get("title", "")).strip()
                desc = str(obj.get("description", "")).strip()
                min_d = 360 if quality_mode == "high" else 160
                if (
                    not title
                    or not desc
                    or len(desc) < min_d
                    or ("#" in title)
                ):
                    last_ge = RuntimeError("Gemini JSON missing/low-quality title/description")
                    continue
                if hashtags:
                    hline = " ".join(hashtags)
                    if hline not in desc:
                        desc = f"{desc}\n\n{hline}"
                return title[:90], desc
            raise RuntimeError(str(last_ge or "Gemini quality retries exhausted"))
        except Exception as e:
            log(f"⚠️  Gemini generation failed, fallback to rule-based: {e}")
            return self._build_title(lines, style=style), self._build_description(
                lines, hashtags
            )

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
        base = base[:48].strip(" .,!?:;-")

        if style == "dramatic":
            prefix = "SUC THAT GAY SOC"
        elif style == "short":
            prefix = "BAN KHONG THE NGO"
        else:
            prefix = "CU LAT NGOAN MUC"

        if not base:
            return prefix

        words = re.findall(r"[A-Za-z0-9À-ỹà-ỹ]+", base)
        core = " ".join(words[:2]).upper() if words else "BAT NGO"
        out = f"{prefix} {core}".strip()
        out_words = out.split()
        if len(out_words) > 5:
            out = " ".join(out_words[:5])
        if len(out_words) < 4:
            out = "SUC THAT QUA BAT NGO"
        return out

    def _hook_llm_script_context(
        self,
        lines: list[str],
        segments,
        center_sec: float,
        quality_mode: str,
    ) -> str:
        xs = [x.strip() for x in (lines or []) if (x or "").strip()]
        if not xs:
            return ""
        if quality_mode != "high":
            return "SCRIPT_EXCERPT:\n" + " ".join(xs[:12])[:2200]

        blocks: list[str] = []
        blocks.append("OPENING:\n" + "\n".join(xs[:12])[:3200])
        if len(xs) > 16:
            mid = len(xs) // 2
            blocks.append(
                "MID:\n" + "\n".join(xs[max(0, mid - 4) : mid + 4])[:3200]
            )
        if len(xs) > 10:
            blocks.append("ENDING:\n" + "\n".join(xs[-8:])[:2800])
        near: list[str] = []
        try:
            lo = float(center_sec) - 55.0
            hi = float(center_sec) + 55.0
        except Exception:
            lo, hi = 0.0, 0.0
        for s in segments or []:
            try:
                st = float(getattr(s, "start", 0.0) or 0.0)
            except Exception:
                st = 0.0
            if st < lo or st > hi:
                continue
            tx = (getattr(s, "translated", "") or "").strip()
            if not tx:
                continue
            near.append(re.sub(r"\s+", " ", tx))
            if len(near) >= 12:
                break
        if near:
            blocks.append("AROUND_THUMB_TIME:\n" + "\n".join(near)[:4000])
        return "\n\n".join(blocks).strip()[:9500]

    def _generate_hook_text_ollama(
        self,
        hook_title: str,
        lines: list[str],
        style: str,
        model: str,
        log,
        *,
        script_context: str = "",
        quality_mode: str = "high",
    ) -> str:
        ctx = (script_context or "").strip() or (
            "SCRIPT_EXCERPT:\n" + " ".join((lines or [])[:10])[:2000]
        )
        style_hint = {
            "dramatic": "dramatic",
            "short": "very short",
            "story": "storytelling",
        }.get(style, "storytelling")
        qx = (
            "HOOK QUALITY: pick ONE concrete conflict/reveal/stake from SCRIPT (names/places ok). "
            "Do not invent events not implied by SCRIPT.\n"
            if quality_mode == "high"
            else ""
        )
        prompt = (
            "You write Vietnamese YouTube thumbnail overlay text (will be shown in UPPERCASE on image).\n"
            f"Style: {style_hint}.\n"
            f"{qx}"
            "Rules:\n"
            "- Exactly 4 or 5 words (never 3 or 6+)\n"
            "- Natural Vietnamese with full diacritics in output (we uppercase later)\n"
            "- High emotional punch but truthful vs SCRIPT\n"
            "- No hashtags, emoji, quotation marks, or line breaks\n"
            "- Output plain text only, one line\n\n"
            f"TITLE_FOR_CONTEXT: {hook_title}\n\n"
            f"{ctx}\n"
        )
        attempts = 3 if quality_mode == "high" else 2
        last_exc: Exception | None = None
        for _att in range(1, attempts + 1):
            try:
                raw = self._ollama_generate(
                    prompt,
                    model=model,
                    num_predict=120 if quality_mode == "high" else 96,
                    temperature=0.35 if quality_mode == "high" else 0.45,
                )
                txt = re.sub(r"\s+", " ", (raw or "").strip())
                txt = re.sub(r"[^A-Za-z0-9À-ỹà-ỹ\s:!?-]", "", txt)
                if not txt:
                    raise RuntimeError("empty hook")
                words = [w for w in txt.split() if w]
                if len(words) > 5:
                    words = words[:5]
                if len(words) < 4:
                    raise RuntimeError("hook word count invalid")
                txt = " ".join(words)
                return txt.upper()
            except Exception as e:
                last_exc = e
                continue
        log(f"⚠️  Ollama hook fallback: {last_exc}")
        return self._pick_hook_text(hook_title, lines, style)

    def _generate_hook_text_gemini(
        self,
        hook_title: str,
        lines: list[str],
        style: str,
        model: str,
        api_key: str | None,
        log,
        *,
        script_context: str = "",
        quality_mode: str = "high",
    ) -> str:
        ctx = (script_context or "").strip() or (
            "SCRIPT_EXCERPT:\n" + " ".join((lines or [])[:10])[:2000]
        )
        style_hint = {
            "dramatic": "dramatic",
            "short": "very short",
            "story": "storytelling",
        }.get(style, "storytelling")
        qx = (
            "Faithful to SCRIPT_CONTEXT: one concrete stakes/reveal, no fabricated plot.\n"
            if quality_mode == "high"
            else ""
        )
        prompt = (
            "Vietnamese YouTube thumbnail overlay line.\n"
            f"Style: {style_hint}.\n"
            f"{qx}"
            "Rules: exactly 4 or 5 words; Vietnamese with diacritics (uppercase optional); "
            "no emoji/hashtag/quotes/newlines; plain text one line.\n\n"
            f"TITLE_FOR_CONTEXT: {hook_title}\n\n"
            f"{ctx}\n"
        )
        attempts = 3 if quality_mode == "high" else 2
        last_exc: Exception | None = None
        for _att in range(1, attempts + 1):
            try:
                raw = self._gemini_generate(
                    prompt,
                    model=model,
                    log=log,
                    api_key=api_key,
                    retries=4 if quality_mode == "high" else 3,
                )
                txt = re.sub(r"\s+", " ", (raw or "").strip())
                txt = re.sub(r"[^A-Za-z0-9À-ỹà-ỹ\s:!?-]", "", txt)
                words = [w for w in txt.split() if w]
                if len(words) < 4:
                    raise RuntimeError("hook too short")
                if len(words) > 5:
                    words = words[:5]
                return " ".join(words).upper()
            except Exception as e:
                last_exc = e
                continue
        log(f"⚠️  Gemini hook fallback: {last_exc}")
        return self._pick_hook_text(hook_title, lines, style)

    def _pick_thumb_timestamp_ollama(
        self,
        segments,
        fallback_sec: float,
        model: str,
        log,
    ) -> float:
        items = []
        for s in segments[:120]:
            txt = (getattr(s, "translated", "") or "").strip()
            if not txt:
                continue
            st = float(getattr(s, "start", 0.0) or 0.0)
            en = float(getattr(s, "end", st) or st)
            items.append(f"{st:.1f}-{en:.1f}: {txt}")
        if not items:
            return max(0.0, float(fallback_sec or 0.0))

        prompt = (
            "You are selecting the best thumbnail moment for a Vietnamese short video.\n"
            "Given timed translated segments, choose one timestamp where visual action is likely strongest and relevant.\n"
            "Rules:\n"
            "- Prefer moments with conflict, reveal, surprise, payoff, or key turning points\n"
            "- Avoid intro/outro and empty moments\n"
            '- Return JSON only: {"second": number, "reason": string}\n\n'
            "SEGMENTS:\n" + "\n".join(items)
        )
        try:
            raw = self._ollama_generate(prompt, model=model)
            obj = self._extract_json_object(raw)
            sec = float(obj.get("second", fallback_sec))
            if sec < 0:
                sec = 0.0
            log(f"🧭 AI picked thumbnail time: {sec:.1f}s")
            return sec
        except Exception as e:
            log(f"⚠️  AI thumb timing fallback: {e}")
            return max(0.0, float(fallback_sec or 0.0))

    def _pick_thumb_timestamp_gemini(
        self,
        segments,
        fallback_sec: float,
        model: str,
        api_key: str | None,
        log,
    ) -> float:
        items = []
        for s in segments[:120]:
            txt = (getattr(s, "translated", "") or "").strip()
            if not txt:
                continue
            st = float(getattr(s, "start", 0.0) or 0.0)
            en = float(getattr(s, "end", st) or st)
            items.append(f"{st:.1f}-{en:.1f}: {txt}")
        if not items:
            return max(0.0, float(fallback_sec or 0.0))
        prompt = (
            "Choose the best thumbnail timestamp from timed Vietnamese script segments.\n"
            "Pick a moment likely to have strongest visual action and content relevance.\n"
            'Output JSON only: {"second": number, "reason": string}\n\n'
            "SEGMENTS:\n" + "\n".join(items)
        )
        try:
            raw = self._gemini_generate(prompt, model=model, log=log, api_key=api_key)
            obj = self._extract_json_object(raw)
            sec = float(obj.get("second", fallback_sec))
            if sec < 0:
                sec = 0.0
            log(f"🧭 Gemini picked thumbnail time: {sec:.1f}s")
            return sec
        except Exception as e:
            log(f"⚠️  Gemini thumb timing fallback: {e}")
            return max(0.0, float(fallback_sec or 0.0))

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
        self,
        src_img: str,
        out_img: str,
        hook_text: str,
        foreground_bg: str,
        log,
    ) -> bool:
        try:
            base = Image.open(src_img).convert("RGB")
            w, h = base.size

            # Slight pop for thumbnail look.
            base = ImageEnhance.Contrast(base).enhance(1.08)
            base = ImageEnhance.Color(base).enhance(1.18)

            canvas = base.convert("RGBA")
            if foreground_bg and Path(foreground_bg).exists():
                fg = Image.open(foreground_bg).convert("RGBA")
                bg_ratio = fg.width / max(1, fg.height)
                out_ratio = w / max(1, h)
                if bg_ratio > out_ratio:
                    nh = h
                    nw = int(h * bg_ratio)
                else:
                    nw = w
                    nh = int(w / max(1e-6, bg_ratio))
                fg = fg.resize((max(1, nw), max(1, nh)), Image.Resampling.LANCZOS)
                x = (nw - w) // 2
                y = (nh - h) // 2
                fg = fg.crop((x, y, x + w, y + h))
                canvas = Image.alpha_composite(canvas, fg)

            overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)

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
            lines = lines[:1]

            line_h = draw.textbbox((0, 0), "Ay", font=font)[3] + 6
            total_h = len(lines) * line_h
            y_text = int(h * 0.80 - total_h / 2)

            for ln in lines:
                bb = draw.textbbox((0, 0), ln, font=font)
                tw = bb[2] - bb[0]
                x = int((w - tw) / 2)
                # Stroke-like outline by drawing multiple shadows.
                for dx, dy in [
                    (-3, 0),
                    (3, 0),
                    (0, -3),
                    (0, 3),
                    (-3, -3),
                    (3, 3),
                    (-2, 2),
                    (2, -2),
                ]:
                    draw.text((x + dx, y_text + dy), ln, font=font, fill=(0, 0, 0, 220))
                draw.text((x, y_text), ln, font=font, fill=(255, 255, 255, 255))
                y_text += line_h

            out = Image.alpha_composite(canvas, overlay).convert("RGB")
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
        segments,
        lines: list[str],
        style: str,
        gen_backend: str,
        ollama_model: str,
        gemini_model: str,
        api_key: str | None,
        foreground_bg: str,
        log,
        thumb_base_priority: str = "video_only",
        thumb_ocr_zh_vi: bool = False,
        ocr_python_exe: str | None = None,
        quality_mode: str = "high",
    ) -> str:
        src = session.latest_video() or session.source_file
        if not src or not Path(src).exists():
            log("⚠️  Cannot generate thumbnail: source video not found")
            return ""

        raw_frame = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        raw_frame.close()
        edited = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        edited.close()
        ocr_tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        ocr_tmp.close()
        if gen_backend == "ollama":
            pick_sec = self._pick_thumb_timestamp_ollama(
                segments=segments,
                fallback_sec=at_sec,
                model=ollama_model,
                log=log,
            )
            hook_ctx = self._hook_llm_script_context(
                lines, segments, pick_sec, quality_mode
            )
            hook = self._generate_hook_text_ollama(
                hook_title=hook_title,
                lines=lines,
                style=style,
                model=ollama_model,
                log=log,
                script_context=hook_ctx,
                quality_mode=quality_mode,
            )
        elif gen_backend == "gemini":
            pick_sec = self._pick_thumb_timestamp_gemini(
                segments=segments,
                fallback_sec=at_sec,
                model=gemini_model,
                api_key=api_key,
                log=log,
            )
            hook_ctx = self._hook_llm_script_context(
                lines, segments, pick_sec, quality_mode
            )
            hook = self._generate_hook_text_gemini(
                hook_title=hook_title,
                lines=lines,
                style=style,
                model=gemini_model,
                api_key=api_key,
                log=log,
                script_context=hook_ctx,
                quality_mode=quality_mode,
            )
        else:
            pick_sec = at_sec
            hook = self._pick_hook_text(hook_title, lines, style)

        translate_backend = gen_backend
        if gen_backend == "ollama" and self._ollama_last_failed:
            translate_backend = "rule"

        def _step2_context_sample(all_lines: list[str]) -> str:
            """6 first + 2 middle + 2 last (dedup), to guide OCR translation."""
            xs = [x.strip() for x in (all_lines or []) if (x or "").strip()]
            if not xs:
                return ""
            picks: list[str] = []
            picks += xs[:6]
            mid = len(xs) // 2
            if len(xs) > 8:
                picks += xs[max(0, mid - 1) : mid + 1]
            picks += xs[-2:]
            out: list[str] = []
            seen = set()
            for s in picks:
                s2 = re.sub(r"\s+", " ", s).strip()
                if not s2 or s2 in seen:
                    continue
                seen.add(s2)
                out.append(s2)
            return "\n".join(out)[:1800]

        def _step2_context_near_timestamp(segs, center_sec: float, window_sec: float = 60.0) -> str:
            """Pick translated lines near thumbnail timestamp (±window_sec)."""
            out: list[str] = []
            try:
                lo = float(center_sec) - float(window_sec)
                hi = float(center_sec) + float(window_sec)
            except Exception:
                lo, hi = 0.0, 0.0
            for s in segs or []:
                try:
                    st = float(getattr(s, "start", 0.0) or 0.0)
                except Exception:
                    st = 0.0
                if st < lo or st > hi:
                    continue
                tx = (getattr(s, "translated", "") or "").strip()
                if not tx:
                    continue
                out.append(re.sub(r"\s+", " ", tx))
                if len(out) >= 12:
                    break
            return "\n".join(out)[:2200]

        try:
            have_base = False
            if thumb_base_priority == "session_first":
                sess_thumb = (session.thumbnail or "").strip()
                if sess_thumb and copy_if_exists(sess_thumb, raw_frame.name):
                    have_base = True
                    log("🖼️  Thumb base: đang dùng thumbnail có sẵn trong session")
            if not have_base:
                if not self._extract_representative_frame(
                    src, pick_sec, raw_frame.name, log
                ):
                    return ""
                log("🖼️  Thumb base: trích khung hình từ video")

            frame_for_render = raw_frame.name
            if thumb_ocr_zh_vi:
                # Build OCR context: metadata + global script sample + local (timestamp) script sample.
                ctx_parts: list[str] = []
                meta = "\n".join(
                    x
                    for x in [
                        (hook_title or "").strip(),
                        (getattr(session, "title", "") or "").strip(),
                        (getattr(session, "description", "") or "").strip(),
                    ]
                    if x
                ).strip()
                if meta:
                    ctx_parts.append(meta)
                global_sample = _step2_context_sample(lines)
                if global_sample:
                    ctx_parts.append("STEP2_SCRIPT_SAMPLE_GLOBAL:\n" + global_sample)
                local_sample = _step2_context_near_timestamp(segments, pick_sec, window_sec=60.0)
                if local_sample:
                    ctx_parts.append("STEP2_SCRIPT_SAMPLE_NEAR_THUMB:\n" + local_sample)
                ocr_context = "\n\n".join(ctx_parts).strip()[:6000]

                if translate_backend == "ollama":

                    def _ocr_translate_fn(p: str) -> str:
                        return self._ollama_generate(p, model=ollama_model)

                    if repaint_thumbnail_remove_zh_overlay(
                        raw_frame.name,
                        ocr_tmp.name,
                        context=ocr_context,
                        ocr_python_exe=ocr_python_exe,
                        translate_fn=_ocr_translate_fn,
                        log=log,
                    ):
                        frame_for_render = ocr_tmp.name
                elif translate_backend == "gemini":

                    def _ocr_translate_fn_g(p: str) -> str:
                        return self._gemini_generate(
                            p, model=gemini_model, log=log, api_key=api_key
                        )

                    if repaint_thumbnail_remove_zh_overlay(
                        raw_frame.name,
                        ocr_tmp.name,
                        context=ocr_context,
                        ocr_python_exe=ocr_python_exe,
                        translate_fn=_ocr_translate_fn_g,
                        log=log,
                    ):
                        frame_for_render = ocr_tmp.name
                else:
                    log(
                        "⚠️  OCR zh→vi cần Ollama hoặc Gemini; bỏ qua repaint (generator: rule)."
                    )

            log(f"🎯 Thumbnail hook text: {hook}")
            if self._render_edited_thumbnail(
                frame_for_render,
                edited.name,
                hook,
                foreground_bg,
                log,
            ):
                return session.save_thumbnail(edited.name)
            return session.save_thumbnail(frame_for_render)
        finally:
            for pth in (raw_frame.name, edited.name, ocr_tmp.name):
                if os.path.exists(pth):
                    try:
                        os.unlink(pth)
                    except OSError:
                        pass

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
                "Ollama (local)",
                "Gemini (Google API)",
                "Rule-based (fast)",
            ]
        )
        self._gen_backend_combo.setCurrentIndex(0)
        self._gen_backend_combo.currentIndexChanged.connect(self._on_backend_changed)
        self._gen_backend_combo.currentTextChanged.connect(self._on_backend_changed)
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

        rgm = QHBoxLayout()
        rgm.addWidget(QLabel("Gemini model:"))
        self._gemini_model_combo = QComboBox()
        self._gemini_model_combo.addItems(
            [
                "gemini-2.0-flash",
                "gemini-1.5-flash",
                "gemini-1.5-pro",
            ]
        )
        self._gemini_model_combo.setCurrentText("gemini-2.0-flash")
        rgm.addWidget(self._gemini_model_combo)
        rgm.addStretch()
        v.addLayout(rgm)

        self._api_lbl = QLabel("API Key:")
        self._api_edit = QLineEdit()
        self._api_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_edit.setPlaceholderText("Gemini API key — aistudio.google.com")
        self._api_edit.textChanged.connect(
            lambda t: setattr(self, "_selected_api_key", t.strip())
        )
        self._api_lbl.setVisible(False)
        self._api_edit.setVisible(False)
        v.addWidget(self._api_lbl)
        v.addWidget(self._api_edit)

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

        rq = QHBoxLayout()
        rq.addWidget(QLabel("Quality:"))
        self._quality_combo = QComboBox()
        self._quality_combo.addItems(["Balanced (fast)", "High (slower, better)"])
        self._quality_combo.setCurrentIndex(1)
        rq.addWidget(self._quality_combo)
        rq.addStretch()
        v.addLayout(rq)

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

        rb = QHBoxLayout()
        rb.addWidget(QLabel("Thumb base:"))
        self._thumb_base_combo = QComboBox()
        self._thumb_base_combo.addItems(
            [
                "From video only",
                "Prefer session thumbnail, else video",
            ]
        )
        self._thumb_base_combo.setCurrentIndex(1)
        rb.addWidget(self._thumb_base_combo)
        rb.addStretch()
        v.addLayout(rb)

        self._thumb_ocr_chk = QCheckBox(
            "OCR: repaint Chinese text on thumb → Vietnamese (PaddleOCR, needs pip install)"
        )
        self._thumb_ocr_chk.setChecked(True)
        v.addWidget(self._thumb_ocr_chk)

        r_ocr = QHBoxLayout()
        r_ocr.addWidget(QLabel("OCR Python (optional):"))
        self._ocr_python_edit = QLineEdit()
        self._ocr_python_edit.setPlaceholderText(
            "VD: C:\\Users\\van12\\AppData\\Local\\Programs\\Python\\Python310\\python.exe"
        )
        self._ocr_python_edit.setToolTip(
            "Nếu app chạy Python 3.13 (NumPy 2.x), PaddleOCR có thể không chạy trong-process.\n"
            "Điền đường dẫn python.exe của env Python 3.10/3.11 đã cài paddleocr để chạy OCR qua subprocess.\n"
            "Hoặc set env var SUBSYNC_OCR_PYTHON."
        )
        r_ocr.addWidget(self._ocr_python_edit, stretch=1)
        v.addLayout(r_ocr)

        self._translate_meta_chk = QCheckBox(
            "If session title/description has Chinese, translate to Vi for LLM context"
        )
        self._translate_meta_chk.setChecked(True)
        v.addWidget(self._translate_meta_chk)

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

        r5 = QHBoxLayout()
        r5.addWidget(QLabel("Foreground image:"))
        self._thumb_bg_label = QLabel("No file selected")
        self._thumb_bg_label.setStyleSheet("color:#889; font-size:10px;")
        self._thumb_bg_label.setMinimumWidth(170)
        r5.addWidget(self._thumb_bg_label)

        up_btn = QPushButton("Upload")
        up_btn.setFixedHeight(24)
        up_btn.clicked.connect(self._pick_thumb_background)
        r5.addWidget(up_btn)

        clr_btn = QPushButton("Clear")
        clr_btn.setFixedHeight(24)
        clr_btn.clicked.connect(self._clear_thumb_background)
        r5.addWidget(clr_btn)
        r5.addStretch()
        v.addLayout(r5)

        self._thumb_bg_preview = QLabel("no preview")
        self._thumb_bg_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_bg_preview.setFixedSize(180, 100)
        self._thumb_bg_preview.setStyleSheet(
            "background:#0a0a1a;border:1px solid #2a3a5a;border-radius:5px;color:#666;font-size:10px;"
        )
        v.addWidget(self._thumb_bg_preview)

        self._overwrite_chk = QCheckBox("Overwrite existing title/description")
        self._overwrite_chk.setChecked(False)
        v.addWidget(self._overwrite_chk)

        hint = QLabel(
            "Thumbnail layers: frame from video (back) + uploaded foreground image + AI hook text."
        )
        hint.setStyleSheet("color:#666;font-size:10px;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        self._load_shared_thumb_background()
        self._on_backend_changed()
        self._refresh_thumb_bg_preview()
        return w

    def _on_backend_changed(self, *_args):
        text = self._gen_backend_combo.currentText() if self._gen_backend_combo else ""
        is_gemini = "Gemini" in text
        is_ollama = "Ollama" in text

        if self._ollama_model_combo is not None:
            self._ollama_model_combo.setEnabled(is_ollama)
        if self._gemini_model_combo is not None:
            self._gemini_model_combo.setEnabled(is_gemini)

        if self._api_lbl is not None:
            self._api_lbl.setVisible(is_gemini)
        if self._api_edit is not None:
            self._api_edit.setVisible(is_gemini)
        if (
            is_gemini
            and self._api_edit is not None
            and not self._api_edit.text().strip()
        ):
            key = get_key("gemini") or os.environ.get("GEMINI_API_KEY", "")
            if key:
                self._api_edit.blockSignals(True)
                self._api_edit.setText(key)
                self._api_edit.blockSignals(False)
                self._selected_api_key = key.strip()

    def _refresh_thumb_bg_preview(self):
        if self._thumb_bg_label is not None:
            if self._thumb_bg_path and Path(self._thumb_bg_path).exists():
                self._thumb_bg_label.setText(Path(self._thumb_bg_path).name)
            else:
                self._thumb_bg_label.setText("No file selected")

        if self._thumb_bg_preview is None:
            return

        if self._thumb_bg_path and Path(self._thumb_bg_path).exists():
            pix = QPixmap(self._thumb_bg_path).scaled(
                180,
                100,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._thumb_bg_preview.setPixmap(pix)
            self._thumb_bg_preview.setText("")
            self._thumb_bg_preview.setStyleSheet(
                "background:#0a0a1a;border:1px solid #3a5a3a;border-radius:5px;"
            )
        else:
            self._thumb_bg_preview.clear()
            self._thumb_bg_preview.setText("no preview")
            self._thumb_bg_preview.setStyleSheet(
                "background:#0a0a1a;border:1px solid #2a3a5a;border-radius:5px;color:#666;font-size:10px;"
            )

    def _pick_thumb_background(self):
        path, _ = QFileDialog.getOpenFileName(
            None,
            "Select foreground image for Step 7 thumbnail",
            "",
            "Images (*.jpg *.jpeg *.png *.webp *.bmp)",
        )
        if not path:
            return
        chosen = path
        if self._base_dir:
            import shutil

            src = Path(path)
            dst = (
                Path(self._base_dir)
                / f"step7_thumb_foreground{src.suffix.lower() or '.png'}"
            )
            Path(self._base_dir).mkdir(parents=True, exist_ok=True)
            for old in Path(self._base_dir).glob("step7_thumb_foreground.*"):
                if old.resolve() != dst.resolve():
                    old.unlink(missing_ok=True)
            shutil.copy2(src, dst)
            chosen = str(dst)
        self._thumb_bg_path = chosen
        self._refresh_thumb_bg_preview()

    def _clear_thumb_background(self):
        shared = self._shared_thumb_background_path()
        if shared:
            try:
                Path(shared).unlink(missing_ok=True)
            except Exception:
                pass
        self._thumb_bg_path = ""
        self._refresh_thumb_bg_preview()

    def apply_config(self, config: dict) -> None:
        if not config:
            return
        _BE_LABEL = {
            "ollama": "Ollama (local)",
            "gemini": "Gemini (Google API)",
            "rule": "Rule-based (fast)",
        }
        _STYLE_LABEL = {
            "story": "Story (balanced)",
            "dramatic": "Dramatic (hook)",
            "short": "Short (compact)",
        }
        _THUMB_LABEL = {
            "keep": "Keep current thumbnail",
            "auto_if_missing": "Auto if missing",
            "auto": "Always auto-generate",
        }
        _BASE_LABEL = {
            "video_only": "From video only",
            "session_first": "Prefer session thumbnail, else video",
        }
        if self._gen_backend_combo and config.get("gen_backend"):
            lbl = _BE_LABEL.get(config["gen_backend"], "")
            if lbl:
                self._gen_backend_combo.setCurrentText(lbl)
        if self._ollama_model_combo and config.get("ollama_model"):
            self._ollama_model_combo.setCurrentText(config["ollama_model"])
        if self._gemini_model_combo and config.get("gemini_model"):
            self._gemini_model_combo.setCurrentText(config["gemini_model"])
        if self._style_combo and config.get("style"):
            self._style_combo.setCurrentText(
                _STYLE_LABEL.get(config["style"], "Story (balanced)")
            )
        if self._max_tags_spin and config.get("max_tags") is not None:
            self._max_tags_spin.setValue(int(config["max_tags"]))
        if getattr(self, "_quality_combo", None) is not None:
            qm = str(config.get("quality_mode") or "high").strip().lower()
            self._quality_combo.setCurrentText(
                "High (slower, better)" if qm == "high" else "Balanced (fast)"
            )
        if self._thumb_mode_combo and config.get("thumb_mode"):
            self._thumb_mode_combo.setCurrentText(
                _THUMB_LABEL.get(config["thumb_mode"], "Auto if missing")
            )
        if self._thumb_at_spin and config.get("thumb_at_sec") is not None:
            self._thumb_at_spin.setValue(float(config["thumb_at_sec"]))
        if self._thumb_base_combo and config.get("thumb_base_priority"):
            self._thumb_base_combo.setCurrentText(
                _BASE_LABEL.get(
                    config["thumb_base_priority"], "From video only"
                )
            )
        if self._thumb_ocr_chk and config.get("thumb_ocr_zh_vi") is not None:
            self._thumb_ocr_chk.setChecked(bool(config["thumb_ocr_zh_vi"]))
        if getattr(self, "_ocr_python_edit", None) is not None:
            self._ocr_python_edit.setText(str(config.get("ocr_python_exe") or ""))
        if (
            self._translate_meta_chk
            and config.get("translate_session_zh_meta") is not None
        ):
            self._translate_meta_chk.setChecked(
                bool(config["translate_session_zh_meta"])
            )
        if self._overwrite_chk and config.get("overwrite") is not None:
            self._overwrite_chk.setChecked(bool(config["overwrite"]))
        # api_key: skip — handled by autofill from ApiKeyManager

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

        thumb_base_priority = {
            "From video only": "video_only",
            "Prefer session thumbnail, else video": "session_first",
        }.get(
            self._thumb_base_combo.currentText() if self._thumb_base_combo else "",
            "video_only",
        )

        gen_backend = "ollama"
        if (
            self._gen_backend_combo
            and "Gemini" in self._gen_backend_combo.currentText()
        ):
            gen_backend = "gemini"
        elif (
            self._gen_backend_combo
            and "Rule-based" in self._gen_backend_combo.currentText()
        ):
            gen_backend = "rule"

        quality_mode = (
            "high"
            if (getattr(self, "_quality_combo", None) is not None and "High" in self._quality_combo.currentText())
            else "balanced"
        )
        return {
            "gen_backend": gen_backend,
            "ollama_model": (
                self._ollama_model_combo.currentText()
                if self._ollama_model_combo
                else "qwen2"
            ),
            "gemini_model": (
                self._gemini_model_combo.currentText()
                if self._gemini_model_combo
                else "gemini-2.0-flash"
            ),
            "api_key": (self._selected_api_key or "").strip() or None,
            "style": style_key,
            "max_tags": self._max_tags_spin.value() if self._max_tags_spin else 8,
            "quality_mode": quality_mode,
            "thumb_mode": thumb_mode,
            "thumb_at_sec": (
                self._thumb_at_spin.value() if self._thumb_at_spin else 12.0
            ),
            "thumb_base_priority": thumb_base_priority,
            "thumb_ocr_zh_vi": (
                self._thumb_ocr_chk.isChecked() if self._thumb_ocr_chk else False
            ),
            "ocr_python_exe": (
                (self._ocr_python_edit.text() or "").strip()
                if getattr(self, "_ocr_python_edit", None) is not None
                else ""
            ),
            "translate_session_zh_meta": (
                self._translate_meta_chk.isChecked()
                if self._translate_meta_chk
                else False
            ),
            "thumb_bg_path": self._thumb_bg_path
            or self._shared_thumb_background_path(),
            "overwrite": (
                self._overwrite_chk.isChecked() if self._overwrite_chk else False
            ),
        }
