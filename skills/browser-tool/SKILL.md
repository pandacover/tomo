---
name: browser-tool
description: Use Tomo's browser tool reliably for rendered page validation, screenshots, page text extraction, and interactive web checks. Use when a task needs browser navigation, screenshots, local dev server validation, visual inspection, or page text from rendered JavaScript apps.
---

# Browser Tool

Use Tomo's `browser` tool for rendered pages, screenshots, UI validation, and client-side behavior.

## Required workflow

1. Navigate with `browser({"action": "navigate", "url": target_url})`, or pass `url` directly to `screenshot`, `text`, `html`, `evaluate`, or `title`.
2. Wait for the page or target UI state:
   - `browser({"action": "wait", "selector": "...", "timeout_ms": 10000})` for known elements.
   - `browser({"action": "wait", "timeout_ms": 1000})` only for brief settling after navigation.
3. Validate with at least one readback:
   - `browser({"action": "text", "selector": "body"})`
   - `browser({"action": "title"})`
   - `browser({"action": "url"})`
   - `browser({"action": "evaluate", "script": "..."})`
4. For screenshots, save a named file and inspect the tool output URL/title before claiming success.

## Screenshot rules

- Do not screenshot `about:blank`.
- Prefer a descriptive path such as `mkbhd-youtube-channel.png` or `validation-homepage.png`.
- Use `full_page: false` for viewport screenshots unless the user specifically needs the full scroll height.
- If the user gives a URL and asks for a screenshot, this call is valid:

```json
{"action": "screenshot", "url": "https://example.com", "path": "example.png", "full_page": false}
```

## Validation rules

- A tool response that only says a file was saved is not enough. Confirm the URL/title, page text, or rendered state.
- If the screenshot is blank, retry by navigating explicitly, waiting for `body`, and using `full_page: false`.
- If a browser tool call errors, do not claim success. Retry once with a simpler sequence: `close`, `navigate`, `wait`, `text`, `screenshot`.

