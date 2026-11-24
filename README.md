# Talos

Talos is a local, self-improving Mac assistant. It listens or chats, remembers evidence,
plans actions, controls the Mac through native accessibility and screen capture, and
improves its own source when the operator is away.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the package layout, the self-improvement
loop, and the computer-use API. Common commands:

```sh
python3 run_talos.py                                  # native room app
python3 agent.py chat                                  # terminal console
python3 -m talos_core.cli status                       # health, permissions, graph
python3 -m talos_core.cli doctor                       # imports, syntax, lint, tests
python3 -m talos_core.cli decompose "TASK" --file PATH # routable task plan
python3 -m talos_core.cli computer frontmost            # native Mac control
python3 -m talos_core.cli computer see TextEdit          # discover visible controls
python3 -m talos_core.cli computer choose Calculator 7 --click
python3 -m talos_core.cli computer window resize --app TextEdit --width 800 --height 600
python3 -m talos_core.cli computer scroll 5 --app TextEdit
python3 -m talos_core.cli graph decomposition manager    # source knowledge graph
python3 -m talos_core.cli console                        # dense ASCII brain + typed fallback
python3 -m talos_core.cli instructions --set "Prefer metric units"  # local prompt policy
python3 -m talos_core.cli capabilities                   # tools, tests and upstream gaps
python3 -m talos_core.cli capabilities --queue           # queue top gaps for coding
python3 -m talos_core.cli code --model qwen3:4b          # draft/verify/apply one plan
python3 -m talos_core.cli initiative                     # think of and queue new capabilities
python3 -m talos_core.cli reflect                        # recompute related knowledge
python3 -m talos_core.cli vision frame.jpg "What do you see?"
python3 -m tools.app_scenarios calculator          # multi-step real app check
python3 -m tools.app_scenarios safari
python3 -m tools.knowledge_audit                    # 14-store personal context audit

# One-time native control extra (Accessibility + Screen Recording granted):
python3 -m pip install -e '.[control]'

python3 -m talos_core.self_improvement.improve_talos --cycle
python3 -m unittest discover -s tests -p 'test_*.py'
```

## Silent mode

Talos can run without producing any sound. Start the launcher with `--mute`, or export `TALOS_MUTE=1` for any entry point:

```sh
python3 run_talos.py --mute
TALOS_MUTE=1 python3 -m talos_core.interfaces.telegram_gateway --owner YOUR_NUMERIC_ID
```

In silent mode the room drops its voice object, disables proactive speech, and returns HTTP 503 from `POST /v1/voice`. Kokoro and Telegram voice-note synthesis raise a clear error before loading models, so callers fall back to text. Typed turns, memory, reminders, Mac control, maintenance and the local model continue to work. The installed launchd service uses `--mute` and `TALOS_MUTE=1` by default.

## Terminal sessions and Telegram

In `python3 agent.py chat`, use `/new project` for a fresh conversation, `/sessions` to list saved sessions and `/resume project` to reopen one. Use `/save FILE.jsonl` to export every turn of the active session to a new private file. Use `/branch project-b` (or `/fork`) to continue from the current conversation. A branch inherits turns up to that point and writes its own subsequent turns. Branches share stored transcript rows. Named sessions use recent turns and can retrieve older matching turns from their visible history, including inherited turns up to the fork. Use `/search QUERY` to search only the current conversation. Automatic recall in the default console excludes named-session turns. Explicit `/recall QUERY` searches the local archive. The console opens in the original `text` session by default.

The Talos terminal supports standing writing goals. Use `/goal draft OBJECTIVE` to save one for the current session, `/subgoal REQUIREMENT` to add a criterion, then `/goal run`. Entering `/goal OBJECTIVE` starts immediately. Talos drafts the answer and sends it to a separate review call. Completion requires the reviewer to quote evidence from the draft. Missing evidence, an unavailable reviewer, or five unfinished steps pauses the run. Use `/goal show`, `/goal pause`, `/goal resume`, or `/goal clear` across console restarts. The latest draft and review are available in the session database. Use `/goal queue OBJECTIVE` to leave work for the running Talos room. Its idle reviewer checks the queue every 30 seconds and yields inference to foreground activity. With the room closed, a separate `python3 agent.py goal-worker --watch` process can process the same queue. The worker polls explicit queued goals on AC power after five minutes of Mac inactivity. Point `TALOS_OLLAMA_PORT` at your dedicated local model server. A queued goal survives closing the console, and a return of activity before review preserves the draft for the next idle cycle. Desktop actions continue to use the room's verified action paths.

For Telegram direct messages, configure a dedicated bot token in the local environment as `TALOS_TELEGRAM_TOKEN`, then run the gateway with your numeric Telegram user ID. When using the room launcher's local Ollama server:

```sh
TALOS_OLLAMA_PORT=11435 python3 -m talos_core.interfaces.telegram_gateway --owner YOUR_NUMERIC_ID
```

Alternatively, run with `--pair` in place of `--owner YOUR_NUMERIC_ID`. Send the one-time code shown in the terminal to the bot in a private message. Future launches use the saved pairing. Add `--check` to verify the bot identity, webhook configuration and local model without polling or sending a message. The gateway polls while the process runs. A reply to an earlier text carries its selected quotation into that turn, even after a gateway restart. The owner can send direct text messages or JPEG photos, including JPEG file attachments, with a question in the caption. Reply directly to your earlier photo to ask a follow-up about that image. Photos use the installed local vision model (default `qwen2.5vl:3b`, configurable with `TALOS_VISION_MODEL`). Talos selects the largest available size within 180,000 bytes and four megapixels, reads it in memory, and keeps the image and caption out of conversation history. Telegram transports the chat. A text reply uses the current request, an explicit reply quotation when present, and up to four prior exchanges from the same bot and owner. A photo question uses one supplied image and its question. Its channel database lives under `state/telegram`. Send `/new` to clear that local context. The gateway has no access path to Mac actions or family recall. If a send times out, delivery is uncertain and Talos skips automatic retransmission.

Telegram voice notes use local CPU transcription. In a dedicated Python 3.12 environment install `faster-whisper==1.2.1`, then run `python -m talos_core.interfaces.telegram_gateway --setup-voice` once with that interpreter. The setup command verifies a pinned multilingual Tiny model before publishing it. To use an existing CTranslate2 Whisper model, set `TALOS_TELEGRAM_WHISPER_MODEL` to its directory. Start the gateway with the same interpreter. It accepts owner Ogg/Opus notes up to 90 seconds and 2 MB. Transcribed words share the bounded text-chat history. Audio is processed in memory.

For spoken replies, also install `kokoro-onnx==0.6.0` in that environment and use the Kokoro assets prepared by the room launcher. Send `/voice voice_only` to hear replies to voice notes, `/voice all` to hear short conversational replies, or `/voice off` for text. Long replies and replies without a working local synthesizer arrive as text.

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

The room shows its connection and microphone state in the main window. The default voice is Kokoro
`bm_george`. Its models and Python environment are prepared on the first online run.
Use `--voice am_michael` to select another English Kokoro voice or `--system-voice` for
macOS speech. Room settings shows the active voice and any fallback.

Room mode starts when the service connects. Allow microphone and speech recognition for dictation. The camera starts when you enable it or have previously opted into facing requests. Each sensor has its own off switch. During a voice reply, a new recognized phrase can interrupt playback. “Hey Talos, stop” also stops announcements. A partial “wait” can silence a conversational reply while the recognizer continues listening for the rest of a correction. During response planning it pauses. The speech client uses
on-device recognition and shows words as it hears them. It gives the recognizer Talos’s name as a vocabulary hint. The app shows reply,
decision time, queue time, and time from transcript submission to first audio.

Choose **Talk to Talos**, or say **“Hey Talos, can we talk?”**, to have a
conversation without repeating a wake word. The session stays open through replies and closes after 90 seconds of inactivity. Choose **End conversation** or say “end this conversation” to stop it early.
With facing requests enabled and the camera on, a sustained view of one person facing the Mac can also start a
direct question. Looking away, another detected person, or stale visual readings stop
new visually directed turns. The camera gives a cue about intended address. It cannot
tell which person spoke off camera.

Talos keeps the last six exchanges for a connected conversational thread. You can
correct yourself mid-reply with phrases like “Wait, I meant…” in an engaged
conversation. The recognizer compares partial captions with playback text to discard matching echo.
Local tasks, notes and timed reminders started this way are read back first. Say
**confirm** or **cancel**. Saying **“Hey Talos”** still starts an explicit request.
Its short follow-up window accepts a reminder delay or “Actually, make that five minutes.”
For a longer
request, choose **Room options → Dictate a draft**, pause as needed, then finish dictation, review the
text, and press **Send**. Dictated messages keep the voice transcript retention setting.

Try “Hey Talos, add buy paper to my to-do list.” For “Hey Talos, remind me to take out
the recycling,” Talos asks for the delay. Reply “Hey Talos, in fifteen minutes” within
two minutes to create the reminder. These requests work while the language model is loading.

The camera card shows the local preview and occupancy. Facing direction and optional
visual templates are computed on the Mac. The room planner receives occupancy.
Enable **Remember voice transcripts** to archive ordinary addressed exchanges.
Confirmed visitors can separately agree to retain their own conversations. Saved
actions keep their local receipts. Room presence by itself creates no work order.

Talos answers hearing, visibility and identity checks directly from the input channel and
current camera state. Kokoro reuses standard sensor answers and clarification phrases after their first synthesis. Other replies use short
structured decisions and start playback when the first sentence is ready. Additional
sentences are synthesized during playback. The local model stays resident while the
launcher runs. Actionable events can trigger a planner decision and one delayed follow-up.
Local actions apply by default in room mode. Use `--shadow` to evaluate what it would do
and see conversational replies with no inbox changes or proactive announcements. Reminders
display their details in the app and use a generic spoken notification. Other unsolicited updates appear silently unless **Speak proactive updates and introductions** is on. Stop
the stack with Ctrl-C in the launcher terminal. The app pauses its room sensors if the
endpoint becomes unreachable.

For development, `--headless` starts the service without opening the app, and `--build-only`
builds and signs `state/Talos.app` without starting a model.

### Ask a visual question

With the camera on, ask “Hey Talos, what am I wearing?” Talos requests one current JPEG for the local vision model. Frames expire after two seconds and are never written to the database. Clothing answers can still be wrong in poor light or when the garment is out of view. The separate text planner receives occupancy. Install the visual model once on the project server:

```sh
OLLAMA_HOST=127.0.0.1:11435 ollama pull qwen2.5vl:3b
```

Use `TALOS_VISION_MODEL` to select another installed local image-capable model. Vision works offline after setup. Turning the camera off prevents frame capture.

### Private sources and commitments

In **Memory → Private sources and commitments**, choose one file, its topic, the other person, and your own sender name. Ask “What do I owe people?” or “What do I owe people before tomorrow?” Selected evidence is private by default. Enable speech per source to include its facts in spoken briefings. Choose a cited file to reveal it in Finder. Select the corresponding promise if Talos asks about an ambiguous date revision. Forgetting a source removes its extracted evidence and links.

| Source | Supported input |
| --- | --- |
| Email | Plain-text EML or an mbox export up to 8 MB. Mailbox entries must match the topic in the subject and the contact in the sender or recipient fields. |
| iMessage | A dated `date,sender,text` CSV or a selected readable Messages SQLite database. Enter the exact chat identifier for a database. The reader uses up to 500 recent plain-text messages in that chat. |
| WhatsApp | A selected exported text chat, with day-first or year-first dates. Refresh the export to include later messages. |
| Project and Calendar | Select a document or a project folder as evidence of preparation, and an ICS file for matching event titles and times. Existing **Goals** can track a project folder and EventKit meeting. |

The reader recognizes explicit English promises, weekday or ISO deadlines, and certain date changes and delivery statements. It links them by the contact and topic you select. A draft file is evidence of preparation. A message about sending something is identified as a possible delivery. The **Refresh briefing** button reloads indexed evidence. While the room is quiet its reviewer checks one eligible source at a time for changes. `python3 -m talos_core.memory.commitment_memory refresh` forces a refresh.

For explicit memory, try “Remember that my bike key is in the green drawer”, “Actually, my bike key is in the blue tin”, and “Where is my bike key?” Saved facts survive restarts within their text, room voice or confirmed visitor scope. “Forget my bike key” removes that fact. Room-wide voice facts require **Remember voice transcripts**. This setting persists between launches.

### Private Mac app lookup

**Tasks, reminders and commitments.** In the private message field, `List my tasks` shows task IDs. Use `Complete task #12` or `Cancel reminder #13` with the ID you received. Ask aloud “When is my next reminder?” for the time, or type it to see the text too. “What did I promise my sister about Atlas?” shows source-linked promises without scheduling a reminder. “Are my commitment sources up to date?” counts current, due, pending and unavailable selected sources. “How fast are you?” reports measured playback latency and lists the slow stages still waiting for review. Type “Who is enrolled for face recognition?” to see active enrollments and their dates.

Double-click `Talos-Check.command` to run all optional acceptance cases, the synthetic turn-to-WAV benchmark and the synthetic portrait replay. It uses the installed Kokoro environment and the project Ollama service on port 11435. Set `TALOS_PORTRAIT_FIXTURE` to use a different four-panel replay fixture. The checks leave the live microphone and camera off.

**Watch Talos in Terminal.** An interactive `Start-Talos.command` shows the live activity feed once the room starts. Double-click `Talos-Activity.command` for a separate read-only viewer, even when the service is stopped. Close the separate viewer with Ctrl-C. Run `./Talos-Activity.command --once` for one snapshot or pass `--port PORT` to select a room. The display separates room and model health, worker locks, operations in progress, past receipts, and the last heartbeat. Talos keeps up to 512 operational events. Open the text console with `python3 agent.py chat`. Use `/activity` or `/agents` for a snapshot, `/context [QUESTION]` for the prompt breakdown, and `/usage [all]` for measured console chat tokens. Use `/bg REQUEST` to queue a durable work order with actions enabled, then `/jobs` to see its outcome or `/jobs cancel ID` to cancel it. Use `/parity Hermes` or `/parity OpenClaw` for command names still missing from the console.

In the private message field, try `Search my Mail memory for "train ticket"` for an exact phrase within Mail, or `List my upcoming reminders` for the next ten pending reminders. To merge the search index and reclaim unused pages, run `python3 -m talos_core.memory.personal_index --compact`.

**Private personal memory.** Type “Search my memory for …” in the private message field. Results show matching passages and source record IDs. The maintenance worker refreshes enabled app databases separately from search. Type “What do you remember about me?” to see facts you explicitly saved in this conversation.

In the text console, `/title Launch plan` labels the current session. Use `/resume Launch plan` to reopen it and `/export-session` for a private HTML transcript in the current workspace. See [the upstream source review handoff](talos_core/UPSTREAM.md#source-review-handoff) for Talos's source comparison commands and verification queue.

To run the full room service at login without opening a window, stop any existing Talos launcher and run `python3 tools/talos_service.py install`. The service uses the downloaded local model, restarts after a crash, and runs during the logged-in Mac session. Check with `python3 tools/talos_service.py status` and unregister with `python3 tools/talos_service.py stop`.

While Talos is idle, the maintenance worker checks one indexed project at a time. Changed evidence can trigger a review by the configured local Ollama model, using up to ten short excerpts. Type “Show memory review for Talos” in the private message field to see candidate aliases, relationships, recurring patterns or changed plans and their source record IDs. Each finding must cite at least two records. Talos hides findings whose citations have changed, disappeared or become disabled, and checks for revised conclusions on later passes. AI findings are hypotheses for private recall.

If a finding is misleading, type “Dismiss memory finding ID for Talos” with the ID beside the claim. “Restore memory finding ID for Talos” reverses that choice.

Run `python3 -m talos_core.memory.personal_index --files --knowledge` to refresh home documents, local mail bodies and supported attachment text, bookmarks and Reading List entries, local Git history, synced cloud folders and confirmation evidence. Use **Memory → Import Calendar and Reminders** and **Memory → Index text in images and scans** for macOS-authorized imports. The maintenance worker picks up these snapshots. Use `python3 -m talos_core.memory.personal_index --apple-notes` to import Apple Notes with macOS Automation permission or `python3 -m talos_core.memory.personal_index --notes-vault /path/to/vault` for an Obsidian vault. For issue trackers and other exports, place Markdown, text or JSONL records under `state/knowledge-imports/Projects`. Search narrower sources with queries like “Search my Calendar memory for Atlas”. **Talos maps → Project connections** lists matching record IDs across sources.

**Continuous improvement.** The idle room worker reviews its own source and writes feature proposals and acceptance tests to `state/feature-requests.jsonl`. Each entry identifies its origin. Run `python3 -m talos_core.self_improvement.feature_queue --pending` to see open requests and `python3 -m talos_core.self_improvement.feature_queue --complete ID "verification result"` after testing a change. Run `python3 -m talos_core.self_improvement.feature_queue --free-respan` to request a proposal with the configured source-only cloud fallback. The scheduled app task consumes this queue and refreshes the private index independently of room restarts.

Choose your apps in **Memory → Private sources and commitments**. You can also enable local lookup with `python3 -m talos_core.mac.mac_context --enable Contacts Messages Mail WhatsApp`. Running the same command with no app names disables lookup. In the private message field, try “Search my apps for cobalt”, “Search my messages for Smith”, or “Show my family relationships”. Family labels come from related-name entries on your Contacts Me card. Matching contact addresses can connect a name with messages in an enabled app. The reply includes the source and record ID. Lookup reads the current databases, including up to 2,000 recent plain-text items per messaging or mail source and 5,000 contacts per address book. Mail lookup covers indexed subjects and summaries. Access depends on the permissions of the process running the room service.

Ask “What did I just say?” to repeat a recent turn even during model startup. Visually directed follow-ups require a current conversation token.

### Idle coding experiments

After five minutes without room requests and keyboard/mouse input, while on AC power, the room can investigate repeated slow decisions or synthesis with the local model. It copies source and tests to a temporary directory, asks for an edit, checks its scope, applies it to the copy and parses the Python. Sandbox-supported runs execute the same stage-specific regression suite against the baseline and candidate. Trial status lives in the local `coding_trials` table. Checked patches appear in `state/proposals` for review. A foreground turn cancels the experiment. Applying a proposal to the running assistant requires a separate deployment.

An overnight run can monitor local Codex usage with `python3 -m talos_core.self_improvement.overnight_goal --watch --until UNIX_TIMESTAMP`. A recent Codex usage record below 3% in either active window enables at most one source-only review per hour through the configured OpenClaude Respan GLM-4.5-Flash route. The worker keeps checked patches and trial results in the state directory. An absent measurement cannot start a handoff. The worker exits at the specified deadline.

For comparison on the target Mac, `PYTHONPATH=. state/kokoro-env/bin/python tests/measure_idle_voice.py` times synthetic reminders from service submission through complete synthesized WAV with and without an indexing batch. The app's first-playback metric covers a different interval.

### Introductions and conversation memory

With **Offer introductions and remember consenting visitors** enabled, a sustained
single-person camera view can trigger a brief introduction. Talos asks for a name,
then asks whether to remember it, a visual reference, and future words addressed to
Talos during that visit. “Skip” ends the introduction. On a later visit, a sufficiently
close and unambiguous image match prompts for confirmation. A confirmed profile lets
Talos use that visitor’s earlier statements. An ambiguous match starts a new
introduction. People with the same name keep separate profiles.

Apple Vision supplies the face region, orientation and image feature print. The
image comparison is experimental. Talos uses the saved descriptor to suggest a name
and asks the person to confirm. Raw audio, video frames and face photos are not saved.
**Memory → Forget visual reference** removes the descriptor and preserves written notes.

After ten seconds without a submitted turn, a background reviewer processes retained
exchanges. It links direct quotations to people and topics, using the local model for
less obvious statements. Unmatched introductions are labeled as self-reports. The
reviewer exports Markdown files without overwriting existing notes. Choose **Memory →
Open memory folder** and open that folder as a vault in Obsidian. The production path
is `state/Talos Memory`. A practice room has its own temporary vault. The SQLite
database stores the searchable records and visual templates locally.

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
these cases with **Use the local model for mid-sentence pauses** off and on.

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
python3 -m talos_core.self_improvement.improve_talos
python3 -m talos_core.self_improvement.improve_talos "Long replies start playing too slowly"
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
uv run --no-sync python -m talos_core.interfaces.bot --check
uv run --no-sync python -m talos_core.interfaces.bot
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
start the voice client with `uv run --no-sync python -m talos_core.interfaces.bot --remember`. Enable this for a voice
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

Use `python -m talos_core.interfaces.bot --check` to diagnose Python, Pipecat installation, speech model files, and the local Ollama model without starting audio. Run the unit tests without models or third-party dependencies using `python3 -m unittest discover -s tests -v`.

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

### Persistent background work and the knowledge map

A normal Talos launch now starts a five-minute heartbeat alongside memory maintenance. Practice and shadow rooms skip it. Each tick revisits up to four due, active goals using their saved evidence and notification preferences, refreshes the Obsidian map and records its outcome in `state/heartbeat.json`. A changed source fingerprint triggers the offline dialogue regression suite. While the Mac is in use, changed code gets a lightweight Ruff review of the dialogue module. After five idle minutes on AC, up to one coding trial per hour rotates among dialogue, recall and speech. It tries the project Ollama model, then the configured source-only OpenClaude route, then Ruff. Offline launches omit OpenClaude. The older deadline-based quota watcher remains available for explicit handoffs.

To run the heartbeat independently of the room, use `state/kokoro-env/bin/python -m talos_core.self_improvement.overnight_goal --heartbeat --watch`. Add `--local-only` for offline work. To register it at login on this Mac, run `state/kokoro-env/bin/python tools/enable_heartbeat.py --at-login`. The LaunchAgent runs once per interval during a logged-in, awake session. A shared lock and persisted review times prevent overlapping trials across entry points. To remove login registration, boot out `gui/$(id -u)/local.talos.heartbeat` with launchctl and remove its plist from ~/Library/LaunchAgents.

In the vault open **Talos maps → Talos memory map**. The conversation index links previously exported exchanges to their existing speaker and topic notes. The source branch shows record coverage for enabled apps. The development branch links source files to actual proposals, acceptance criteria and recorded verification. The heartbeat note distinguishes an attempted review, a candidate patch and its replay result. The worker can generate the views offline. Existing private conversations and user-authored notes stay in place.

#### Architecture decisions

| Concern | Decision and evidence |
| --- | --- |
| Foreground interaction | Keep Swift for macOS audio, camera, permissions and UI. Keep Python for intent routing, recall and job coordination. Ollama, SQLite and the speech engines already execute the expensive operations outside Python. Compare end-to-end first-audio time and retrieval latency on the target Mac before moving another component to Swift or Rust. |
| Agent framework | Keep the existing room API, bounded workers and local tool handlers. Bind work to a saved goal, event or due time. A heartbeat covers missed events after sleep. An extra orchestration server would add a process and a second place to recover state. |
| Operational memory | Keep SQLite and FTS5 for scoped records, source references, corrections, deletion and exact phrase retrieval. Evaluate semantic search with a fixed set of paraphrase and exact-name queries. Add a local embedding index only if it increases recall within a measured latency and memory budget. |
| Knowledge graph | Keep Obsidian as a generated, navigable view. Store durable relationships as typed, source-backed records in SQLite as new features need them. A relationship should carry speaker or owner, source, observation time and retraction status. A message mentioning a person alone is insufficient to establish a relationship. |
| Ongoing improvement | Keep model inference off the foreground path. Distinguish observations, proposals, tested candidates and deployed revisions. The next architectural step is a shared durable job table with a goal, due time, expiring lease, attempt count, evidence and deployment revision. Migrate one worker at a time and test interruption, duplicate delivery and rollback. |

### Native recurring work and dictation

In the private message field, try `Brief me on Atlas every weekday at 09:00 in Europe/Paris` or `Remind me every Monday at 7:15 am to check the calendar`. Use `List my routines` and `Cancel routine #1` to inspect or stop them. The room worker and heartbeat advance the same SQLite schedules. After sleep, a due briefing uses current local evidence and advances to the next future occurrence. Its contents are available in the local inbox. The shared room announces a private notification. Calendar calculations require Node 22.18 or later.

Choose **Room options → Dictate a draft** to start recording. The live transcript replaces the current selection as recognition revises it. Finish to edit and send up to 4,000 characters, or cancel to restore the draft. When dictation starts the microphone, finishing also turns it off. Dictated messages retain the existing voice-memory setting.

The source and attribution inventory is in [talos_core/UPSTREAM.md](talos_core/UPSTREAM.md).

Type `Queue this work: Save a note that Atlas launches Tuesday and add a task to review the launch.` Talos stores the request in SQLite and can finish explicit local work through its room worker or heartbeat. Model-assisted planning uses the room worker. A job with unresolved steps asks for clarification before applying any steps. `Show my background jobs` lists outcomes and receipt IDs. `Cancel background job #1` cancels queued work or an in-flight plan before it commits.

### Watch the Obsidian graph

Open the existing vault in `state/Talos Memory` and navigate to **Talos maps → Talos memory map**. The publisher checks every two seconds while the room runs. Shared indexed evidence, current AI hypotheses, development proposals, and Python imports appear as linked Markdown notes. The code index shows actual file revisions, and **Recent source edits** lists the last 50 observed edits. A source save can change a note without adding an edge. Revoking a source removes its generated evidence on the next refresh.

Normal room and memory-only launches start self-repair, memory maintenance, Obsidian publishing, and scheduled source and knowledge reviews together. Run `./Start-Talos.command --memory-only --offline` for a live terminal feed with the installed on-device reviewer and no room or cloud connection. Normal launches without `--offline` also start the Respan and source-section reviewers. Ctrl-C stops workers started by that launcher. The console supports `/model status`, `/models`, `/model NAME`, `/clear`, `/redraw`, `/copy`, and `/version`.

## Running the room and watching its work

On the Mac, open `Start-Talos-Service.command` to register the room at login, open its native app and display the activity feed. The existing local model and the Respan credential must already be configured. The login service keeps ordinary room conversations local and enables the background Respan reviewer and the OpenCode and OpenClaude source-section workers. The service runs while the Mac is awake and the user is logged in. To stop it, run `python3 tools/talos_service.py stop`.

Open `Talos-Activity.command` at any time. The monitor probes port 8765 and shows the status of each worker separately. Respan events identify the model, actual source path and byte range, request and response, token usage when returned, accepted finding or abstention, and the next retry. Connection failures leave the source position available for retry. A memory-only launch runs without a listening room. Use `./Start-Talos.command --memory-only --offline --respan-reviews` for that mode with cloud review enabled.

Try saying “Talos, check in with me in three hours.” The room keeps the request across restarts. If the question plays and you do not answer, it asks once more ten minutes later. “I’m okay” closes the check-in. Say “Cancel” immediately after hearing the question to stop its follow-up, or say “cancel my check-in” later. A question overdue by more than two minutes expires. Saying “I feel unwell” offers the check-in locally. Silence alone never triggers contact with another person.

If Obsidian has the project’s `state` directory open as its vault, open **Talos live** there for recent reviewer and model activity, or **Talos migrations** for verified changes linked to their individual notes. The existing project, evidence and code maps stay inside `Talos Memory/Talos maps`. The publisher updates the root notes as the worker records real activity and verified migrations.

## Speech, background reviews and progress

Use **Talk to Talos** (⌘⇧D) for a conversation. To continue an unfinished addressed request such as “Hey Talos, remind me in ten minutes to…”, say “continue check the oven” within 30 seconds. “Cancel that request” discards it. The keyboard button opens a text field. Room options includes reviewed draft dictation, memory, goals and the live activity note. Long action plans open in a scrollable sheet for review before applying them.

Try “What did we say about my notebook?”, “Pause my routine to water the plants”, or “What’s eighteen percent of 240?” followed by “Split that between three people.” Topic recall uses the current conversation. Arithmetic, conversions and routine controls run locally.

Use `python3 -m talos_core.self_improvement.section_workers --sections` for the upstream assignments. `./Start-Talos.command --memory-only --offline --respan-reviews --section-reviews` starts the live terminal and both external section reviewers without a room listener. Each reviewer compares bounded source excerpts, verifies cited quotes and can draft an implementation and regression for its finding. Inspect `state/section-patches/<id>/change.diff`, `regression.py` and `receipt.json` to review a draft. Checks cover applying the diff and parsing the code. Running its behavioral regression and approving the live change are separate steps.

The **Talos live** note in the `state` vault reports when its sample was taken, worker locks, provider replies and retry deadlines. Local index maintenance records its completed merge, document count and search checks. Developer-verified changes have a separate label.

### On-device source reviews

When the Respan source reviewer is unavailable, Talos can compare OpenClaw or Hermes excerpts using the project’s existing Qwen3 4B Q4_K_M weights. On Apple Silicon, run `python3 tools/setup_local_reviewer.py` once with Docker Desktop’s bundled llama.cpp build `ac4cdde` installed. The installer verifies the model digest, fetches the [pinned upstream C headers](https://github.com/ggml-org/llama.cpp/tree/ac4cddeb0dbd778f650bf568f6f08344a06abe3a) and builds a CPU-only helper. For an offline installation, pass `--headers PATH` with those same seven headers. Run `python3 -m talos_core.self_improvement.respan_worker --local-source Hermes` for one immediate comparison.

Inference reads bounded source excerpts in a local child process and yields to foreground conversation. The activity feed records the model, actual token counts, elapsed time and source comparison outcome. A model proposal enters the development queue after its cited source lines are checked. Reviewing the proposal’s meaning, replaying the behavior and implementing the change are separate steps.

The room launcher and memory-only launcher supervise their background workers. If a worker exits and its service lock is free, the launcher retries that worker with backoff up to five minutes. The terminal records the exit, restart and subsequent lock check. An independently running owner keeps its job when another launcher closes.

### Reporting and diagnosing a Talos problem

In the local console, use `/repair DESCRIPTION` to report a problem and `/repairs` to see unresolved incidents. `/repairs ID` includes your original description on the private console. In the room, say “report a problem: my search missed a result” or “what needs repair?” Status responses and the machine-readable handoff omit the words of private reports.

Even with an empty incident queue, the repair watcher rotates through nine offline feature checks at ten-minute intervals and records outcomes in Talos live. A failing check opens an incident for replay. Checks yield to foreground conversations.

The repair watcher collects bounded batches from Talos’s activity journal, groups repeated observations and runs a related offline regression against synthetic data when one is available. A passing fixture leaves the original report open. Two failed assertion replays identify a repeatable failure in that fixture. A supervisor’s confirmed process recovery or a provider’s successful response can clear its corresponding operational blocker. `python3 -m talos_core.self_improvement.self_diagnosis --handoff` prints a read-only JSON handoff containing incident identifiers, areas, outcomes and the associated fixtures.

Automatic source repair currently covers memory-search ranking and worker reconnect/polling policies. Talos reproduces a failing regression, tests a candidate in a temporary copy, installs it only against the source revision it tested, and reruns the regression. Failed post-install verification triggers rollback. Inspect `state/self-repair/<id>/receipt.json` and `change.diff` for each attempted change. Other reproducible incidents can receive an on-device investigation under `state/self-repair/investigations/`. Open **Talos live** in the `state` vault for current worker status and open incidents.
