---
name: browser-tool
description: Use Tomo's browser tool (agent-browser) for rendered page validation, snapshots, screenshots, and interactive web checks. Use when a task needs browser navigation, element refs, screenshots, local dev server validation, or page text from rendered JavaScript apps.
---

# Browser Tool

Tomo's `browser` tool drives **agent-browser** (headless Chromium). Prefer the snapshot-and-ref workflow over raw CSS selectors.

## Core loop

1. `browser({"action": "navigate", "url": target_url})`
2. `browser({"action": "snapshot"})` — read `@e1`, `@e2`, ... refs
3. `browser({"action": "click", "selector": "@e3"})` or `fill` / `type`
4. `browser({"action": "snapshot"})` again after any navigation or DOM change
5. Validate with `text`, `title`, `url`, or `evaluate`
6. `browser({"action": "screenshot", "path": "validation.png", "full_page": false})`

Refs go stale after page changes. Always re-snapshot before the next ref interaction.

## Actions

| Action | Purpose |
|--------|---------|
| `navigate` | Open a URL |
| `snapshot` | Interactive accessibility tree with `@eN` refs |
| `click` / `fill` / `type` / `press` / `scroll` | Interact (selector or x/y for click) |
| `screenshot` | Save image; pass `url` to navigate first |
| `text` / `html` / `evaluate` | Read page content |
| `wait` | Wait for selector or milliseconds |
| `title` / `url` | Page metadata |
| `batch` | Run multiple agent-browser commands in one call |
| `close` | Tear down all browser sessions |

## Batch (multi-step in one call)

```json
{
  "action": "batch",
  "commands": [
    "open https://example.com",
    "wait --load domcontentloaded",
    "snapshot -i",
    "screenshot example.png"
  ]
}
```

Use `batch` for navigate → wait → snapshot → screenshot sequences. Stops on first error (`--bail`).

## Screenshot rules

- Do not screenshot `about:blank`.
- Prefer descriptive paths: `validation-homepage.png`.
- Use `full_page: false` for viewport shots unless full scroll height is needed.
- Confirm URL/title or page text after saving; a saved file alone is not proof.

```json
{"action": "screenshot", "url": "https://example.com", "path": "example.png", "full_page": false}
```

## CSS selector fallback

When snapshot refs fail, use CSS selectors or semantic finds via `batch`:

```json
{"action": "batch", "commands": ["find role button click --name Submit"]}
```

## Validation rules

- Confirm URL/title, snapshot output, or page text before claiming success.
- If a browser call errors, do not claim success. Retry once: `close`, `navigate`, `snapshot`, `text`, `screenshot`.
- If install errors mention missing Chrome, run `npx agent-browser install` from the Tomo repo.

## Setup

```bash
npm install
npx agent-browser install
npx agent-browser doctor
```