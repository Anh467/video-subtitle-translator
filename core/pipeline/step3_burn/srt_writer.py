"""SRT serialization for burn / session save."""

from pathlib import Path

from core.pipeline.step3_burn.constants import COLOR_MAP

def color_name_to_ass_bgr(c):
    rgb = COLOR_MAP.get(c.lower(), "FFFFFF")
    return rgb[4:6] + rgb[2:4] + rgb[0:2]


def _srt_time(s):
    h, r = divmod(int(s), 3600)
    m, sec = divmod(r, 60)
    return f"{h:02}:{m:02}:{sec:02},{int((s-int(s))*1000):03}"


def write_srt(segments, path, field="translated"):
    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(
            f"{i}\n{_srt_time(seg.start)} --> {_srt_time(seg.end)}\n"
            f"{getattr(seg, field, seg.translated).strip()}\n"
        )
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return path


def _ass_ts(t: float) -> str:
    """ASS time — centiseconds."""
    ct = max(0, int(round(float(t) * 100)))
    h, ct = divmod(ct, 3600 * 100)
    m, ct = divmod(ct, 60 * 100)
    sec, cs = divmod(ct, 100)
    return f"{h:d}:{int(m):02d}:{int(sec):02d}.{int(cs):02d}"


def _ass_dialog_escape(text: str) -> str:
    t = str(text).replace("\\", "\\\\")
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace("\n", "\\N")
    return t.replace("{", "\\{").replace("}", "\\}")


def write_ass_for_hard_burn(
    segments,
    path,
    field="translated",
    *,
    font_size,
    font_family="Arial",
    bold=False,
    italic=False,
    font_color="white",
    outline_color="black",
    outline_width=2,
    shadow=0,
    bg_style="semi",
    bg_color="black",
    bg_opacity=50,
    alignment=2,
    margin_v=6,
    video_w=1920,
    video_h=1080,
):
    """
    Write UTF-8 ASS so FFmpeg can use subtitles=path without force_style.
    Avoids fragile filtergraph escaping (FFmpeg 8.x + filter_complex on macOS).
    """
    use_bg = bg_style != "none"
    bg_opacity = max(0.0, min(100.0, float(bg_opacity)))
    bg_alpha_hex = f"{int((100.0 - bg_opacity) / 100.0 * 255):02X}"

    outline_val = outline_width if outline_color and outline_color != "none" else 0
    o_bgr = color_name_to_ass_bgr(outline_color) if outline_val > 0 else "000000"
    outline_colour = f"&H00{o_bgr}"
    primary = f"&H00{color_name_to_ass_bgr(font_color)}"
    back = f"&H{bg_alpha_hex}{color_name_to_ass_bgr(bg_color)}"
    secondary = primary
    border_style = 4 if use_bg else 1
    bold_i = -1 if bold else 0
    italic_i = -1 if italic else 0

    style_line = (
        f"Style: Default,{font_family or 'Arial'},{int(font_size)},"
        f"{primary},{secondary},{outline_colour},{back},"
        f"{bold_i},{italic_i},0,0,100,100,0,0,"
        f"{border_style},{outline_val},{int(shadow)},"
        f"{int(alignment)},10,10,{int(margin_v)},1"
    )

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        f"PlayResX: {int(video_w)}\n"
        f"PlayResY: {int(video_h)}\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"{style_line}\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    parts = [header]
    for seg in segments:
        text = getattr(seg, field, seg.translated).strip()
        if not text:
            continue
        parts.append(
            f"Dialogue: 0,{_ass_ts(seg.start)},{_ass_ts(seg.end)},"
            f"Default,,0,0,0,,{_ass_dialog_escape(text)}\n"
        )

    Path(path).write_text("".join(parts), encoding="utf-8")
    return path
