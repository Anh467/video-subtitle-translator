# Automation For n8n Publish Flow

This folder contains a complete helper set so n8n only needs to provide a `base_dir`
that contains all session folders. The scripts will automatically discover:

- final video output
- title
- description
- hashtags
- thumbnail
- already-posted markers

## 1. Export publish jobs

Run this from the project root:

```powershell
python automation/export_publish_jobs.py "D:\sessions" --platform youtube --platform facebook --pretty
```

Output JSON shape:

```json
{
  "base_dir": "D:/sessions",
  "platforms": ["youtube", "facebook"],
  "count": 2,
  "jobs": [
    {
      "session_folder": "D:/sessions/demo_20260429_101010",
      "video_path": "D:/sessions/demo_20260429_101010/result/step6_output_demo.mp4",
      "thumbnail_path": "D:/sessions/demo_20260429_101010/thumbnail.jpg",
      "title": "...",
      "description": "...",
      "hashtags": ["#abc", "#xyz"],
      "ready": true,
      "missing": [],
      "youtube_posted": false,
      "facebook_posted": false
    }
  ]
}
```

## 2. Mark a session as scheduled/uploaded

After n8n uploads or schedules to a platform, write a marker file back into the
same session folder.

YouTube example:

```powershell
python automation/mark_publish_result.py "D:\sessions\demo_20260429_101010" --platform youtube --remote-id abc123 --url "https://youtube.com/watch?v=abc123" --scheduled-at "2026-04-30T09:00:00Z"
```

Facebook example:

```powershell
python automation/mark_publish_result.py "D:\sessions\demo_20260429_101010" --platform facebook --remote-id 998877 --scheduled-at "2026-04-30T09:30:00Z"
```

This creates either:

- `posted_youtube.json`
- `posted_facebook.json`

## 3. Suggested n8n structure

Use `Execute Command` for the scan step:

```powershell
python automation/export_publish_jobs.py "{{$json.base_dir}}" --platform youtube --platform facebook --pretty
```

Then in n8n:

1. `Manual Trigger` or `Form Trigger`
2. `Set` node with `base_dir`
3. `Execute Command` node calling `export_publish_jobs.py`
4. `Code` node to parse the command stdout JSON and return one item per job
5. `Split In Batches`
6. YouTube/Facebook upload nodes
7. `Execute Command` node calling `mark_publish_result.py`

## 4. Discovery rules used by the scanner

- Session folder must contain both `session.json` and `step7_publish_info.json`
- Video priority:
  1. `result/step6_output_*.*`
  2. `step6_output.*`
  3. `step5_output.*`
  4. `step3_output.*`
- Thumbnail priority:
  1. `thumbnail.jpg`
  2. `thumbnail.jpeg`
  3. `thumbnail.png`
  4. `thumbnail.webp`

## 5. Notes

- n8n does not need to store the schedule itself. It should call YouTube/Facebook
  APIs with schedule time, and the platforms will keep the schedule.
- By default, `export_publish_jobs.py` skips sessions already marked as posted for
  all selected platforms.
- Add `--include-posted` if you want to re-export everything.
