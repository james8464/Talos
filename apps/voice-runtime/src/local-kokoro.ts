/**
 * Local Kokoro text to speech. A small Python helper synthesizes one phrase to
 * a private temporary WAV; this module reads it and removes the file. It never
 * plays audio.
 */
import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";

const exec = promisify(execFile);

export type KokoroTone = "calm" | "alert" | "menacing";

/**
 * One voice per situation: a deep, even assistant by default, a sharper voice
 * for an alert, and a slower British voice when the situation is a threat.
 * Callers can still override the voice explicitly.
 */
const TONE_PROFILES: Record<KokoroTone, { voice: string; speed: number }> = {
  calm: { voice: "am_onyx", speed: 0.98 },
  alert: { voice: "am_fenrir", speed: 1.03 },
  menacing: { voice: "bm_lewis", speed: 0.84 },
};

export function kokoroToneProfile(tone: KokoroTone): {
  voice: string;
  speed: number;
} {
  return TONE_PROFILES[tone];
}

export interface KokoroOptions {
  /** A Python interpreter with kokoro_onnx and numpy installed. */
  readonly python: string;
  /** The WAV-writing helper shipped in this package. */
  readonly helper: string;
  readonly model: string;
  readonly voices: string;
  /** Overrides the tone's voice for every call. */
  readonly voice?: string;
  readonly speed?: number;
}

export interface KokoroClip {
  readonly wav: Buffer;
  readonly sampleRate: number;
  readonly seconds: number;
}

export type KokoroRunner = (
  file: string,
  args: readonly string[],
) => Promise<{ stdout: string }>;

const run: KokoroRunner = (file, args) =>
  exec(file, [...args], { timeout: 180_000 });

/** Returns a synthesizer that writes and reads one clip per call. */
export function kokoroSynthesizer(
  options: KokoroOptions,
  runner: KokoroRunner = run,
): (text: string, tone?: KokoroTone) => Promise<KokoroClip> {
  return async (text, tone = "calm") => {
    const profile = options.voice
      ? { voice: options.voice, speed: options.speed ?? 1 }
      : kokoroToneProfile(tone);
    const folder = await mkdtemp(path.join(os.tmpdir(), "talos-kokoro-"));
    const file = path.join(folder, "speech.wav");
    try {
      const { stdout } = await runner(options.python, [
        options.helper,
        options.model,
        options.voices,
        profile.voice,
        String(profile.speed),
        text,
        file,
      ]);
      const reported = JSON.parse(stdout) as {
        sample_rate?: unknown;
        seconds?: unknown;
      };
      return {
        wav: await readFile(file),
        sampleRate: Number(reported.sample_rate) || 24_000,
        seconds: Number(reported.seconds) || 0,
      };
    } finally {
      await rm(folder, { recursive: true, force: true });
    }
  };
}
