# SubSync — AI Video Subtitle Pipeline

Tự động **transcribe → dịch → burn subtitle → tách giọng → thêm giọng tiếng Việt** vào video.

```
Video gốc (Trung/Anh/Nhật...)
  ① Whisper       → transcript + timestamps
  ② Translate     → dịch sang tiếng Việt
  ③ Burn Subtitle → ghép phụ đề vào video (ASS hard-sub, preview trực tiếp)
  ④ Separate Voice→ tách giọng người / nhạc nền (Demucs)
  ⑤ TTS           → sinh file giọng đọc tiếng Việt (FPT/Zalo/gTTS/OpenAI/ElevenLabs)
  ⑥ Add Voice     → ghép TTS + nhạc nền vào video cuối
```

---

## Yêu cầu hệ thống

|        | Tối thiểu                         | Khuyến nghị               |
| ------ | --------------------------------- | ------------------------- |
| OS     | Windows 10 / macOS 12 / Ubuntu 20 | Windows 11 / macOS 14     |
| Python | 3.11                              | 3.11                      |
| RAM    | 8 GB                              | 16 GB                     |
| Disk   | 5 GB                              | 20 GB (models)            |
| GPU    | Không cần                         | NVIDIA (tăng tốc Whisper) |

> ⚠️ **Python 3.13 không khuyến nghị** — PyTorch chưa hỗ trợ đầy đủ Python 3.13.  
> Dùng Python **3.11** để tránh lỗi.

---

## Cài đặt

### Bước 1 — Python 3.11

**Windows:**

```
https://www.python.org/downloads/release/python-3119/
```

Tải bản `Windows installer (64-bit)`, tích chọn **Add Python to PATH**.

**macOS:**

```bash
brew install python@3.11
```

**Ubuntu:**

```bash
sudo apt install python3.11 python3.11-venv
```

---

### Bước 2 — ffmpeg (bắt buộc)

**Windows:**

```powershell
winget install ffmpeg
# Hoặc tải tại: https://ffmpeg.org/download.html
# Giải nén và thêm vào PATH
```

**macOS:**

```bash
brew install ffmpeg
```

**Ubuntu:**

```bash
sudo apt install ffmpeg
```

Kiểm tra:

```bash
ffmpeg -version
```

---

### Bước 3 — Tạo virtual environment

```bash
# Windows
py -3.11 -m venv venv
venv\Scripts\activate

# macOS / Linux
python3.11 -m venv venv
source venv/bin/activate
```

---

### Bước 4 — Cài Python packages

```bash
pip install -r requirements.txt
```

> Lần đầu có thể mất 5-10 phút do tải PyTorch + Whisper.

---

### Bước 5 — Chạy app

```bash
python main.py
```

---

## API Keys (tuỳ chọn)

### Gemini (Free — Step 2 dịch tốt nhất)

1. Vào **https://aistudio.google.com**
2. Đăng nhập Google → **Get API Key** → Create API key
3. Copy key dạng `AIzaSy...`
4. Paste vào ô API Key trong Step 2

Giới hạn free: **1500 request/ngày**, không cần thẻ tín dụng.

---

### FPT AI TTS (Free — Step 5 giọng Việt tốt nhất)

1. Vào **https://fpt.ai/tts**
2. Đăng ký tài khoản → vào Dashboard
3. Tạo API Key
4. Paste vào ô API Key trong Step 5

Giới hạn free: **1 triệu ký tự** (đủ dùng hàng trăm video).

---

### OpenAI (Trả phí, ~$0.002/video)

1. Vào **https://platform.openai.com**
2. Tạo API Key
3. Dùng cho Step 2 (dịch) hoặc Step 5 (TTS)

---

### Zalo AI TTS (Free — giọng Việt native)

1. Vào **https://zalo.ai/developers**
2. Đăng ký → tạo App → lấy API Key

---

## Ollama — AI verify offline (Free, không cần internet)

Dùng để verify lại bản dịch sau Google Translate.

### Cài Ollama

```
https://ollama.com → Download
```

### Pull model (chọn 1)

```bash
ollama pull llama3      # 4.7GB — tốt nhất
ollama pull mistral     # 4.1GB — nhẹ hơn
ollama pull qwen2       # 4.4GB — tốt cho tiếng Á
ollama pull gemma2      # 5.4GB — Google
```

### Kiểm tra Ollama đang chạy

```bash
ollama list             # xem các model đã pull
curl http://localhost:11434/api/tags   # phải trả về JSON
```

Trong UI: Step 2 → Verify via → **Ollama — local free ⭐** → chọn model.

---

## Cấu trúc project

```
subsync/
├── main.py                        ← Entry point
├── requirements.txt               ← Python packages
├── requirements.docker.txt        ← Packages cho Docker (không có PyQt6)
├── Dockerfile                     ← Docker image
├── docker-compose.yml             ← Docker Compose
├── api_server.py                  ← FastAPI backend (cho Docker mode)
│
├── core/
│   ├── session.py                 ← Quản lý session folder
│   └── pipeline/
│       ├── base.py                ← Abstract BaseStep + Worker
│       ├── step1_transcribe.py    ← Whisper STT
│       ├── step2_translate.py     ← Dịch + SmartFix + Verify
│       ├── step3_burn.py          ← Burn subtitle (ffmpeg ASS + QPainter preview)
│       ├── step4_separate.py      ← Tách giọng (Demucs)
│       ├── step5_tts.py           ← Text-to-Speech
│       └── step6_add_voice.py     ← Ghép TTS + nhạc nền vào video
│
└── ui/
    ├── main_window.py             ← Cửa sổ chính PyQt6 + throughput logging
    ├── multi_session_window.py    ← Multi-session queue window
    └── widgets/
        ├── step_card.py           ← Card cho mỗi step
        ├── drop_zone.py           ← Drag & drop zone
        ├── subtitle_editor.py     ← Editable subtitle panel
        └── session_info_editor.py ← Title / notes / thumbnail editor
```

---

## Session folder

Mỗi lần chạy tạo 1 folder riêng:

```
<base_dir>/<tên_file>_<YYYYMMDD_HHMMSS>/
  ├── session.json              ← metadata
  ├── step1_transcript.json     ← transcript + timestamps
  ├── step1_transcript.txt      ← plain text
  ├── step2_translated.json     ← bản dịch từng segment
  ├── step2_translated.srt      ← file SRT tiếng Việt
  ├── step3_output.mp4          ← video có subtitle
  ├── step4_vocals.mp3          ← giọng người (tách riêng)
  ├── step4_background.mp3      ← nhạc nền (tách riêng)
  ├── step5_tts.mp3             ← audio TTS tiếng Việt
  ├── step5_tts_assets/          ← TTS assets per segment
  └── step6_output.mp4          ← video cuối (TTS + nhạc nền mixed)
```

---

## Chạy bằng Docker

```bash
# Build và chạy backend
docker-compose up -d

# Xem log
docker-compose logs -f subsync

# UI vẫn chạy native trên máy
python main.py
```

Biến môi trường (tạo file `.env`):

```env
GEMINI_API_KEY=AIzaSy...
OPENAI_API_KEY=sk-...
FPT_API_KEY=...
ZALO_API_KEY=...
VIDEO_DIR=D:/Videos
```

---

## Troubleshooting

### Lỗi `No module named 'core.pipeline'`

```bash
# Tạo file __init__.py còn thiếu
python -c "
import os
for d in ['core', 'core/pipeline', 'ui', 'ui/widgets', 'utils']:
    open(os.path.join(d, '__init__.py'), 'a').close()
print('Done')
"
```

### Lỗi `PyAudioOp` / `audioop`

```bash
pip install audioop-lts
```

### Lỗi `Fatal Python error: _PyThreadState_Attach`

Đang dùng Python 3.13 — cần dùng Python 3.11:

```bash
py -3.11 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### Lỗi `ffmpeg not found`

Cài ffmpeg và đảm bảo có trong PATH:

```bash
ffmpeg -version   # phải in ra version
```

### Gemini lỗi 429 Rate limit

Model đang bị giới hạn. App tự động chờ và retry.
Hoặc đổi sang Google Translate (free, không limit).

### Ollama `Connection refused`

```bash
ollama serve      # khởi động Ollama server
```

---

## So sánh các option dịch

| Backend            | Giá           | Chất lượng VI | Cần key | Offline |
| ------------------ | ------------- | ------------- | ------- | ------- |
| Google Translate   | Free          | ⭐⭐          | ❌      | ❌      |
| Gemini Flash       | Free          | ⭐⭐⭐⭐      | ✅      | ❌      |
| OpenAI GPT-4o-mini | ~$0.002/video | ⭐⭐⭐⭐⭐    | ✅      | ❌      |
| Ollama (verify)    | Free          | ⭐⭐⭐        | ❌      | ✅      |

## So sánh các option TTS

| Backend       | Giá              | Chất lượng VI | Cảm xúc    |
| ------------- | ---------------- | ------------- | ---------- |
| gTTS (Google) | Free             | ⭐⭐          | ❌         |
| FPT AI        | 1M chars free    | ⭐⭐⭐⭐⭐    | ✅         |
| Zalo AI       | Free tier        | ⭐⭐⭐⭐⭐    | ✅         |
| OpenAI TTS    | ~$0.015/1k chars | ⭐⭐⭐⭐      | ✅         |
| ElevenLabs    | 10k chars free   | ⭐⭐⭐⭐⭐    | ⭐⭐⭐⭐⭐ |
