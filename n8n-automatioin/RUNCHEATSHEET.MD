1) Chuẩn bị máy Windows
Cài Docker Desktop, bật chạy.
Repo clone tại chỗ ví dụ:
D:\Nguyen-Anh-Viet\Tutorial\Python\audio-to-text\video-subtitle-translator
Workspace session: ví dụ
D:\Downloads\workspace\horror_story
2) Sửa docker-compose.n8n.example.yml
Hai volume phải trỏ đúng đường Windows của bạn:

environment:
  NODES_EXCLUDE: "[]"
  N8N_RESTRICT_FILE_ACCESS_TO: "/repo;/workspace"
volumes:
  - n8n_data:/home/node/.n8n
  - "D:/Nguyen-Anh-Viet/Tutorial/Python/audio-to-text/video-subtitle-translator:/repo:rw"
  - "D:/Downloads/workspace/horror_story:/workspace:rw"
(Lưu ý dùng / trong path bên trái.)

3) Build và bật n8n
PowerShell:

cd D:\Nguyen-Anh-Viet\Tutorial\Python\audio-to-text\video-subtitle-translator
docker compose -f n8n-automatioin/docker-compose.n8n.example.yml up -d --build

docker compose -f n8n-automatioin/docker-compose.n8n.example.yml up -d
Mở trình duyệt: http://localhost:5678
(lần đầu tạo tài khoản nếu được hỏi).

4) Chỉnh một node Code trong workflow
Import n8n-automatioin/workflows/publish_sessions.template.json.

Mở node **2 Publishing config**, chỉnh trong Code:

- Giữ **`N8N_IN_DOCKER = true`** khi chạy n8n trong Docker (Linux). Template sẽ **tự đổi** `REPO_ROOT`/`WORKSPACE` kiểu `D:\...` thành **`/repo`** và **`/workspace`** — tránh lỗi `can't open file ... D:` trong container.
- Đặt **`N8N_IN_DOCKER = false`** chỉ khi bạn cài **n8n trực tiếp trên Windows** (không Docker) và cần đường dẫn ổ đĩa thật.

Fallback mặc định trong template vẫn là `/repo`, `/workspace`; chỉnh `FACEBOOK_PAGE_ID`, `SCHEDULE_*` nếu cần. Save workflow.

**Facebook upload chunk (Python):** tạo file `.env` cạnh compose với `FACEBOOK_PAGE_ACCESS_TOKEN=` (token Fanpage giống credential Graph), rồi `docker compose ... up -d` lại — biến được inject vào container (xem `docker-compose.n8n.example.yml`).

5) Gắn credential
YouTube OAuth2 - publish workspace
Facebook Graph API - page token (token Fanpage)
Đúng tên như trong template (hoặc đổi tên credential cho khớp).

6) (Khuyến nghị) Thử export trước khi bấm n8n

**Trong container Docker** — đường dẫn `/repo` và `/workspace` chỉ tồn tại **bên trong** container (compose đã mount):

docker compose -f n8n-automatioin/docker-compose.n8n.example.yml exec n8n sh -lc "python /repo/n8n-automatioin/export_publish_jobs.py /workspace --platform youtube --platform facebook --debug --audit --pretty 2>&1 | tail -40"

**Trên Windows (PowerShell, Python trên máy)** — **không** dùng `/repo/...` (Windows có thể hiểu thành `D:\repo` và báo *can't open file*). Gọi đúng file trong repo clone của bạn và trỏ **workspace thật trên ổ**, ví dụ:

cd D:\Nguyen-Anh-Viet\Tutorial\Python\audio-to-text\video-subtitle-translator
python n8n-automatioin\export_publish_jobs.py "D:\Downloads\workspace\horror_story" --platform youtube --platform facebook --debug --audit --pretty

(Dữ liệu giống nhau; chỉ khác chỗ chạy script và chỗ đặt session.)

count > 0 → được phép vào nhánh job trong n8n.
count: 0 → xem [skip] / `scan_summary` / `audit_trail` trong JSON (thiếu asset, marker exported, v.v.).
7) Chạy automation trong UI
Workflow → Execute workflow (Manual trigger đầu graph).

Ngừng n8n:

docker compose -f n8n-automatioin/docker-compose.n8n.example.yml down
Checklist ngắn:
compose đúng 2 đường /repo + /workspace → up -d --build → chỉnh Publishing config → credential → (tùy chọn) exec python export kiểm tra → Execute trong n8n.

Nếu bạn báo chỗ đang kẹt (lệnh nào, dòng log nào), có thể khoanh tiếp được.