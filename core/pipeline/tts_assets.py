"""Helpers for reading Step 5 TTS assets and composing timeline-aligned audio."""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from pathlib import Path

from core.ffmpeg_utils import ffmpeg_executable


def resolve_single_tts_path(session, tts_source: str) -> str:
    if tts_source and Path(tts_source).exists():
        return tts_source

    if Path(session.step5_tts).exists():
        return str(session.step5_tts)

    assets_dir = session.step5_tts_assets_dir
    if assets_dir.exists():
        files = sorted(assets_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
        if files:
            return str(files[-1])

    # Backward compatibility for old sessions.
    cache_dir = session.step5_tts_session_dir
    if cache_dir.exists():
        files = sorted(cache_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
        if files:
            return str(files[-1])
    return ""


def collect_manifest_paths(session) -> list[Path]:
    manifests = []
    assets_dir = session.step5_tts_assets_dir
    if assets_dir.exists():
        manifests.extend(assets_dir.glob("*.json"))

    # Backward compatibility for old session layout.
    old_cache_dir = session.step5_tts_session_dir
    if old_cache_dir.exists():
        manifests.extend(old_cache_dir.glob("*.json"))

    return sorted(manifests, key=lambda p: p.stat().st_mtime)


def resolve_manifests(
    session,
    source_mode: str,
    tts_source: str,
    manifest_pick: str | None = None,
) -> list[Path]:
    manifests = collect_manifest_paths(session)
    if not manifests:
        return []

    if source_mode == "latest":
        # Prefer the manifest chosen in Step 6 UI (dropdown); else newest on disk.
        if manifest_pick:
            p = Path(manifest_pick)
            if p.is_file() and p.suffix.lower() == ".json":
                return [p]
        return manifests[-1:]

    if source_mode == "custom":
        result = []
        for line in tts_source.replace(";", "\n").splitlines():
            raw = line.strip()
            if not raw:
                continue
            p = Path(raw)
            if p.exists() and p.suffix.lower() == ".json":
                result.append(p)
            elif p.exists() and p.suffix.lower() == ".mp3":
                candidate = p.with_name(f"{p.stem}.json")
                if candidate.exists():
                    result.append(candidate)
        return result

    return manifests


def load_source_from_manifest(mf: Path):
    from pydub import AudioSegment

    data = json.loads(mf.read_text(encoding="utf-8"))
    seg_dir_name = data.get("segment_dir", "")
    clip_segments = data.get("segments", []) or []

    clip_map = {}
    if seg_dir_name and clip_segments:
        seg_dir = mf.with_name(seg_dir_name)
        if seg_dir.exists():
            for seg in clip_segments:
                clip_name = seg.get("clip_file")
                if not clip_name:
                    continue
                clip_path = seg_dir / clip_name
                if not clip_path.exists():
                    continue
                idx = int(seg.get("index", -1))
                if idx >= 0:
                    clip_map[idx] = AudioSegment.from_mp3(str(clip_path))

    if clip_map:
        return {"manifest": data, "clip_map": clip_map, "name": mf.stem}

    audio_name = data.get("audio_file", "")
    audio_path = mf.with_name(audio_name) if audio_name else None
    if audio_path and audio_path.exists():
        return {
            "manifest": data,
            "audio": AudioSegment.from_mp3(str(audio_path)),
            "name": mf.stem,
        }

    return None


def _speed_fit_audio(audio, audio_ms: int, target_ms: int):
    from pydub import AudioSegment

    ratio = max(0.5, min(2.0, audio_ms / target_ms))
    if abs(ratio - 1.0) < 0.05:
        return audio

    tmp_in = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp_out = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp_in.close()
    tmp_out.close()
    try:
        audio.export(tmp_in.name, format="mp3")
        atempo = (
            f"atempo={ratio:.4f}"
            if ratio <= 2.0
            else f"atempo=2.0,atempo={ratio/2.0:.4f}"
        )
        r = subprocess.run(
            [
                ffmpeg_executable(),
                "-y",
                "-i",
                tmp_in.name,
                "-af",
                atempo,
                "-c:a",
                "mp3",
                tmp_out.name,
            ],
            capture_output=True,
            text=True,
        )
        return AudioSegment.from_mp3(tmp_out.name) if r.returncode == 0 else audio
    except Exception:
        return audio
    finally:
        os.unlink(tmp_in.name)
        if os.path.exists(tmp_out.name):
            os.unlink(tmp_out.name)


def fit_audio_to_segment(audio, seg_duration_ms: int, sync_mode: str = "trim"):
    from pydub import AudioSegment

    audio_ms = len(audio)

    if sync_mode == "none" or seg_duration_ms <= 0:
        return audio

    if sync_mode == "trim":
        if audio_ms <= seg_duration_ms:
            return audio
        return _speed_fit_audio(audio, audio_ms, seg_duration_ms)

    if sync_mode == "pad":
        if audio_ms < seg_duration_ms:
            return audio + AudioSegment.silent(duration=seg_duration_ms - audio_ms)
        if audio_ms > seg_duration_ms:
            return _speed_fit_audio(audio, audio_ms, seg_duration_ms)
        return audio

    if sync_mode == "stretch":
        if abs(audio_ms - seg_duration_ms) < 100:
            return audio
        return _speed_fit_audio(audio, audio_ms, seg_duration_ms)

    return audio


def compose_timeline_audio(manifests, segments, log, sync_mode: str = "trim"):
    try:
        from pydub import AudioSegment
    except ImportError as e:
        raise RuntimeError("Run: pip install pydub audioop-lts") from e

    loaded = []
    for mf in manifests:
        try:
            source = load_source_from_manifest(mf)
            if source is not None:
                loaded.append(source)
        except Exception:
            continue

    if not loaded:
        return ""

    log(f"🧩 Timeline compose from {len(loaded)} Step 5 source(s)")

    total_ms = max((int(seg.end * 1000) for seg in segments), default=0)
    result = AudioSegment.silent(duration=total_ms)
    chosen = 0
    silent = 0
    mixed = 0

    for idx, seg in enumerate(segments):
        start_ms = int(seg.start * 1000)
        end_ms = int(seg.end * 1000)
        seg_ms = max(0, end_ms - start_ms)

        parts = []
        for src in loaded:
            clip_map = src.get("clip_map")
            if clip_map:
                part = clip_map.get(idx)
                if part is None:
                    continue
            else:
                audio = src["audio"]
                if start_ms >= len(audio):
                    continue
                part = audio[start_ms : min(end_ms, len(audio))]

            part = fit_audio_to_segment(part, seg_ms, sync_mode)
            if len(part) < max(80, int(seg_ms * 0.25)):
                continue
            if len(part) > seg_ms > 0:
                part = part[:seg_ms]
            parts.append(part)

        if not parts:
            silent += 1
            continue

        if len(parts) == 1:
            clip = parts[0]
        else:
            gain_down = min(12.0, 20.0 * math.log10(len(parts)))
            clip = parts[0].apply_gain(-gain_down)
            for part in parts[1:]:
                clip = clip.overlay(part.apply_gain(-gain_down))
            mixed += 1

        # Absolute placement by subtitle timeline avoids drift between segments.
        result = result.overlay(clip, position=start_ms)
        chosen += 1

        if (idx + 1) % 10 == 0 or idx + 1 == len(segments):
            log(f"   [{idx+1}/{len(segments)}] compose")

    out = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    out.close()
    result.export(out.name, format="mp3")
    log(
        f"✅ Composed voice track: {chosen} segments, {mixed} mixed segments, {silent} silence fallback"
    )
    return out.name
