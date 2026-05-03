Bundled FFmpeg for PyInstaller (macOS)
======================================

Before building SubSync.app, place **libass-enabled** binaries here:

  bin/ffmpeg
  bin/ffprobe

Example (Homebrew tap with subtitles filter):

  mkdir -p bin
  cp "$(brew --prefix)/opt/ffmpeg/bin/ffmpeg"  bin/
  cp "$(brew --prefix)/opt/ffmpeg/bin/ffprobe" bin/

Or if you use homebrew-ffmpeg/ffmpeg:

  brew install homebrew-ffmpeg/ffmpeg/ffmpeg
  cp "$(brew --prefix ffmpeg)/bin/ffmpeg"  bin/
  cp "$(brew --prefix ffmpeg)/bin/ffprobe" bin/

Do **not** commit these files to git (they are large and platform-specific).

Then run from the project root (macOS):

  bash scripts/build_mac_app.sh

The app resolves tools via core.ffmpeg_utils (MEIPASS/bin and PATH).
