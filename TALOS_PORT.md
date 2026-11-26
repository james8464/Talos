# Porting Talos onto Talos

Status: groundwork only. No Talos service (and no audio device) has been
started from this checkout.

- Talos base: `09363b78e3bded26d5d18cb52e6c65fb7f3a7aab` (2026-11-25)
- Talos reference: `james8464/Talos`, 830 unit tests, 90/90 safe live checks,
  running muted (`TALOS_MUTE=1`, `/v1/voice` 503)
- Branch: `talos/foundation`

## License

Talos is Apache-2.0 with two extra conditions: no hosted/managed service for
third parties without permission, and the `apps/web/` logo/copyright notice
must stay intact. Talos-on-Talos is a personal local install, so neither
condition is triggered. The owner removed `LICENSE` and `NOTICE` for this
private local build; third-party components keep their own licenses.

## Why this is a replatform, not a merge

| Talos                | Talos                                               |
| -------------------- | --------------------------------------------------- |
| Python 3.12          | Go 1.26 + Node 26 + React 19                        |
| Swift menu-bar app   | Electron desktop + React web                        |
| SQLite state         | PostgreSQL 17 (TeamHub) + SQLite (Computer Daemon)  |
| Ollama endpoint      | LLM Gateway, local models, PolyStrata               |
| Custom room service  | TeamHub Rooms + Agent Service + LiveKit voice       |
| No container runtime | Docker Compose for Postgres, LiveKit, web, services |

The working Talos tree must stay untouched while this branch is evaluated: it
is the only verified, muted implementation. Do not delete it, replace its
service, or point its launchd job at Talos until the Talos path passes the
same safety checks.

## Talos capabilities to carry over

1. **Hard mute.** `TALOS_MUTE=1` disables playback, synthesis returns
   `503`, and `/v1/voice` is never served. Talos's voice worker must refuse to
   start its Live/recorder paths under the same flag.
2. **Consent-gated capture.** Readiness without capture
   (`av-readiness`), a refusal guard (`capture-check` exits 2 without
   `--consent-capture`), one frame + one short clip only on consent, endpoint
   verification, and unconditional media deletion.
3. **Self-reported identity.** Name introductions are session-local, expire,
   are clearable, and never claim biometric verification.
4. **Bounded coding jobs.** One-file and multi-file steps, sandbox verification,
   one retry on test failure, dependency-ordered plans, shared plan budget, two
   plan revisions, catalog/conflict guards, verify-only default.
5. **Process hygiene.** `talos processes` inventory and label-scoped cleanup;
   live checks end with `stale: 0`, `audio: 0`.
6. **Verification harness.** A safe live pass that exercises user-visible
   behaviour, plus a loud/non-consented path kept opt-in.
7. **Native Mac control.** Accessibility-driven clicks, typing, reading, and
   non-speaking screen highlights. Talos uses Cua/Playwright; compare before
   porting anything.

## Proposed phases

0. **Safety shell (no audio).** Add a Talos-level mute policy checked by
   TeamHub and the voice runtime; tests prove no worker starts under
   `TALOS_MUTE=1`. No LiveKit server is launched. **Done:** shared
   `isMuted()` in `@talos/service-kit`; the voice runtime exits before
   `loadConfig`, and the Node voice supervisor starts no worker and mounts no
   routes when muted. Focused tests and typecheck/lint pass.
1. **Local baseline.** Install pnpm/Go, bring up Postgres + TeamHub + web
   without voice, and run the repository's fast checks. Record exact commands.
2. **Capture/readiness parity.** Port the readiness probe and consent guard as
   macOS-side helpers, reusing the Swift binaries where practical. **Done:**
   readiness, direct capture, IPC/preload and Settings button; a live capture
   produced a frame and clip and deleted both. Endpoint verification remains.
3. **Coding jobs parity.** Port the job/plan state machine and sandbox checks
   into the Agent Service or Computer Daemon, with the same guards and reports.
4. **Identity and room commands.** Self-reported claims with expiry in the
   Agent Service; room text commands for jobs/plans.
5. **Verification parity.** Port the safe live harness and process inventory;
   prove `/v1/voice` equivalent disabled and no audio process in Talos mode.
6. **Cutover decision.** Only after 0–5 pass does the Talos launchd service
   change.

## Prerequisites on this machine

- Node 26.5.0: present
- Go 1.26.11: present
- pnpm 11.7.0: present
- PostgreSQL 17 with pgvector: present on port 5433
- Ollama with `qwen3:4b` and `qwen2.5vl:3b`: present
- Playwright Chromium: installed
- Docker: not required for the local proof

## Open decision

Full replatform on Talos (Electron/web, Go/Postgres) versus incremental
integration that keeps the verified Python/Swift Talos running and ports
selected Talos services. The phases above assume the full replatform.

## Live Room coding proof

The local model path costs nothing per job, so it can run 24/7. It drafts the
patch, the Computer verifies it in a sandbox, and the Room's Agent reports the
result. Without an Agent Runtime, `qwen3:4b` through Ollama is the drafter; with
a Runtime configured, the coding role uses it instead and names that model.

```bash
# 1. One database and owner for this checkout.
TEAMHUB_DATABASE_URL='postgres://simplexai@127.0.0.1:5433/talos_dev_live?sslmode=disable'   pnpm dev:prepare

# 2. TeamHub owns the queue and the Agent reply.
TEAMHUB_DATABASE_URL='postgres://simplexai@127.0.0.1:5433/talos_dev_live?sslmode=disable' TEAMHUB_CODING_WORKER=1 pnpm dev:teamhub

# 3. Coding on this Computer; the local model drafts and go test verifies.
NODE_SERVICES_ROLES='coding,identity,perception' \
TALOS_CODING_ROOT="$PWD" \
TALOS_CODING_VERIFY='["sh","-c","cd apps/teamhub && go test ./internal/roomsenses"]' \
  pnpm dev:node-services

# 4. Ask Talos in a real Room and wait for the Agent's completion reply.
node scripts/talos-room-live.mjs
```

A successful run prints, for example:

```text
reply[Talos]: Coding job codingjob_… verified in a sandbox with qwen3:4b: sandbox passed. No change was applied.
```

Override the target and request with `TALOS_LIVE_CODING_TARGET` and
`TALOS_LIVE_CODING_REQUEST`. The script exits non-zero unless the reply reports
a sandbox verification, so it can gate a check.

Codex is an alternative drafter, but a ChatGPT plan with exhausted usage fails
the job honestly ("You've hit your usage limit…"). The local model path needs
no quota or network.

## Follow-through guards

A transport failure is visible: the Room receives `retrying in 15s (attempt 1
of 4)` before the next attempt, and one final failure reply when attempts run
out, matching old Talos's retry report.

## Duplicate work

A repeated `coding job` with the same target and request joins the active job
instead of starting a second one; the Agent replies with the existing ID and how
to cancel it. `coding cancel <id>` releases the request. This is the Room-side
counterpart to OpenClaw's no-progress detector: identical work is not enqueued
twice while it is still running. Unit and Postgres integration coverage lives in
`TestDuplicateCodingCommandsShareOneJob`.

## Capability replies

Identity, hearing, seeing and speaking questions get deterministic
Agent-authored replies without a model call. A speak question reports the mute
state: with `TALOS_MUTE=1` the Agent says audio is muted and it will answer in
text; without it, audio is not muted and voice mode may use the speaker. The
seeing answer never invents a camera reading. Unit and Postgres coverage is in
`TestSpeakAnswersFollowMute`, `TestCaptureConsentReplies` and
`TestSensorQuestionsWriteAgentReplies`; the live harness runs `room-commands`
with all three. A camera or microphone request is answered with the explicit
Settings capture step instead of starting a capture.

## Screen highlight

`ScreenOverlay` is a transparent, click-through, always-on-top Electron window
that outlines one screen region with a ring and an optional label. It never
focuses, never receives input and never speaks; it hides itself after four
seconds. Settings › Test screen highlight shows it, and the IPC accepts any
region an Agent action reports. Wiring the computer tools to raise it on every
action is still open.

## Camera and microphone readiness

Settings shows the macOS camera and microphone permission state from
`SystemPermissions.status()` without capturing anything, and offers Allow for a
state that is not granted. The status is the same one the capture helper obeys,
so a person can check readiness before the first capture.

## Presence reading

The Settings capture check also classifies the captured frame locally and the
Web records `{people, faces}` through `POST /v0/presence-readings`. The reading
is private to the Human who took it, carries the server's capture time, and
answers that Human's seeing questions for five minutes; older or missing
readings answer "I don't have a current camera reading." Nothing captures on a
Room request.

## Process inventory

`talos-computer doctor` reports audio-capable and leftover Talos test processes
by label and pid, never by command line, and treats them as warnings. The
scanner lives in `apps/computer/local-daemon/internal/processinventory` and is
injected so tests need no real processes. `doctor --stop-stale` signals only
the stale test labels and never an audio player.

## Coding receipts

A verified patch reports each target's size and short SHA-256 before and after,
the regression file's hash, a hash of the changed hunk text and the seconds left
in the job budget, without copying file contents into the Room.
The receipt is stored on `room_coding_jobs.receipt`; `coding job <id>` shows the
status, attempts, result and receipt for one job. Coverage lives in
`TestCodingCommandsWriteAgentReplies`.

## Routine journey

The live harness also creates a recurring Room routine through the public API,
makes one occurrence overdue, runs the reminder worker, and asserts the wake
message carries the instruction and mentions the Agent
(`TestRoomRoutineFiresAsAgentMessage`). The browser suite separately creates,
edits and fires a routine from Agent details.

The TeamHub CLI's `computer permissions request --wait` now uses OpenClaw's
quiet-poll schedule (5s, 10s, 30s, 60s) and resets on any change, instead of a
fixed two-second poll.

## Typed Room reminders

`reminder in 10 minutes: stretch` creates a one-shot Room Routine through the
public reminder service and replies as the Agent. The live harness creates it,
makes it due, runs the reminder worker and reads the delivered message
(`TestRoomReminderCommandCreatesAndFires`). Accepted units are minutes, hours
and days, from one minute to thirty days.

## No-progress streak

`internal/noprogress` ports OpenClaw's `getNoProgressStreak`, including the
exec terminal-failure tail, argument-validation skips, veto anchoring and the
failure-identity match. The Room coding worker stores one hash per sandbox
failure; when two identical failures repeat it stops instead of retrying and
reports that the same failure repeated. `TestRoomCodingWorkerStopsRepeatedSandboxFailures`
covers the Postgres path and the live harness runs it.

## Room Document journey

`pnpm talos:live` also runs two real browser journeys against TeamHub, the
Document Runtime and Web: it renames a Document and edits its body, then adds
an anchored comment and reopens the Thread in another session. The check is
`room-document-e2e`; it is skipped when `TALOS_LIVE_DOCUMENT_E2E=0` or no test
database is configured.

## Missed-run grace

Hermes retires a one-shot past its grace window instead of firing it late.
Talos applies that to an Agent's one-shot follow-up: more than two minutes
late, it is retired without a message. Human Reminders keep firing because a
late reminder is still useful. `TestAgentOneShotPastGraceIsRetired` covers both
sides and the live harness runs it.

## Local draft fallback

The coding service asks the Agent Runtime first. If that call fails (no Computer,
offline, or a provider quota), it retries the same request once with the
on-device `qwen3:4b` drafter, so a Room coding job still completes without a
provider bill. The response names the model that actually drafted the patch.

## Typed Room pin

`pin this room` and `unpin this room` change the requesting Human's private pin
and reply as the Agent. The pin is per Human, so another member's list is
unaffected. `TestRoomPinCommandPinsForTheRequester` covers both commands and the
stored state; the live harness runs it.

## Local model through the Codex Runtime

`scripts/ollama-codex-home.sh` writes a private Codex home whose provider is
Ollama's Responses API, backed by `qwen3:4b-8k` (the 4B model with an 8k
context). The live harness runs `codex exec` against that home
(`ollama-codex-model`), proving a real model call through the Codex Runtime with
no provider bill.

To use it with a Local Computer, export the home and a dummy provider key
before starting the daemon:

```bash
export CODEX_HOME="$(bash scripts/ollama-codex-home.sh)"
export OLLAMA_API_KEY=dummy
bash scripts/dev-local-computer.sh start
```

The script also writes `model_catalog.json` and points Codex at it, so the
Local Computer daemon reports the model:

```text
codex  status=ready  models=["qwen3:4b-8k"]
```

Placement accepts it, and a real Agent Service model call completed through the
daemon:

```text
model_call.invoked kind=coding runtime_type=codex mode=structured
  outcome=completed model=qwen3:4b-8k
```

The patch that model returned was rejected by the exact-substring validator on
its first attempt, so model quality for strict patches remains a separate
limitation from the call path.

## Thread journey

The live harness creates a Thread from a Room message, replies in it, reads the
parent reply count, reopens the Thread messages and finds the reply through
tenant search (`TestRoomThreadJourneyLive`, `room-thread-journey`).

## Live Agent conversation

With TeamHub, the Agent Service and a Local Computer running, this sends a
Direct Room message and waits for the Agent's reply:

```bash
TEAMHUB_BASE_URL=http://127.0.0.1:8090 node scripts/talos-agent-live.mjs
```

The same check is available in `pnpm talos:live` when
`TALOS_LIVE_AGENT=1` and `TALOS_LIVE_AGENT_BASE_URL` point at that stack.

On the Ollama-backed Codex stack the Agent did make a real model request:

```text
agent.model_request model=qwen3:4b-8k duration_ms=41234
  usage_input=2961 usage_output=1172
```

The catalog now enables the shell tool (`shell_type: "shell_command"`), and the
one-shot Codex home keeps `model_catalog_json`. A direct Codex check proves the
tool path: the model read a random token from a file and replied with it.

The Room run still failed with `reply_not_sent` (`failedMessageWrites=0`,
`finalResponseLength=98`): the 4B model wrote text instead of running
`teamhub message send`, even after the reply guard's reminder. The model call and
tool plumbing work; following the Room reply contract is the remaining small-model
limitation. A larger local model or a reviewed product fallback would be needed.

## Search and Direct Room journeys

The live harness also runs `TestGlobalSearchMessagesPublicHTTP`
(`room-search-journey`: visibility, pagination and case-insensitive search) and
`TestPrivateAgentVisibilityAndDirectRoomLifecycle` (`room-direct-journey`:
Direct Room lifecycle and Agent visibility).

### Reviewed text-reply fallback

Both 4B and 8B write text instead of running `teamhub message send`. With
`TALOS_AGENT_TEXT_REPLY=1`, TeamHub posts that final text as the Agent reply when
the Run finishes `reply_not_sent`. The review trims whitespace, strips one
`<response>…</response>` wrapper, rejects empty or over-2000-rune text and NUL
bytes, and is off by default. Cloud runtimes can keep the strict guard.

Verified live with `qwen3:8b-16k`:

```text
agent.model_request model=qwen3:8b-16k (3158/167, 152/205 tokens)
local_run.text_reply_posted run_id=local-run-6da7bd12e729443a908986957187a241
message author=agent
body="Hello! I'm Talos, your AI assistant here to help with onboarding and tasks."
```

`scripts/talos-agent-live.mjs` waits for that persisted Agent-authored reply;
`pnpm talos:live` runs it when `TALOS_LIVE_AGENT=1` points at a running stack.

Re-verified with the local bundle on the `talos_ollama_live` database:

```text
agent.model_request model=qwen3:8b-16k (3602/551, 152/741 tokens)
room_reply_guard.blocked unansweredInboxIds=[room_d31e3bb14ac7b600c28d2d1b6f018a4a]
local_run.text_reply_posted run_id=local-run-7e54f811186941fc9c85c3d9828cb3af
reply[Talos]: Hello! I'm Talos, your AI assistant here to help with onboarding and tasks.
AGENT_REPLY_FOUND
```

`scripts/ollama-codex-home.sh` writes one `model_catalog.json` entry per
installed Ollama model (primary first, context from a `-8k`/`-16k` suffix). A
Computer whose catalog named only `qwen3:8b-16k` rejected Agents configured for
`qwen3:4b-8k` with `runner.placement.unavailable`; the daemon now reports
`models=6` and no placement warning.

## Proactive check-in journey

The live harness runs `TestProactiveWindowsAndQuietWakes`
(`room-proactive-journey`): a twenty-message proactive window opens once and is
judged once, queued wakes wait for three quiet minutes, deliver in Room order,
and mention AnotherMe without interrupting the conversation.

## Agent Memory journey

The live harness runs `TestAgentMemoryScopePublication` and
`TestAgentMemoryReadUsesRevisionColumn` as `room-memory-journey`: publish memory,
reject a stale revision, publish the next revision and read it back.

## Personal Wiki journey

The live harness runs the Personal Wiki Postgres suites as `room-wiki-journey`:
the input tree and immutable versions, and a Markdown source upload that admits
a refresh.

## Heartbeat quiet vs substantive

`internal/heartbeat` ports OpenClaw's `HEARTBEAT_OK` edge rules. The text-reply
fallback suppresses a message that is only `HEARTBEAT_OK`/`NO_REPLY` or a short
acknowledgement around the token; a long report is posted without the token.
`heartbeat-quiet-guard` runs the unit tests in the live harness.

## Offline voice transcription

`apps/voice-runtime/scripts/setup-local-whisper.sh` creates a local Python
environment with `mlx-whisper`; the runtime then transcribes finished clips
with `mlx-community/whisper-base-mlx` instead of Soniox. It has no streaming
partials or speaker labels, and it never plays audio. The supervisor starts
the ASR worker from that interpreter when `TALOS_LOCAL_WHISPER_PYTHON` is set
and Soniox has no key, and
`local-whisper-transcribe` in the live harness synthesizes a clip to a file
with `say -o` and asserts the transcript.

## Hermes-style standing goals

`goal: <objective>` sets a 20-turn Room goal; `goal status`, `goal pause`,
`goal resume` and `goal clear` control it. The continuation prompt builder
matches Hermes' standing-goal wording. `room-goal-journey` runs the Postgres command lifecycle. `room-goal-judge`
covers the judge state machine and turn budget; the App worker judges active
goals every 30 seconds, posts the Hermes continuation prompt as the goal
creator mentioning the Agent, and stops on done/blocked or after the budget.
`goal-judge` runs a real local-model judge in the live harness.
