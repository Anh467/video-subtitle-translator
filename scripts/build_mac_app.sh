#!/usr/bin/env bash
# Build SubSync.app with PyInstaller + bundled bin/ffmpeg (macOS).
# Run from project root on a Mac. Requires: pip install pyinstaller
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f bin/ffmpeg || ! -f bin/ffprobe ]]; then
  echo "Missing bin/ffmpeg or bin/ffprobe. See bin/README.txt" >&2
  exit 1
fi

# --windowed → macOS .app bundle (onedir inside the app; reliable for Qt).
# Use --onefile only if you want a single Mach-O binary (not always a .app).
pyinstaller main.py \
  --name SubSync \
  --windowed \
  --noconfirm \
  --clean \
  --add-data "bin:bin" \
  --hidden-import PyQt6 \
  --hidden-import PyQt6.QtCore \
  --hidden-import PyQt6.QtGui \
  --hidden-import PyQt6.QtWidgets \
  "$@"

echo "Built: dist/SubSync.app"
