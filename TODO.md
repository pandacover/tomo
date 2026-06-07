# TODO

- Improve `/session` picker UX by focusing the currently selected session when the modal opens, and optionally marking it as current in the list.
- Move auth inline so users can start/login from the TUI instead of having to run `uv run tomo login` separately.
- Rename new sessions from the first user message instead of leaving them as `New Chat`.
- Refactor the TUI UI/UX for a more polished chat and session management experience.
- Consider moving sessions to Tomo-global storage under `~/.local/share/tomo/sessions/` so chats persist across projects and working directories.
- **`grep` / `ls` exclusion:** `EXCLUDED_BUILTIN_TOOLS` on the `xai` harness profile should strip Deep Agents builtin `grep` and `ls` from the model tool list; use `files_search` instead. Add a test (or runtime logging) that after `make_agent()`, `grep` and `ls` never appear in tools passed to the model; investigate if invalid-tool errors still list them.
- **`schedule_task` / `at`:** `schedule_task` shells out to the system `at` command; it fails when `at` is missing or `atd` is not running. Install `at` and start `atd` on hosts where delayed commands should work. Not all agent surfaces expose `schedule_task` in the tool list.
