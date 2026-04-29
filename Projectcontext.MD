# SubSync Project Context

## Overview

SubSync is a PyQt6 desktop pipeline for subtitle and voice-over production.

Current processing flow:

1. Step 1 `Transcribe`: speech-to-text with timestamps.
2. Step 2 `Translate`: translated subtitle segments.
3. Step 3 `Burn Subtitle`: subtitle burn + optional delogo (remove existing sub) + optional channel branding overlay.
4. Step 4 `Separate Voice`: split vocals/background.
5. Step 5 `Text-to-Speech`: generate TTS assets only.
6. Step 6 `Add Voice`: compose/mix/mux audio into final video.

Main design rule: Step 5 does API calls and stores reusable assets; Step 6 does composition and final video output.

## Repository Structure

- `main.py`: app entry point.
- `core/session.py`: session paths, state, chaining, title/description/thumbnail metadata.
- `core/api_keys.py`: API key manager for `.subsync_keys`. Keys: Gemini, OpenAI, FPT, Zalo, ElevenLabs, Google Cloud TTS.
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
- `ui/multi_session_window.py`: standalone multi-session runner window.
- `ui/widgets/step_card.py`: auto-width step card widget.
- `ui/widgets/drop_zone.py`: drag-and-drop file zone.
- `ui/widgets/subtitle_editor.py`: editable Original+Translated panel, saves to json+srt.
- `ui/widgets/session_info_editor.py`: title, description, thumbnail editor per session.

## Session Model

Each run creates a session folder:

- `<base_dir>/<source_stem>_<timestamp>/`

Important outputs:

- `session.json` — metadata: source_file, created, title, description
- `thumbnail.*` — optional cover image (jpg/png/webp)
- `step1_transcript.json`, `step1_transcript.txt`
- `step2_translated.json`, `step2_translated.srt`
- `step3_output.*`
- `step4_vocals.mp3`, `step4_background.mp3`
- `step5_tts.mp3`
- `step5_tts_assets/` (TTS assets per segment)
- `step6_output.*`

### Session Metadata

Stored in `session.json`:

- `source_file`, `folder`, `created`
- `title`: user-defined display name (shown instead of folder name when set)
- `description`: free-form notes

Methods:

- `session.save_info(title, description)` — writes to session.json
- `session.save_thumbnail(src_path)` — copies image to `thumbnail.*` in session folder
- `session.thumbnail` — property returning thumbnail path or `""`

### Session List Ordering

`Session.list_sessions()` returns sessions sorted alphabetically by **folder name** (`name.lower()`).

Display priority:

- If `title` is set → show title as primary name, folder name as secondary hint
- If no title → show folder name

### Video Chaining — `Session.latest_video()`

- If both `step3_output` and `step6_output` exist, return the **most recently modified** one.
- Ensures queue runs (3 → 6) chain correctly without manual state tracking.
- Step 3 always burns from `source_file` (never from step6 output) to avoid stacking subtitles.

### Channel Profiles Storage

- Stored at `<base_dir>/.subsync_channel_profiles/` (shared across sessions of same base folder).
- Each channel gets its own subfolder: `<safe_name>/avatar.<ext>` + `channel_name.txt`.
- Profile scanning: `_load_channel_profiles(base_dir)` in `step3_burn.py`.

## API Keys

Keys are stored in one file:

- `<base_dir>/.subsync_keys`

Managed by `core/api_keys.py`:

- `ApiKeyManager.load(base_dir)`
- `ApiKeyManager.save(base_dir)`
- Known keys: Gemini, OpenAI, FPT, Zalo, ElevenLabs, **Google Cloud TTS**.

UI behavior in `ui/main_window.py`:

- Choosing base folder triggers `_load_api_keys(base_dir)`.
- Keys are auto-loaded and auto-filled into step config UI.
- API keys dialog writes back to `.subsync_keys` and refreshes autofill.
- Autofill uses normalized candidate mapping from `core/pipeline/selection.py`.
- Step 1 Whisper API key auto-filled from `OPENAI_API_KEY`.
- Step 1 field `textChanged` syncs back to `ApiKeyManager` (bidirectional).

## Step 1 (Transcribe) Current Logic

File: `core/pipeline/step1_transcribe.py`

### Backends

- **OpenAI Whisper API** (default): calls `openai.audio.transcriptions.create`, `whisper-1` model. Auto-extracts + compresses audio to MP3 64k if >25MB. ~$0.006/min.
- **Local Whisper**: runs via subprocess to avoid PyTorch+Qt thread crash. Models: tiny/base/small/medium/large.

Config:

- Backend selector (API default)
- API key field (auto-filled from OPENAI_API_KEY)
- Source language
- Min silence gap (local only)

## Step 2 (Translate) Current Logic

File: `core/pipeline/step2_translate.py`

### Backends

- **Gemini Flash** (index 0): concurrent 2 workers + Semaphore + adaptive retry on 429.
- **Google Translate** (index 1): chunk mode, parallel 3 workers.
- **OpenAI GPT-4o** (index 2, default): concurrent 5 workers, no sleep needed.
- **OpenAI GPT-4o-mini** (index 3): same as GPT-4o but cheaper/faster.

`collect_config` maps `openai_gpt4o` → backend=`openai` + `openai_model="gpt-4o"`.
`_translate_openai(model=...)` dispatches correct model.

Optimization vs original:

- Gemini: removed `sleep(4)`, now `ThreadPoolExecutor(max_workers=2)` + `Semaphore(2)`
- OpenAI: `ThreadPoolExecutor(max_workers=5)`, batches run concurrently

### selection.py — TRANSLATE_BACKEND_BY_INDEX

```
0: gemini
1: google
2: openai_gpt4o   ← default
3: openai (mini)
```

## Step 3 (Burn Subtitle) Current Logic

File: `core/pipeline/step3_burn.py`

### Subtitle Burn

- Always burns from `session.source_file` (NOT from latest_video) to avoid double-subtitle stacking.
- Font size: `% of video height` (QDoubleSpinBox, 0.5 step). Default: `2.0%`.
- Subtitle position: ASS style with `MarginV=6`.
- Background box: `BorderStyle=4` opaque box, `BackColour=&H80000000`.

### Remove Existing Subtitle (delogo)

New feature: FFmpeg `delogo` filter to remove hardcoded subtitles from source video.

- Checkbox "Remove hardcoded subtitle with delogo filter" (off by default).
- **Auto-detect**: samples frame at 10s, runs `cropdetect` on bottom 25% to find subtitle region.
- **Manual**: x, y, w, h spinboxes for precise region control.
- Filter chain order: `delogo → subtitles → branding` (delogo first so new sub burns onto clean frame).
- No extra dependencies — FFmpeg only.

```python
def _delogo_filter(x, y, w, h) -> str:
    return f"delogo=x={x}:y={y}:w={w}:h={h}:show=0"
```

### Channel Branding Overlay

(unchanged from original — see original context)

## Step 5 (TTS) Current Logic

File: `core/pipeline/step5_tts.py`

### Backends

| Backend              | Key                  | Cost            | Notes                                    |
| -------------------- | -------------------- | --------------- | ---------------------------------------- |
| FPT AI TTS           | FPT_API_KEY          | Free (1M chars) | Vietnamese voices, async polling         |
| Zalo AI TTS          | ZALO_API_KEY         | Free            | Vietnamese voices                        |
| gTTS                 | —                    | Free            | Google Translate TTS, 5 workers parallel |
| **Google Cloud TTS** | GOOGLE_CLOUD_TTS_KEY | $4–16/1M chars  | Neural2/WaveNet/Standard, 5 workers      |
| OpenAI TTS           | OPENAI_API_KEY       | $15/1M chars    | tts-1 model, 5 workers                   |
| ElevenLabs           | ELEVENLABS_API_KEY   | ~$30/1M chars   | eleven_multilingual_v2                   |

Default backend: **gTTS** (index 3 in combo).

### Google Cloud TTS

- REST API: `https://texttospeech.googleapis.com/v1/text:synthesize?key=...`
- No SDK needed — uses `requests` only.
- Voice dropdown: Neural2-A/D, Wavenet-A/B/C, Standard-A/B for Vietnamese.
- Voice format: `vi-VN-Neural2-A` → lang_code auto-inferred from voice name prefix.
- 5 concurrent workers with retry on 429.

Setup:

1. Enable Cloud Text-to-Speech API at console.cloud.google.com
2. Create API key → paste in API Keys Manager

### Character Count Display

- `TTSStep.update_char_count(session)` — public method, call when session changes.
- Shows: `📝 12,450 characters  (221 segments)`
- Shows: `💸 Cost: Free` or `💸 Cost: ~$0.187 USD  (~4,670 VNĐ)`
- Cost recalculates automatically when backend combo changes.
- `_last_char_count` cached for recalc without re-reading files.

### Persisted assets per backend-run

(unchanged from original)

## Step 6 (Add Voice) Current Logic

(unchanged from original)

### Volume Defaults (patched via patch_defaults.py)

- TTS voice: **124%**
- Background music: **145%**
- Original voice: **31%**

## UI Architecture

### MainWindow (`ui/main_window.py`)

- **StepCard** scroll area (horizontal).
- **SessionInfoEditor** between session bar and file input.
- **SubtitleEditor** replaces old orig_edit/trans_edit split.
- Button "⚡ Multi-Session" in title row → opens `MultiSessionWindow`.
- `_multi_window: MultiSessionWindow | None` — singleton, reused across opens.
- `update_base_dir(d)` called on `_multi_window` when base dir changes.
- `update_char_count(session)` called on all steps when session loads/changes.

### MultiSessionWindow (`ui/multi_session_window.py`)

Standalone `QMainWindow`, opened from MainWindow. Receives `steps[]` (shared instances).

Layout:

```
┌─────────────────────┬──────────────────────────────────┐
│  SessionListPanel   │  SessionInfoEditor (top)         │
│  - checkbox rows    │  SubtitleEditor (bottom)         │
│  - thumbnail 48x27  │                                  │
├─────────────────────┴──────────────────────────────────┤
│  Step cards (shared config, full width, h=220px)       │
├────────────────────────────────────────────────────────┤
│  Run Selected  │  Run All Sessions  │  Stop  │  Cancel │
└────────────────────────────────────────────────────────┘
```

#### SessionListPanel

- Checkbox default: **False** (unchecked) — user must explicitly select.
- `_get_checked_folders()` snapshots state before `_rebuild_list`.
- `_rebuild_list(preserve_checked=True)` restores checkbox state after rebuild.
- `refresh()` also uses `preserve_checked=True`.
- `reset_all_status()` uses `preserve_checked=True` — checked state survives queue start.
- "＋" button → `_add_session()` opens multi-file picker, creates Session objects, refreshes list.
- `session_clicked = pyqtSignal(dict)` emitted on row click.
- Display name: `title` if set, else `folder name`. Tooltip shows folder name.
- Thumbnail 48×27px shown if `session["thumbnail"]` exists.

#### Run behavior

- **Run Selected**: runs only checked sessions × enabled steps.
- **Run All Sessions**: runs `_session_panel._sessions` directly (does NOT mutate checkboxes).
- On error: skips remaining steps for that session, continues next session.
- After completion: calls `_session_panel.refresh()` to update done_steps display.

### SubtitleEditor (`ui/widgets/subtitle_editor.py`)

Editable Translated panel. Format (SRT-style):

```
1
[0.0s–1.28s] original text here
translated text — edit this line

2
[1.28s–2.84s] next original
next translated
```

- `load_session(session)` → loads step1 (read-only) + step2 (editable).
- `set_session_for_save(session)` → enables save after `load_from_text`.
- Save (💾 or Ctrl+S) → writes `step2_translated.json` + `step2_translated.srt`.
- Parser: `_parse_translated_block(text)` splits on blank lines, finds `[Xs–Ys]` line, everything after = translated.
- Emits `saved(folder_path)` signal after successful save.

### SessionInfoEditor (`ui/widgets/session_info_editor.py`)

Layout: thumbnail preview (96×54) + upload/clear buttons on left; Title + Notes fields on right.

- **Title** (QLineEdit): shown as primary session name in lists.
- **Notes** (QTextEdit, 52px): free text.
- **Thumbnail**: upload jpg/png/webp → `session.save_thumbnail(path)` copies to `thumbnail.*`. Saves immediately on upload (no Save button needed). Preview updates live.
- Save (Ctrl+Shift+S) → calls `session.save_info(title, description)`.
- `clear()` resets all fields and thumbnail preview.

## Known FFmpeg Filter Constraints

| Filter     | overlay_w/h | if() | max()/min() | Notes                                    |
| ---------- | ----------- | ---- | ----------- | ---------------------------------------- |
| `overlay`  | ✅          | ✅   | ✅          | Supports all position expressions        |
| `drawtext` | ❌          | ❌   | ❌          | Use only: t, W, H, numeric, sin(), cos() |
| `delogo`   | N/A         | N/A  | N/A         | x, y, w, h, show only                    |

**Rule**: In `drawtext`, never use `overlay_w`, `overlay_h`, `if()`, `max()`, `min()`.

## Maintenance Guidelines

- **Step 3**: Input must ALWAYS be `session.source_file`, never `session.latest_video()`, to prevent double-subtitle stacking.
- **FFmpeg drawtext**: Use sin/cos only for expressions. No nested if/max/min/overlay_w.
- **delogo filter**: must come BEFORE subtitles filter in the chain.
- Keep Step 5 free of mux/mix logic.
- Keep Step 6 free of TTS API calls.
- Keep backend/model/API-key mapping inside `core/pipeline/selection.py`.
- When adding new TTS backend, ensure:
  1. Add to `TTS_BACKEND_LABEL_TO_KEY` in `selection.py`
  2. Add to `COST_PER_1M` in `step5_tts.py`
  3. Add dispatch case in `_generate_tts`
  4. Add placeholder in `_on_backend_changed`
  5. Add key to `KNOWN_KEYS` in `api_keys.py` if needs API key
- If key handling changes, update both `core/api_keys.py` and autofill in `ui/main_window.py`.
- `update_char_count(session)` must be called whenever active session changes in both MainWindow and MultiSessionWindow.
- Step 1 `_api_key_edit.textChanged` syncs to ApiKeyManager — do not remove this signal.
- Checkbox default in `SessionListPanel._make_row` is **False**. Do not change to True.
- `_rebuild_list(preserve_checked=True)` must be used in all cases except initial load.

## Default Values (after patch_defaults.py)

| Step   | Setting            | Default                 |
| ------ | ------------------ | ----------------------- |
| Step 1 | Backend            | OpenAI Whisper API      |
| Step 2 | Backend            | OpenAI GPT-4o (index 2) |
| Step 5 | Backend            | gTTS (index 3)          |
| Step 6 | TTS voice vol      | 124%                    |
| Step 6 | BGM vol            | 145%                    |
| Step 6 | Original voice vol | 31%                     |
