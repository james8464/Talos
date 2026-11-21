# Talos

Talos is a local room assistant with a native Mac app, a local HTTP endpoint, searchable
conversation memory, and a background planner.

## Run the room assistant

On a Mac with Python 3.12 or later, Xcode command-line tools, and Ollama installed, run.
Install uv for the launcher's isolated Kokoro setup.

```sh
cd /Users/james/Developer/Talos
python3 run_talos.py
```

The launcher builds and opens the native app. It starts its own Ollama server on
loopback port 11435 and prepares a local Qwen3 4B model on the first run. The model store lives
under `models/ollama`, so this startup path works independently of a missing external
Ollama disk or an existing Ollama instance on port 11434. Keep the launcher running.
The first model download needs internet access. Use `--offline` for subsequent runs if
you prefer to require an already downloaded model. Use `--model NAME` to select a
different locally runnable Ollama model. The launcher loads the model in the background and keeps it warm between turns.
The app shows model readiness. Sensor checks and explicit timed reminders work during
model startup. The launcher restarts its own model process if it exits. Voice turns use a smaller planning budget.

Say “Hey Talos, remind me in one minute to stretch” to set a local reminder even while the model
loads. Whole minutes, hours and days are supported on this path. Other phrasing goes to
the normal planner.

Listen for **“Talos online”** after the app connects. The default voice is Kokoro
`bm_george`. Its models and Python environment are prepared on the first online run.
Use `--voice am_michael` to select another English Kokoro voice or `--system-voice` for
macOS speech. The app labels the active voice and any fallback.

Room mode starts when the service connects. On first launch, grant macOS microphone,
speech recognition, and camera permissions. Each sensor has its own off switch. During playback the microphone accepts an explicit “Hey Talos, stop” interruption. During response planning it pauses. The speech client uses
on-device recognition and shows words as it hears them. It gives the recognizer Talos’s name as a vocabulary hint. The app shows reply,
decision time, queue time, and time from transcript submission to first audio.

Start a new hands-free request with **“Hey Talos”**. You can also say the name first and then speak.
For eight seconds after a voice reply, Talos accepts a linked follow-up such as “And what about Friday?”
or a delay in response to a reminder question. “Actually, make that five minutes” corrects the
last single voice reminder. A follow-up can amend that reminder or answer a pending question.
Starting another action requires addressing Talos again. For a longer
request, choose **Dictate a message**, pause as needed, then finish dictation, review the
text, and press **Send**. Dictated messages keep the voice transcript retention setting.

Try “Hey Talos, add buy paper to my to-do list.” For “Hey Talos, remind me to take out
the recycling,” Talos asks for the delay. Reply “Hey Talos, in fifteen minutes” within
two minutes to create the reminder. These requests work while the language model is loading.

The camera card shows a live local preview and a **Person detected** or **No person detected**
result. The endpoint receives occupancy only. Enable **Remember voice transcripts**
if you want Talos to archive those turns locally. With archiving off, it can follow a short
conversation using a rolling memory that expires after five minutes of no voice turns.
A voice request that creates a task,
reminder, note, or alert keeps the resulting local item.

Arrivals and departures update occupancy silently. Ordinary room chatter can be ignored.
Talos answers hearing, visibility and identity checks directly from the input channel and
current camera state. Their Kokoro audio is prepared at startup. Other replies use short
structured decisions and start playback when the first sentence is ready. Additional
sentences are synthesized during playback. The local model stays resident while the
launcher runs. Actionable events can trigger a planner decision and one delayed follow-up.
Local actions apply by default in room mode. Use `--shadow` to evaluate what it would do
and see conversational replies with no inbox changes or proactive announcements. Reminders
and alerts display their details in the app and use a generic spoken notification. Stop
the stack with Ctrl-C in the launcher terminal. The app pauses its room sensors if the
endpoint becomes unreachable.

For development, `--headless` starts the service without opening the app, and `--build-only`
builds and signs `state/Talos.app` without starting a model.

### Track a meeting goal

Type “Get me ready for the Atlas review” to find a timed event in the next two weeks.
Review the matched event and save the goal. You can also open **Goals**, enter an outcome
and date manually, and choose a project folder. Use words from a filename for **Required
document name**. Talos checks Markdown, plain text and CSV files and shows the relative
path, modification time and an excerpt. Competing candidate files appear for review.
Saved commitments and tasks are matched by topic and retain their record IDs.

The goal survives a restart. A moved Calendar event updates its existing reminder.
Pause or resume tracking in **Goals**. Choose when to receive updates, or keep changes
in the goal history. A zero reminder lead switches off the scheduled reminder.
The Mac must be running with Calendar read access for event changes to reach the service.

### Test speech in a practice room

When the physical room is available for a test, switch off its live microphone to prevent
both apps from hearing the same request. From this directory run the following command
with the project's local Ollama instance available.

```sh
python3 run_talos.py --room-trial
```

The practice window starts with both sensors off. Its inbox, goals, history and token use
a temporary directory. Switch on the practice microphone, use synthetic names and compare
these cases with **Use local model for mid-sentence pauses** off and on.

| Say or do | Check |
| --- | --- |
| “Hey Talos, can you hear me?” | The answer confirms hearing. Record the displayed time from detected speech end to playback start. |
| “Hey Talos, remind me in ten minutes to” … pause for two seconds … “check the blue folder” | One reminder with the full subject. Record an early cutoff if the subject is lost. |
| “Actually, make that five minutes” immediately after the reply | The existing reminder changes. |
| Ask another person to remind you about the red folder | Talos stays silent and creates no item. |
| During a long reply, say “Hey Talos, stop” | Playback stops, then a new addressed question works. |

Repeat each case from the usual speaking position, including one run with another person
talking. Note the final caption, any unintended action and any discrepancy between the
displayed playback timestamp and what you heard. Quit the trial window and press Ctrl-C
in its terminal to remove the temporary state. Restore the live microphone when finished.

The synthetic transcript benchmark compares installed local Ollama models independently
of the microphone and speaker.

```sh
TALOS_OLLAMA_PORT=11435 PYTHONPATH=. python3 tests/compare_turn_models.py qwen3:4b qwen3:8b
```

With the pinned Pipecat environment installed, the same script can call its upstream
Smart Turn analyzer on labeled synthetic waveforms. Supply mono 16 kHz PCM16 WAVs
ending at the pause, up to eight seconds each. Each flag can appear several times.

```sh
PYTHONPATH=. python tests/compare_turn_models.py --audio-hold midphrase.wav --audio-end finished.wav
```

### Public questions and web search

Say “Hey Talos, search the web for the next lunar eclipse” to send that query to DuckDuckGo
through DDGS. The first search uses uv to install the pinned search client. Talos reads
up to four result snippets, answers with the local model, and shows the titles and URLs.
Typed questions that need current public facts also let the planner choose search. The
spoken path requires a search request. Search receives the current query only.

Say “Hey Talos, ask Respan why the Moon has phases” to try the explicitly named free model route.
Set `RESPAN_API_KEY` or put the key in `state/respan.key` with owner-only permissions.
Only the question after “Ask Respan” goes to that provider. The room loop continues
using Ollama. Provider errors and rate limits surface in the reply. `--offline` disables
both external lookups. The Respan route is defined in `room_research.py`.

### Investigate a recurring problem with Codex

Talos counts slow voice decisions (at least two seconds) and slow synthesis requests
(at least one second). It keeps a count and the worst duration for each stage. From a
second terminal while the room launcher is running, use the installed Codex CLI through:

```sh
python3 improve_talos.py
python3 improve_talos.py "Long replies start playing too slowly"
```

With no argument, the command selects the most frequent slow stage seen at least twice.
It copies Talos source and tests into a temporary directory, asks Codex for a diff,
checks that the diff applies there with Git, and saves it under `state/proposals/`.
Inspect and test the patch before merging it. A failed model call or invalid diff
leaves the running assistant intact. Local generation defaults to the project Ollama
port and model `qwen3:4b`. Use `--model` for another installed coding model. The optional
`--cloud` uses your signed-in Codex account and its usage limits. The room microphone
cannot start a coding session.

### Local API

The user-facing endpoint binds to `http://127.0.0.1:8765`. `GET /health` checks the server.
The remaining routes take an Authorization Bearer header with the token in `state/room.token`.

| Route | Purpose |
| --- | --- |
| `GET /v1/status` | Room presence, model, and queue state |
| `POST /v1/turn` | Conversation or a local task, with `text`, `source`, and optional `remember` |
| `POST /v1/voice` | Synthesize up to 600 characters with the configured local Kokoro voice |
| `POST /v1/voice/latency` | Record estimated speech-end to playback-start time for a completed voice turn |
| `POST /v1/presence` | Presence signal with `occupied` set to true, false, or null |
| `POST /v1/events` | Queue an observation with `kind` and `summary` |
| `GET /v1/events/{id}` | Look up the processing state and decision receipt |
| `GET /v1/inbox` | Review open local items |
| `GET /v1/speech` | Fetch the next ready room announcement |
| `POST /v1/speech/ack` | Acknowledge a delivered announcement by `id` |

After upgrading, close the previous Talos window and stop its launcher before relaunching.
Run `python3 -m unittest discover -s tests -v` to validate the local service. To verify a
live setup, speak a phrase only you know, request a local timer, then leave and re-enter
the camera's view. Check the text reply, listening and room indicators, and the event
receipts. Arriving and leaving should stay silent. The Mac app requires an active desktop session and approved device permissions.

### Repeat the room acceptance checks

The acceptance suite sends authenticated HTTP requests over connected local sockets into
an isolated database. It can wait through a one-minute reminder, restart the room service
at the application layer, verify deferred delivery and acknowledgment, and render a new reply
with the installed Kokoro voice. For real spoken replies, `GET /v1/status` includes the sample count, median and 95th
percentile for the last 64 reported turns. The native client measures from the last
above-threshold microphone buffer to the start of software playback.

On a Mac the suite can also feed the synthesized waveform to the same
end-of-speech timing code used by the microphone.

```sh
TALOS_TEST_REAL_MINUTE=1 TALOS_TEST_KOKORO=1 state/kokoro-env/bin/python -m unittest discover -s tests -v
```

Set `TALOS_TEST_OLLAMA=1` and `TALOS_OLLAMA_PORT=11435` when the project model server is
running to exercise an open-ended question and a follow-up with Qwen. The tests use
synthetic turns. Physical speech recognition, room acoustics and camera detection still
need verification with the Mac's devices.

## Optional legacy browser benchmark

On an Apple Silicon Mac with [uv](https://docs.astral.sh/uv/getting-started/installation/) and [Ollama](https://docs.ollama.com/), run from the project directory.

```sh
uv sync --python 3.12
mkdir -p models
uv run hf download mlx-community/whisper-base-mlx config.json weights.npz --revision 1e3e249 --local-dir models/whisper-base-mlx

curl --fail --location --output models/kokoro-v1.0.onnx \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.onnx
curl --fail --location --output models/voices-v1.0.bin \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin
```

Start a local Ollama server in a separate terminal. If the Ollama app is already listening on port 11434, quit that instance first so the command starts with the explicit local-only setting. Leave the server terminal open.

```sh
OLLAMA_NO_CLOUD=1 OLLAMA_HOST=127.0.0.1:11434 ollama serve
```

Back in the project terminal, download the baseline model once, then start Talos.

```sh
ollama pull qwen2.5:1.5b
uv run --no-sync python bot.py --check
uv run --no-sync python bot.py
```

Open [the local Pipecat test client](http://127.0.0.1:7860), connect, and allow microphone access. Disconnect or close the page to end the session. Before each reply, Talos trims the in-memory history to up to 12 messages and 6,000 characters, plus its system instructions. By default the voice session is ephemeral. Use `--remember` to archive its text turns and
enable recall across sessions. The voice client processes audio in memory. This is a development
runner for short tests. Unattended voice service, ambient address detection, and hardware room
sensors are future milestones.

## Talk to Talos and check its abilities

`python3 agent.py status` reports which capabilities exist and checks the prerequisites for
voice and text with no microphone access. For a text-only trial, start Ollama with cloud
features disabled as shown above. In another Terminal window, use the project directory:

```sh
cd /Users/james/Developer/Talos
ollama pull qwen2.5:1.5b
python3 agent.py chat
```

If your existing Ollama models are on an external drive, mount the drive first. To use a
project-local model store instead, start the loopback server from the project directory as
shown below, after quitting any existing Ollama server on port 11434. This leaves your
existing Ollama model location unchanged.

```sh
mkdir -p models/ollama
OLLAMA_NO_CLOUD=1 OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS="$PWD/models/ollama" ollama serve
```

Speech models and Pipecat are required only for the browser voice session.

Try these questions and compare the replies with `/status`:

1. **Perception.** Ask "Can you see or hear me right now?" In the text console, Talos
   receives typed text. In a working browser voice session, read a novel phrase aloud
   and ask Talos to repeat it. Talos has no camera integration.
2. **Personality.** Ask "Roast the fact that I have 40 browser tabs open, in one sentence."
   Follow with a serious request and see whether it drops the sarcasm.
3. **Memory.** Tell Talos a made-up detail, quit with `/quit`, reopen the console, and ask
   about that detail. The `recall` command can verify whether the transcript was saved.
4. **Tool use.** Enter `/task Remind me in one minute to test the speakers.` in the console.
   It proposes the task in shadow mode. Relaunch with `python3 agent.py chat --apply`
   and try again to allow the local action. Use `/inbox` to verify that the reminder
   exists. In a second terminal, run `python3 agent.py watch` to see it become due.

Leave the default text console in shadow mode for evaluation. Use a separate test database
for synthetic memory and actions, for example `python3 agent.py --db /tmp/talos-test.sqlite3 chat`.

## Local memory

Use the loopback Ollama server and the local model from setup. The commands in this section
and the planner use Python 3.12+ with SQLite FTS5. They work independently of Pipecat and the
speech models.

```sh
python3 agent.py chat "The spare bike key is in the blue drawer."
python3 agent.py chat "Where did I leave the spare bike key?"
python3 agent.py recall "spare bike key"
python3 agent.py transcript
```

Completed text exchanges are saved locally. To save and recall active browser voice sessions,
start the voice client with `uv run --no-sync python bot.py --remember`. Enable this for a voice
session whose participants know it is being transcribed. The archive contains text turns, and
Talos retrieves relevant excerpts for the model. Earlier sessions that were never saved are
unavailable for recall. The `transcript` command prints the full saved archive as JSON lines.

## Autonomous planner and local tools

By default the planner works in shadow mode. Give it a new observation and it can look up
conversation memory, its inbox, previous decisions and feedback, or the current time. It can
perform up to three read-only lookups before deciding whether to act, ask, wait, or ignore.
Review the result and tell it what you would change:

```sh
python3 agent.py listen <<'JSONL'
{"kind":"synthetic","summary":"Rain started while the window is open. Nobody is in the room."}
{"kind":"synthetic","summary":"The window has just been closed."}
JSONL
python3 agent.py history
python3 agent.py feedback 1 "If the situation has already resolved, skip the alert."
```

Use the ID shown by `history` when giving feedback. Relevant older corrections and recent
reviews are retrieved for future decisions. The stored feedback informs the next decision
through its context.

Use `--apply` to authorize writes to the local inbox. Talos can create a task, save a note,
log an alert, or schedule a reminder. Use `task` for a direct request from the terminal, or
`listen --apply` for an event stream from a local producer you control.

```sh
python3 agent.py task --apply "Remind me in 10 minutes to bring in the laundry."
python3 agent.py inbox
python3 agent.py watch
```

`watch` prints due alerts and reminders in its terminal. It can run in parallel with a
listener, and it uses no model calls. Use `python3 agent.py done ITEM_ID` to acknowledge an
entry. Pending reminders survive restarts, and a new watcher prints any due, unacknowledged
entries again. In `history`, a decision's action status records whether its effect was applied,
blocked, or skipped. A `speak` proposal is currently available for evaluation in shadow mode.
A `wait` proposal can be reconsidered when another observation arrives.

Use `--db PATH` before the subcommand to isolate a test. The default database
`state/agent.sqlite3` contains conversations, observations, feedback, decisions, and inbox items.
Use `--model NAME` before the subcommand to compare a different locally installed Ollama model.
The default is the small latency baseline, and the planner uses
[structured output](https://docs.ollama.com/capabilities/structured-outputs). No model runs
while the listener waits for input.

## Verify and benchmark

Use `bot.py --check` to diagnose Python, Pipecat installation, speech model files, and the local Ollama model without starting audio. Run the unit tests without models or third-party dependencies using `python3 -m unittest discover -s tests -v`.

During a conversation, the terminal reports server-side turn timing from Pipecat. Use its transcription, model, and synthesis breakdown to see where time goes. For audible response latency and interruption time, measure at the speaker. The server cannot measure browser buffering or when sound reaches the listener.

For the live acceptance check, block WAN access once the assets are installed, leaving the local network interface active for browser WebRTC. Start a new session and verify a reply and follow-up. Test with headphones first, then with room speakers. Measure elapsed time from the end of your speech to first audible output, and from your interruption to playback stopping. Collect median and 95th-percentile times for warm turns, record cold starts separately, and record average wall power at idle and in use. No live latency or power results have been measured yet.

The installed model is a deliberately small latency baseline. A larger local model may give better answers. Compare them on the same prompts before making a hardware decision. For the always-available version, keep cheap sensors and voice activity detection active and invoke heavier models only when a turn or room event needs them.

## Upstream components

[Pipecat examples](https://github.com/pipecat-ai/pipecat/tree/main/examples) · [Whisper MLX](https://huggingface.co/mlx-community/whisper-base-mlx) · [Ollama local-only mode](https://docs.ollama.com/faq) · [Qwen2.5 1.5B](https://ollama.com/library/qwen2.5:1.5b) · [Kokoro ONNX](https://github.com/thewh1teagle/kokoro-onnx)

See [the technical research memo](Talos-technical-research.md) for architecture alternatives and proposed latency targets.

### Multi-step requests and meeting preparation

Say “Hey Talos, save a note that the Atlas spending cap is 12000, create a task to
send the Atlas forecast and remind me in fifteen minutes.” Talos shows a receipt
for every saved item. The task and its reminder are committed together. A client
retrying a compound request can reuse its UUID in the optional `request_id` field.

For a less structured job, type it into the message field and choose **Preview plan**.
The local Ollama model proposes inbox changes. Review the titles, reminder delays
and any work marked **Still open**. **Apply listed steps** commits the displayed
plan. Reapplying the same plan returns the same receipts, including after a service
restart. Shadow mode keeps the inbox unchanged.

Type “Brief me on Atlas” to retrieve matching saved notes, open tasks, reminders
and archived first-person commitment excerpts. The brief includes record IDs.
**Next meeting brief** uses Apple Calendar to find the next timed event starting
within seven days. Type “Prepare me for tomorrow’s meeting” for the first timed
event tomorrow. Calendar access is requested on first use. Once connected,
“Hey Talos, prepare me for my next meeting” can display the brief without reading
its contents to the room. Titles such as “Weekly Atlas review” match against the
local records for Atlas. A generic event name may need a more specific typed topic.

You can add “and remind me before I leave” to a meeting request. Talos displays
the brief and asks for a delay. Reply on the same input channel within two minutes;
in hands-free mode, start the reply with “Hey Talos”. It sets the local timer once
you give it a time. Calendar events and camera presence do not establish when you
intend to leave.
