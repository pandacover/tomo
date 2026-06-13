---
name: social-browser
description: Use Tomo's managed logged-in X browser profile for authenticated X access, social reading, drafting, and approval-gated publishing.
---

# Social Browser

Use `social_browser` for logged-in X access. Do not use the generic `browser`
or `terminal` tools to operate logged-in social accounts.

## Workflow

1. Start with `social_browser({"platform": "x", "action": "status"})` or
   `social_browser({"platform": "x", "action": "login_check"})`.
2. If the user is not logged in, call
   `social_browser({"platform": "x", "action": "login_start"})`. This opens a
   real Chrome incognito window with local debugging enabled, because X may
   block fresh headless/automation-looking login sessions.
3. After the user finishes login in Chrome, use
   `social_browser({"platform": "x", "action": "connect_chrome"})` or
   `social_browser({"platform": "x", "action": "login_check"})`. These actions
   attach to the same Chrome debugging session on port `9222`.
4. For posts and replies, use `draft_post` or `draft_reply` first.
5. Only use `publish_post` or `publish_reply` after the user explicitly asks to
   publish and approves Tomo's approval prompt.

## Rules

- Never ask for passwords, backup codes, or one-time codes.
- Never bypass approval by using `browser`, `terminal`, or direct cookies.
- `connect_chrome` and `login_check` inspect the local Chrome debugging session
  rather than a separate headless browser context.
- Treat account changes as sensitive actions.
- If X shows verification, rate limits, or an unexpected page, stop and ask the
  user to handle it in the managed browser window.
