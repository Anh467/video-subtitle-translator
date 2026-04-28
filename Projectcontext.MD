# SubSync Project Context

## Overview

SubSync is a PyQt6 desktop pipeline for subtitle and voice-over production.

Current processing flow:

1. Step 1 `Transcribe`: speech-to-text with timestamps.
2. Step 2 `Translate`: translated subtitle segments.
3. Step 3 `Burn Subtitle`: subtitle mux/burn into video.
4. Step 4 `Separate Voice`: split vocals/background.
5. Step 5 `Text-to-Speech`: generate TTS assets only.
6. Step 6 `Add Voice`: compose/mix/mux audio into final video.

Main design rule: Step 5 does API calls and stores reusable assets; Step 6 does composition and final video output.

## Repository Structure

- `main.py`: app entry point.
- `core/session.py`: session paths, state, chaining.
- `core/api_keys.py`: API key manager for `.subsync_keys`.
- `core/pipeline/base.py`: step abstractions and worker.
- `core/pipeline/selection.py`: shared backend/model/API-key mapping helpers.
- `core/pipeline/tts_assets.py`: shared Step 5 asset resolve/compose helpers.
- `core/pipeline/step1_transcribe.py`
- `core/pipeline/step2_translate.py`
- `core/pipeline/step3_burn.py`
- `core/pipeline/step4_separate.py`
- `core/pipeline/step5_tts.py`
- `core/pipeline/step6_add_voice.py`
- `ui/main_window.py`: orchestration, queue, session manager, key loading.

## Session Model

Each run creates a session folder:

- `<base_dir>/<source_stem>_<timestamp>/`

Important outputs:

- `step1_transcript.json`, `step1_transcript.txt`
- `step2_translated.json`, `step2_translated.srt`
- `step3_output.*`
- `step4_vocals.mp3`, `step4_background.mp3`
- `step5_tts.mp3`
- `step5_tts_assets/` (new TTS assets)
- `step6_output.*`

Video chaining from `Session.latest_video()`:

- Prefer step6 output, then step3 output, else source video.

## API Keys

Keys are stored in one file:

- `<base_dir>/.subsync_keys`

Managed by `core/api_keys.py`:

- `ApiKeyManager.load(base_dir)`
- `ApiKeyManager.save(base_dir)`
- known keys: Gemini, OpenAI, FPT, Zalo, ElevenLabs.

UI behavior in `ui/main_window.py`:

- Choosing base folder triggers `_load_api_keys(base_dir)`.
- Keys are auto-loaded and auto-filled into step config UI.
- API keys dialog writes back to `.subsync_keys` and refreshes autofill.
- Autofill now uses normalized candidate mapping from `core/pipeline/selection.py`
  to avoid wrong key assignment when switching backend/model.

Model/API selection consistency rules:

- Translate backend index -> backend key via `translate_backend_from_index()`.
- Verify backend index -> backend key via `verify_backend_from_index()`.
- Ollama combo text -> model id via `ollama_model_from_combo_text()`.
- TTS backend label -> backend key via `tts_backend_from_label()`.
- API key candidate list is centralized with:
  - `translate_key_candidates()`
  - `tts_key_candidates()`

## Step 5 (TTS) Current Logic

File: `core/pipeline/step5_tts.py`

Responsibilities:

- Read translated segments.
- Call one backend or batch all backends.
- Generate raw segment outputs and asset manifests.
- Generate final merged track (`step5_tts.mp3`) only as quick fallback.
- Persist detailed TTS assets for Step 6.

Persisted assets per backend-run:

- `<asset_prefix>.mp3`: merged track for that request.
- `<asset_prefix>.json`: manifest.
- `<asset_prefix>_segments/seg_XXXX.mp3`: per-segment audio files from API/local TTS.

Manifest contains:

- metadata: backend, voice, language, created time.
- `audio_file`: merged track file name.
- `segment_dir`: per-segment folder name.
- `segments[]`: index/start/end/text and `clip_file` per segment.

Important:

- Step 5 does NOT apply sync trim/pad/stretch.
- Segment files are stored as raw API/local TTS outputs.

Backends:

- FPT
- Zalo
- gTTS
- OpenAI TTS
- ElevenLabs

## Step 6 (Add Voice) Current Logic

Files:

- `core/pipeline/step6_add_voice.py`
- `core/pipeline/tts_assets.py`

Responsibilities:

- Select TTS source(s) from session assets.
- Compose timeline-aligned voice track from manifest(s).
- Mix with background/original audio based on mode.
- Mux into final video output.

Source modes:

- all session manifests
- latest manifest only
- single audio file
- custom manifest/audio list

Refactor status:

- Manifest collection, source loading, and timeline composition are extracted to
  `core/pipeline/tts_assets.py`.
- `step6_add_voice.py` now focuses on orchestration (resolve -> compose -> mix -> mux).

Composition behavior:

- Prefer per-segment files from `segment_dir` + `clip_file`.
- Fallback to slicing merged `audio_file` by segment timestamps.
- Apply sync strategy during compose (`trim`/`pad`/`stretch`/`none`).
- If multiple sources exist, overlay with gain normalization.
- Missing segment audio inserts silence for timing safety.

Mix modes:

- replace: TTS only
- bgm_only: TTS + Step 4 background
- full_mix: TTS + BGM + low-volume original audio

## Logic/Consistency Notes

- Step responsibilities are separated correctly:
  - Step 5 = generation/storage.
  - Step 6 = composition/finalization.
- Storage is session-local (`step5_tts_assets`) to avoid cross-session mismatch.
- Backward compatibility remains for old `step5_tts_cache` sessions in Step 6 fallback.
- Step 6 can still work if only `step5_tts.mp3` exists (legacy/single mode).

## Maintenance Guidelines

- Keep Step 5 free of mux/mix logic.
- Keep Step 6 free of TTS API calls.
- Keep backend/model/API-key mapping inside `core/pipeline/selection.py`.
- When adding new TTS backend, ensure:
  1. per-segment export list is returned,
  2. manifest includes valid `clip_file` entries,
  3. Step 6 can consume without backend-specific branching.
- If key handling changes, update both `core/api_keys.py` and autofill logic in `ui/main_window.py`.
