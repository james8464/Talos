# Talos technical research

21 November 2026 · Phase 1 research

## Recommendation

Benchmark a **local, streaming voice pipeline on an existing computer**. Use Python 3.12, uv, and Pipecat with its stock browser test client, local Whisper, Ollama, and Kokoro. The first experiment answers whether a short conversation feels fast at room distance, with a speaker playing and a person able to interrupt. This setup can run with no model API fees. Once downloaded, models should run with WAN access blocked. Hardware and electricity remain real costs.

Keep the eventual room device separate from the inference host. A Raspberry Pi can serve as the microphone, speaker, and camera endpoint over the LAN. An existing Apple Silicon Mac or a stronger Linux/Windows PC can host the models. Keep the compute on one machine for the first experiment to avoid adding a network and a device port to the baseline.

## Architecture choices

| Option | Reuse | Role in Talos |
| --- | --- | --- |
| **Pipecat** | Python pipeline, browser client, SmallWebRTC transport, interruption handling, local Whisper and Ollama integrations, and local Smart Turn endpoint detection | First choice for a single room and easy comparison of individual models. |
| **LiveKit Agents** | Agent SDK, turn detection, interruption handling, self-hosted WebRTC deployment | Revisit if multi-room sessions, multiple clients, or network operations justify a dedicated media server. |
| **Home Assistant Assist** | Device control, local speech components, Linux Voice Assistant satellite using ESPHome | Integrate when Talos needs actual home controls. Its local Speech-to-Phrase engine is aimed at predefined commands. Home Assistant documents around eight seconds for Whisper commands on a Pi 4 in its setup, a useful warning against sizing the project around that board. |
| **Moshi / PersonaPlex** | Local full-duplex speech-to-speech. Moshi provides an Apple Silicon MLX path. PersonaPlex provides text role prompts and voice conditioning with a separate gated model license. | Use as a second experiment for overlaps and conversational rhythm. Test tool use, prevention of unwanted output, and sustained hardware load before committing to this architecture. |
| **OpenAI GPT-Live** | Hosted speech-to-speech over WebRTC | Optional comparison using an opted-in, bounded test with scripted audio. At the published GPT-Live 1 rate of US$0.05 per session minute, ten minutes is US$0.50 and a 24-hour open session is US$72, both before separately charged backend model or tool usage. |

Sources [Pipecat](https://github.com/pipecat-ai/pipecat) · [SmallWebRTC](https://docs.pipecat.ai/api-reference/server/services/transport/small-webrtc) · [Smart Turn](https://github.com/pipecat-ai/smart-turn) · [LiveKit turns](https://docs.livekit.io/agents/logic/turns/) · [LiveKit self-hosting](https://docs.livekit.io/transport/self-hosting/) · [Home Assistant local voice](https://www.home-assistant.io/voice_control/voice_remote_local_assistant/) · [Linux Voice Assistant](https://github.com/OHF-Voice/linux-voice-assistant) · [Moshi MLX](https://github.com/kyutai-labs/moshi/tree/main/moshi_mlx) · [PersonaPlex](https://github.com/NVIDIA/personaplex) · [GPT-Live pricing](https://developers.openai.com/api/docs/pricing) · [GPT-Live WebRTC](https://developers.openai.com/api/docs/guides/voice-webrtc)

## Components to try

| Layer | Initial choice | When to compare |
| --- | --- | --- |
| Audio capture and playback | Browser mic and speaker with echo cancellation requested, via Pipecat’s stock client and SmallWebRTC on the local machine or LAN | If speaker playback triggers Talos or masks interruptions, test placement and settings. For headless Linux, evaluate the PipeWire WebRTC echo canceller, routing both playback and capture through it. |
| Speech detection | Silero voice activity detection and Pipecat Smart Turn | Tune on pauses, hesitation, and background audio. Voice activity detects speech. Endpointing estimates when the turn ends. Address detection decides whom the speech is for. |
| Transcription | Pipecat’s MLX Whisper backend on Apple Silicon, or its faster-whisper backend on another PC where supported. Start with a small model. | If turn-final transcription dominates delay, compare a genuinely streaming sherpa-onnx recognizer with the same utterances. Measure accuracy in each language Talos needs as well as time to a usable transcript. |
| Language model | Ollama with a small quantized local instruct model. Qwen3 4B with thinking disabled is an available baseline. Keep the model warm and bound conversational context during the test. | Try a second model only if measurements show a speed, accuracy, or personality issue. Compare using the same prompts and versioned model tags or digests. |
| Speech synthesis | Kokoro 82M via the Pipecat integration. Send a short, complete clause to synthesis as soon as it is available. | Compare Piper if Kokoro is too slow for the host, or if speech later has to run on the Pi. Review the exact voice and engine terms before distribution. Kokoro’s model card lists Apache 2.0. Piper’s current engine is GPL-3.0-or-later and its voices have separate terms. |
| Vision, when needed | Existing local person/zone events via Frigate if a camera is needed for a specific use case | Start with a synthetic event for the first proactive reminder. A local occupancy or door sensor may be sufficient. Evaluate snapshots only for tasks that genuinely need visual context. |

Sources [MDN echo cancellation](https://developer.mozilla.org/en-US/docs/Web/API/MediaTrackConstraints/echoCancellation) · [PipeWire echo cancellation](https://docs.pipewire.org/page_module_echo_cancel.html) · [Pipecat dependencies](https://github.com/pipecat-ai/pipecat/blob/main/pyproject.toml) · [Pipecat Whisper backend](https://github.com/pipecat-ai/pipecat/blob/main/src/pipecat/services/whisper/stt.py) · [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) · [Ollama chat API](https://docs.ollama.com/api/chat) · [Ollama FAQ](https://docs.ollama.com/faq) · [Qwen3 4B](https://ollama.com/library/qwen3:4b) · [Kokoro model card](https://huggingface.co/hexgrad/Kokoro-82M) · [Piper licensing](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/LICENSING.md) · [Frigate motion detection](https://docs.frigate.video/configuration/motion_detection/)

## What to reuse and what Talos owns

Start from the BSD-2-Clause [official Pipecat examples](https://github.com/pipecat-ai/pipecat-examples) and the [Pipecat quickstart](https://docs.pipecat.ai/pipecat/get-started/quickstart). The independent [locat project](https://github.com/vipyne/locat) is an integration reference for local speech, Ollama, and browser audio. I did not find a reuse license in the reviewed locat repository, so take implementation code from licensed upstream examples. Pin Python, application dependencies with a checked-in uv lockfile, and model identities to make measurements reproducible. [uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/)

Talos should own a small, testable policy for when it speaks. Start the first voice test in a manually opened listening session, then allow follow-ups with no wake word within a bounded window. Treat ambient, wake-word-free address detection as its own experiment. Test deliberate speech directed at Talos against speech directed at another person, TV dialogue, and the system’s own speaker output. Default to silence when the evidence is weak. Speaker recognition and room occupancy can be useful signals, but neither establishes who someone is addressing.

For the first proactive interaction, an existing reminder is eligible when an arrival event occurs. The policy checks mute, quiet hours, current conversation, reminder expiry, and whether it has already been delivered. It defers or drops stale events and avoids repeating an alert after reconnects. A model can phrase an approved reminder in Talos’s dry voice. The application grants permission to speak and can cancel queued audio independently.

Use local processing and no raw recording or transcript retention by default. The room device should provide an obvious capture indicator and a physical mute control. The benchmark can log stage durations, decision outcomes, and anonymous test-case IDs. Use explicitly prepared test recordings for repeatability.

## First benchmark

1. **Reproduce a known baseline.** Run the official Pipecat browser example with one supported local backend per stage. Preload the assets and verify the whole path with internet access blocked while localhost or the LAN stays available. Compare headphones and speakers to localize acoustic problems.
2. **Measure the whole experience.** Record the end of human speech to first audible Talos audio, and human interruption onset to audible playback stop. Use a loopback or external recording for scripted cases and mark stage boundaries in the pipeline to isolate endpointing, final transcript, first model text, first synthesized audio, and playback delay. Report p50 and p95 separately for warm and cold runs, and note CPU or GPU use and peak memory.
3. **Use provisional acceptance targets.** For short, warm, tool-free exchanges, aim for p50 ≤ 800 ms and p95 ≤ 1.5 s to first audible speech, and p95 ≤ 300 ms to stop playback when interrupted. These are design targets. Report failures and premature turn endings alongside latency. Start with at least 50 scripted turns, then expand before treating tail numbers as a dependable estimate.
4. **Test one proactive reminder.** Inject an arrival event and verify the eligible reminder speaks once. Repeat during playback, human conversation, mute, quiet hours, and reconnect. Run a negative set with TV and bystander speech and report false replies per hour alongside misses on directed speech.
5. **Decide from results.** Swap only the component that dominates the measured delay. If timing passes and interaction still feels rigid, compare Moshi on the same host, with the same interruption and false-reply cases. Test the Pi as a room endpoint once the single-machine audio baseline is understood.

The numbers are proposed targets, and the component choices are sourced candidates. No stack was installed, no hardware was profiled, and no performance on the intended room setup was measured in this research pass.
