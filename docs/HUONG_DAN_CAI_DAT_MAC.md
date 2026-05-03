# Hướng dẫn cài SubSync trên macOS (máy mới, chưa cài gì)

Ứng dụng **SubSync** (dịch phụ đề video) chạy trên máy Mac của bạn. Bạn **không cần biết lập trình** — chỉ cần làm đúng thứ tự các bước dưới đây và **copy–dán** lệnh vào **Terminal**.

---

## 0. Chuẩn bị trước khi bắt đầu

| Nội dung | Ghi chú |
|----------|---------|
| **Thời gian** | Lần đầu thường **30–90 phút** (tải Python, thư viện, có thể tải mô hình AI). |
| **Internet** | Cần mạng ổn định. |
| **Dung lượng** | Nên trống **ít nhất ~15–20 GB** ổ cứng (thư viện + mô hình Whisper có thể nặng). |
| **Phiên bản macOS** | Nên **macOS 12 trở lên** (khuyến nghị 14+). |

Bạn cần có **bộ mã nguồn SubSync** trên máy Mac, một trong hai cách:

- **Cách A — Tải ZIP:** vào trang GitHub của dự án → **Code** → **Download ZIP** → giải nén ra thư mục (ví dụ `Downloads/video-subtitle-translator`).
- **Cách B — Git:** nếu bạn đã quen Git thì `git clone ...` (bỏ qua nếu không biết).

Ghi nhớ **đường dẫn thư mục** sau khi giải nén, ví dụ:

`/Users/TenBan/Downloads/video-subtitle-translator`

---

## 1. Mở Terminal (Cửa sổ dòng lệnh)

1. Nhấn **Command + Space** (mở Spotlight).
2. Gõ: **Terminal**
3. Nhấn **Enter** — cửa sổ đen/trắng mở ra, đó là Terminal.

Mọi lệnh dưới đây gõ (hoặc dán) vào Terminal rồi nhấn **Enter**.

---

## 2. Cài “Bộ công cụ dòng lệnh cho nhà phát triển” (bắt buộc một lần)

Apple thường yêu cầu gói nhỏ tên **Command Line Tools** (miễn phí).

Dán lệnh sau, nhấn Enter, làm theo hộp thoại (Accept / Cài đặt):

```bash
xcode-select --install
```

- Nếu báo **đã cài** → bỏ qua bước này.
- Nếu có cửa sổ cài đặt → chờ cài xong (có thể vài phút đến vài chục phút).

---

## 3. Cài Homebrew (trình quản lý gói — giúp cài Python và ffmpeg dễ)

Dán **một dòng** sau (lấy từ trang chủ Homebrew nếu bạn muốn bản mới nhất; dưới đây là lệnh chuẩn thường dùng):

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

- Làm theo hướng dẫn trên màn hình (có thể hỏi mật khẩu máy Mac).
- Khi xong, Terminal có thể in vài dòng **“Next steps”** — làm theo (thường là chạy thêm 1–2 lệnh `echo` / `eval` để thêm `brew` vào PATH). **Copy đúng** các lệnh đó chạy cho xong.

Kiểm tra Homebrew đã chạy được:

```bash
brew --version
```

Nếu thấy số phiên bản (ví dụ `Homebrew 4.x`) là được.

---

## 4. Cài Python 3.11 và ffmpeg

SubSync khuyến nghị **Python 3.11** (tránh 3.13 vì thư viện AI có thể chưa tương thích đầy đủ).

```bash
brew install python@3.11 ffmpeg
```

Kiểm tra:

```bash
python3.11 --version
ffmpeg -version
```

Cả hai lệnh đều in ra thông tin (không báo `command not found`) là ổn.

---

## 5. Vào thư mục chứa SubSync

Thay `ĐƯỜNG_DẪN_THƯ_MỤC` bằng đường dẫn thật của bạn (có thể kéo thả **cả thư mục** từ Finder vào cửa sổ Terminal để dán đường dẫn).

```bash
cd "ĐƯỜNG_DẪN_THƯ_MỤC"
```

Ví dụ:

```bash
cd "/Users/TenBan/Downloads/video-subtitle-translator"
```

Kiểm tra đúng chỗ (phải thấy file `main.py`):

```bash
ls main.py
```

Nếu báo `No such file` → bạn chưa `cd` đúng thư mục (hoặc chưa giải nén đủ).

---

## 6. Tạo “môi trường ảo” Python (không làm hỏng Python của macOS)

Trong **cùng thư mục** dự án:

```bash
python3.11 -m venv venv
```

Bật môi trường (mỗi lần mở Terminal mới để chạy SubSync, cần chạy lại dòng này):

```bash
source venv/bin/activate
```

Khi thành công, đầu dòng Terminal thường có chữ `(venv)`.

---

## 7. Cài thư viện của SubSync (chờ lâu là bình thường)

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

- Có thể **10–40 phút** tùy máy và mạng.
- Nếu có dòng đỏ **ERROR** — chụp màn hình hoặc copy toàn bộ lỗi gửi người hỗ trợ.

---

## 8. Chạy ứng dụng

Vẫn trong thư mục dự án, với `(venv)` đang bật:

```bash
python main.py
```

Cửa sổ **SubSync** sẽ mở. **Giữ Terminal mở** trong lúc dùng app (đóng Terminal có thể đóng luôn app).

---

## 9. Lần sau muốn mở SubSync

1. Mở **Terminal**
2. Vào thư mục dự án:
   ```bash
   cd "/đường/dẫn/video-subtitle-translator"
   ```
3. Bật venv:
   ```bash
   source venv/bin/activate
   ```
4. Chạy:
   ```bash
   python main.py
   ```

---

## 10. Nếu macOS chặn mở app / “không tin cậy”

- SubSync chạy từ Python **không** qua App Store. Lần đầu nếu có cảnh báo liên quan **bảo mật**, vào **System Settings → Privacy & Security** xem có nút **Open Anyway** không (tùy phiên bản macOS).
- Nếu chỉ là **Gatekeeper** với file tải từ mạng: chuột phải file → **Open** (một số trường hợp).

---

## 11. Lỗi thường gặp (tự xử lý nhanh)

| Hiện tượng | Việc nên làm |
|-------------|----------------|
| `command not found: python3.11` | Chạy lại bước 4 `brew install python@3.11`, hoặc dùng đường dẫn đầy đủ: `$(brew --prefix python@3.11)/bin/python3.11` |
| `command not found: ffmpeg` | `brew install ffmpeg` |
| `No module named 'PyQt6'` | Đảm bảo đã `source venv/bin/activate` rồi `pip install -r requirements.txt` |
| Cài `torch` / Whisper rất lâu hoặc lỗi | Kiểm tra mạng; thử lại `pip install -r requirements.txt`; dùng Python đúng 3.11 |
| `audioop`, `audioop-lts`, hoặc `pydub` báo không có module | Trên **Python 3.13+**, chạy với **venv đang bật** (`source venv/bin/activate`): `pip install audioop-lts` hoặc cài lại `pip install -r requirements.txt` — gói `audioop-lts` đã nằm trong `requirements.txt` |
| App mở nhưng transcribe lỗi | Kiểm tra đủ RAM/disk; lần đầu Whisper có thể tải model |

---

## 12. Ghi chú quan trọng

- **ffmpeg** phải có trên máy (đã cài ở bước 4) — SubSync dùng để xử lý video.
- **API key** (Google Gemini, OpenAI, v.v.) nếu bước nào cần — cấu hình trong app theo hướng dẫn trong **README** chính của repo (mục API).
- Nếu bạn muốn **biểu tượng trên Dock** như app App Store: cần người có Mac **đóng gói** sẵn file `.app` (PyInstaller) hoặc dùng **Shortcuts** trên macOS tạo lối tắt chạy 3 lệnh ở mục 9 — đó là bước nâng cao, không bắt buộc để dùng được.

---

## 13. (Nâng cao) Đóng gói SubSync thành file `.app` bằng PyInstaller

Mục này dành khi bạn muốn **một nhấp vào Finder** như app thường. **Việc tạo file `.app` phải chạy trên macOS** (GitHub Actions `macos-latest` hoặc máy Mac thật; không đóng gói Mac app từ Windows).

Người dùng bản `.app` vẫn cần **đã `brew install ffmpeg`** (như mục 4) trừ khi bạn tự nhúng binary ffmpeg khi đóng gói — gọi FFmpeg qua **`FFMPEG_EXECUTABLE`** chỉ là tùy chọn (xem ô dưới).

**Đường dẫn thật của `ffmpeg` trên Mac:** sau `brew install ffmpeg`, trong Terminal chạy `which ffmpeg`; thường là **`/opt/homebrew/bin/ffmpeg`** (chip Apple Silicon) hoặc **`/usr/local/bin/ffmpeg`** (Intel). Chỉ khi muốn **ép app dùng đúng file đó** (thay cho cái trên PATH) thì trong cùng phiên Terminal mới đó:

```bash
export FFMPEG_EXECUTABLE="$(which ffmpeg)"
python main.py
```

### 13.1. Cài PyInstaller trong venv

```bash
source venv/bin/activate
pip install pyinstaller
```

### 13.2. Lệnh gói tối thiểu (cửa sổ, không có console)

Đổi `THU_MUC_DU_AN` bằng đường dẫn thực tới repo (kéo-thả folder vào Terminal cũng được):

```bash
cd "THU_MUC_DU_AN"
pyinstaller --windowed --name SubSync --clean main.py
```

Kết quả nằm trong thư mục **`dist/SubSync.app`**. Whisper / PyTorch / PyQt có thể cần **`--collect-all`** hoặc `--hidden-import` bổ sung nếu lúc chạy báo `ModuleNotFoundError` — bật **`--onedir`** thay cho onefile thường dễ gỡ lỗi hơn (`pyinstaller --windowed --name SubSync --onedir main.py`).

### 13.3. Sau khi gói: Gatekeeper và thuộc tính tải về

- Lần đầu có thể cần: **chuột phải** `SubSync.app` → **Open**.
- Hoặc (chỉ dùng khi bạn hiểu rủi ro): trong Terminal đổi đường dẫn cho đúng file `.app`:  
  `xattr -cr "/đường/đầy/đủ/tới/dist/SubSync.app"`

Kiểm thử trong Terminal vẫn nên được: **`ffmpeg -version`**.

---

*Bản hướng dẫn này nhằm cài chạy từ mã nguồn trên Mac. Không liên quan n8n / Docker.*
