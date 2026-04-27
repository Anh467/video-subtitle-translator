# SubSync — Project Context Document

> Paste toàn bộ file này vào đầu conversation mới để AI hiểu đủ context.

---

## Tổng quan

**SubSync** là desktop app (PyQt6 + Python 3.11) pipeline xử lý video:

```
Video gốc (Trung/Anh/Nhật...)
  ① Whisper       → transcript + timestamps (subprocess để tránh PyTorch+Qt crash)
  ② Translate     → dịch sang VI (Google/Gemini/OpenAI + SmartFixer + Ollama verify)
  ③ Burn Subtitle → burn sub vào video (ffmpeg soft/hard)
  ④ Separate Voice→ tách giọng/nhạc (Demucs)
  ⑤ Add Voice TTS → TTS tiếng Việt + mix audio (FPT/Zalo/gTTS/OpenAI/ElevenLabs)
```

**Path trên máy user:** `D:\Nguyen-Anh-Viet\Tutorial\Python\audio-to-text\video-subtitle-translator\`

**Python:** 3.13 (có lỗi PyTorch, fix bằng subprocess cho Whisper)

---

## Cấu trúc project

```
subsync/
├── main.py                        ← QApplication entry point
├── requirements.txt
├── core/
│   ├── session.py                 ← Session folder per run + latest_video() chaining
│   └── pipeline/
│       ├── base.py                ← BaseStep, StepWorker(QRunnable), CancelledError, _fmt()
│       ├── step1_transcribe.py    ← Whisper qua subprocess (fix Python 3.13 crash)
│       ├── step2_translate.py     ← Translate + SmartFixer + Ollama verify
│       ├── step3_burn.py          ← ffmpeg burn subtitle
│       ├── step4_separate.py      ← Demucs vocal separation
│       └── step5_tts.py           ← TTS + audio mix
└── ui/
    ├── main_window.py             ← MainWindow: Run All queue, 5 step cards
    └── widgets/
        ├── step_card.py           ← StepCard widget
        └── drop_zone.py           ← Drag & drop zone
```

---

## core/session.py

```python
class Session:
    folder: Path          # <base_dir>/<stem>_<YYYYMMDD_HHMMSS>/
    source_file: str

    # Output paths
    step1_json, step1_txt
    step2_json, step2_srt
    step3_video           # <stem>.mp4
    step4_vocals, step4_background
    step5_tts, step5_video

    # Smart chaining — step3 và step5 có thể chain vào nhau bất kỳ thứ tự
    def latest_video() -> str:
        # step5_video > step3_video > source_file
```

---

## core/pipeline/base.py

```python
class BaseStep(ABC):
    STEP_ID: str
    LABEL: str
    COLOR: str
    ENABLED_BY_DEFAULT: bool

    def run(self, session, config, log, cancel): ...
    def build_config_widget(self, parent=None): ...
    def collect_config(self) -> dict: ...
    def make_worker(self, session, config) -> StepWorker: ...

class StepWorker(QRunnable):
    signals: WorkerSignals  # progress(str), finished(object), error(str), cancelled()
    # setAutoDelete(False) để tránh Qt xóa trước khi signal xử lý xong

class CancelledError(Exception): pass

def _fmt(s: float) -> str: ...  # "1.23s" hoặc "1m 23.4s"
```

---

## Step 1 — Transcribe (step1_transcribe.py)

**Key design:** Whisper chạy trong **child subprocess** (không phải Qt thread) để tránh `Fatal: _PyThreadState_Attach` trên Python 3.13 + PyTorch.

```python
LANGUAGES = {
    "Auto detect": None, "Vietnamese": "vi", "English": "en",
    "Japanese": "ja", "Korean": "ko", "Chinese": "zh",  # "zh" không phải "zh-CN"
    ...
}
WHISPER_MODELS = ["tiny","base","small","medium","large"]

# Normalize language trước khi truyền vào Whisper
if language:
    language = language.split("-")[0].lower()  # "zh-CN" → "zh"

# Runner script tạm + subprocess.run()
runner_code = f"""
import whisper, json, sys
model = whisper.load_model({repr(model_size)})
r = model.transcribe({repr(audio_path)}, language={lang_arg})
print(json.dumps(r))
"""
r = subprocess.run([sys.executable, tmp_script.name],
    capture_output=True, text=True, encoding="utf-8", errors="replace")
raw = json.loads(r.stdout)

# Output
@dataclass class Segment: start, end, text
@dataclass class TranscriptResult: text, segments, language, source_file
```

---

## Step 2 — Translate (step2_translate.py)

### Backends

- **Google** (free): chunk mode — gộp 15 segs/request với `|||` separator
- **Gemini** (free): batch 20 segs + context window ±5 + character summary
  - SDK: `google.genai` (mới), model: `gemini-2.0-flash`
  - Auto-retry 429 với wait time từ error message
  - Delay 4s giữa batches
- **OpenAI** (trả phí): batch 20 segs + context window

### SmartFixer (rule-based, free, no API)

```python
class SmartFixer:
    # Fix pronoun-gender mismatch: "她是我爸爸" → "Đây là bố tôi"
    # 3 tầng: word-level fix → ZH pronoun rules → context inference từ neighbours
    def fix(self, original, translated, prev_segs, next_segs) -> str
```

### Verify pass (optional)

- **Ollama** (local, free): prompt bilingual `[original] → translation` để model thấy contradiction
- **Gemini / OpenAI**
- Batch size 15 cho local models
- `_build_verify_prompt()` riêng cho ollama (ngắn) vs API (chi tiết)

### Ollama models trong dropdown

```
qwen2   — 🇨🇳 Tiếng Trung → VI (recommended, 4.4GB)
llama3  — 🇬🇧 Tiếng Anh → VI (best English, 4.7GB)
llama3.1 — 🇬🇧 Tiếng Anh → VI (newer)
mistral — 💾 RAM thấp <8GB (4.1GB)
gemma2  — ⚖️ Cân bằng (5.4GB)
```

`collect_config` parse model name: `raw.split("—")[0].strip().split()[0]`

### Dataclass

```python
@dataclass class TranslatedSegment: start, end, original, translated
```

### Default UI

- Backend: Google Translate (index 1)
- Verify: None (index 0)
- Rule fix: ON

---

## Step 3 — Burn Subtitles (step3_burn.py)

```python
# Input: session.latest_video() — chain với step5 nếu đã chạy trước
# Soft: mux SRT track (nhỏ, bật/tắt được)
# Hard: burn pixels qua libx264 (mọi player đọc được)
# Font size, color, outline, alignment (ASS format)

def _soft_cmd(input, srt, out): ...   # ffmpeg -c copy -c:s mov_text
def _hard_cmd(input, srt, out, ...):  # ffmpeg -vf subtitles=... libx264
```

**Default:** Hard (burned)

---

## Step 4 — Separate Voice (step4_separate.py)

```python
# Demucs (Meta, local): htdemucs / htdemucs_ft / mdx_extra
# Fix UnicodeEncodeError: copy file sang ASCII temp name trước khi chạy
# PYTHONUTF8=1 + encoding="utf-8" env cho subprocess
# Output: step4_vocals.mp3 + step4_background.mp3
```

---

## Step 5 — TTS + Audio Mix (step5_tts.py)

### TTS Backends

```python
TTS_BACKENDS = {
    "FPT AI TTS (free ⭐ VI)": "fpt",
    "Zalo AI TTS (free VI)":   "zalo",
    "gTTS (Google, free)":     "gtts",
    "OpenAI TTS (natural)":    "openai_tts",
    "ElevenLabs (best+emotion)":"elevenlabs",
}
```

**FPT AI:**

- POST `https://api.fpt.ai/hmi/tts/v5`
- Async polling: response có `async` URL → poll đến khi ready
- Voices: banmai, leminh, lannhi, minhquang, giahuy, linhsan, myan, ngoclam
- Free: 1M ký tự/tháng

**Zalo AI:**

- POST `https://api.zalo.ai/v1/tts/synthesize`
- Trả về WAV (RIFF header) không phải MP3 → dùng `from_wav()`
- Rate limit: delay 1s/request + retry 3 lần khi 429
- Voices: 1=nữ Nam, 2=nam Nam, 3=nữ Bắc, 4=nam Bắc

### Mix modes

```python
MIX_MODES = {
    "TTS only (replace original)":          "replace",
    "TTS + Background music (Step 4)":      "bgm_only",   # ← default
    "TTS + BGM + Original voice (low vol)": "full_mix",
}
```

### Audio mix flow

```
TTS(100%) + BGM(30%) → ffmpeg filter_complex amix → mixed.mp3
mixed.mp3 + video(no audio) → ffmpeg mux → step5_output.mp4
```

### Smart chaining

```python
# Step 5 dùng latest_video() làm input
# Nếu step3 đã chạy trước → step5 chain vào video có subtitle
```

**Default:** FPT AI TTS, banmai, bgm_only

---

## ui/main_window.py

### Layout

```
Title → Session bar → File input → Horizontal scroll cards → Run All row → Log/Preview
```

### Run All queue

```python
self._queue: list = []       # list of enabled steps
self._stop_queue: bool       # flag dừng sau step hiện tại

# Buttons:
# ▶▶ Run All Enabled Steps  → _run_all()
# ⏹ Stop Queue              → _request_stop_queue() (finish current, skip rest)
# ✕ Cancel Job              → _cancel() (kill current immediately)
# Queue label: "Queue: ① Transcribe (5 left)"
```

### Step cards

- Checkbox enable/disable
- QScrollArea maxHeight=180, AlignTop
- Run button với step color
- Status: Waiting/Running/Done/Error/Cancelled

### Video chaining

- Step 3 và Step 5 đều dùng `session.latest_video()` làm input
- Thứ tự chạy bất kỳ đều cho ra 1 video có cả sub + TTS

---

## Các lỗi đã fix

| Lỗi                                                | Fix                                                                   |
| -------------------------------------------------- | --------------------------------------------------------------------- |
| `No module named 'core.pipeline'`                  | Tạo `__init__.py` trong mọi folder                                    |
| `Fatal: _PyThreadState_Attach` (Python 3.13)       | Chạy Whisper trong subprocess                                         |
| `UnicodeDecodeError cp1258`                        | `encoding="utf-8", errors="replace"` cho mọi subprocess               |
| `google.generativeai` deprecated                   | Dùng `google.genai`, `genai.Client()`                                 |
| Gemini 404 NOT_FOUND                               | Model name `gemini-2.0-flash` không có prefix `models/`               |
| Gemini 429 rate limit                              | Auto-retry với wait time, delay 4s/batch                              |
| Gemini dịch sang tiếng Trung                       | Prompt explicit: `"Translate ZH INTO Vietnamese"` + few-shot examples |
| `session is not defined` trong `_translate_gemini` | Truyền `src_lang=transcript.language` từ `run()`                      |
| `pyaudioop` Python 3.13                            | `pip install audioop-lts`                                             |
| Demucs Unicode crash                               | Copy file sang ASCII temp name                                        |
| UI tắt sau 1 job                                   | `_on_cancelled` gọi `_run_next()` thay vì `_set_busy(False)`          |
| `setAutoDelete(False)`                             | Tránh Qt xóa worker trước khi signal xử lý xong                       |
| Whisper `Unsupported language: zh-cn`              | Normalize: `language.split("-")[0].lower()`                           |
| Zalo trả WAV không phải MP3                        | Detect RIFF header → `from_wav()`                                     |
| Zalo 429 rate limit                                | Retry 3 lần + delay 1s/request                                        |

---

## Packages

```
# requirements.txt
PyQt6
openai-whisper
openai
deep-translator
google-genai          # Gemini (KHÔNG dùng google-generativeai)
demucs
gtts
pydub
elevenlabs
audioop-lts           # Fix pydub Python 3.13
ffmpeg-python
requests
```

**System:** `ffmpeg` (winget install ffmpeg), `Ollama` (ollama.com)

---

## API Keys

| Service    | Env var              | Link                |
| ---------- | -------------------- | ------------------- |
| Gemini     | `GEMINI_API_KEY`     | aistudio.google.com |
| OpenAI     | `OPENAI_API_KEY`     | platform.openai.com |
| FPT AI     | `FPT_API_KEY`        | fpt.ai/tts          |
| Zalo AI    | `ZALO_API_KEY`       | zalo.ai/developers  |
| ElevenLabs | `ELEVENLABS_API_KEY` | elevenlabs.io       |

---

## Ollama setup (Windows)

```powershell
# Cài vào ổ D (set trước khi cài)
[System.Environment]::SetEnvironmentVariable("OLLAMA_MODELS","D:\Ollama\models","Machine")

# Pull models
ollama pull qwen2    # tiếng Trung → VI
ollama pull llama3   # tiếng Anh → VI
```

---

## Pending / Known issues

- User đang dùng Python 3.13 (không phải 3.11 như khuyến nghị)
- Zalo TTS vẫn còn rate limit 429 dù đã có retry
- FPT AI chưa test thực tế (user chưa lấy key)
- Ollama đã cài, models đang pull vào D:\Ollama\models
