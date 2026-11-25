# Sound and scene perception

Talos classifies media without playing or storing it.

## Non-word sound

`talos sound --file clip.wav` uses Apple's built-in SoundAnalysis classifier
(`tools/native/sound_classify.swift`). It returns ranked labels such as speech,
music, applause, laughter, glass, alarms and everyday sounds. On the generated
Kokoro speech WAV it reported `speech 94%`; silence returned no events.

`POST /v1/sound` accepts `{"audio": "<base64 WAV>"}` and returns
`{"events": [...], "description": "..."}`. The temporary WAV is private and
deleted after classification. Nothing is played.

## Scene / people

`talos scene --file frame.png` uses Apple Vision
(`tools/native/vision_scene.swift`) to count faces and people. A blank frame
returns `{"people": 0, "faces": 0, "empty": true}` and the description
`The scene looks empty.`

`POST /v1/vision/scene` accepts a base64 JPEG/PNG and returns the same counts.
The image is written to a private temporary file, inspected, and deleted.

This detects presence, not biometric identity. Saying “that is me” would
require an explicit owner-face enrollment feature; it is not enabled by default.
The native room app can send its camera frame to the endpoint.

## Silence

While `TALOS_MUTE` is set, `/v1/voice` returns 503 and no playback path runs.
Kokoro can still be tested by synthesizing to a WAV without playback; a 5.12 s
clip was generated and the player was exercised at volume 0. The running service
remains muted.

## Verified examples

- `Ping.aiff` repeated for length: telephone 69%, telephone bell 58%, beep 52%.
- `Glass.aiff` repeated: music 60%, percussion 53%, glockenspiel 41%.
- A standard 512×512 public test portrait: `{"people": 1, "faces": 1}`.
- A blank frame: `{"people": 0, "faces": 0, "empty": true}`.
- Kokoro produced a valid 5.12 s, 24 kHz mono WAV; the player path was
  exercised at volume 0 and the running service stayed muted.

## Room presence

`/v1/turn` accepts a `camera_jpeg` plus `Is anyone there?` / `Is the room empty?`.
The room runs the Vision detector on that frame and answers from the count:
an empty frame → `The room looks empty.`; one person → `I see one person.`
It deliberately does not identify the person without an explicit face
enrollment feature.

## Native room app

The room menu now has **Classify camera scene** and **Classify ambient sound**.
The first sends the current camera JPEG to `/v1/vision/scene`; the second records
a two-second private WAV while the microphone is off and sends it to
`/v1/sound`. Both display a text line in the room and never play audio. Camera
and microphone permissions are requested by macOS; nothing is captured unless
one of those menu actions is chosen. The native `RoomClient.scene`/`sound`
methods are exercised by `tests/NativeSenseReplay.swift` against an isolated
room with synthetic media. Actual camera/mic capture was not run automatically
to avoid recording the sleeping user.

## Camera and microphone readiness

`talos av-readiness --json` and `GET /v1/av/readiness` compile and run a small
AVFoundation probe that reads `authorizationStatus` and device presence. It
never creates a capture session, records media, or triggers a permission
prompt: `not_determined` is reported as-is. The response includes
`capture_started` and `recording_started`, both `false`, and `ready` only when
both permissions are authorized and both devices exist.

## Consent-gated capture check

`talos capture-check` is off unless `--consent-capture` is passed. With the
flag it requests camera/microphone access, captures one JPEG frame and a short
WAV clip, sends them to `/v1/vision/scene` and `/v1/sound`, and deletes the
temporary media even when verification fails. Without the flag it exits `2`
before compiling or capturing anything. The native helper is only invoked on
the consented path.

## Self-reported identity

A name introduced in conversation is remembered for the running room process
only. Asking "do you recognize me?" then answers with that self-reported name
and the caveat that camera identity is not verified. A stored owner profile
still answers "who am I?" with the saved name; nothing here is biometric
identification, and the claim is lost on restart.
