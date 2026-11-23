"""Talos voice benchmark using the Pipecat development runner."""

import argparse
import asyncio
import os
import sqlite3
import sys
from contextlib import closing
from uuid import uuid4

from talos_core.memory.agent_memory import STATE_PATH, open_memory
from talos_core.cognition.conversation import limit_history
from talos_core.runtime.local_runtime import (
    KOKORO_MODEL,
    KOKORO_VOICES,
    MODELS_DIR,
    OLLAMA_HOST,
    OLLAMA_MODEL,
    OLLAMA_PORT,
    WHISPER_MODEL,
    runtime_checks,
)
from talos_core.cognition.talos_identity import PERSONALITY, capability_context
from talos_core.memory.transcript_memory import recall_prompt, remember, search_transcripts


async def bot(runner_args):
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.frames.frames import LLMContextFrame
    from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
    from pipecat.processors.audio.vad_processor import VADProcessor
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
    from pipecat.runner.utils import create_transport
    from pipecat.services.kokoro.tts import KokoroTTSService
    from pipecat.services.ollama.llm import OLLamaLLMService
    from pipecat.services.whisper.stt import WhisperSTTServiceMLX
    from talos_core.runtime.respan_llm_service import RespanLLMService
    from pipecat.transports.base_transport import TransportParams
    from pipecat.workers.runner import WorkerRunner

    voice_session = f"voice:{uuid4().hex}" if os.environ.get("TALOS_REMEMBER_VOICE") == "1" else ""

    def recall(query: str) -> str:
        with closing(open_memory(STATE_PATH)) as database:
            return recall_prompt(search_transcripts(database, query, exclude_session=voice_session))

    def save_turn(role: str, text: str) -> None:
        with closing(open_memory(STATE_PATH)) as database:
            remember(database, voice_session, [{"role": role, "content": text}])

    async def archive_turn(role: str, text: str | None) -> None:
        if text and text.strip():
            try:
                await asyncio.to_thread(save_turn, role, text)
            except (OSError, sqlite3.Error):
                print("Talos: Could not save a voice transcript turn.", file=sys.stderr, flush=True)

    class LimitHistory(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if direction == FrameDirection.DOWNSTREAM and isinstance(frame, LLMContextFrame):
                frame.context.transform_messages(limit_history)
                if voice_session:
                    messages = frame.context.get_messages()
                    latest = messages[-1]
                    if latest["role"] == "user":
                        try:
                            memory = await asyncio.to_thread(recall, latest["content"])
                        except (OSError, sqlite3.Error):
                            print("Talos: Could not read conversation memory.", file=sys.stderr, flush=True)
                        else:
                            if memory:
                                frame.context = LLMContext([
                                    {**messages[0], "content": messages[0]["content"] + memory},
                                    *messages[1:],
                                ])
            await self.push_frame(frame, direction)

    transport = await create_transport(runner_args, {
        "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
    })
    stt = WhisperSTTServiceMLX(settings=WhisperSTTServiceMLX.Settings(
        model=str(MODELS_DIR / WHISPER_MODEL),
    ))
    llm = RespanLLMService(
        settings=RespanLLMService.Settings(
            model="zai/glm-4.7-flash",  # Best model for fine-grained task decomposition
            temperature=0.7,
            max_tokens=4096,
            respan_key=open("/Users/james/Developer/Talos/state/respan.key").read().strip(),
        )
    )
    tts = KokoroTTSService(
        model_path=str(MODELS_DIR / KOKORO_MODEL),
        voices_path=str(MODELS_DIR / KOKORO_VOICES),
        settings=KokoroTTSService.Settings(voice="am_michael"),
    )

    context = LLMContext([{
        "role": "system",
        "content": PERSONALITY + "\n" + capability_context("voice", remembers_voice=bool(voice_session)),
    }])
    user, assistant = LLMContextAggregatorPair(context)

    if voice_session:
        @user.event_handler("on_user_turn_stopped")
        async def on_user_turn_stopped(_aggregator, _strategy, message):
            await archive_turn("user", message.content)

        @assistant.event_handler("on_assistant_turn_stopped")
        async def on_assistant_turn_stopped(_aggregator, message):
            await archive_turn("assistant", message.content)

    vad = VADProcessor(vad_analyzer=SileroVADAnalyzer())

    timing = UserBotLatencyObserver()

    @timing.event_handler("on_latency_breakdown")
    async def on_latency_breakdown(_observer, breakdown):
        lines = breakdown.turn_contribution_lines()
        if lines:
            print("Talos turn timing (server)\n" + "\n".join(lines), flush=True)

    pipeline = Pipeline([
        transport.input(), vad, stt, user, LimitHistory(), llm, tts, transport.output(), assistant,
    ])
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True),
        observers=[timing],
    )
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        await runner.cancel()

    await runner.run()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local Talos voice benchmark.")
    parser.add_argument("--check", action="store_true", help="Check setup without starting audio.")
    parser.add_argument(
        "--remember", action="store_true", help="Save active-session transcripts locally and enable recall."
    )
    args = parser.parse_args()

    results = runtime_checks()
    for label, detail, passed in results:
        if args.check or not passed:
            status = "OK" if passed else "ERROR"
            print(f"[{status}] {label} | {detail}", file=sys.stdout if passed else sys.stderr)
    if any(not passed for _, _, passed in results):
        raise SystemExit(1)
    if args.check:
        return

    if args.remember:
        try:
            with closing(open_memory(STATE_PATH)):
                pass
        except (OSError, sqlite3.Error) as error:
            raise SystemExit(f"Talos: Could not open conversation memory: {error}") from None
    os.environ["TALOS_REMEMBER_VOICE"] = "1" if args.remember else "0"
    os.environ["LOGURU_LEVEL"] = "WARNING"
    os.environ["HF_HUB_OFFLINE"] = "1"
    no_proxy = os.environ.get("no_proxy", os.environ.get("NO_PROXY", ""))
    os.environ["no_proxy"] = os.environ["NO_PROXY"] = ",".join(
        filter(None, (no_proxy, OLLAMA_HOST))
    )
    os.environ.pop("PIPECAT_ICE_SERVERS", None)

    from pipecat.runner.run import main as run_pipecat

    sys.argv[1:] = ["-t", "webrtc", "--host", "127.0.0.1", "--port", "7860"]
    run_pipecat()


if __name__ == "__main__":
    main()
