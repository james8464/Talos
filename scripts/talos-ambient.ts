/**
 * Experimental ambient Talos loop: listen, transcribe locally, decide whether
 * the speech is addressed to Talos, and answer with the local Kokoro voice.
 * It is the first slice of the always-on assistant; camera presence and
 * computer actions are not wired yet.
 *
 * Run: pnpm talos:ambient [--once]
 */
import { spawn } from "node:child_process";
import { mkdir, mkdtemp, rm, stat, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { execFile } from "node:child_process";

import { isMuted } from "../packages/service-kit/src/index.js";

import { TalosMediaCapture } from "../apps/desktop/src/main/talos-media-capture.js";
import { TalosPerception } from "../apps/desktop/src/main/talos-perception.js";
import { classifySceneBase64 } from "../apps/desktop/src/main/talos-perception-endpoint.js";
import {
  kokoroSynthesizer,
  type KokoroTone,
} from "../apps/voice-runtime/src/local-kokoro.js";
import { mlxWhisperTranscriber } from "../apps/voice-runtime/src/local-whisper.js";
import { UtteranceGate } from "./ambient-gate.js";
import { SileroSpeechActivityDetector } from "../apps/voice-runtime/src/voice-activity.js";

const exec = promisify(execFile);
const ROOT = path.resolve(import.meta.dirname, "..");
const OLLAMA = process.env.TALOS_OLLAMA_URL ?? "http://127.0.0.1:11434";
const DECISION_MODEL =
  process.env.TALOS_AMBIENT_DECISION_MODEL ?? "qwen3:4b-8k";
const REPLY_MODEL = process.env.TALOS_AMBIENT_REPLY_MODEL ?? "qwen3:8b-16k";
const CUA = path.join(
  ROOT,
  "apps/computer/dist/bundle/cli/computer-tools/cua/cua-driver",
);
const PYTHON = path.join(ROOT, "apps/voice-runtime/.venv/bin/python");

export type Addressed = "yes" | "no" | "other";

export interface AddressDecision {
  readonly addressed: Addressed;
  /** The model's own choice of delivery tone. */
  readonly tone: KokoroTone;
}

export interface Presence {
  readonly people: number;
  readonly faces: number;
  readonly empty: boolean;
}

export interface AmbientAction {
  readonly tool: "launch_app" | "get_accessibility_tree";
  readonly arguments: Record<string, unknown>;
}

/** Validates a model-chosen computer action; anything else is ignored. */
export function parseAmbientAction(value: unknown): AmbientAction | null {
  if (typeof value !== "object" || value === null) return null;
  const action = (value as { action?: unknown }).action;
  if (typeof action !== "object" || action === null) return null;
  const record = action as Record<string, unknown>;
  const tool = record.tool;
  if (tool !== "launch_app" && tool !== "get_accessibility_tree") return null;
  const args =
    typeof record.arguments === "object" && record.arguments !== null
      ? (record.arguments as Record<string, unknown>)
      : {};
  return { tool, arguments: args };
}

/** Validates the local model's one-object decision. */
export function parseAddressDecision(value: unknown): AddressDecision {
  if (typeof value !== "object" || value === null)
    throw new Error("The ambient model returned no object.");
  const record = value as Record<string, unknown>;
  if (
    record.addressed !== "yes" &&
    record.addressed !== "no" &&
    record.addressed !== "other"
  )
    throw new Error("The ambient model returned no addressed value.");
  const tone =
    record.tone === "alert" || record.tone === "menacing"
      ? record.tone
      : "calm";
  return { addressed: record.addressed, tone };
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

async function classify(
  transcript: string,
  presence?: Presence,
): Promise<AddressDecision> {
  const response = await fetch(`${OLLAMA.replace(/\/+$/, "")}/api/chat`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      model: DECISION_MODEL,
      stream: false,
      think: false,
      format: "json",
      messages: [
        {
          role: "system",
          content:
            'You decide whether speech is addressed to Talos. The name Talos means yes. Talking to another person is no; background speech is other. Also choose the delivery tone: calm normally, alert when something needs attention, menacing only when the user faces a genuine immediate threat. Return one JSON object: {"addressed":"yes|no|other","tone":"calm|alert|menacing"}.',
        },
        {
          role: "user",
          content: `${
            presence
              ? `Camera: ${presence.people} ${presence.people === 1 ? "person" : "people"} in view.\n`
              : ""
          }Speech: ${transcript}`,
        },
      ],
      keep_alive: "30m",
      options: { temperature: 0, num_predict: 60 },
    }),
  });
  if (!response.ok)
    throw new Error(`The ambient model answered ${response.status}.`);
  const body = (await response.json()) as { message?: { content?: unknown } };
  const content =
    typeof body.message?.content === "string" ? body.message.content : "";
  return parseAddressDecision(JSON.parse(content));
}

async function chooseAction(transcript: string): Promise<AmbientAction | null> {
  const response = await fetch(`${OLLAMA.replace(/\/+$/, "")}/api/chat`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      model: DECISION_MODEL,
      stream: false,
      think: false,
      format: "json",
      messages: [
        {
          role: "system",
          content:
            'If the user explicitly asks you to do something on this Mac, choose one computer tool. Tools: launch_app {name: "TextEdit"} launches an installed app in the background; get_accessibility_tree {} lists running apps and visible windows. Only these two exist. If no action is requested, return {"action":null}. Return one JSON object: {"action":{"tool":"launch_app","arguments":{"name":"..."}}}.',
        },
        { role: "user", content: `Speech: ${transcript}` },
      ],
      keep_alive: "30m",
      options: { temperature: 0, num_predict: 100 },
    }),
  });
  if (!response.ok) return null;
  const body = (await response.json()) as { message?: { content?: unknown } };
  const content =
    typeof body.message?.content === "string" ? body.message.content : "";
  try {
    return parseAmbientAction(JSON.parse(content));
  } catch {
    return null;
  }
}

async function runAction(action: AmbientAction): Promise<string> {
  const { stdout } = await exec(
    CUA,
    ["call", action.tool, JSON.stringify(action.arguments)],
    { timeout: 120_000, maxBuffer: 8 * 1024 * 1024 },
  );
  return stdout.trim().slice(0, 2_000);
}

const SENTENCE = /^[\s\S]*?[.!?](?=\s|$)/;

/**
 * Streams the 8B reply and speaks each finished sentence while the rest is
 * still generating, which cuts the wait to first audio.
 */
async function replyAndSpeak(
  transcript: string,
  history: readonly string[],
  presence: Presence | undefined,
  tone: KokoroTone,
  actionNote?: string,
): Promise<string> {
  const response = await fetch(`${OLLAMA.replace(/\/+$/, "")}/api/chat`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      model: REPLY_MODEL,
      stream: true,
      think: false,
      messages: [
        {
          role: "system",
          content: `You are Talos, the user's own local AI on this Mac. You are knowledgeable, direct and warm. Answer from your own knowledge; never claim you cannot answer something you know. The current date and time is ${new Date().toString()}. The user is speaking to you, so reply as natural spoken conversation in their language, briefly. ${
            presence
              ? `${presence.people} ${presence.people === 1 ? "person is" : "people are"} visible on the camera. `
              : ""
          }You may be firm and protective for a genuine threat, but never generate threats, harassment or abuse toward anyone.${
            actionNote ? ` A computer action just ran: ${actionNote}` : ""
          }`,
        },
        {
          role: "user",
          content: `${history.length ? `Recent conversation:\n${history.join("\n")}\n\n` : ""}The user said: ${transcript}`,
        },
      ],
      keep_alive: "30m",
      options: { temperature: 0.6, num_predict: 400 },
    }),
  });
  if (!response.ok || !response.body)
    throw new Error(`The reply model answered ${response.status}.`);
  let buffered = "";
  let spoken = "";
  let full = "";
  const flushSentence = async () => {
    const match = SENTENCE.exec(buffered);
    if (!match) return;
    const sentence = match[0];
    buffered = buffered.slice(sentence.length);
    const text = sentence.trim();
    if (text.length < 2) return;
    spoken += `${text} `;
    await speakReply(text, tone);
  };
  const decoder = new TextDecoder();
  let carry = "";
  for await (const chunk of response.body as unknown as AsyncIterable<Uint8Array>) {
    carry += decoder.decode(chunk, { stream: true });
    let newline = carry.indexOf("\n");
    while (newline >= 0) {
      const line = carry.slice(0, newline).trim();
      carry = carry.slice(newline + 1);
      newline = carry.indexOf("\n");
      if (!line) continue;
      const event = JSON.parse(line) as {
        message?: { content?: unknown };
        done?: boolean;
      };
      const delta =
        typeof event.message?.content === "string" ? event.message.content : "";
      if (delta) {
        buffered += delta;
        full += delta;
        await flushSentence();
      }
    }
  }
  const rest = buffered.trim();
  if (rest) {
    spoken += `${rest} `;
    await speakReply(rest, tone);
  }
  return full.trim() || spoken.trim();
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

const FRAME_SAMPLES = 512;

/** Compiles the streaming microphone helper once per machine temp dir. */
async function streamBinary(): Promise<string> {
  const folder = path.join(os.tmpdir(), "talos-ambient-bin");
  const binary = path.join(folder, "av_stream");
  const source = path.join(
    ROOT,
    "apps/desktop/resources/talos/av_stream.swift",
  );
  await mkdir(folder, { recursive: true });
  const fresh = await stat(binary).catch(() => undefined);
  const sourceInfo = await stat(source);
  if (!fresh || fresh.mtimeMs < sourceInfo.mtimeMs)
    await exec("swiftc", ["-O", source, "-o", binary], { timeout: 180_000 });
  return binary;
}

function perception(): TalosPerception {
  return new TalosPerception({
    sourceDir: path.join(ROOT, "apps/desktop/resources/talos"),
    runtimeDir: path.join(os.tmpdir(), "talos-ambient-perception"),
    compiler: "swiftc",
  });
}

async function presenceOf(
  vision: TalosPerception | undefined,
): Promise<Presence | undefined> {
  if (!vision) return undefined;
  const folder = await mkdtemp(path.join(os.tmpdir(), "talos-ambient-frame-"));
  try {
    const receipt = await new TalosMediaCapture({
      source: path.join(ROOT, "apps/desktop/resources/talos/av_capture.swift"),
      runtimeDir: folder,
      compiler: "swiftc",
    }).capture(true, 0.1);
    const scene = await classifySceneBase64(vision, receipt.frameBase64);
    console.log(
      `[ambient] presence: ${scene.people} ${scene.people === 1 ? "person" : "people"}, ${scene.faces} ${scene.faces === 1 ? "face" : "faces"}`,
    );
    return scene;
  } catch (error) {
    console.log(`[ambient] presence unavailable: ${String(error)}`);
    return undefined;
  } finally {
    await rm(folder, { recursive: true, force: true });
  }
}

export async function runAmbient(once = false): Promise<void> {
  if (isMuted(process.env)) {
    console.log(
      "[ambient] TALOS_MUTE is set; staying silent and not listening.",
    );
    return;
  }
  const binary = await streamBinary();
  const stream = spawn(binary, [], { stdio: ["ignore", "pipe", "pipe"] });
  stream.stderr.on("data", (chunk) =>
    process.stderr.write(`[ambient:stream] ${chunk}`),
  );
  const vision =
    process.env.TALOS_AMBIENT_VISION === "1" ? perception() : undefined;
  const vad = await SileroSpeechActivityDetector.openPrewarmed();
  const history: string[] = [];
  let gate = new UtteranceGate({ silenceMs: 800, minSpeechMs: 250 });
  let pending = Buffer.alloc(0);
  let preroll: Buffer[] = [];
  let utterance: Buffer[] = [];
  let collecting = false;
  let busy = false;
  let discardUntil = 0;
  let handled = 0;
  const debug = process.env.TALOS_AMBIENT_DEBUG_RMS === "1";
  console.log("[ambient] listening continuously from the open microphone…");

  const handle = async (pcm: Buffer) => {
    busy = true;
    try {
      console.log(
        `[ambient] utterance ${(pcm.length / 2 / 16_000).toFixed(2)}s`,
      );
      const started = Date.now();
      const transcript = (await transcribe(pcm)).trim();
      console.log(`[ambient] transcribed in ${Date.now() - started}ms`);
      if (!transcript) {
        console.log("[ambient] heard nothing intelligible");
        return;
      }
      const presence = await presenceOf(vision);
      console.log(`[ambient] heard: ${transcript}`);
      const decisionStarted = Date.now();
      const decision = await classify(transcript, presence);
      console.log(
        `[ambient] addressed=${decision.addressed} tone=${decision.tone} (${Date.now() - decisionStarted}ms)`,
      );
      if (decision.addressed === "yes") {
        let actionNote: string | undefined;
        const action = await chooseAction(transcript);
        if (action) {
          console.log(
            `[ambient] action ${action.tool} ${JSON.stringify(action.arguments)}`,
          );
          try {
            const result = await runAction(action);
            const launched = /"pid"\s*:\s*(\d+)/.exec(result)?.[1];
            actionNote = `${action.tool} succeeded${launched ? ` (pid ${launched})` : ""}.`;
            if (action.tool === "get_accessibility_tree")
              actionNote += ` ${result.slice(0, 300)}`;
            console.log(`[ambient] action result: ${result.slice(0, 200)}`);
          } catch (error) {
            actionNote = `${action.tool} failed: ${(error as Error).message}`;
            console.log(
              `[ambient] action failed: ${String(error).slice(0, 200)}`,
            );
          }
        }
        const replyStarted = Date.now();
        const reply = await replyAndSpeak(
          transcript,
          history.slice(-8),
          presence,
          decision.tone,
          actionNote,
        );
        console.log(
          `[ambient] reply (${decision.tone}, ${Date.now() - replyStarted}ms): ${reply}`,
        );
        history.push(`Them: ${transcript}`, `Talos: ${reply}`);
        if (history.length > 16) history.splice(0, history.length - 16);
      } else {
        history.push(`Them: ${transcript} (not addressed)`);
      }
      handled += 1;
    } finally {
      busy = false;
      discardUntil = Date.now() + 700;
      gate = new UtteranceGate({ silenceMs: 800, minSpeechMs: 250 });
      collecting = false;
      utterance = [];
      preroll = [];
    }
  };

  for await (const chunk of stream.stdout) {
    pending = Buffer.concat([pending, chunk as Buffer]);
    while (pending.length >= FRAME_SAMPLES * 2) {
      const frame = pending.subarray(0, FRAME_SAMPLES * 2);
      pending = pending.subarray(FRAME_SAMPLES * 2);
      if (busy || Date.now() < discardUntil) continue;
      const samples = new Int16Array(
        frame.buffer,
        frame.byteOffset,
        frame.length / 2,
      );
      const activity = await vad.pushPCM(samples);
      const speech = activity.some((window) => window.speech);
      if (debug)
        console.log(
          `[ambient] frame speech=${speech} level=${activity[0]?.levelDb?.toFixed(1) ?? "?"}dB`,
        );
      const event = gate.push(speech ? 1 : 0, samples.length);
      if (event === "start") {
        collecting = true;
        utterance = [...preroll, frame];
        console.log("[ambient] speech detected");
      } else if (collecting) {
        utterance.push(frame);
      } else {
        preroll.push(frame);
        if (preroll.length > 8) preroll.shift();
      }
      if (event !== "utterance") continue;
      const pcm = Buffer.concat(utterance);
      utterance = [];
      preroll = [];
      collecting = false;
      await handle(pcm);
      if (once && handled > 0) {
        stream.kill("SIGTERM");
        return;
      }
    }
  }
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
