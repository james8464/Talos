# Talos → Talos feature gap matrix

Reference: Talos `830 tests / 90 live checks` at the point of the replatform
decision. Talos base `09363b7`; branch `talos/foundation`.

Status meanings: **ported** (works here and is tested here), **present**
(Talos already has an equivalent, not yet compared for parity), **partial**
(some machinery exists, Talos behaviour is missing), **missing**.

## Safety and operations

| Talos behaviour                                           | Talos status | Where / next step                                                                                                                                              |
| --------------------------------------------------------- | ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hard mute: `TALOS_MUTE=1` stops speech, voice returns 503 | **ported**   | `isMuted()` in `@talos/service-kit`; voice runtime exits before config; Node voice supervisor starts no worker. Tests at all three boundaries.                 |
| No capture without consent                                | **present**  | `TalosMediaCapture.capture` requires explicit `true` consent and the Settings check is the only caller; the helper also applies macOS permissions.             |
| Camera/mic readiness without capture                      | **present**  | Settings reads the camera and microphone states through `SystemPermissions.status()` and can prompt for either without capturing media.                        |
| Process inventory + label-scoped cleanup                  | **present**  | `doctor` reports audio-capable and stale test processes by label and pid; `doctor --stop-stale` stops only the stale test labels.                              |
| Safe live acceptance harness                              | **present**  | `pnpm talos:live` covers mute, voice refusal, identity, capture, vision/sound and coding; the Room e2e suite sends identity and sensing messages in a browser. |
| Never launch audible checks by default                    | **present**  | `TALOS_MUTE=1` is set by the harness and the voice role exits before starting under mute.                                                                      |

## Perception and identity

| Talos behaviour                                                            | Talos status | Where / next step                                                                                                                                  |
| -------------------------------------------------------------------------- | ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/v1/vision/scene` people/face counts                                      | **present**  | `POST /talos/vision/scene` uses native Vision; `POST /talos/vision/describe` answers free-form questions with local `qwen2.5vl:3b` through Ollama. |
| `/v1/sound` non-word SoundAnalysis labels                                  | **present**  | `POST /talos/sound` classifies a clip with SoundAnalysis.                                                                                          |
| Presence answers from a camera frame                                       | **present**  | The Settings check records the asking Human's fresh scene counts; seeing questions answer from them for five minutes and never capture on request. |
| One-frame + short-clip consented check with media deletion                 | **present**  | `TalosMediaCapture` captures one frame and a 1s clip, then deletes the media.                                                                      |
| Self-reported name, 30-minute expiry, “forget my name”, no biometric claim | **present**  | TeamHub answers identity text with a deterministic Agent-authored Room reply.                                                                      |
| Camera/mic capability answers that explain consent                         | **present**  | `roomsenses` answers hear/see/speak, explains the explicit Settings capture step for camera/mic requests, and never invents a reading.             |

## Coding autonomy

| Talos behaviour                                                            | Talos status | Where / next step                                                                                                                                                                  |
| -------------------------------------------------------------------------- | ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| One-file coding job with model draft, sandbox verify, apply/rollback       | **present**  | Live Room proof: `qwen3:4b` drafted a patch, the sandbox verified it, and the Agent reported it; the Agent Runtime is used when configured.                                        |
| Retry once on failed sandbox test with failure output                      | **present**  | `runCodingJob` retries once with the failure text as feedback.                                                                                                                     |
| Multi-file step verified as one patch                                      | **present**  | `runCodingJob` applies and verifies every patch of a draft as one sandbox patch.                                                                                                   |
| Dependency-ordered plans, shared budget, two revisions, superseded steps   | **present**  | `createPlan`/`recordStepResult`/`applyRepair` enforce order, budget and two revisions.                                                                                             |
| Catalog guard (source/tests only) and independent same-file conflict guard | **present**  | `allowedTargets` and `assertIndependentTargets` guard a plan.                                                                                                                      |
| Typed room commands `coding job`, `coding plan`, `coding jobs`             | **present**  | Durable commands, cancel, visible retry schedule and duplicate suppression; every reply is Agent-authored.                                                                         |
| Receipts: diff, regression, hashes, attempts, remaining budget             | **present**  | Verified jobs store per-target sizes and SHA-256, the regression hash, a diff hash, attempts and budget left; `coding job <id>` reports them. The diff text stays on the Computer. |

## Control and UI

| Talos behaviour                            | Talos status | Where / next step                                                                                                                               |
| ------------------------------------------ | ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Native Swift menu-bar app and overlays     | **missing**  | Talos desktop is Electron; decide whether overlays become Electron windows or keep a Swift helper.                                              |
| AX-first click/type/read with OCR fallback | **present**  | Cua/Playwright/Shell behind the Guardrail. Compare permission prompts and receipts.                                                             |
| Non-speaking screen highlight              | **missing**  | Port the ring/label overlay.                                                                                                                    |
| Muted capability and identity replies      | **present**  | Identity, hear, see and speak questions get deterministic Agent replies; speak reports `TALOS_MUTE` and never claims a camera reading.          |
| Offline voice transcription                | **present**  | Local MLX Whisper adapter, supervisor selection without a Soniox key, unit tests and a silent `say -o` transcription check in the live harness. |

## Room collaboration (live)

| Behaviour                                                 | Talos status | Evidence                                                                                                                                                             |
| --------------------------------------------------------- | ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Routine fires into a Room with an Agent mention           | **present**  | `TestRoomRoutineFiresAsAgentMessage` covers the API, worker and wake message; the browser suite edits and fires routines.                                            |
| Typed Room reminder command                               | **present**  | `reminder in <n><m/h/d>: <task>` creates a one-shot Routine, replies as the Agent, and fires through the worker.                                                     |
| No-progress streak on repeated sandbox failures           | **present**  | OpenClaw `getNoProgressStreak` port with branch tests; the coding worker stops after two identical failures.                                                         |
| Room Document create, edit, comment and reopen            | **present**  | Browser e2e covers both journeys and `pnpm talos:live` runs them as `room-document-e2e`.                                                                             |
| Stale Agent one-shot follow-up grace                      | **present**  | Hermes-style two-minute grace retires a late Agent follow-up; Human reminders still fire.                                                                            |
| Typed Room pin and unpin commands                         | **present**  | `pin this room` / `unpin this room` update the requester’s private pin and reply as the Agent.                                                                       |
| Real model call through the Codex Runtime on local Ollama | **present**  | `ollama-codex-model` runs Codex locally and a Direct Room reply used `qwen3:8b-16k`; the setup home advertises every installed Ollama model, so 4b Agents place too. |
| Room Thread create, reply, summary and search             | **present**  | `TestRoomThreadJourneyLive` covers the full path and the live harness runs it.                                                                                       |
| Real model-generated Agent conversation                   | **present**  | `qwen3:8b-16k` answered a Direct Room message through Codex and the reviewed text-reply fallback persisted it; the guard still blocks unaided local models.          |
| Tenant message search journey                             | **present**  | `room-search-journey` covers visibility, pagination and case-insensitive hits.                                                                                       |
| Direct Room lifecycle and Agent visibility                | **present**  | `room-direct-journey` covers opening, visibility and deletion.                                                                                                       |
| Proactive check-in windows and quiet wakes                | **present**  | `room-proactive-journey` runs the Postgres proactive window, quiet-wake and delivery suite.                                                                          |
| Agent memory publish, revision and read                   | **present**  | `room-memory-journey` runs the Postgres memory scope and revision suite.                                                                                             |
| Personal Wiki input tree, versions and refresh            | **present**  | `room-wiki-journey` covers the input tree, immutable versions and upload-triggered refresh.                                                                          |
| Heartbeat quiet vs substantive reporting                  | **present**  | OpenClaw token rules ported; quiet acknowledgements stay unposted, long reports surface without the token.                                                           |
| Standing goals: commands, judge and continuation          | **present**  | `goal:` commands persist a 20-turn goal; the worker judges each turn locally, posts the Hermes continuation prompt and stops on done, blocked or the budget.         |

## Already stronger in Talos

- Rooms, documents, wiki and long-term memory.
- Proactive agent and nightly rule learning.
- Model autopilot/local inference gateway.
- Multi-agent task delegation.
- Electron desktop install/update path.

## Porting order

1. Media readiness + consent gate (smallest safety-critical gap).
2. Vision and sound helpers behind explicit consent.
3. Coding jobs/plans with sandbox verification and rollback.
4. Identity claims and deterministic capability replies.
5. Process inventory + live harness.
6. Native overlays or Electron equivalents.
7. Full parity replay: run the Talos acceptance list against Talos, muted.
