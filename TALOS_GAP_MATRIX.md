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
| No capture without consent                                | **missing**  | Add a camera/mic consent gate before Electron `getUserMedia` or any helper.                                                                                    |
| Camera/mic readiness without capture                      | **missing**  | Electron only reports screen recording and accessibility. Extend `SystemPermissions`.                                                                          |
| Process inventory + label-scoped cleanup                  | **partial**  | `apps/computer/local-daemon/internal/process` and diagnostics exist; no Talos-style `stale/audio` inventory. Compare and port.                                 |
| Safe live acceptance harness                              | **present**  | `pnpm talos:live` covers mute, voice refusal, identity, capture, vision/sound and coding; the Room e2e suite sends identity and sensing messages in a browser. |
| Never launch audible checks by default                    | **present**  | `TALOS_MUTE=1` is set by the harness and the voice role exits before starting under mute.                                                                      |

## Perception and identity

| Talos behaviour                                                            | Talos status | Where / next step                                                                                                                                  |
| -------------------------------------------------------------------------- | ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/v1/vision/scene` people/face counts                                      | **present**  | `POST /talos/vision/scene` uses native Vision; `POST /talos/vision/describe` answers free-form questions with local `qwen2.5vl:3b` through Ollama. |
| `/v1/sound` non-word SoundAnalysis labels                                  | **present**  | `POST /talos/sound` classifies a clip with SoundAnalysis.                                                                                          |
| Presence answers from a camera frame                                       | **missing**  | Depends on vision helper.                                                                                                                          |
| One-frame + short-clip consented check with media deletion                 | **present**  | `TalosMediaCapture` captures one frame and a 1s clip, then deletes the media.                                                                      |
| Self-reported name, 30-minute expiry, “forget my name”, no biometric claim | **present**  | TeamHub answers identity text with a deterministic Agent-authored Room reply.                                                                      |
| Camera/mic capability answers that explain consent                         | **partial**  | `roomsenses` answers hear/see questions without inventing a camera reading; no live consent probe yet.                                             |

## Coding autonomy

| Talos behaviour                                                            | Talos status | Where / next step                                                                                                                           |
| -------------------------------------------------------------------------- | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| One-file coding job with model draft, sandbox verify, apply/rollback       | **present**  | Live Room proof: `qwen3:4b` drafted a patch, the sandbox verified it, and the Agent reported it; the Agent Runtime is used when configured. |
| Retry once on failed sandbox test with failure output                      | **present**  | `runCodingJob` retries once with the failure text as feedback.                                                                              |
| Multi-file step verified as one patch                                      | **present**  | `runCodingJob` applies and verifies every patch of a draft as one sandbox patch.                                                            |
| Dependency-ordered plans, shared budget, two revisions, superseded steps   | **present**  | `createPlan`/`recordStepResult`/`applyRepair` enforce order, budget and two revisions.                                                      |
| Catalog guard (source/tests only) and independent same-file conflict guard | **present**  | `allowedTargets` and `assertIndependentTargets` guard a plan.                                                                               |
| Typed room commands `coding job`, `coding plan`, `coding jobs`             | **present**  | Durable commands, cancel, visible retry schedule and duplicate suppression; every reply is Agent-authored.                                  |
| Receipts: diff, regression, hashes, attempts, remaining budget             | **partial**  | The job row keeps status and result; a diff/hash receipt is not stored yet.                                                                 |

## Control and UI

| Talos behaviour                            | Talos status | Where / next step                                                                                  |
| ------------------------------------------ | ------------ | -------------------------------------------------------------------------------------------------- |
| Native Swift menu-bar app and overlays     | **missing**  | Talos desktop is Electron; decide whether overlays become Electron windows or keep a Swift helper. |
| AX-first click/type/read with OCR fallback | **present**  | Cua/Playwright/Shell behind the Guardrail. Compare permission prompts and receipts.                |
| Non-speaking screen highlight              | **missing**  | Port the ring/label overlay.                                                                       |
| Muted capability and identity replies      | **partial**  | Agent prompt only; add deterministic handlers.                                                     |

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
   | Routine fires into a Room with an Agent mention | **present** | `TestRoomRoutineFiresAsAgentMessage` covers the API, worker and wake message; the browser suite edits and fires routines. |
   | Typed Room reminder command | **present** | `reminder in <n><m/h/d>: <task>` creates a one-shot Routine, replies as the Agent, and fires through the worker. |
