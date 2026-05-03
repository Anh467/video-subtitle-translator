"""FFmpeg command lines for soft mux and hard burn."""

from pathlib import Path

from core.ffmpeg_utils import ffmpeg_executable, subtitles_filter_clause
from core.pipeline.step3_burn.constants import DEFAULT_CRF, DEFAULT_PRESET
from core.pipeline.step3_burn.delogo import delogo_filter, escape_drawtext_text


def soft_sub_cmd(video, srt, out):
    codec = "srt" if Path(out).suffix.lower() == ".mkv" else "mov_text"
    return [
        ffmpeg_executable(),
        "-y",
        "-i",
        video,
        "-i",
        srt,
        "-c",
        "copy",
        "-c:s",
        codec,
        "-metadata:s:s:0",
        "language=vie",
        out,
    ]


def hard_burn_cmd(
    video,
    subs_path,
    out,
    video_w=1920,
    video_h=1080,
    crf=DEFAULT_CRF,
    preset=DEFAULT_PRESET,
    delogo=None,
    branding=None,
):
    """
    Hard-burn subtitles from an ASS (or SRT) file.
    Style for hard burn must be encoded inside the ASS — do not use force_style
    (fragile with FFmpeg 8.x + filter_complex, especially on macOS).
    """
    sub_filter = subtitles_filter_clause(subs_path)

    if delogo and delogo.get("enabled"):
        dx, dy = delogo["x"], delogo["y"]
        dw, dh = delogo["w"], delogo["h"]
        dx = max(0, min(dx, video_w - 4))
        dy = max(0, min(dy, video_h - 4))
        dw = max(3, min(dw, (video_w - dx) - 1))
        dh = max(3, min(dh, (video_h - dy) - 1))
        if dw >= 3 and dh >= 3:
            en = delogo.get("enable_expr")
            vf_base = f"{delogo_filter(dx, dy, dw, dh, enable_expr=en)},{sub_filter}"
        else:
            vf_base = sub_filter
    else:
        vf_base = sub_filter

    if not branding or not branding.get("enabled"):
        return [
            ffmpeg_executable(),
            "-y",
            "-i",
            video,
            "-vf",
            vf_base,
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(crf),
            "-c:a",
            "copy",
            out,
        ]

    name = escape_drawtext_text(branding.get("name", "").strip())
    avatar = branding.get("avatar", "").strip()
    avatar_exists = bool(avatar) and Path(avatar).exists()

    opacity = max(0.0, min(1.0, float(branding.get("opacity", 30)) / 100.0))
    avatar_w = max(24, int(video_w * float(branding.get("avatar_pct", 9.0)) / 100.0))
    name_size = max(12, int(video_h * float(branding.get("name_pct", 2.0)) / 100.0))
    margin = max(
        0, int(min(video_w, video_h) * float(branding.get("margin_pct", 2.0)) / 100.0)
    )
    pos = branding.get("pos", "random")
    gap = max(6, int(name_size * 0.35))
    est_text_h = int(name_size * 1.4)

    use_random_movement = pos == "random"

    if use_random_movement:
        x_span_overlay = max(0, video_w - avatar_w - 2 * margin)
        y_span_overlay = max(0, video_h - avatar_w - est_text_h - gap - 2 * margin)
        x_span_text = max(0, video_w - avatar_w - 2 * margin)
        y_span_text = max(0, video_h - avatar_w - est_text_h - gap - 2 * margin)
        x_avatar_overlay = f"{margin}+({x_span_overlay})*(0.5+0.5*sin(t/6))"
        y_avatar_overlay = f"{margin}+({y_span_overlay})*(0.5+0.5*cos(t/7))"
        x_avatar_text = f"{margin}+({x_span_text})*(0.5+0.5*sin(t/6))"
        y_avatar_text = f"{margin}+({y_span_text})*(0.5+0.5*cos(t/7))"
        y_text_name = f"({y_avatar_text})+{avatar_w}+{gap}"
    else:
        if pos == "top_right":
            x_avatar_overlay = f"W-overlay_w-{margin}"
            y_avatar_overlay = margin
            x_avatar_text = f"W-{avatar_w}-{margin}"
            y_avatar_text = margin
        elif pos == "bottom_left":
            x_avatar_overlay = str(margin)
            y_avatar_overlay = max(0, video_h - avatar_w - est_text_h - gap - margin)
            x_avatar_text = str(margin)
            y_avatar_text = max(0, video_h - avatar_w - est_text_h - gap - margin)
        elif pos == "bottom_right":
            x_avatar_overlay = f"W-overlay_w-{margin}"
            y_avatar_overlay = max(0, video_h - avatar_w - est_text_h - gap - margin)
            x_avatar_text = f"W-{avatar_w}-{margin}"
            y_avatar_text = max(0, video_h - avatar_w - est_text_h - gap - margin)
        else:
            x_avatar_overlay = str(margin)
            y_avatar_overlay = margin
            x_avatar_text = str(margin)
            y_avatar_text = margin
        y_text_name = f"{y_avatar_text}+{avatar_w}+{gap}"

    text_width_approx = max(50, len(name) * name_size * 0.5)
    center_offset = (avatar_w - int(text_width_approx)) / 2

    filters = [f"[0:v]{vf_base}[sburn]"]
    map_label = "sburn"

    if avatar_exists:
        filters.append(
            f"[1:v]scale={avatar_w}:-1,format=rgba,colorchannelmixer=aa={opacity:.3f}[logo]"
        )
        filters.append(
            f"[sburn][logo]overlay=x={x_avatar_overlay}:y={y_avatar_overlay}[vlogo]"
        )
        map_label = "vlogo"

    if name:
        filters.append(
            f"[{map_label}]drawtext=text='{name}':"
            f"fontcolor=white@{opacity:.3f}:fontsize={name_size}:"
            f"box=1:boxcolor=black@0.45:boxborderw=8:"
            f"x=({x_avatar_text})+{center_offset}:y={y_text_name}[vout]"
        )
        map_label = "vout"

    cmd = [ffmpeg_executable(), "-y", "-i", video]
    if avatar_exists:
        cmd += ["-i", avatar]
    cmd += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        f"[{map_label}]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-c:a",
        "copy",
        out,
    ]
    return cmd
