---
name: youtube-transcript
description: Fetches full transcripts from YouTube videos with optional timestamps or plain text output. Use when the user shares a YouTube URL and needs the transcript for summarization, analysis, note-taking, or content review.
---

# YouTube Transcript Fetcher

## Quick start

```bash
# From project root
uv run --with youtube-transcript-api python src/tomo/scripts/fetch_transcript.py "https://www.youtube.com/watch?v=VIDEO_ID" --text-only --timestamps
```

**Common flags**
- `--text-only` — plain text output (default: structured JSON)
- `--timestamps` / `-t` — include `MM:SS text` lines (or `timestamped_text` in JSON)
- `--language` / `-l en,tr` — prefer specific language(s)

**Install dependency (one-time)**
```bash
uv pip install youtube-transcript-api
```

## Output modes

- **Default (JSON)**: `video_id`, `segment_count`, `duration`, `full_text`, optional `timestamped_text`
- **--text-only**: single block of text
- **--text-only --timestamps**: one line per segment with `MM:SS ` prefix

## Workflow for summarization / analysis

1. Fetch with `--text-only --timestamps` and redirect to `/tmp/transcript.txt`
2. Use `read_file /tmp/transcript.txt` (with offset/limit) to review sections
3. Summarize key points, extract quotes, or feed sections to other tools

## Notes

- Works on videos with community or auto-generated captions
- Falls back gracefully on disabled/no transcripts
- The underlying library normalizes youtube-transcript-api v1.x responses

See the script itself for full argument help: `python src/tomo/scripts/fetch_transcript.py --help`