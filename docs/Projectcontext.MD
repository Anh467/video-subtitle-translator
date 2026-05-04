# SubSync Project Context

## Overview

SubSync is a PyQt6 desktop pipeline for subtitle and voice-over production.

Current processing flow:

1. Step 1 `Transcribe`: speech-to-text with timestamps.
2. Step 2 `Translate`: translated subtitle segments.
3. Step 3 `Burn Subtitle`: subtitle burn + optional delogo (remove existing sub) + optional channel branding overlay.
4. Step 4 `Separate Voice`: separate audio into vocals + background or 4-stem outputs (vocals, drums, bass, other).
5. Step 5 `Text-to-Speech`: generate TTS assets only.
6. Step 6 `Add Voice`: compose/mix/mux audio into final video.
7. Step 7 `Publish Info`: generate title/description/hashtags and edited thumbnail.

Main design rule: Step 5 does API calls and stores reusable assets; Step 6 does composition and final video output.

## Repository Structure

- `main.py`: app entry point.
- `core/session.py`: session paths, state, chaining, title/description/thumbnail metadata; `publish_plan` + `set_publish_plan` / `append_publish_jobs` / `patch_publish_job`.
- `core/session_listing.py`: `Session.list_sessions()` implementation (folder scan + sizes + **publish summary** từ `publish_plan`).
- `core/config_store.py`: persist step widget configs per workspace.
- `core/api_keys.py`: API key manager for `.subsync_keys`. Keys: Gemini, OpenAI, FPT, Zalo, ElevenLabs, Google Cloud TTS.
- `core/publish_profiles.py`: nhiều **publish profile** (Facebook Page / YouTube OAuth / TikTok fields) → file workspace `.subsync_publish_profiles.json`.
- `core/publish_jobs.py`: build `publish_plan` jobs, enrich snapshot, `platforms_still_need_upload`, sort.
- `core/publish/`: `facebook_upload.py`, `youtube_upload.py`, `runner.py`, `youtube_oauth_local.py` (OAuth localhost `127.0.0.1:8742` → refresh token); `scripts/youtube_oauth_refresh_token.py` CLI.
- `core/logger.py` / `core/logging/` / `core/log_file.py`: Qt log handler + daily file log (`FileLogger`).
- `core/pipeline/base.py`: step abstractions and worker.
- `core/pipeline/selection.py`: shared backend/model/API-key mapping helpers.
- `core/pipeline/tts_assets.py`: shared Step 5 asset resolve/compose helpers.
- `core/pipeline/step1_transcribe/` — package: constants, models (`Segment`, `TranscriptResult`), format utils, `TranscribeStep`.
- `core/pipeline/step2_translate/` — package: constants, `TranslatedSegment`, `SmartFixer`, `TranslateStep`.
- `core/pipeline/step3_burn/` — package: constants, SRT writer, ffprobe, channel profiles, delogo, ffmpeg builders, `BurnStep`.
- `core/pipeline/step4_separate/` — package: Demucs constants, `SeparateStep`.
- `core/pipeline/step5_tts/` — package: `budget.py` (cost table + char count), constants, `TTSStep`.
- `core/pipeline/step6_add_voice/` — package: mix-mode constants, `AddVoiceStep`.
- `core/pipeline/step7_publish/` — package: `stop_words`, `PublishInfoStep` (`STEP_ID` remains `step7_publish_info`).
- `n8n-automatioin/session_publish_jobs.py`: scan sessions to publish jobs payload.
- `n8n-automatioin/export_publish_jobs.py`: CLI export JSON jobs for n8n.
- `n8n-automatioin/mark_publish_result.py`: CLI write posted markers per platform.
- `ui/main_window.py`: orchestration, queue, session manager, key loading.
- `ui/multi_session_window.py`: thin import shim → `ui/multi_session/`.
- `ui/multi_session/`: `cost_worker.py`, `session_list_panel.py`, `window.py` (`MultiSessionWindow`).
- `ui/dialogs/`, `ui/theme.py`: shared Qt dialogs and stylesheet (`ApiKeysDialog`: tab API Keys + **Publish profiles**; `PublishPlatformsDialog`).
- `ui/widgets/publish_profiles_tab.py`: chỉnh sửa publish profiles trong API Keys Manager.
- `ui/multi_session/publish_thread.py`: `QThread` chạy publish + log + `patch_publish_job`.
- `ui/widgets/step_card.py`: auto-width step card widget.
- `ui/widgets/drop_zone.py`: drag-and-drop file zone.
- `ui/widgets/subtitle_editor.py`: editable Original+Translated panel, saves to json+srt.
- `ui/widgets/subtitle_editor_io.py`: parse/format SRT-like panel text, ffprobe duration helpers.
- `ui/widgets/session_info_editor.py`: title, description, thumbnail editor per session.

## Session Model

Each run creates a session folder:

- `<base_dir>/<source_stem>_<timestamp>/`

Important outputs:

- `session.json` — metadata: source_file, created, title, description, published_at, `subtitle_studio`, **`publish_plan`** (danh sách job đăng đa nền tảng: platform, lịch, media snapshot, status, `remote_asset_id`, …)
- `thumbnail.*` — optional cover image (jpg/png/webp)
- `step1_transcript.json`, `step1_transcript.txt`
- `step2_translated.json`, `step2_translated.srt`
- `step3_output.*`
- `step4_vocals.mp3`, `step4_background.mp3`, `step4_drums.mp3`, `step4_bass.mp3`, `step4_other.mp3`
- `step5_tts.mp3`
- `step5_tts_assets/` (TTS assets per segment)
- `result/result_output_*.*` or `step6_output.*` — final mixed video/audio output
- `step7_publish_info.json`
- `thumb_background.*` (optional Step 7 foreground layer persisted per session)
- `posted_youtube.json` / `posted_facebook.json` (optional automation markers)

### Session Metadata

Stored in `session.json`:

- `source_file`, `folder`, `created`
- `title`: user-defined display name (shown instead of folder name when set)
- `description`: free-form notes

Methods:

- `session.save_info(title, description)` — writes to session.json
- `session.save_thumbnail(src_path)` — copies image to `thumbnail.*` in session folder
- `session.thumbnail` — property returning thumbnail path or `""`
- `session.save_thumb_background(src_path)` — copies image to `thumb_background.*`
- `session.thumb_background` — property returning Step 7 foreground layer path or `""`
- `step7_thumb_foreground.*` — optional shared Step 7 thumbnail foreground in the base folder

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

- Stored at `<base_dir>/.subsync_channel_profiles/` and `<base_dir>/.subsync_channel_profiles.json` (shared across sessions of same base folder).
- Each channel gets its own subfolder: `<safe_name>/avatar.<ext>` + `channel_name.txt`.
- Profile scanning: `load_channel_profiles(base_dir)` in `core/pipeline/step3_burn/channel_profiles.py`.

## API Keys

Keys are stored in one file:

- `<base_dir>/.subsync_keys`

Managed by `core/api_keys.py`:

- `ApiKeyManager.load(base_dir)`
- `ApiKeyManager.save(base_dir)`
- Known keys: Gemini, OpenAI, FPT, Zalo, ElevenLabs, **Google Cloud TTS**.
- Step 7 uses `GEMINI_API_KEY` when Generator = Gemini.

### Publish profiles (đăng Facebook / YouTube / TikTok)

- File workspace: `<base_dir>/.subsync_publish_profiles.json` (gitignored cùng kiểu secrets).
- `core/publish_profiles.py`: `load_profiles` / `save_profiles`, `platform_configured`, `profile_platforms_ready`.
- UI: **API Keys Manager** → tab **Publish profiles** (`PublishProfilesTab`): nhiều profile đặt tên; mỗi profile có `credentials.facebook` (page_id, page_access_token), `credentials.youtube` (client_id, client_secret, refresh_token), `credentials.tiktok` (access_token — app chưa gọi API TikTok).

UI behavior in `ui/main_window.py`:

- Choosing base folder triggers `_load_api_keys(base_dir)`.
- Keys are auto-loaded and auto-filled into step config UI.
- API keys dialog writes back to `.subsync_keys` **và** (khi có base folder) `.subsync_publish_profiles.json` khi Save; refreshes autofill.
- Autofill uses normalized candidate mapping from `core/pipeline/selection.py`.
- Step 1 Whisper API key auto-filled from `OPENAI_API_KEY`.
- Step 1 field `textChanged` syncs back to `ApiKeyManager` (bidirectional).
- Step 7 Gemini API key field behaves like Step 5 key field: autofill from manager in both single and multi-session windows.

## Step 1 (Transcribe) Current Logic

Package: `core/pipeline/step1_transcribe/` — main class in `transcribe_step.py`; shared types in `models.py`.

### Backends

- **OpenAI Whisper API** (default): calls `openai.audio.transcriptions.create`, `whisper-1` model. Auto-extracts + compresses audio to MP3 64k if >25MB. ~$0.006/min.
- **Local Whisper**: runs via subprocess to avoid PyTorch+Qt thread crash. Models: tiny/base/small/medium/large.

Config:

- Backend selector (API default)
- API key field (auto-filled from OPENAI_API_KEY)
- Source language
- Min silence gap (local only)

## Step 2 (Translate) Current Logic

Package: `core/pipeline/step2_translate/` — main class in `translate_step.py`.

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

Package: `core/pipeline/step3_burn/` — main class in `burn_step.py`; `write_srt` exported from package root.

### Subtitle Burn

- Always burns from `session.source_file` (NOT from latest_video) to avoid double-subtitle stacking.
- Font size: `% of ASS PlayResY (288)` space via `int(ASS_PLAYRES_Y * font_pct / 100)`. Default: `2.0%`.
- Subtitle position: ASS style with `MarginV=6`.
- Background box: `BorderStyle=4` uses `BackColour=&H{alpha_hex}{bgr_hex}` for opaque/semi-transparent box.
- **ASS color format**: `&HAABBGGRR` — alpha is **inverted** (00=opaque, FF=transparent), color in BGR byte order.
- `COLOR_MAP` stores true RGB hex; `color_name_to_ass_bgr()` (in `srt_writer.py`) swaps to BGR for libass.
- `bg_alpha_hex = int((100 - opacity) / 100 * 255)` — inverted correctly.
- `ASS_PLAYRES_Y = 288` constant ensures font sizes are in script-space, not pixel-space.
- **Preview** (QPainter): uses `fm.ascent()` + `fm.descent()` for correct box sizing so text is centered inside box.
- `set_source_file(path)` method: called on browse/load/session change to refresh preview with actual video dimensions.
- **Default quality**: `DEFAULT_CRF=20`, `DEFAULT_PRESET="medium"` for near-original quality.
- **Default preview ratio**: `PREVIEW_ASPECT_AUTO` = "Auto (from source video)" with fallback logic.

### Remove Existing Subtitle (delogo)

New feature: FFmpeg `delogo` filter to remove hardcoded subtitles from source video.

- Checkbox "Remove hardcoded subtitle with delogo filter" (off by default).
- **Auto-detect**: samples frame at 10s, runs `cropdetect` on bottom 25% to find subtitle region.
- **Manual**: x, y, w, h spinboxes for precise region control.
- Filter chain order: `delogo → subtitles → branding` (delogo first so new sub burns onto clean frame).
- No extra dependencies — FFmpeg only.

FFmpeg helper: `delogo_filter(x, y, w, h)` in `core/pipeline/step3_burn/delogo.py`.

### Channel Branding Overlay

(unchanged from original — see original context)

## Step 4 (Separate) Current Logic

Package: `core/pipeline/step4_separate/` — `separate_step.py`, options in `constants.py`.

## Step 5 (TTS) Current Logic

Package: `core/pipeline/step5_tts/` — `tts_step.py`; cost table in `budget.py`.

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

Package: `core/pipeline/step6_add_voice/` — `add_voice_step.py`, mix modes in `constants.py`.

### Audio composition

- `_ensure_background_track(session, log)`: returns `step4_background` if it exists; otherwise auto-runs Demucs 2-stem (`--two-stems vocals`) on `source_file` to extract background track. Cleans up temp Demucs output after use.
- `_mix_audio(tts, bg, vol_tts, vol_bg, vol_orig, orig, log)` returns `(mixed_path, temp_files_list)`.
- `_mux()` uses `-c:v copy` (stream copy, no re-encode) + `-c:a aac -b:a 192k` for stable audio quality.

### Volume Defaults

- TTS voice: **124%**
- Background music: **145%**
- Original voice: **31%**

## Step 7 (Publish Info) Current Logic

Package: `core/pipeline/step7_publish/` — `publish_step.py`; `stop_words.py` for hashtag filtering. Public import: `from core.pipeline.step7_publish import PublishInfoStep`.

### Generation backends

- **Ollama (local)**: title/description/hook/time-pick with retry + fallback.
- **Gemini (Google API)**: title/description/hook/time-pick via `google-genai` and `GEMINI_API_KEY`.
- **Rule-based**: deterministic fallback for all text pieces.

### Thumbnail layering

Final thumbnail composition order:

1. Video representative frame (back)
2. Uploaded foreground image (full opacity overlay)
3. Hook text with stroke (no dark background box)

Hook rules:

- 4-5 words only.
- One visible line.
- Uppercase preference for AI outputs.

### Shared foreground image behavior

- Upload once in Step 7 UI.
- Saved in base folder as `step7_thumb_foreground.*`.
- Auto-reused in single and multi-session runs for the same base folder.
- UI includes preview pane and clear action.

### Step 7 config UX

- Backend selector dynamically enables only relevant model combo:
  - Ollama selected -> Ollama model enabled, Gemini model disabled.
  - Gemini selected -> Gemini model enabled, Ollama model disabled, API key field visible.
  - Rule-based selected -> both model combos disabled.

## UI Architecture

### MainWindow (`ui/main_window.py`)

- **StepCard** scroll area (horizontal).
- **SessionInfoEditor** between session bar and file input.
- **SubtitleEditor** replaces old orig_edit/trans_edit split.
- Button "⚡ Multi-Session" in title row → opens `MultiSessionWindow`.
- `_multi_window: MultiSessionWindow | None` — singleton, reused across opens.
- `update_base_dir(d)` called on `_multi_window` when base dir changes.
- `update_char_count(session)` called on all steps when session loads/changes.
- Step 7 key autofill integrated in `_autofill_api_keys` (Gemini service key).
- `_set_step3_source_file(source_file)`: calls `BurnStep.set_source_file()` on browse, load, and session change.
- **Throughput metrics**: `_single_run_started_at`, `_single_total_steps`, `_single_done_steps` logged at queue end with `actions/min`.

### MultiSessionWindow (`ui/multi_session/window.py`, re-exported from `ui/multi_session_window.py`)

Standalone `QMainWindow`, opened from MainWindow. Receives `steps[]` (shared instances).

- `_set_step3_source_file(source_file)`: called in `_on_session_clicked()` and `_run_next()` to keep BurnStep preview in sync with queued session.
- **Throughput metrics**: `_multi_started_at`, `_multi_total_jobs`, `_multi_done_jobs`, `_multi_session_stats`, `_multi_failed_sessions` logged per-session and as aggregate `actions/min` + `sessions/min`.
- **📤 Đăng đa nền tảng**: mở `PublishPlatformsDialog` — profile + platform + lịch + phạm vi (`all` ghi đè `publish_plan` vs `only_missing_success` append job chưa `done`). `MultiPublishThread` gọi `core.publish.runner.run_publish` và `Session.patch_publish_job`.

Layout:

```
┌─────────────────────┬──────────────────────────────────┐
│  SessionListPanel   │  SessionInfoEditor (top)         │
│  - checkbox rows    │  SubtitleEditor (bottom)         │
│  - thumbnail 48x27  │                                  │
│  - dòng «Đã đăng»   │                                  │
├─────────────────────┴──────────────────────────────────┤
│  Step cards (shared config, full width, h=220px)       │
├────────────────────────────────────────────────────────┤
│  Run Selected  │  Run All  │  📤 Publish  │ Stop │ …   │
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
- **Đã đăng (multi-platform)**: mỗi row hiển thị `📤 Đã đăng: Facebook · YouTube` từ `publish_done_platforms` (do `session_listing.summarize_publish_plan` đọc `publish_plan` trong `session.json`). Nếu có job chưa `done`, tooltip dòng đó hiện `publish_incomplete_hint` (platform + status tóm tắt).

#### Run behavior

- **Run Selected**: runs only checked sessions × enabled steps.
- **Run All Sessions**: runs `_session_panel._sessions` directly (does NOT mutate checkboxes).
- On error: skips remaining steps for that session, continues next session.
- After completion: calls `_session_panel.refresh()` to update done_steps display.
- `_autofill_keys()` also pushes Gemini key into Step 7 API field before queue runs.

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
- Parser: `parse_translated_panel_text(text)` in `subtitle_editor_io.py` splits on blank lines, finds `[Xs–Ys]` line, everything after = translated.
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
- **Step 3 ASS colors**: `COLOR_MAP` stores true RGB hex. `color_name_to_ass_bgr()` swaps pairs to BGR. `BackColour=&H{alpha}{bgr}` where alpha is inverted (00=opaque, FF=transparent). Do not revert.
- **Step 3 FontSize**: Must use `int(ASS_PLAYRES_Y * font_pct / 100)` (ASS script space). Do NOT use video pixel height.
- **Step 3 BorderStyle**: Must be `4` for background box fill (BackColour). `BorderStyle=3` is outline-only and ignores BackColour.
- **Step 3 preview**: Call `set_source_file(path)` whenever active session or source file changes so preview reflects correct video dimensions.
- **Step 6**: `_ensure_background_track()` auto-runs Demucs if `step4_background` is missing. Keep Demucs temp cleanup logic intact.
- **FFmpeg drawtext**: Use sin/cos only for expressions. No nested if/max/min/overlay_w.
- **delogo filter**: must come BEFORE subtitles filter in the chain.
- Keep Step 5 free of mux/mix logic.
- Keep Step 6 free of TTS API calls.
- Keep Step 7 generation logic independent from Step 5/6 media composition.
- Keep backend/model/API-key mapping inside `core/pipeline/selection.py`.
- When adding new TTS backend, ensure:
  1. Add to `TTS_BACKEND_LABEL_TO_KEY` in `selection.py`
  2. Add to `COST_PER_1M` in `core/pipeline/step5_tts/budget.py`
  3. Add dispatch case in `_generate_tts`
  4. Add placeholder in `_on_backend_changed`
  5. Add key to `KNOWN_KEYS` in `api_keys.py` if needs API key
- If key handling changes, update both `core/api_keys.py` and autofill in `ui/main_window.py`.
- `update_char_count(session)` must be called whenever active session changes in both MainWindow and MultiSessionWindow.
- Step 1 `_api_key_edit.textChanged` syncs to ApiKeyManager — do not remove this signal.
- Step 7 `_api_edit.textChanged` syncs runtime key selection for collect_config.
- Checkbox default in `SessionListPanel._make_row` is **False**. Do not change to True.
- `_rebuild_list(preserve_checked=True)` must be used in all cases except initial load.

## Automation (n8n helper)

Folder: `n8n-automatioin/`

- `export_publish_jobs.py` scans session folders and outputs JSON jobs.
- `session_publish_jobs.py` discovers video/title/description/hashtags/thumbnail and ready-state.
- `mark_publish_result.py` writes per-platform markers after scheduling/upload.

Video discovery priority in scanner:

1. `result/step6_output_*.*`
2. `step6_output.*`
3. `result_output.*`
4. `step5_output.*`
5. `step3_output.*`

Marker files used to skip already posted sessions:

- `posted_youtube.json`
- `posted_facebook.json`

## Default Values (after patch_defaults.py)

| Step   | Setting            | Default                 |
| ------ | ------------------ | ----------------------- |
| Step 1 | Backend            | OpenAI Whisper API      |
| Step 2 | Backend            | OpenAI GPT-4o (index 2) |
| Step 5 | Backend            | gTTS (index 3)          |
| Step 6 | TTS voice vol      | 124%                    |
| Step 6 | BGM vol            | 145%                    |
| Step 6 | Original voice vol | 31%                     |
