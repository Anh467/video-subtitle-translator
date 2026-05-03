"""Rule-based translation touch-ups after model output."""

class SmartFixer:
    ZH_VI_RULES = [
        (["cô ấy là bố", "bà ấy là bố", "cô ta là bố"], "爸爸", "Đây là bố"),
        (["cô ấy là anh", "bà ấy là anh"], "哥哥", "Đây là anh"),
        (["cô ấy là ông", "bà ấy là ông"], "爷爷", "Đây là ông"),
        (["cô ấy là chú", "bà ấy là chú"], "叔叔", "Đây là chú"),
        (["anh ấy là mẹ", "ông ấy là mẹ"], "妈妈", "Đây là mẹ"),
        (["anh ấy là chị", "ông ấy là chị"], "姐姐", "Đây là chị"),
        (["anh ấy là bà", "ông ấy là bà"], "奶奶", "Đây là bà"),
        (["đây là của tôi bố"], "爸爸", "Đây là bố tôi"),
        (["đây là của tôi mẹ"], "妈妈", "Đây là mẹ tôi"),
    ]

    WORD_FIXES_VI = {
        "cô ấy là bố tôi": "Đây là bố tôi",
        "cô ấy là anh tôi": "Đây là anh tôi",
        "cô ấy là ông tôi": "Đây là ông tôi",
        "bà ấy là bố tôi": "Đây là bố tôi",
        "bà ấy là anh tôi": "Đây là anh tôi",
        "anh ấy là mẹ tôi": "Đây là mẹ tôi",
        "anh ấy là chị tôi": "Đây là chị tôi",
        "ông ấy là mẹ tôi": "Đây là mẹ tôi",
    }

    def __init__(self, src_lang="zh", tgt_lang="vi"):
        self.src_lang = src_lang.lower()
        self.tgt_lang = tgt_lang.lower()

    def fix(self, original, translated, prev_segs=None, next_segs=None):
        t = translated.strip()
        if not t:
            return translated
        t = self._word_fix(t)
        if self.src_lang in ("zh", "zh-cn", "zh-tw"):
            t = self._zh_pronoun_fix(original, t)
        if prev_segs or next_segs:
            t = self._context_pronoun_fix(original, t, prev_segs, next_segs)
        return t

    def _word_fix(self, text):
        lower = text.lower()
        for wrong, correct in self.WORD_FIXES_VI.items():
            if wrong in lower:
                return text[: len(text) - len(text.lstrip())] + correct
        return text

    def _zh_pronoun_fix(self, original, translated):
        tl = translated.lower()
        for patterns, zh_keyword, replacement in self.ZH_VI_RULES:
            if zh_keyword in original:
                for p in patterns:
                    if p in tl:
                        return (
                            replacement
                            + translated[translated.lower().index(p) + len(p) :]
                        )
        return translated

    def _context_pronoun_fix(self, original, translated, prev_segs, next_segs):
        tl = translated.lower()
        all_neighbours = list(prev_segs or []) + list(next_segs or [])
        neighbour_originals = " ".join(
            s.original for s in all_neighbours if hasattr(s, "original")
        )
        neighbour_translated = " ".join(
            s.translated for s in all_neighbours if hasattr(s, "translated")
        )
        if "爸爸" in neighbour_originals or "bố" in neighbour_translated:
            if "cô ấy" in tl or "bà ấy" in tl:
                fixed = (
                    translated.replace("Cô ấy", "Ông ấy")
                    .replace("cô ấy", "ông ấy")
                    .replace("Bà ấy", "Ông ấy")
                    .replace("bà ấy", "ông ấy")
                )
                if fixed != translated:
                    return fixed
        if "妈妈" in neighbour_originals or "mẹ" in neighbour_translated:
            if "anh ấy" in tl or "ông ấy" in tl:
                fixed = (
                    translated.replace("Anh ấy", "Cô ấy")
                    .replace("anh ấy", "cô ấy")
                    .replace("Ông ấy", "Cô ấy")
                    .replace("ông ấy", "cô ấy")
                )
                if fixed != translated:
                    return fixed
        return translated

