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

## Routine journey

The live harness also creates a recurring Room routine through the public API,
makes one occurrence overdue, runs the reminder worker, and asserts the wake
message carries the instruction and mentions the Agent
(`TestRoomRoutineFiresAsAgentMessage`). The browser suite separately creates,
edits and fires a routine from Agent details.

The TeamHub CLI's `computer permissions request --wait` now uses OpenClaw's
quiet-poll schedule (5s, 10s, 30s, 60s) and resets on any change, instead of a
fixed two-second poll.
