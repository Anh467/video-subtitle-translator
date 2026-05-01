# Automation For n8n Publish Flow

This folder contains helpers for n8n so it can publish multiple sessions from one
workspace folder to YouTube and Facebook. The export script now also assigns a
scheduled publish time every 4 hours based on the original `published_at` value
stored in each session's `session.json`.

## 1. What this automation does

- Scans a workspace folder containing many session subfolders
- Reads `session.json` from each session
- Uses session metadata for:
  - final video path
  - thumbnail
  - title
  - description
  - hashtags
  - published_at
- Orders jobs by `published_at` and session folder name
- Assigns a `scheduled_at` timestamp in 4-hour increments
- Scans session folders recursively by default
- Skips sessions already marked as posted for selected platforms
- Skips sessions already exported in previous runs by default
- Writes back markers after a platform schedules/uploads the post

## 2. Run the export script

From the repository root run:

```powershell
python n8n-automatioin/export_publish_jobs.py "D:\Downloads\workspace\horror_story" --platform youtube --platform facebook --schedule-interval-hours 4 --pretty
```

Useful options:

- `--debug`: print reasons each session folder is kept/skipped (to stderr)
- `--audit`: print each scanned session folder path and skip/include reason (to stderr)
- `--non-recursive`: only scan direct child folders of `base_dir`
- `--thumbnail-pattern "<glob>"`: add custom thumbnail filename patterns; repeatable
- `--include-exported`: include sessions already exported before
- `--no-mark-exported`: do not write exported markers
- `--info-file`: filename for the run summary info JSON under `base_dir`

Example with debug and custom thumbnail patterns:

```powershell
python n8n-automatioin/export_publish_jobs.py "D:\Downloads\workspace\horror_story" --platform youtube --platform facebook --schedule-interval-hours 4 --thumbnail-pattern "*.jpg" --thumbnail-pattern "cover_*.*" --debug --pretty
```

After each run, the exporter now writes:

- Per-session marker file: `exported_publish_job.json` inside each exported session folder (includes **`status`**; see below)
- Run summary file: `export_publish_jobs_info.json` in `base_dir` (or custom `--info-file`)

**`exported_publish_job.json` → `status`:** **`pending_n8n`** after the Python exporter writes the marker. **`partial`** once some **`posted_<platform>.json`** markers exist; **`completed`** when every platform listed in **`platforms`** on that file exists. **`mark_publish_result.py`** refreshes **`status`** + **`posted_*`** after each successful mark. **`failed`** does **not** block re-scan (nor does **`pending_n8n`**): if **Node 4** / exporter succeeded but **n8n crashed**, the folder can appear again in **`jobs`** on the next cron so you don’t silently “lose” the session (**`completed`** and legacy files **without `status`** still block by default unless **`--include-exported`**). Duplicate uploads while **`partial`** are mitigated because **`posted_*`** markers exclude already-posted platforms from the scanner.

Former **`--pending-export-stale-hours`** flag **was removed**; **`pending_n8n`** no longer skips the exporter scan (**`completed`** and legacy markers without **`status`** still do).

If the folder contains jobs, the output includes `scheduled_at` for each one:

```json
{
  "base_dir": "D:/Downloads/workspace/horror_story",
  "platforms": ["youtube", "facebook"],
  "count": 3,
  "schedule_interval_hours": 4.0,
  "schedule_start": "2026-04-17T08:00:00+00:00",
  "jobs": [
    {
      "session_folder": "D:/Downloads/workspace/horror_story/2026-04-17_...",
      "video_path": "...",
      "thumbnail_path": "...",
      "title": "...",
      "description": "...",
      "hashtags": ["#悬疑"],
      "published_at": "2026-04-17",
      "scheduled_at": "2026-04-17T08:00:00+00:00",
      "ready": true,
      "missing": [],
      "youtube_posted": false,
      "facebook_posted": false
    }
  ]
}
```

### Notes on published_at

- The export uses the `published_at` value from `session.json` first.
- If `published_at` is missing, it falls back to the session folder name
  prefix like `2026-04-17_...`.
- Jobs are sorted by that timestamp so the oldest original publish date is
  scheduled first.

## 3. Getting started from scratch (install n8n and first run)

This repo does not bundle n8n. Use this checklist once per machine/environment,
then reuse the workflow for each publish batch.

### 3.1 Install Node.js and start n8n (Windows-friendly quick path)

1. Install [Node.js LTS](https://nodejs.org/) if needed.
2. In PowerShell:

   ```powershell
   npx n8n
   ```

   Wait until n8n is listening; then open **http://localhost:5678** (exact URL
   and port appear in the terminal output).

3. For Docker or production hosting (HTTPS, persistence), follow:
   https://docs.n8n.io/hosting/installation/docker/

### 3.2 Where YouTube / Facebook secrets live

- **Not** in this Git repo and not inside `session.json`.
- OAuth **Client ID / Client Secret** come from Google Cloud Console and Meta for
  Developers.
- Paste them under **Credentials** inside n8n and attach those credentials to
  your upload/post nodes (Section 5 below).

### 3.3 Python and the Execute Command node

- **Execute Command** runs on the **same host/container** where n8n runs. That
  machine needs `python` on PATH—or use the **full path** to `python.exe` (venv
  is fine).
- Point to scripts under this repo, e.g.
  `<repo-root>\n8n-automatioin\export_publish_jobs.py`. Either set the node
  **working directory** to the repo root (then use paths like
  `n8n-automatioin/export_publish_jobs.py`), or pass **absolute** paths for both
  the exporter and `mark_publish_result.py`.
- **n8n Cloud** does **not** ship the Execute Command node at all (see [Execute Command](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.executecommand/): *Not available on Cloud*). Use **self-hosted** n8n.

#### n8n 2.0+ — `Unrecognized node type: n8n-nodes-base.executeCommand`

From **n8n 2.0**, risky nodes (including **Execute Command** and **Read/Write Files from Disk**) are **blocked by default** via `NODES_EXCLUDE`. If your instance still excludes them, imported workflows fail with *Unrecognized node type* even though the JSON is valid.

**Fix (self-hosted only):** enable those nodes by clearing the exclude list, e.g. set:

```text
NODES_EXCLUDE=[]
```

See [Block access to nodes](https://docs.n8n.io/hosting/securing/blocking-nodes/) (*Enable nodes that are blocked by default*). Apply the variable in `docker-compose.yml`, a `.env` file, or your process manager, then **restart** n8n. A minimal Docker example is `n8n-automatioin/docker-compose.n8n.example.yml`.

This template also uses **Read/Write Files from Disk** for local `video_path`; the same `NODES_EXCLUDE=[]` (or a custom list that **omits** both `n8n-nodes-base.executeCommand` and `n8n-nodes-base.readWriteFile`) is required for the full flow.

If you use a **narrower** `NODES_EXCLUDE` instead of `[]`, remove only the entries you need—do not leave `executeCommand` or `readWriteFile` in the list.

**n8n Cloud:** there is no env toggle; you cannot run this workflow as-is. Options: self-host, or replace shell/file nodes with your own **HTTP** microservice that runs the Python scripts on a machine you control.

#### Docker Desktop (Windows): bước cụ thể

1. Cài **Docker Desktop** và để trạng thái **Running** (WSL2 backend nếu được hỏi).
2. Sửa **`n8n-automatioin/docker-compose.n8n.example.yml`**: hai dòng **volumes** đầu là đường dẫn **máy Windows** của bạn → map vào **`/repo`** (clone repo) và **`/workspace`** (thư mục cha session). Trong file mẫu dùng `/` thay cho `\` kiểu `D:/path/to/...`.
3. Từ **thư mục gốc repo** (nơi có `n8n-automatioin/`), chạy:
   ```powershell
   docker compose -f n8n-automatioin/docker-compose.n8n.example.yml up -d --build
   ```
4. Mở **`http://localhost:5678`** (lần đầu tạo tài khoản owner nếu được hỏi).
5. Trong workflow **sau khi import** — chỉ node **`2 Publishing config` (Code)**: chỉnh **`REPO_ROOT`**, **`WORKSPACE`**, **`FACEBOOK_PAGE_ID`**, **`SCHEDULE_INTERVAL_HOURS`**, **`SCHEDULE_START`** cho khớp Docker/khớp máy (**không còn node Set** trong template; Manual trigger chỉ cần **Execute**).
6. Biến **`N8N_RESTRICT_FILE_ACCESS_TO`** trong compose phải chứa các path container tương ứng; **nhiều thư mục — cách nhau bằng `;`** (vd. `/repo;/workspace`). Dùng **phẩy `,`** sẽ bị hiểu là **một** path duy nhất và Read/Write sẽ từ chối `/workspace/...`.

Image được build từ **`n8n-automatioin/Dockerfile.n8n`** (n8n + Python) vì **Execute Command chạy trong container**, không dùng Python cài trên Windows host.

**Build lỗi “no apk/apt-get”:** image `n8nio/n8n` giai đoạn runtime **đã gỡ `apk`**, nên không thể `RUN apk add` trực tiếp. `Dockerfile.n8n` cài Python trên **`alpine:3.22`** rồi **COPY** sang image n8n (multi-stage). Nếu Alpine đổi phiên bản thư viện `libpython3.12`, bạn có thể cần chỉnh tên file trong `COPY` cho khớp gói `python3` trên [pkgs.alpinelinux.org](https://pkgs.alpinelinux.org/package/v3.22/main/x86_64/python3).

### 3.4 Build the workflow step by step

1. n8n → **Workflows** → new workflow.
2. Add **Manual Trigger** for tests add **Cron** when you want a schedule.
3. Add **Set** → field **`base_dir`** → your workspace, e.g.
   `D:\Downloads\workspace\horror_story`.
4. Add **Execute Command** to run `export_publish_jobs.py` as in Section 4.
   Verify stdout is the JSON object with a `jobs` array.
5. Add **Code** to parse stdout into one item per job (examples in Section 4).
6. Add **Split In Batches**, batch size **1**.
7. Add **YouTube** upload node; map expressions in Section 4.
8. Add **Facebook / Meta** post node; map fields in Section 4.
9. After YouTube succeeds, **Execute Command** → `mark_publish_result.py`
   **`--platform youtube`** (templates in Section 4). Rename JSON fields (`videoId`,
   `url`, …) if your node version differs.
10. After Facebook succeeds, **`mark_publish_result.py`** **`--platform facebook`**.
11. Section 5 → create credentials → attach them to YouTube/Facebook nodes.
12. Save → **Execute workflow** with Manual Trigger using **one** ready session first.

### 3.5 Verify exports outside n8n when debugging

If n8n receives `count: 0`, run Section 2 from the shell with **`--debug`** or
**`--audit`** to see `incomplete`, `exported`, or `posted` skips before fixing the graph.

`session_publish_jobs.py` still expects **title** and **description** in **`step7_publish_info.json`** or **`session.json`**, and a **thumbnail image** (either matching the default thumbnail glob patterns or **any** `.jpg`/`.png`/… under the session folder — newest wins as a fallback after pattern search). For **video**, it looks under **`result/`**, then common `step6_output.*` names; if nothing matches, it falls back to the **newest** file with a supported video extension **anywhere under the session folder** (skipping `.git` / `node_modules`–like dirs). If the wrong file is picked among many clips, place the final render in **`result/`** or rename it to the `step6_output` style and remove extras.

### 3.6 Import a ready-made workflow (JSON templates)

There are two workflow files under `n8n-automatioin/workflows/`:

| File | Purpose |
|------|---------|
| **`publish_sessions.template.json`** | Full path: export jobs → read video from disk → **YouTube upload** → markers → **Facebook Graph** (video) → markers, with **Code** assertion steps and **Execute Command** logging. |
| **`publish_sessions_error.template.json`** | Optional **Error Trigger** chain that appends one JSON line to an error log via `workflow_log_append.py`. |

#### Import steps (main workflow)

1. n8n → **Workflows** → **Import from File** (or drag onto the canvas).
2. Open **`n8n-automatioin/workflows/publish_sessions.template.json`**.
3. **Configure once after import** — open the **Code** node **`2 Publishing config (sửa 1 lần trong Code)`** only; all paths and schedule defaults live there (**no separate Set node** in the shipped template):
   - **`WORKSPACE`** (default parent folder for sessions — same meaning as CLI `base_dir`; Docker compose usually maps host folder → **`/workspace`**).
   - **`REPO_ROOT`**: repo root containing `n8n-automatioin/` (**`/repo`** in Docker compose, Windows path if n8n is native Windows).
   - **`FACEBOOK_PAGE_ID`**, **`SCHEDULE_INTERVAL_HOURS`**, **`SCHEDULE_START`** (empty **ISO** ⇒ Python falls back to the first **`published_at`** — see **`--schedule-start`** in **`export_publish_jobs.py`**).

   **`Webhook`/Cron overrides:** inputs may include **`workspace`**, **`schedule_interval_hours`**, or **`schedule_start`**; when present they override the constants for that execution.

   The snippet trims **`workspace`**/`base_dir`, derives **`log_file`**, builds **`schedule_args`**, and outputs **`repo_root`**, **`base_dir`**, **`log_file`**, **`facebook_page_id`**, **`schedule_args`**, … for downstream nodes.

4. **Credentials (single naming convention)** — in n8n **Credentials**, create and authorize:
   - **YouTube OAuth2 API** named exactly **`YouTube OAuth2 - publish workspace`** (used by the **YouTube upload** node).
   - **Facebook Graph API** named exactly **`Facebook Graph API - page token`** (Page access token with video upload permissions).

   After import, if n8n reports missing credentials, open each node and **re-select** credentials with these names (or rename your saved credentials to match the names in the workflow JSON).

5. **Read/Write Files from Disk** — the template reads `video_path` inside the container. Self‑hosted n8n **`N8N_RESTRICT_FILE_ACCESS_TO`** must list dirs as a **semicolon‑separated** list (`;`), e.g. `/repo;/workspace`. See [Read/Write Files from Disk](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.readwritefile/).

6. If **`repo_root`** or **`base_dir`** chứa khoảng trắng, có thể cần bọc argument trong quotes trong các node **Execute Command** (như **`session_folder`** trong lệnh `mark_publish_result.py` đã có dấu ngoặc kép trong template).

7. **Logging** — Python helper **`n8n-automatioin/workflow_log_append.py`** appends one JSON object per line with `ts`, `level`, `event`, and optional `message` / `data`. The main workflow writes events such as `workflow_start`, `job_begin`, `youtube_ok`, `facebook_ok`.

8. **Optional error workflow** — import **`publish_sessions_error.template.json`**, edit **Set error paths**, then in the **main** workflow’s **Settings**, set **Error workflow** to this workflow (or attach it in your n8n deployment as documented). Failed runs then append a row to your **error** JSONL with `event: workflow_error` and `level: ERROR`.

**Job order** — Jobs are sorted by **`published_at`** ascending (oldest first). **`export_publish_jobs.py`** assigns each job’s **`scheduled_at`** starting at **`schedule_start`** (or the first **`published_at`** if unset), adding **`schedule_interval_hours`** per job index. It also sets **`scheduled_publish_unix`** (UTC epoch seconds) for Facebook and **nudges `schedule_start`** forward to at least **now + 11 minutes** so **`scheduled_publish_time`** meets Meta’s usual “not in the immediate past / too soon” rules.

**Why all three videos fired at once** — In per-item mode, **`$('5 Split jobs …').item`** often stays pinned to the **first** job, so every execution reused the **same** `scheduled_at`. The template now uses **`$json.*`** on the current item (after **`6b Carry job → Read`** / **`12b Carry job → Read FB`**) for title, `publishAt`, and Facebook schedule fields. Re-import the template and **re-run** the exporter so jobs include **`scheduled_publish_unix`**.

**Facebook vs YouTube scheduling** — **YouTube** uses **`status.privacyStatus=private`** plus **`publishAt`** (ISO string from **`scheduled_at`**). **Facebook** **`/videos`** uses **`published=false`**, **`scheduled_publish_time`**, and **`unpublished_content_type=SCHEDULED`** as in the template.

After import, n8n may migrate node versions (e.g. **Set** v1 → newer); that is normal.

If the run fails with **`Unrecognized node type: n8n-nodes-base.executeCommand`**, your host is still **excluding** that node (common on **n8n 2.x** defaults) or you are on **n8n Cloud** — see **Section 3.3** (`NODES_EXCLUDE`, self‑hosting).

## 4. Recommended workflow overview and node examples

End-to-end order (aligned with **`publish_sessions.template.json`**):

1. **`Execute workflow`** → **`2 Publishing config`** (**`Code`**): constants **`WORKSPACE`**, **`REPO_ROOT`**, **`FACEBOOK_PAGE_ID`**, schedule (**optional input overrides from Webhook/Cron**).
2. Parallel: **log workflow start**, and **`Execute Command`** → **`export_publish_jobs.py`** **`{{ $json.schedule_args }}`** (plus **`base_dir`** / platforms / **`--pretty`**).
3. **`5 Split jobs (+ giữ config)`** (`Code`) — merges exporter JSON stdout with **`cfg`** from **`2 Publishing config`**, one row per job (jobs already sorted **`published_at`** ASC in Python).
4. Các node theo **từng job**: log → đọc file video → upload YouTube/Facebook → `mark_publish_result.py` (**template không dùng `Split In Batches`** — mỗi job là một luồng item).

### Exporter via `Execute Command` (single PowerShell-style line)

```powershell
python {{$json.repo_root}}/n8n-automatioin/export_publish_jobs.py {{$json.base_dir}} --platform youtube --platform facebook {{$json.schedule_args}} --pretty
```

**`schedule_args`** is built in **`2 Publishing config`** (e.g. `--schedule-interval-hours 4` and optionally `--schedule-start 2026-05-01T08:00:00+07:00`). For a one-off CLI run you can pass the same flags manually; see **`export_publish_jobs.py`** **`--schedule-start`** and **`--schedule-interval-hours`**.

### Exporter via `Execute Command` (split command / arguments)

- Command: `python`
- Arguments (one fragment per UI slot is typical):

  - `n8n-automatioin/export_publish_jobs.py`
  - `{{$json["base_dir"]}}`
  - `--platform`, `youtube`, `--platform`, `facebook`
  - `--schedule-interval-hours`, `4` (or your value)
  - optional: `--schedule-start`, `2026-05-01T08:00:00+07:00`
  - `--pretty`

### Example `Code` node (references previous node output shape)

Depending on Execute Command wiring, stdin/stdout appears under `text`, `stdout`, or `data`:

```js
const output = JSON.parse(
  items[0].json.text || items[0].json.stdout || items[0].json.data,
);
return output.jobs.map((job) => ({ json: job }));
```

Or when the exporter output is already on `$json`:

```js
const outputText = $json.stdout || $json.text || $json.data;
const parsed = JSON.parse(outputText);
return parsed.jobs.map((job) => ({ json: job }));
```

### Example `Split In Batches`

- Batch size: `1`
- Keep input data (if your template uses it after the loop).

### Posted markers (`mark_publish_result.py`)

After **YouTube** (field names vary by community node—in n8n, inspect the node's
output schema and remap `videoId` / `url` if needed):

```powershell
python n8n-automatioin/mark_publish_result.py "{{$json["session_folder"]}}" --platform youtube --remote-id "{{$node["YouTube"].json["videoId"]}}" --url "{{$node["YouTube"].json["url"]}}" --scheduled-at "{{$json["scheduled_at"]}}"
```

After **Facebook** (same caveat for output field names):

```powershell
python n8n-automatioin/mark_publish_result.py "{{$json["session_folder"]}}" --platform facebook --remote-id "{{$node["Facebook"].json["postId"]}}" --url "{{$node["Facebook"].json["permalink_url"]}}" --scheduled-at "{{$json["scheduled_at"]}}"
```

### Example YouTube node field mapping

- Video file: `{{$json["video_path"]}}`
- Title: `{{$json["title"]}}`
- Description: `{{$json["description"]}}`
- Scheduled at: `{{$json["scheduled_at"]}}`
- Thumbnail: `{{$json["thumbnail_path"]}}`

### Example Facebook node field mapping

- Message / caption: `{{$json["description"]}}`
- Scheduled publish time: `{{$json["scheduled_at"]}}`
- Video or media path: `{{$json["video_path"]}}` (and thumbnail if your node expects it separately)

## 5. OAuth credentials for YouTube and Facebook

### YouTube API credentials

1. Open Google Cloud Console: https://console.cloud.google.com/
2. Create/select a project.
3. Enable `YouTube Data API v3`.
4. Go to `APIs & Services > Credentials`.
5. Create `OAuth client ID`:
   - Application type: `Web application`
   - Authorized redirect URI: your n8n callback URL, e.g. `https://<your-n8n-host>/rest/oauth2-credential/callback`
6. Copy `Client ID` and `Client secret`.
7. In n8n, create a YouTube OAuth2 credential using these values and authorize it.

> Note: YouTube video upload in n8n generally requires OAuth2 authorization, not a plain API key.

### Facebook / Meta credentials (Fan Page, not personal profile)

The template uploads with **Graph `POST /{page-id}/videos`**. Two things must line up:

1. **`FACEBOOK_PAGE_ID`** in workflow node **`2 Publishing config`** — set this to your **Fan Page ID** (numeric ID of the Page you manage). The node **Facebook • upload video** uses it as the Graph **`node`** so the video is published **on that Page’s timeline**, not on a personal profile.
2. **Page access token** — in n8n, credential **`Facebook Graph API - page token`** must be a **Page** access token with permission to post as that Page (not only a user token meant for `/me` on a personal account).

How to get the values:

1. Open Meta for Developers: https://developers.facebook.com/
2. Create a new App.
3. Add `Facebook Login` and `Pages API`.
4. In `Settings > Basic`, copy `App ID` and `App Secret`.
5. Generate a **Page** access token for the target Fan Page with scopes such as:
   - `pages_manage_posts`
   - `pages_read_engagement`
   - `pages_manage_engagement`
   - `pages_show_list`
   - `pages_read_user_content`
6. In n8n, create a Facebook Graph API credential (same name as in the template) and paste or OAuth as required so the effective token is the **Page** token for your Fan Page.

**Finding the Fan Page ID:** Meta Business Suite / Page **About** section, or Graph API Explorer (`me/accounts` with a user token that has `pages_show_list`), or third-party “Page ID” tools — use the ID that matches the Page you want.

## 6. What to run

From the repository root:

```powershell
python n8n-automatioin/export_publish_jobs.py "D:\Downloads\workspace\horror_story" --platform youtube --platform facebook --schedule-interval-hours 4 --pretty
```

Then process the generated jobs in n8n.

## 7. Notes

- The exporter reads `published_at` from `session.json` first.
- The exporter scans session folders recursively by default.
- If missing, it falls back to the folder name prefix `YYYY-MM-DD_...`.
- Jobs are ordered by original publish date and then by folder name.
- `scheduled_at` is set every 4 hours in order.
- Use `--include-posted` to re-export already-posted sessions.
- Use `--include-exported` to include sessions already exported in prior runs.
- Use `--debug` to print why each session folder is skipped.
- Use one or more `--thumbnail-pattern` values when your thumbnail file does not follow `thumbnail.*`.

## 8. Troubleshooting

- If **Execute Command** fails, confirm Python is available to the **same process**
  that runs n8n (PATH vs full path), and that **working directory** or script paths
  point at this repo correctly.
- If **npx n8n** does not start, check firewall/port clashes for default **5678**;
  inspect the startup log URL n8n prints.
- If there are no jobs, confirm session folders contain `session.json`.
- If metadata is missing, check `step7_publish_info.json` or `session.json`.
- If a folder is skipped and you do not know why, rerun with `--debug` or `--audit`.
- If thumbnail files use custom names, pass explicit patterns via `--thumbnail-pattern`.
- If YouTube upload fails, verify OAuth redirect URI and permission scopes.
- If Facebook posting fails, verify the page token and required permissions.

## 9. Reminder

Always test with a small number of sessions first.
