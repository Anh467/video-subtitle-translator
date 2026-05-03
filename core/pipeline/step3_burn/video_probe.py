"""Video stream geometry via ffprobe."""

import json
import subprocess

def get_video_size(video_path: str) -> tuple[int, int]:
    """Get video width x height using ffprobe."""
    try:
        r = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "json",
                video_path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        data = json.loads(r.stdout)
        stream = data["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except Exception:
        return 1920, 1080
