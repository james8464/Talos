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

- Node 26.13.1: present
- Docker 29.6.2: present
- Go: missing
- pnpm: missing
- LiveKit server: not verified
- PostgreSQL 17: not verified

## Open decision

Full replatform on Talos (Electron/web, Go/Postgres) versus incremental
integration that keeps the verified Python/Swift Talos running and ports
selected Talos services. The phases above assume the full replatform.
