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

- Per-session marker file: `exported_publish_job.json` inside each exported session folder
- Run summary file: `export_publish_jobs_info.json` in `base_dir` (or custom `--info-file`)

This lets you know which sessions were already exported and avoids exporting them again in the next run.

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
- **n8n Cloud** often **disallows** Execute Command for security. This flow expects
  **self-hosted** n8n (`npx`, Docker on your PC, or your server).

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

## 4. Recommended workflow overview and node examples

End-to-end order (matches Section 3.4):

1. `Manual Trigger` or `Cron Trigger`
2. `Set` with `base_dir` (e.g. `D:\Downloads\workspace\horror_story`)
3. `Execute Command` → `export_publish_jobs.py`
4. `Code` → parse exporter JSON stdout into one row per job
5. `Split In Batches` → size `1`
6. YouTube upload node (with Credential)
7. Facebook post node (with Credential)
8. `Execute Command` → `mark_publish_result.py` after each successful platform

### Exporter via `Execute Command` (single PowerShell-style line)

```powershell
python n8n-automatioin/export_publish_jobs.py {{$node["Set"].json["base_dir"]}} --platform youtube --platform facebook --schedule-interval-hours 4 --pretty
```

Adjust the **`Set`** node name in the expression if yours is not `"Set"`.

### Exporter via `Execute Command` (split command / arguments)

- Command: `python`
- Arguments (one fragment per UI slot is typical):

  - `n8n-automatioin/export_publish_jobs.py`
  - `{{$json["base_dir"]}}`
  - `--platform`, `youtube`, `--platform`, `facebook`
  - `--schedule-interval-hours`, `4`, `--pretty`

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

### Facebook / Meta credentials

1. Open Meta for Developers: https://developers.facebook.com/
2. Create a new App.
3. Add `Facebook Login` and `Pages API`.
4. In `Settings > Basic`, copy `App ID` and `App Secret`.
5. In the app, generate a Page access token with these scopes:
   - `pages_manage_posts`
   - `pages_read_engagement`
   - `pages_manage_engagement`
   - `pages_show_list`
   - `pages_read_user_content`
6. In n8n, create a Facebook credential and authorize with the app.

> If the Facebook node accepts a direct access token, use the Page access token.

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
