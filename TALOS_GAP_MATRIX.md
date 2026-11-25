# Talos → Talos feature gap matrix

Reference: Talos `830 tests / 90 live checks` at the point of the replatform
decision. Talos base `09363b7`; branch `talos/foundation`.

Status meanings: **ported** (works here and is tested here), **present**
(Talos already has an equivalent, not yet compared for parity), **partial**
(some machinery exists, Talos behaviour is missing), **missing**.

## Safety and operations

| Talos behaviour                                           | Talos status | Where / next step                                                                                                                               |
| --------------------------------------------------------- | ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Hard mute: `TALOS_MUTE=1` stops speech, voice returns 503 | **ported**    | `isMuted()` in `@talos/service-kit`; voice runtime exits before config; Node voice supervisor starts no worker. Tests at all three boundaries. |
| No capture without consent                                | **missing**   | Add a camera/mic consent gate before Electron `getUserMedia` or any helper.                                                                     |
| Camera/mic readiness without capture                      | **missing**   | Electron only reports screen recording and accessibility. Extend `SystemPermissions`.                                                           |
| Process inventory + label-scoped cleanup                  | **partial**   | `apps/computer/local-daemon/internal/process` and diagnostics exist; no Talos-style `stale/audio` inventory. Compare and port.                  |
| Safe live acceptance harness                              | **missing**   | Add a Talos live pass that checks muted voice, no audio processes, consent refusal and the ported features.                                    |
| Never launch audible checks by default                    | **missing**   | Encode in the live harness and CI path.                                                                                                         |

## Perception and identity

| Talos behaviour                                                            | Talos status | Where / next step                                                                         |
| -------------------------------------------------------------------------- | ------------- | ----------------------------------------------------------------------------------------- |
| `/v1/vision/scene` people/face counts                                      | **missing**   | Talos has screen capture, not camera frame classification. Add a Vision helper and tool. |
| `/v1/sound` non-word SoundAnalysis labels                                  | **missing**   | ASR/dictation exists; no sound-event classifier.                                          |
| Presence answers from a camera frame                                       | **missing**   | Depends on vision helper.                                                                 |
| One-frame + short-clip consented check with media deletion                 | **missing**   | Port `talos capture-check` semantics.                                                     |
| Self-reported name, 30-minute expiry, “forget my name”, no biometric claim | **missing**   | Add session-local claim handling to the Agent/Room path.                                  |
| Camera/mic capability answers that explain consent                         | **missing**   | Add deterministic replies, not model prose.                                               |

## Coding autonomy

| Talos behaviour                                                            | Talos status | Where / next step                                                                    |
| -------------------------------------------------------------------------- | ------------- | ------------------------------------------------------------------------------------ |
| One-file coding job with model draft, sandbox verify, apply/rollback       | **missing**   | Agent runtime executes tasks but has no bounded patch job or sandbox patch verifier. |
| Retry once on failed sandbox test with failure output                      | **missing**   | Port with the job state machine.                                                     |
| Multi-file step verified as one patch                                      | **missing**   | Port.                                                                                |
| Dependency-ordered plans, shared budget, two revisions, superseded steps   | **missing**   | Port.                                                                                |
| Catalog guard (source/tests only) and independent same-file conflict guard | **missing**   | Port.                                                                                |
| Typed room commands `coding job`, `coding plan`, `coding jobs`             | **missing**   | Add Room/Agent command routing.                                                      |
| Receipts: diff, regression, hashes, attempts, remaining budget             | **missing**   | Persist alongside Agent work.                                                        |

## Control and UI

| Talos behaviour                            | Talos status | Where / next step                                                                                   |
| ------------------------------------------ | ------------- | --------------------------------------------------------------------------------------------------- |
| Native Swift menu-bar app and overlays     | **missing**   | Talos desktop is Electron; decide whether overlays become Electron windows or keep a Swift helper. |
| AX-first click/type/read with OCR fallback | **present**   | Cua/Playwright/Shell behind the Guardrail. Compare permission prompts and receipts.                 |
| Non-speaking screen highlight              | **missing**   | Port the ring/label overlay.                                                                        |
| Muted capability and identity replies      | **partial**   | Agent prompt only; add deterministic handlers.                                                      |

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
