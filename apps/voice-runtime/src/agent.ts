import { defineAgent, log } from "@livekit/agents";
import { TeamHubVoiceRuntimeClient } from "@talos/wire-contracts/teamhub-internal/client";

import {
  loadConfig,
  sonioxLanguageHints,
  sonioxRealtimeModel,
} from "./config.js";
import type { ASRProvider } from "./asr.js";
import {
  LocalWhisperProvider,
  mlxWhisperTranscriber,
} from "./local-whisper.js";
import { SonioxASRProvider } from "./soniox.js";
import {
  VoiceTrackWorker,
  voiceTrackFailureCode,
  voicePurposeFromMetadata,
} from "./livekit-worker.js";
import type { ASRRecovery } from "./recovering-asr.js";

export default defineAgent({
  entry: async (ctx) => {
    const config = loadConfig();
    if (!config.sonioxApiKey && !config.localWhisper)
      throw new Error(
        "Voice transcription requires SONIOX_API_KEY or the local Whisper setup",
      );
    const dictation =
      voicePurposeFromMetadata(ctx.job.metadata) === "chat_dictation";
    // Recording always diarizes speakers; dictation has one speaker.
    const diarization = !dictation;
    const client = new TeamHubVoiceRuntimeClient(config.teamhubBaseUrl, () =>
      Promise.resolve(config.teamhubToken),
    );
    const onRecovery = ({ cause, replayMs, elapsedMs }: ASRRecovery) => {
      log().warn(
        {
          failureCode: voiceTrackFailureCode(cause),
          replayMs,
          elapsedMs,
          roomName: ctx.job.room?.name,
        },
        "Voice Runtime ASR replay submitted after reconnect",
      );
    };
    let asr: ASRProvider;
    if (config.localWhisper) {
      // The local model has no streaming partials or speaker labels; it
      // transcribes each finished clip after the turn ends.
      log().info(
        { model: config.localWhisper.model },
        "Voice Runtime transcribing with local Whisper",
      );
      asr = new LocalWhisperProvider({
        transcribe: mlxWhisperTranscriber(config.localWhisper),
      });
    } else {
      asr = new SonioxASRProvider({
        apiKey: config.sonioxApiKey,
        model: sonioxRealtimeModel,
        languageHints: sonioxLanguageHints,
        diarization,
        dictation,
        proxy: config.providerProxy,
        onRecovery,
      });
    }
    // The SDK logs rejected shutdown callbacks but otherwise exits the job with 0.
    await new VoiceTrackWorker(client, asr, () => process.exit(1)).run(ctx);
  },
});
