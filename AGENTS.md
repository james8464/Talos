# Talos project instructions

Use these instructions for Codex sessions working in the Talos workspace.

- For architecture, call paths, or unfamiliar code spanning files, start with `graphify query "specific symbols or feature" --budget 500`. Use `graphify path` for a specific relationship. Verify the relevant source lines with `rg -n` and narrow reads. For an exact string or a known file, go straight to the source.
- Build the local source index with `graphify extract . --code-only` if it is missing. After edits that affect indexed code, run `graphify update .` before finishing. If Graphify is unavailable, use targeted `rg` searches. Keep runtime data, private files, and generated assets out of the index.
- When RTK is available, use it for supported noisy commands. Read raw output when the summary omits a detail needed for diagnosis. Cap verbose output at the tool boundary and reuse earlier results. If the same attempt fails twice, inspect the cause before retrying.
- For third-party documentation, prefer a focused Context7 lookup by library, version, and question when available. Otherwise use the relevant official documentation section.
- Carry forward verified paths, decisions, and test results in a few lines. Reuse accepted results unless the underlying files or requirements changed. Keep private data out of notes and graph output.
- When available, use Token Saver for narrow source selection, Headroom for context-savings diagnostics, and project-scoped CodeBurn reports for usage analysis. Use the quota watcher for remaining account capacity. For small source-only reviews, reuse the configured free OpenClaude route in overnight_goal.py and verify proposed edits locally.
- Keep source changes up to date in the project repository, `james8464/Talos`. Check the current remote base, preserve concurrent changes, and publish tested changes on a review branch. Exclude the private `state/` vault, models, and generated indexes from GitHub.
- Develop Talos toward observable, context-aware initiative and reliable follow-through. For OpenClaw and Hermes parity, trace a behavior to its pinned implementation and replay its user-visible outcome, including failures, restarts and spoken interaction where applicable. Report real model activity and distinguish a proposal from a verified code change.
