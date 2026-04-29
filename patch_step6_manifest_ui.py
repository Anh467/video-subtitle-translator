"""
Patch step6_add_voice.py and session.py:
- Step 6 output saved in: <session_folder>/result/step6_output_{manifest_stem}{ext}
- Example: result/step6_output_google_cloud_tts_vi-VN-Neural2-A_vi_20260429_143918.mp4
- session.step6_video searches result/ folder first, then session root (backward compat)
- Works for both single and multi session

Run from project root: python patch_step6_output_naming.py
"""

import re
from pathlib import Path


def read(p):
    return Path(p).read_text(encoding="utf-8")


def write(p, s):
    Path(p).write_text(s, encoding="utf-8")
    print(f"  💾 Saved {p}")


# ── 1. Patch session.py — step6_video searches result/ first ─────────────────
print("=== Patching core/session.py ===")
p = Path("core/session.py")
src = read(p)

old_step6 = '''    @property
    def step6_video(self):
        for f in self.folder.glob("step6_output.*"):
            return f
        for f in self.folder.glob("step5_output.*"):
            return f
        return self.folder / f"step6_output{Path(self.source_file).suffix}"'''

new_step6 = '''    @property
    def step6_video(self):
        # Check result/ subfolder first (new naming with manifest stem)
        result_dir = self.folder / "result"
        if result_dir.exists():
            # Return most recently modified step6_output in result/
            candidates = sorted(
                result_dir.glob("step6_output_*.*"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                return candidates[0]
        # Fallback: legacy location in session root
        for f in self.folder.glob("step6_output.*"):
            return f
        for f in self.folder.glob("step5_output.*"):
            return f
        return self.folder / "result" / f"step6_output{Path(self.source_file).suffix}"

    @property
    def result_dir(self) -> Path:
        """Folder for final output files."""
        d = self.folder / "result"
        d.mkdir(parents=True, exist_ok=True)
        return d'''

if old_step6 in src:
    src = src.replace(old_step6, new_step6)
    print("  ✅ step6_video property updated — searches result/ first")
    write(p, src)
else:
    print("  ❌ step6_video property not found — check manually")
    print("  Manually add to session.py:")
    print(
        """
    @property
    def result_dir(self) -> Path:
        d = self.folder / "result"
        d.mkdir(parents=True, exist_ok=True)
        return d
"""
    )


# ── 2. Patch step6_add_voice.py — output to result/ with manifest name ────────
print("\n=== Patching core/pipeline/step6_add_voice.py ===")
p6 = Path("core/pipeline/step6_add_voice.py")
src6 = read(p6)

# Find where out = str(session.step6_video) or similar output path assignment
# and replace it with the result/ + manifest_stem version

# Pattern: out = str(session.step6_video) or out = session.step6_video
OLD_OUT_PATTERNS = [
    "out = str(session.step6_video)",
    "out_path = str(session.step6_video)",
    "output = str(session.step6_video)",
]

# Build replacement that uses manifest stem
NEW_OUT_CODE = """# Build output path: result/step6_output_{manifest_stem}.mp4
        _ext = Path(session.source_file).suffix or ".mp4"
        _manifest = config.get("manifest_path") or config.get("selected_manifest") or ""
        if _manifest:
            _stem = Path(_manifest).stem
            # Truncate very long stems (keep backend+voice+date parts)
            _parts = _stem.split("_")
            # Keep: backend(1-2 parts) + voice(1-2 parts) + lang(1) + date(1) = ~6 parts
            if len(_parts) > 7:
                _stem = "_".join(_parts[:7])
        else:
            # Multi-session or no manifest selected: use timestamp
            import time as _time
            _stem = f"latest_{int(_time.time())}"
        out = str(session.result_dir / f"step6_output_{_stem}{_ext}")
        log(f"   📁 Output: result/step6_output_{_stem}{_ext}")"""

replaced = False
for old_out in OLD_OUT_PATTERNS:
    if old_out in src6:
        src6 = src6.replace(old_out, NEW_OUT_CODE, 1)
        print(f"  ✅ Replaced '{old_out}' with result/ path")
        replaced = True
        break

if not replaced:
    # Try regex — find any assignment to out that uses session
    m = re.search(
        r"([ \t]+)(out|out_path|output)\s*=\s*str\(session\.(step6_video|folder[^)]+)\)",
        src6,
    )
    if m:
        indent = m.group(1)
        src6 = src6[: m.start()] + indent + NEW_OUT_CODE + src6[m.end() :]
        print("  ✅ Replaced output assignment via regex")
        replaced = True
    else:
        print("  ❌ Could not find output path assignment")
        print("  Manual fix: in step6_add_voice.py run(), replace:")
        print("      out = str(session.step6_video)")
        print("  With:")
        print(
            """      _ext = Path(session.source_file).suffix or ".mp4"
      _manifest = config.get("manifest_path") or config.get("selected_manifest") or ""
      if _manifest:
          _stem = Path(_manifest).stem
          _parts = _stem.split("_")
          if len(_parts) > 7:
              _stem = "_".join(_parts[:7])
      else:
          import time as _time
          _stem = f"latest_{int(_time.time())}"
      out = str(session.result_dir / f"step6_output_{_stem}{_ext}")"""
        )

if replaced:
    write(p6, src6)


# ── 3. Also update session.done_steps() to check result/ for step6 ───────────
print("\n=== Patching session.done_steps() ===")
src_sess = Path("core/session.py").read_text(encoding="utf-8")

old_step6_done = """    @property
    def step6_done(self):
        return self.step6_video.exists()"""

new_step6_done = """    @property
    def step6_done(self):
        # Check result/ folder first
        result_dir = self.folder / "result"
        if result_dir.exists() and any(result_dir.glob("step6_output_*.*")):
            return True
        return self.step6_video.exists()"""

if old_step6_done in src_sess:
    src_sess = src_sess.replace(old_step6_done, new_step6_done)
    Path("core/session.py").write_text(src_sess, encoding="utf-8")
    print("  ✅ step6_done checks result/ folder")
else:
    print("  ℹ️  step6_done not patched (may already be fine)")


print("\n✅ All done. Restart SubSync.")
print("\nOutput example:")
print("  sessions/MyVideo_20260429_143000/")
print("  └── result/")
print("      └── step6_output_google_cloud_tts_vi-VN-Neural2-A_vi_20260429.mp4")
