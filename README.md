# Video Subtitle Translator

Tự động dịch video từ tiếng Anh/Trung/Nhật... sang tiếng Việt và ghép phụ đề.

## Pipeline
```
Video/Audio  →  [Whisper]  →  Transcript
                               ↓
                          [Google/OpenAI]  →  Translated segments
                               ↓
                           [ffmpeg]  →  Video with Vietnamese subtitles
```

## Cài đặt
```bash
# 1. Cài ffmpeg
brew install ffmpeg         # macOS
winget install ffmpeg       # Windows

# 2. Tạo venv
python -m venv venv
source venv/bin/activate    # macOS/Linux
venv\Scripts\activate       # Windows

# 3. Cài packages
pip install -r requirements.txt
```

## Chạy
```bash
python main.py
```

## 3 bước sử dụng

### Step 1 — Transcribe
- Chọn model Whisper (base = nhanh, medium/large = chính xác hơn)
- Chọn ngôn ngữ gốc (hoặc Auto detect)
- Nhấn **Transcribe**

### Step 2 — Translate
- **Google (free)**: miễn phí, không cần API key, ~100 segments/phút
- **OpenAI GPT-4o-mini**: chất lượng cao hơn, cần API key
- Nhấn **Translate**

### Step 3 — Burn Subtitles
- **Soft sub**: đính kèm subtitle track → file nhỏ, có thể bật/tắt sub trong player
- **Hard sub**: burn thẳng vào pixel → mọi player/thiết bị đều hiện sub
- Nhấn **Burn into Video**

## Lưu ý
- Soft sub với `.mp4` dùng codec `mov_text`, với `.mkv` dùng `srt`
- Hard sub yêu cầu re-encode (chậm hơn, file lớn hơn)
- Có thể **Save SRT only** nếu không cần burn vào video
