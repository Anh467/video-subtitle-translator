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
