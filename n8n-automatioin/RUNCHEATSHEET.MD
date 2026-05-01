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
Mở trình duyệt: http://localhost:5678
(lần đầu tạo tài khoản nếu được hỏi).

4) Chỉnh một node Code trong workflow
Import n8n-automatioin/workflows/publish_sessions.template.json.

Mở node 2 Publishing config (sửa 1 lần trong Code), sửa đầu file cho đúng Docker:

const REPO_ROOT = String.raw`/repo`;
const WORKSPACE = String.raw`/workspace`;
const FACEBOOK_PAGE_ID = 'ID_FANPAGE_CUA_BAN';
const SCHEDULE_INTERVAL_HOURS = 4;   // hoặc 24, tùy bạn
const SCHEDULE_START = '';          // hoặc chuỗi ISO nếu cần
Save workflow.

5) Gắn credential
YouTube OAuth2 - publish workspace
Facebook Graph API - page token (token Fanpage)
Đúng tên như trong template (hoặc đổi tên credential cho khớp).

6) (Khuyến nghị) Thử export trong container trước khi bấm n8n
docker compose -f n8n-automatioin/docker-compose.n8n.example.yml exec n8n sh -lc "python /repo/n8n-automatioin/export_publish_jobs.py /workspace --platform youtube --platform facebook --debug --audit --pretty 2>&1 | tail -40"
count > 0 → được phép vào nhánh job trong n8n.
count: 0 → xem dòng [skip] (thiếu video/title/description/thumbnail đã exported…).
7) Chạy automation trong UI
Workflow → Execute workflow (Manual trigger đầu graph).

Ngừng n8n:

docker compose -f n8n-automatioin/docker-compose.n8n.example.yml down
Checklist ngắn:
compose đúng 2 đường /repo + /workspace → up -d --build → chỉnh Publishing config → credential → (tùy chọn) exec python export kiểm tra → Execute trong n8n.

Nếu bạn báo chỗ đang kẹt (lệnh nào, dòng log nào), có thể khoanh tiếp được.