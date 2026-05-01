"""Session — one folder per run, stores all step outputs.

Features:
- Auto-save tất cả output sau mỗi step
- Load lại session cũ để resume từ bất kỳ step nào
- list_sessions() để hiển thị danh sách sessions có thể chọn
- title + description: editable metadata per session
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv"}
THUMB_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
INFO_EXTS = {".json"}


class Session:
    def __init__(self, base_dir: str, source_file: str):
        stem = Path(source_file).stem
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.folder = Path(base_dir) / f"{stem}_{ts}"
        self.source_file = str(source_file)
        self.created = datetime.now().isoformat()
        self.title: str = ""
        self.description: str = ""
        self._thumb_background: str = ""
        self.subtitle_studio: dict = {}
        self.folder.mkdir(parents=True, exist_ok=True)
        self._save_meta()

    @classmethod
    def load(cls, folder: str) -> "Session":
        f = Path(folder)
        meta = json.loads((f / "session.json").read_text(encoding="utf-8"))
        obj = object.__new__(cls)
        obj.folder = f
        obj.source_file = meta["source_file"]
        obj.created = meta.get("created") or datetime.now().isoformat()
        obj.title = meta.get("title", "")
        obj.description = meta.get("description", "")
        obj._thumb_background = meta.get("thumb_background", "")
        obj.subtitle_studio = meta.get("subtitle_studio", {}) or {}
        # thumbnail is detected from folder, not stored in meta
        return obj

    @staticmethod
    def _find_video_folders(root: str) -> list[Path]:
        root_path = Path(root)
        found = []
        for path in root_path.rglob("*"):
            if not path.is_dir():
                continue
            names = [p.name for p in path.iterdir() if p.is_file()]
            if not names:
                continue
            video_files = [p for p in path.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS]
            if not video_files:
                continue
            image_files = [p for p in path.iterdir() if p.is_file() and p.suffix.lower() in THUMB_EXTS]
            info_files = [p for p in path.iterdir() if p.is_file() and p.suffix.lower() in INFO_EXTS and p.name.lower() != "session.json"]
            if image_files and info_files:
                found.append(path)
        return sorted(found, key=lambda p: p.name.lower())

    @staticmethod
    def _unique_session_folder(base_dir: str, name: str) -> Path:
        folder = Path(base_dir) / name
        if not folder.exists():
            return folder
        counter = 2
        while True:
            candidate = Path(base_dir) / f"{name}_{counter}"
            if not candidate.exists():
                return candidate
            counter += 1

    @staticmethod
    def _copy_thumbnail_to_session(src_thumb: Path, dst_folder: Path) -> str:
        dst = dst_folder / "thumbnail.png"
        try:
            if src_thumb.suffix.lower() != ".png":
                from PIL import Image

                image = Image.open(src_thumb)
                image = image.convert("RGB")
                image.save(dst, format="PNG")
            else:
                shutil.copy2(str(src_thumb), dst)
        except Exception:
            # Fallback to copy original extension if conversion fails
            dst = dst_folder / f"thumbnail{src_thumb.suffix.lower()}"
            shutil.copy2(str(src_thumb), dst)
        return str(dst)

    @staticmethod
    def _extract_session_metadata_from_json(info_file: Path) -> dict:
        try:
            data = json.loads(info_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

        title = ""
        description = ""

        if isinstance(data.get("caption"), str) and data.get("caption").strip():
            title = data["caption"].strip()
        if isinstance(data.get("desc"), str) and data.get("desc").strip():
            description = data["desc"].strip()

        if not title and description:
            title = description.split("\n", 1)[0].strip()[:120]

        if title and not description:
            description = title

        return {"title": title, "description": description}

    @classmethod
    def create_from_video_folder(cls, base_dir: str, folder_path: str) -> "Session":
        source_dir = Path(folder_path)
        if not source_dir.exists() or not source_dir.is_dir():
            raise FileNotFoundError(f"Video folder not found: {folder_path}")

        video_file = next(
            (p for p in sorted(source_dir.iterdir()) if p.is_file() and p.suffix.lower() in VIDEO_EXTS),
            None,
        )
        if video_file is None:
            raise FileNotFoundError(f"No supported video file found in {folder_path}")

        thumbnail_file = next(
            (p for p in sorted(source_dir.iterdir()) if p.is_file() and p.suffix.lower() in THUMB_EXTS),
            None,
        )
        if thumbnail_file is None:
            raise FileNotFoundError(f"No thumbnail image found in {folder_path}")

        session_folder = cls._unique_session_folder(base_dir, source_dir.name)
        session_folder.mkdir(parents=True, exist_ok=True)

        obj = object.__new__(cls)
        obj.folder = session_folder
        obj.source_file = str(video_file)
        obj.created = datetime.now().isoformat()
        obj.title = ""
        obj.description = ""
        obj._thumb_background = ""
        obj.subtitle_studio = {}

        info_file = next(
            (
                p
                for p in sorted(source_dir.iterdir())
                if p.is_file() and p.suffix.lower() == ".json" and p.name.lower() != "session.json"
            ),
            None,
        )
        if info_file is not None:
            metadata = cls._extract_session_metadata_from_json(info_file)
            obj.title = metadata.get("title", "")
            obj.description = metadata.get("description", "")

        obj._copy_thumbnail_to_session(thumbnail_file, session_folder)
        obj._save_meta()
        return obj

    @classmethod
    def import_sessions_from_workspace(cls, base_dir: str, workspace_root: str) -> list[str]:
        created = []
        for folder in cls._find_video_folders(workspace_root):
            try:
                session = cls.create_from_video_folder(base_dir, str(folder))
                created.append(str(session.folder))
            except Exception:
                continue
        return created

    @staticmethod
    def list_sessions(base_dir: str) -> list[dict]:
        """List all sessions in base_dir, sorted newest first."""
        base = Path(base_dir)
        if not base.exists():
            return []
        sessions = []
        for d in sorted(base.iterdir(), key=lambda x: x.name.lower()):
            if not d.is_dir():
                continue
            meta_path = d / "session.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                done = []
                if (d / "step1_transcript.json").exists():
                    done.append("①")
                if (d / "step2_translated.json").exists():
                    done.append("②")
                if any(d.glob("step3_output.*")):
                    done.append("③")
                if (d / "step4_vocals.mp3").exists():
                    done.append("④")
                if (d / "step5_tts.mp3").exists():
                    done.append("⑤")
                result_dir = d / "result"
                has_step6 = (
                    any(d.glob("step6_output.*"))
                    or any(d.glob("result_output.*"))
                    or (
                        result_dir.exists() and any(result_dir.glob("step6_output_*.*"))
                    )
                    or (
                        result_dir.exists()
                        and any(result_dir.glob("result_output_*.*"))
                    )
                )
                if has_step6 or any(d.glob("step5_output.*")):
                    done.append("⑥")
                if (d / "step7_publish_info.json").exists():
                    done.append("⑦")

                size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                # Detect thumbnail
                thumb = ""
                for ext in (".jpg", ".jpeg", ".png", ".webp"):
                    tp = d / f"thumbnail{ext}"
                    if tp.exists():
                        thumb = str(tp)
                        break
                sessions.append(
                    {
                        "folder": str(d),
                        "name": d.name,
                        "source_file": meta.get("source_file", ""),
                        "title": meta.get("title", ""),
                        "description": meta.get("description", ""),
                        "thumb_background": meta.get("thumb_background", ""),
                        "thumbnail": thumb,
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

    # ── Info (title + description + thumbnail) ───────────────────────────────

    def save_info(self, title: str = "", description: str = ""):
        """Save title + description into session.json (non-destructive update)."""
        self.title = title.strip()
        self.description = description.strip()
        self._save_meta()

    def save_subtitle_studio(self, studio: dict | None):
        """Save per-session subtitle studio settings into session.json."""
        self.subtitle_studio = dict(studio or {})
        self._save_meta()

    def load_subtitle_studio(self) -> dict:
        """Load per-session subtitle studio settings from session.json."""
        return dict(self.subtitle_studio or {})

    def save_thumbnail(self, src_path: str) -> str:
        """Copy an image to session folder as thumbnail.jpg. Returns saved path."""
        import shutil
        from pathlib import Path as _Path

        src = _Path(src_path)
        ext = src.suffix.lower() or ".jpg"
        dst = self.folder / f"thumbnail{ext}"
        # Remove any old thumbnail with different extension
        for old in self.folder.glob("thumbnail.*"):
            if old.resolve() != dst.resolve():
                old.unlink(missing_ok=True)
        shutil.copy2(src, dst)
        return str(dst)

    def save_thumb_background(self, src_path: str) -> str:
        """Copy an image to session folder for Step 7 thumbnail overlay layer."""
        import shutil
        from pathlib import Path as _Path

        src = _Path(src_path)
        ext = src.suffix.lower() or ".jpg"
        dst = self.folder / f"thumb_background{ext}"
        for old in self.folder.glob("thumb_background.*"):
            if old.resolve() != dst.resolve():
                old.unlink(missing_ok=True)
        shutil.copy2(src, dst)
        self._thumb_background = str(dst)
        self._save_meta()
        return str(dst)

    @property
    def thumbnail(self) -> str:
        """Return path to thumbnail image if it exists, else empty string."""
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            p = self.folder / f"thumbnail{ext}"
            if p.exists():
                return str(p)
        return ""

    @property
    def thumb_background(self) -> str:
        """Return Step 7 foreground background image path if it exists."""
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
            p = self.folder / f"thumb_background{ext}"
            if p.exists():
                return str(p)
        p = Path(self._thumb_background) if self._thumb_background else None
        if p and p.exists():
            return str(p)
        return ""

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
        return self.step5_tts_assets_dir

    @property
    def step5_tts_assets_dir(self):
        return self.folder / "step5_tts_assets"

    @property
    def step5_tts_library_dir(self):
        return self.step5_tts_assets_dir

    @property
    def step5_tts_session_dir(self):
        return self.folder / "step5_tts_cache"

    @property
    def step6_video(self):
        # Check result/ subfolder first (new naming with manifest stem)
        result_dir = self.folder / "result"
        if result_dir.exists():
            candidates = []
            for pattern in ("result_output_*.*", "step6_output_*.*"):
                candidates.extend(result_dir.glob(pattern))
            candidates = sorted(
                candidates, key=lambda f: f.stat().st_mtime, reverse=True
            )
            if candidates:
                return candidates[0]
        # Fallback: legacy location in session root
        for f in self.folder.glob("step6_output.*"):
            return f
        for f in self.folder.glob("result_output.*"):
            return f
        for f in self.folder.glob("step5_output.*"):
            return f
        return self.folder / "result" / f"result_output{Path(self.source_file).suffix}"

    @property
    def step7_info(self):
        return self.folder / "step7_publish_info.json"

    @property
    def result_dir(self) -> Path:
        """Folder for final output files."""
        d = self.folder / "result"
        d.mkdir(parents=True, exist_ok=True)
        return d

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
        # Check result/ folder first
        result_dir = self.folder / "result"
        if result_dir.exists() and (
            any(result_dir.glob("step6_output_*.*"))
            or any(result_dir.glob("result_output_*.*"))
        ):
            return True
        return self.step6_video.exists()

    @property
    def step7_done(self):
        return self.step7_info.exists()

    def done_steps(self) -> list[str]:
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
        if self.step7_done:
            steps.append("step7_publish_info")
        return steps

    # ── smart video chaining ──────────────────────────────────────────────────
    def latest_video(self) -> str:
        s3 = self.step3_video
        s6 = self.step6_video
        if s3.exists() and s6.exists():
            return str(s3 if s3.stat().st_mtime >= s6.stat().st_mtime else s6)
        if s6.exists():
            return str(s6)
        if s3.exists():
            return str(s3)
        return self.source_file

    def final_video(self) -> str:
        return self.latest_video()

    def _save_meta(self):
        (self.folder / "session.json").write_text(
            json.dumps(
                {
                    "source_file": self.source_file,
                    "folder": str(self.folder),
                    "created": self.created,
                    "title": self.title,
                    "description": self.description,
                    "thumb_background": self.thumb_background,
                    "subtitle_studio": self.subtitle_studio,
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
