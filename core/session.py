"""Session — one folder per run, stores all step outputs.

Features:
- Auto-save tất cả output sau mỗi step
- Load lại session cũ để resume từ bất kỳ step nào
- list_sessions() để hiển thị danh sách sessions có thể chọn
"""

import json
from datetime import datetime
from pathlib import Path


class Session:
    def __init__(self, base_dir: str, source_file: str):
        stem = Path(source_file).stem
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.folder = Path(base_dir) / f"{stem}_{ts}"
        self.source_file = str(source_file)
        self.folder.mkdir(parents=True, exist_ok=True)
        self._save_meta()

    @classmethod
    def load(cls, folder: str) -> "Session":
        f = Path(folder)
        meta = json.loads((f / "session.json").read_text(encoding="utf-8"))
        obj = object.__new__(cls)
        obj.folder = f
        obj.source_file = meta["source_file"]
        return obj

    @staticmethod
    def list_sessions(base_dir: str) -> list[dict]:
        """List all sessions in base_dir, sorted newest first."""
        base = Path(base_dir)
        if not base.exists():
            return []
        sessions = []
        for d in sorted(base.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if not d.is_dir():
                continue
            meta_path = d / "session.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                # Detect which steps are done
                done = []
                if (d / "step1_transcript.json").exists():
                    done.append("①")
                if (d / "step2_translated.json").exists():
                    done.append("②")
                # step3 video has dynamic extension
                if any(d.glob("step3_output.*")):
                    done.append("③")
                if (d / "step4_vocals.mp3").exists():
                    done.append("④")
                if (d / "step5_tts.mp3").exists():
                    done.append("⑤")
                if any(d.glob("step6_output.*")) or any(d.glob("step5_output.*")):
                    done.append("⑥")

                size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                sessions.append(
                    {
                        "folder": str(d),
                        "name": d.name,
                        "source_file": meta.get("source_file", ""),
                        "done_steps": done,
                        "size_mb": round(size / 1024 / 1024, 1),
                        "mtime": d.stat().st_mtime,
                    }
                )
            except Exception:
                continue
        return sessions

    @staticmethod
    def clear_session(folder: str):
        """Delete all files in session folder (keep folder itself)."""
        import shutil

        p = Path(folder)
        if p.exists():
            shutil.rmtree(str(p))

    # ── output paths ──────────────────────────────────────────────────────────
    @property
    def step1_json(self):
        return self.folder / "step1_transcript.json"

    @property
    def step1_txt(self):
        return self.folder / "step1_transcript.txt"

    @property
    def step2_json(self):
        return self.folder / "step2_translated.json"

    @property
    def step2_srt(self):
        return self.folder / "step2_translated.srt"

    @property
    def step3_video(self):
        # Try to find existing file first (any extension)
        for f in self.folder.glob("step3_output.*"):
            return f
        return self.folder / f"step3_output{Path(self.source_file).suffix}"

    @property
    def step4_vocals(self):
        return self.folder / "step4_vocals.mp3"

    @property
    def step4_background(self):
        return self.folder / "step4_background.mp3"

    @property
    def step4_drums(self):
        return self.folder / "step4_drums.mp3"

    @property
    def step4_bass(self):
        return self.folder / "step4_bass.mp3"

    @property
    def step4_other(self):
        return self.folder / "step4_other.mp3"

    @property
    def step5_tts(self):
        return self.folder / "step5_tts.mp3"

    @property
    def step5_tts_cache_dir(self):
        # Backward compatibility alias.
        return self.step5_tts_library_dir

    @property
    def step5_tts_library_dir(self):
        # Shared persistent library for all sessions under the same base folder.
        return self.folder.parent / "_tts_library"

    @property
    def step5_tts_session_dir(self):
        # Optional per-session storage (kept for compatibility/debug if needed).
        return self.folder / "step5_tts_cache"

    @property
    def step6_video(self):
        for f in self.folder.glob("step6_output.*"):
            return f
        # Backward compatibility with older sessions
        for f in self.folder.glob("step5_output.*"):
            return f
        return self.folder / f"step6_output{Path(self.source_file).suffix}"

    # ── completion checks ─────────────────────────────────────────────────────
    @property
    def step1_done(self):
        return self.step1_json.exists()

    @property
    def step2_done(self):
        return self.step2_json.exists()

    @property
    def step3_done(self):
        return self.step3_video.exists()

    @property
    def step4_done(self):
        return self.step4_vocals.exists()

    @property
    def step5_done(self):
        return self.step5_tts.exists()

    @property
    def step6_done(self):
        return self.step6_video.exists()

    def done_steps(self) -> list[str]:
        """Return list of completed step IDs."""
        steps = []
        if self.step1_done:
            steps.append("step1_transcribe")
        if self.step2_done:
            steps.append("step2_translate")
        if self.step3_done:
            steps.append("step3_burn")
        if self.step4_done:
            steps.append("step4_separate")
        if self.step5_done:
            steps.append("step5_tts")
        if self.step6_done:
            steps.append("step6_add_voice")
        return steps

    # ── smart video chaining ──────────────────────────────────────────────────
    def latest_video(self) -> str:
        if self.step6_video.exists():
            return str(self.step6_video)
        if self.step3_video.exists():
            return str(self.step3_video)
        return self.source_file

    def final_video(self) -> str:
        return self.latest_video()

    def _save_meta(self):
        (self.folder / "session.json").write_text(
            json.dumps(
                {
                    "source_file": self.source_file,
                    "folder": str(self.folder),
                    "created": datetime.now().isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    # ── step 1 ────────────────────────────────────────────────────────────────
    def save_transcript(self, result):
        data = {
            "source_file": result.source_file,
            "language": result.language,
            "text": result.text,
            "segments": [
                {"start": s.start, "end": s.end, "text": s.text}
                for s in result.segments
            ],
        }
        self.step1_json.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.step1_txt.write_text(result.text, encoding="utf-8")

    def load_transcript(self):
        from core.pipeline.step1_transcribe import Segment, TranscriptResult

        d = json.loads(self.step1_json.read_text(encoding="utf-8"))
        segs = [Segment(s["start"], s["end"], s["text"]) for s in d["segments"]]
        return TranscriptResult(d["text"], segs, d["language"], d["source_file"])

    # ── step 2 ────────────────────────────────────────────────────────────────
    def save_translated(self, segments):
        from core.pipeline.step3_burn import write_srt

        data = [
            {
                "start": s.start,
                "end": s.end,
                "original": s.original,
                "translated": s.translated,
            }
            for s in segments
        ]
        self.step2_json.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        write_srt(segments, str(self.step2_srt))

    def load_translated(self):
        from core.pipeline.step2_translate import TranslatedSegment

        data = json.loads(self.step2_json.read_text(encoding="utf-8"))
        return [
            TranslatedSegment(d["start"], d["end"], d["original"], d["translated"])
            for d in data
        ]
