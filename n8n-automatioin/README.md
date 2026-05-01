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

## 3. Use it in n8n

### Recommended workflow

1. `Manual Trigger` or `Cron Trigger`
2. `Set` node with a field like `base_dir` set to your workspace folder
   e.g. `D:\Downloads\workspace\horror_story`
3. `Execute Command` node to run:

```powershell
python n8n-automatioin/export_publish_jobs.py {{$node["Set"].json["base_dir"]}} --platform youtube --platform facebook --schedule-interval-hours 4 --pretty
```

4. `Code` node to parse stdout and return one item per job:

```js
const output = JSON.parse(
  items[0].json["text"] || items[0].json["stdout"] || items[0].json["data"],
);
return output.jobs.map((job) => ({ json: job }));
```

5. `SplitInBatches` to process jobs sequentially
6. YouTube upload node
   - Video file: `{{$json["video_path"]}}`
   - Title: `{{$json["title"]}}`
   - Description: `{{$json["description"]}}`
   - Publish at / scheduled time: `{{$json["scheduled_at"]}}`
7. Facebook post node
   - Use page/post scheduling support
   - Schedule publish time: `{{$json["scheduled_at"]}}`
   - Message: `{{$json["description"]}}`
   - Add thumbnail or video media as required
8. `Execute Command` node after each upload to mark the session as posted:

```powershell
python n8n-automatioin/mark_publish_result.py "{{$json["session_folder"]}}" --platform youtube --remote-id "{{$node["YouTube"].json["videoId"]}}" --url "{{$node["YouTube"].json["url"]}}" --scheduled-at "{{$json["scheduled_at"]}}"
```

and similarly for Facebook:

```powershell
python n8n-automatioin/mark_publish_result.py "{{$json["session_folder"]}}" --platform facebook --remote-id "{{$node["Facebook"].json["postId"]}}" --url "{{$node["Facebook"].json["permalink_url"]}}" --scheduled-at "{{$json["scheduled_at"]}}"
```

## 4. Example n8n workflow

Use this general node structure in n8n:

1. `Manual Trigger` or `Cron Trigger`
2. `Set` node with `base_dir`
3. `Execute Command` node running the exporter
4. `Code` node to parse JSON output
5. `SplitInBatches` node
6. YouTube upload node
7. Facebook upload node
8. `Execute Command` nodes to write posted marker files

### Example `Execute Command` node

- Command: `python`
- Arguments:
  - `n8n-automatioin/export_publish_jobs.py`
  - `{{$json["base_dir"]}}`
  - `--platform`
  - `youtube`
  - `--platform`
  - `facebook`
  - `--schedule-interval-hours`
  - `4`
  - `--pretty`

### Example `Code` node

```js
const outputText = $json["stdout"] || $json["text"] || $json["data"];
const parsed = JSON.parse(outputText);
return parsed.jobs.map((job) => ({ json: job }));
```

### Example `SplitInBatches`

- Batch size: `1`
- Keep input data

### Example YouTube node settings

- Video file: `{{$json["video_path"]}}`
- Title: `{{$json["title"]}}`
- Description: `{{$json["description"]}}`
- Scheduled at: `{{$json["scheduled_at"]}}`
- Thumbnail: `{{$json["thumbnail_path"]}}`

### Example Facebook node settings

- Video or Post text: `{{$json["description"]}}`
- Scheduled publish time: `{{$json["scheduled_at"]}}`
- Add video/media from `{{$json["video_path"]}}` or thumbnail if available

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

- If there are no jobs, confirm session folders contain `session.json`.
- If metadata is missing, check `step7_publish_info.json` or `session.json`.
- If a folder is skipped and you do not know why, rerun with `--debug`.
- If thumbnail files use custom names, pass explicit patterns via `--thumbnail-pattern`.
- If YouTube upload fails, verify OAuth redirect URI and permission scopes.
- If Facebook posting fails, verify the page token and required permissions.

## 9. Reminder

Always test with a small number of sessions first.
