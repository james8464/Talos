/**
 * Experimental ambient Talos loop: listen, transcribe locally, decide whether
 * the speech is addressed to Talos, and answer with the local Kokoro voice.
 * It is the first slice of the always-on assistant; camera presence and
 * computer actions are not wired yet.
 *
 * Run: pnpm talos:ambient [--once]
 */
import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { execFile } from "node:child_process";

import { TalosMediaCapture } from "../apps/desktop/src/main/talos-media-capture.js";
import { TalosPerception } from "../apps/desktop/src/main/talos-perception.js";
import { classifySceneBase64 } from "../apps/desktop/src/main/talos-perception-endpoint.js";
import {
  kokoroSynthesizer,
  type KokoroTone,
} from "../apps/voice-runtime/src/local-kokoro.js";
import {
  mlxWhisperTranscriber,
  pcmFromWAV,
} from "../apps/voice-runtime/src/local-whisper.js";

const exec = promisify(execFile);
const ROOT = path.resolve(import.meta.dirname, "..");
const OLLAMA = process.env.TALOS_OLLAMA_URL ?? "http://127.0.0.1:11434";
const MODEL = process.env.TALOS_AMBIENT_MODEL ?? "qwen3:4b-8k";
const PYTHON = path.join(ROOT, "apps/voice-runtime/.venv/bin/python");
const LISTEN_SECONDS = Number(process.env.TALOS_AMBIENT_SECONDS ?? 6);

export type Addressed = "yes" | "no" | "other";

export interface AmbientDecision {
  readonly addressed: Addressed;
  readonly reply: string;
}

export interface Presence {
  readonly people: number;
  readonly faces: number;
  readonly empty: boolean;
}

/** Validates the local model's one-object decision. */
export function parseAmbientDecision(value: unknown): AmbientDecision {
  if (typeof value !== "object" || value === null)
    throw new Error("The ambient model returned no object.");
  const record = value as Record<string, unknown>;
  if (
    record.addressed !== "yes" &&
    record.addressed !== "no" &&
    record.addressed !== "other"
  )
    throw new Error("The ambient model returned no addressed value.");
  return {
    addressed: record.addressed,
    reply:
      typeof record.reply === "string" ? record.reply.trim().slice(0, 800) : "",
  };
}

/**
 * Picks the voice tone for a decision. The menacing tone is reserved for a
 * future verified-threat signal; today every answer is calm.
 */
export function chooseTone(_decision: AmbientDecision): KokoroTone {
  return "calm";
}

const transcribe = mlxWhisperTranscriber({
  python: PYTHON,
  helper: path.join(
    ROOT,
    "apps/voice-runtime/scripts/transcribe_mlx_whisper.py",
  ),
  model: "mlx-community/whisper-base-mlx",
  initialPrompt: process.env.TALOS_AMBIENT_NAME ?? "Talos",
});

const speak = kokoroSynthesizer({
  python: PYTHON,
  helper: path.join(ROOT, "apps/voice-runtime/scripts/synthesize_kokoro.py"),
  model: path.join(ROOT, "apps/voice-runtime/.kokoro/kokoro-v1.0.int8.onnx"),
  voices: path.join(ROOT, "apps/voice-runtime/.kokoro/voices-v1.0.bin"),
});

async function decide(
  transcript: string,
  history: readonly string[],
  presence?: Presence,
): Promise<AmbientDecision> {
  const response = await fetch(`${OLLAMA.replace(/\/+$/, "")}/api/chat`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      model: MODEL,
      stream: false,
      think: false,
      format: "json",
      messages: [
        {
          role: "system",
          content:
            'You are Talos, a local voice assistant. Decide whether the latest speech is addressed to you (yes), is the user talking to someone else (no), or is not directed speech (other). The name Talos addressed to you means yes. If addressed, reply briefly in the speaker\'s language. Return one JSON object: {"addressed":"yes|no|other","reply":"..."}.',
        },
        {
          role: "user",
          content: `${history.length ? `Recent turns:\n${history.join("\n")}\n\n` : ""}${
            presence
              ? `Camera: ${presence.people} ${presence.people === 1 ? "person" : "people"} in view.\n\n`
              : ""
          }Latest speech: ${transcript}`,
        },
      ],
      options: { temperature: 0.2, num_predict: 300 },
    }),
  });
  if (!response.ok)
    throw new Error(`The ambient model answered ${response.status}.`);
  const body = (await response.json()) as { message?: { content?: unknown } };
  const content =
    typeof body.message?.content === "string" ? body.message.content : "";
  return parseAmbientDecision(JSON.parse(content));
}

async function speakReply(text: string, tone: KokoroTone): Promise<void> {
  const clip = await speak(text, tone);
  const folder = await mkdtemp(path.join(os.tmpdir(), "talos-ambient-"));
  const file = path.join(folder, "reply.wav");
  try {
    await writeFile(file, clip.wav);
    await new Promise<void>((resolve, reject) => {
      const player = spawn("afplay", [file], { stdio: "ignore" });
      player.once("exit", (code) =>
        code === 0 ? resolve() : reject(new Error(`afplay exited ${code}`)),
      );
      player.once("error", reject);
    });
  } finally {
    await rm(folder, { recursive: true, force: true });
  }
}

async function listenOnce(): Promise<{
  transcript: string;
  frameBase64: string;
}> {
  const folder = await mkdtemp(path.join(os.tmpdir(), "talos-ambient-listen-"));
  try {
    const capture = new TalosMediaCapture({
      source: path.join(ROOT, "apps/desktop/resources/talos/av_capture.swift"),
      runtimeDir: folder,
      compiler: "swiftc",
    });
    const receipt = await capture.capture(true, LISTEN_SECONDS);
    const raw = path.join(folder, "raw.wav");
    const converted = path.join(folder, "16k.wav");
    await writeFile(raw, Buffer.from(receipt.audioBase64, "base64"));
    await exec("/usr/bin/afconvert", [
      "-f",
      "WAVE",
      "-d",
      "LEI16@16000",
      "-c",
      "1",
      raw,
      converted,
    ]);
    const pcm = pcmFromWAV(await readFile(converted));
    return {
      transcript: await transcribe(pcm),
      frameBase64: receipt.frameBase64,
    };
  } finally {
    await rm(folder, { recursive: true, force: true });
  }
}

function perception(): TalosPerception {
  return new TalosPerception({
    sourceDir: path.join(ROOT, "apps/desktop/resources/talos"),
    runtimeDir: path.join(os.tmpdir(), "talos-ambient-perception"),
    compiler: "swiftc",
  });
}

export async function runAmbient(once = false): Promise<void> {
  // Warm the capture helper so the first listen does not miss speech.
  const warm = await mkdtemp(path.join(os.tmpdir(), "talos-ambient-warm-"));
  try {
    await new TalosMediaCapture({
      source: path.join(ROOT, "apps/desktop/resources/talos/av_capture.swift"),
      runtimeDir: warm,
      compiler: "swiftc",
    }).capture(true, 0.1);
  } finally {
    await rm(warm, { recursive: true, force: true });
  }
  const history: string[] = [];
  const vision =
    process.env.TALOS_AMBIENT_VISION === "1" ? perception() : undefined;
  do {
    console.log(`[ambient] listening for ${LISTEN_SECONDS}s…`);
    const heard = await listenOnce();
    const transcript = heard.transcript.trim();
    let presence: Presence | undefined;
    if (vision && heard.frameBase64) {
      try {
        const scene = await classifySceneBase64(vision, heard.frameBase64);
        presence = scene;
        console.log(
          `[ambient] presence: ${scene.people} ${scene.people === 1 ? "person" : "people"}, ${scene.faces} ${scene.faces === 1 ? "face" : "faces"}`,
        );
      } catch (error) {
        console.log(`[ambient] presence unavailable: ${String(error)}`);
      }
    }
    if (!transcript) {
      console.log("[ambient] heard nothing");
      continue;
    }
    console.log(`[ambient] heard: ${transcript}`);
    const decision = await decide(transcript, history.slice(-6), presence);
    console.log(`[ambient] addressed=${decision.addressed}`);
    if (decision.addressed === "yes" && decision.reply) {
      const tone = chooseTone(decision);
      console.log(`[ambient] reply (${tone}): ${decision.reply}`);
      await speakReply(decision.reply, tone);
      history.push(`Them: ${transcript}`, `Talos: ${decision.reply}`);
    } else {
      history.push(`Them: ${transcript} (not addressed)`);
    }
  } while (!once);
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === path.resolve(import.meta.filename)
) {
  runAmbient(process.argv.includes("--once")).catch((error) => {
    console.error("[ambient] failed:", error);
    process.exit(1);
  });
}
