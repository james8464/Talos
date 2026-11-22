# Upstream source used by Talos

The reference tree in `state/openclaw-src` is OpenClaw v2026.11.6, commit `06afb10d3272cc9deaf38ca6a3b7b839d5935638`. The GitHub source archive SHA-256 was `953b323a3d1825eabe968f29b8c1c3352a42bec560c60717d89501befc87dd2e`. It contains platform apps, extensions, tests, and documentation. The modules below run inside Talos independently of that checkout.

| Upstream implementation | Talos integration |
| --- | --- |
| src/cron/schedule.ts, parse.ts, schedule-number.ts | Standalone calendar engine for future occurrences, anchored intervals and timezone-aware cron expressions. Talos persists reminders and briefing schedules in its existing SQLite store. |
| src/infra/format-time/parse-offsetless-zoned-datetime.ts, map-size.ts, src/shared/iso-time.ts, packages/normalization-core/src/number-coercion.ts and string-coerce.ts | Calendar validation, bounded formatter caches and handling of skipped and repeated hours. Module imports point at local files. Node 22.18+ runs these TypeScript sources directly. |
| extensions/memory-core/src/memory/mmr.ts and tokenize.ts | Python adaptation of normalized relevance, incremental maximum similarity and CJK tokenization for FTS5 transcript search. Fetches 24 scoped candidates and selects five. |
| packages/retry/src/index.ts | Bounded exponential retry adapted for Talos background orders. Talos adds SQLite task claims, cancellation, and atomic receipts. |
| src/agents/tool-loop-no-progress.ts | Talos compares read-only tool arguments and outcomes across its bounded planning pass. An unchanged repeat triggers a final decision with lookups disabled. |
| extensions/telegram/src/allow-from.ts and src/update-offset-store.ts | Positive numeric sender IDs, optional tg/telegram prefixes and a per-bot SQLite polling cursor. |
| src/tts/speech-text.ts and src/shared/text/strip-markdown.ts | Spoken replies use link labels, drop Markdown emphasis, and direct code-heavy answers to the screen. Both voices receive the same text. |
| apps/macos/Sources/OpenClaw/QuickChatDictation.swift and SpeechAudioBufferNormalizer.swift | Native selection-aware transcript replacement, punctuation spacing and multichannel-to-mono audio normalization. Talos keeps its own microphone lifecycle, on-device recognition and explicit Send action. |

OpenClaw code is distributed under the accompanying MIT license. The calendar parser, Croner, carries its own license in the scheduling directory. The full MIT notice applies to the Swift and Python adaptations as well.

[OpenClaw revision](https://github.com/openclaw/openclaw/tree/06afb10d3272cc9deaf38ca6a3b7b839d5935638)

## Capability coverage

| Capability | Behavior verified in Talos | Remaining scope |
| --- | --- | --- |
| Durable work | Explicit local work orders survive restart, concurrent cancellation prevents late writes, and receipts commit in the same SQLite transaction as job completion. | General-purpose workflows, nested workers, and cross-device handoff. |
| Scheduled work | Private recurring reminders and evidence-based project briefings, missed-run coalescing, and timezone transitions. | Event and stream triggers, remote delivery, and general scheduled tool execution. |
| Conversation and recall | Session filtering precedes FTS retrieval and diversity ranking. Reviewed dictation supports revised captions and long turns. | Model-quality and end-to-end microphone latency comparisons on a live Mac. |
| Integrations | Owner-only Telegram text, quoted replies, JPEG attachments, photo follow-ups, local Ogg/Opus transcription, selectable voice replies and delivery receipts have fixture coverage. | Live Telegram delivery, audio files beyond voice notes, video, albums, groups, other channels, device nodes and plugins. |

## Hermes comparison

Reviewed NousResearch/hermes-agent at `0f602c5f6f18615632535251d62a2db74ce93b69`. Talos's terminal now exposes recent text history and ranked transcript recall without loading a model. Hermes's command registry provided the reference for making history directly discoverable.

| Hermes source | Next Talos acceptance test |
| --- | --- |
| `plugins/platforms/telegram/adapter.py` | Fixtures now verify expiring private-chat pairing, sender rejection, replay after restart, and multipart delivery after rate limiting. Owner JPEG photos and JPEG attachments now use bounded reads and local vision. Replies to photos reopen just the selected image. Direct text replies use a source-checked quote even outside the recent history window. Live transport, albums, other audio attachments and video remain open. Keep family records and biometric data on the Mac. |
| `tools/transcription_local.py` and `gateway/session_config.py` | Talos adapts the pinned voice activity detector settings and segment confidence filter for local CPU Whisper. The private chat remembers its off, voice-only or all-replies preference across restarts. |
| `hermes_cli/goals.py` and `goal_command.py` | The terminal can set a writing objective per session, add acceptance criteria, persist a draft, request revisions and stop on a supported completion verdict. Fixtures cover an expired claim, pause during review, restart, separate sessions and the run budget. A separate worker picks up explicitly queued writing goals at idle; a subprocess fixture drives enqueue, local model protocol and durable completion. |
| `hermes_cli/setup_quick.py` | Start with an empty temporary profile, select a local provider, interrupt setup, and resume without losing the selection. |
| `tools/skill_manager_tool.py` | Convert a successful repeated procedure into a versioned candidate, evaluate it against held-out tasks, and restore the previous version if quality drops. |

## Extension inventory at the pinned OpenClaw revision

Each entry corresponds to an upstream plugin manifest. End-to-end parity for these plugins is unverified. The individual algorithms already adapted above cover part of their behavior. This inventory excludes core commands and platform applications.

**Channel plugins (28)**

`a2a`, `buzz`, `clickclack`, `discord`, `feishu`, `googlechat`, `imessage`, `irc`, `line`, `matrix`, `mattermost`, `msteams`, `nextcloud-talk`, `nostr`, `qa-channel`, `raft`, `reef`, `signal`, `slack`, `sms`, `synology-chat`, `telegram`, `tlon`, `twitch`, `whatsapp`, `x`, `zalo`, `zalouser`.

**Provider plugins (69)**

`abliteration`, `amazon-bedrock`, `amazon-bedrock-mantle`, `anthropic`, `anthropic-vertex`, `apple-fm`, `arcee`, `baseten`, `bifrost`, `byteplus`, `cerebras`, `chutes`, `clawrouter`, `cloudflare-ai-gateway`, `codex`, `cohere`, `comfy`, `copilot-proxy`, `deepinfra`, `deepseek`, `fal`, `featherless`, `fireworks`, `github-copilot`, `gmi`, `google`, `groq`, `huggingface`, `kie`, `kilocode`, `kimi-coding`, `levanto`, `liquid-ai`, `litellm`, `llama-cpp`, `lmstudio`, `longcat`, `meta`, `microsoft-foundry`, `minimax`, `mistral`, `moonshot`, `novita`, `nvidia`, `ollama`, `omlx`, `onnx`, `openai`, `opencode`, `opencode-go`, `openrouter`, `qianfan`, `qwen`, `radius`, `requesty`, `sglang`, `stepfun`, `synthetic`, `tencent`, `together`, `typesafe`, `venice`, `vercel-ai-gateway`, `vllm`, `volcengine`, `vydra`, `xai`, `xiaomi`, `zai`.

**Other plugins (81)**

`acpx`, `active-memory`, `admin-http-rpc`, `advisor`, `agent-guidance`, `agentsapi`, `alibaba`, `autoreview`, `azure-speech`, `beam`, `bonjour`, `brave`, `browser`, `canvas`, `cloudflare`, `code-mode-quickjs`, `copilot`, `crabbox`, `cua-computer`, `deepgram`, `device-pair`, `diagnostics-otel`, `diagnostics-prometheus`, `diffs`, `diffs-language-pack`, `document-extract`, `duckduckgo`, `elevenlabs`, `exa`, `facetime`, `file-transfer`, `firecrawl`, `fish-audio-speech`, `geolocation`, `github`, `google-meet`, `gradium`, `imap`, `inworld`, `knowledge`, `linux-node`, `llm-task`, `lobster`, `logbook`, `mcp-events`, `mcp-relay`, `memory-core`, `memory-lancedb`, `memory-wiki`, `microsoft`, `migrate-claude`, `migrate-hermes`, `muse`, `mxc`, `oc-path`, `onepassword`, `openshell`, `parallel`, `perplexity`, `pixverse`, `policy`, `qa-lab`, `runway`, `searxng`, `senseaudio`, `session-share`, `slack-huddles`, `subq`, `talk-voice`, `tavily`, `team-reports`, `teams-meetings`, `tokenjuice`, `tts-local-cli`, `vault`, `visitor-access`, `voice-call`, `voyage`, `web-readability`, `workboard`, `zoom-meetings`.


## Hermes command coverage

The pinned Hermes registry declares 105 literal command definitions. Terminal fixture coverage includes fresh named sessions, reopening a session, recent history and JSONL export. Telegram fixtures cover private text, quoted replies, transient JPEG inspection, corrections, reset, durable polling and multipart replies. Real Telegram transport, group routing, music files and video remain unverified. The gateway adapts Hermes’s UTF-16 prefix calculation from `gateway/platforms/base.py` under the accompanying MIT license.

**Session** · `/start`, `/new`, `/topic`, `/clear`, `/redraw`, `/history`, `/save`, `/retry`, `/prompt`, `/undo`, `/rewind`, `/title`, `/handoff`, `/branch`, `/worktree`, `/compress`, `/rollback`, `/snapshot`, `/stop`, `/discard`, `/pause`, `/approve`, `/deny`, `/bg`, `/btw`, `/agents`, `/journey`, `/queue`, `/steer`, `/goal`, `/heartbeat`, `/refine`, `/review`, `/loop`, `/plan`, `/moa`, `/subgoal`, `/status`, `/egress`, `/context`, `/sethome`, `/resume`, `/sessions`, `/restart`.

**Configuration** · `/export`, `/import`, `/config`, `/model`, `/codex-runtime`, `/personality`, `/statusbar`, `/battery`, `/timestamps`, `/verbose`, `/focus`, `/footer`, `/yolo`, `/approvals`, `/reasoning`, `/fast`, `/skin`, `/indicator`, `/voice`, `/wake`, `/busy`, `/autoprune`.

**Info** · `/whoami`, `/profile`, `/diff`, `/commands`, `/help`, `/palette`, `/usage`, `/subscription`, `/login`, `/topup`, `/insights`, `/platforms`, `/platform`, `/copy`, `/paste`, `/image`, `/update`, `/version`, `/debug`.

**Tools & Skills** · `/tools`, `/toolsets`, `/skills`, `/memory`, `/bundles`, `/pet`, `/hatch`, `/learn`, `/init`, `/cron`, `/suggestions`, `/blueprint`, `/curator`, `/kanban`, `/reload`, `/reload-mcp`, `/reload-skills`, `/browser`, `/plugins`.

**Exit** · `/quit`.

The Hermes gateway-only `/commands [page]` entry is available in Talos Telegram private chat. `TelegramGatewayTests.test_commands_pages_use_only_local_gateway_capabilities` drives both pages, invalid arguments, sender restrictions and replay through polling. The catalog covers the commands implemented by this gateway. Dynamic skill discovery is still open.

On November 22 the isolated Mac model suite executed seven optional tests. Five passed, covering room conversation and correction, a reviewed writing goal, Telegram correction after restart, and a spoken Telegram correction. The room and Telegram image tests both failed on the same synthetic portrait sheet. Both described a green jacket. The expected label was “shirt”, but the visible dark collared outer layer is ambiguous and the lower panels show faces. The room test stopped before the sweater case. A replacement single-subject test with an independently verified, unambiguous garment and color is still needed. These runs used synthetic inputs and no live Telegram delivery or camera.

## Hermes comparison

The regular files from the pinned Hermes Git archive are available under `state/hermes-src`. The title sanitizer in `talos_core/session_titles.py` comes from `persistence/conversation/sessions.py` under `LICENSE-Hermes`. Talos stores console titles separately from the session ID so renaming preserves transcript and fork references. The console can resume by exact ID or title.

The console HTML export adapts OpenClaw's explicit-path and default-name collision handling from `src/auto-reply/reply/commands-export-session.ts` under `LICENSE-OpenClaw`. It exports Talos's visible conversation turns as escaped HTML in an owner-only file. OpenClaw's richer tool-call and trajectory formats remain separate behaviors.

## Source review handoff

Talos adapts the OpenClaw status, tools and commands listings and Hermes's agents, context, commands and whoami views to its local console. Runtime visibility uses live worker locks and an endpoint probe. Its context view measures Ollama's last reported token use and output throughput and previews actual prompt components. Talos also exposes durable local work orders through `/bg` and `/jobs`, and reports cumulative provider counts for measured console replies with `/usage`. Hermes’s remote fleet management, plugin command catalog, and provider-specific diagnostics remain outside this coverage.

Run `python3 upstream_review.py --inventory` after replacing a reference tree. The SQLite inventory under `state/` records regular-file paths, byte counts and SHA-256 digests. The inventory includes tests and resources. A hash records file identity and carries no behavioral verdict.

`python3 upstream_review.py` compares literal upstream console declarations with Talos’s dispatcher. It prioritizes commands with specific replay cases and then queues other missing command names with a reference and an acceptance check. Run `python3 upstream_review.py --status` to see command name coverage without running a model. Matching names can have different behavior. `python3 upstream_review.py --compare` compares a bounded upstream source excerpt with related Talos source using the local model. The reviewer rotates through mapped areas such as memory, scheduling, and speech. It also selects other indexed source files in the two reference trees, including tests, in supported languages up to 2 MiB per file. It chooses a candidate Talos module by filename overlap when a direct mapping is unavailable. Every proposal still requires supporting excerpts from both projects. It writes proposals with both excerpts and an acceptance check to `state/feature-requests.jsonl`. Idle source review invokes both paths. The comparison cursor advances after a validated response and retries after interruption. Inspect pending requests with `python3 feature_queue.py --pending`, then record a passing behavior replay with `python3 feature_queue.py --complete ID "verification result"`.

The local console also supports model inventory and selection, screen clearing, repaint, clipboard copy and version reporting.

## Behavioral parity in background review and check-ins

OpenClaw's `src/auto-reply/heartbeat.ts` distinguishes a quiet heartbeat acknowledgment from a report worth surfacing. The Respan source reviewer accepts a quiet result, records its coverage and advances the cursor. Hermes's `cron/missed_runs.py` retires stale one-shot jobs after a two-minute grace period. The room applies that rule to overdue check-ins, distinguishes playback from a human reply and persists one follow-up.

The reviewer compares bounded excerpts from both local reference trees with Talos implementation files. It verifies file hashes and quoted evidence, saves actionable proposals for review and resumes from its recorded byte position. Each tree and the knowledge review have independent ten-minute intervals after success and retry delays after failure. Knowledge review sends anonymous shared-record counts for candidate links or source-type counts to select a local investigation. Personal text and project names stay on the Mac. Accepted links are rechecked against enabled evidence when publishing the vault.

The behavior replays are in `tests/test_proactive_review.py`. Set `TALOS_TEST_KOKORO=1` and `TALOS_TEST_SPOKEN_CHECKIN=1` with the project speech environments and models installed to run actual speech generation and recognition.
