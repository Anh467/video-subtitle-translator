"""Session — one folder per run, stores all step outputs."""

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
    def step5_video(self):
        return self.folder / f"step5_output{Path(self.source_file).suffix}"

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
        return self.step5_video.exists()

    # ── smart video chaining ──────────────────────────────────────────────────
    def latest_video(self) -> str:
        """
        Returns the best available video to use as input for the next step.
        Priority: step5 > step3 > original source
        This allows step3 and step5 to chain in any order into one video.
        """
        if self.step5_video.exists():
            return str(self.step5_video)
        if self.step3_video.exists():
            return str(self.step3_video)
        return self.source_file

    def final_video(self) -> str:
        """Returns the most processed video available."""
        return self.latest_video()

    def _save_meta(self):
        (self.folder / "session.json").write_text(
            json.dumps(
                {"source_file": self.source_file, "folder": str(self.folder)},
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
