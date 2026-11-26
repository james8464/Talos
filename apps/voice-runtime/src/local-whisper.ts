/**
 * Local MLX Whisper transcription for a short PCM clip. It buffers 16 kHz
 * mono frames, writes one temporary WAV, runs the Python helper and emits one
 * final transcript. No audio is played.
 */
import { execFile } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";

import type { ASREvent, ASRProvider, ASRStream } from "./asr.js";

const exec = promisify(execFile);
const SAMPLE_RATE = 16_000;
const MAX_SECONDS = 300;

export type WhisperTranscriber = (pcm: Buffer) => Promise<string>;

/** Builds a WAV container around 16 kHz mono s16le samples. */
/** Reads the PCM payload of a 16 kHz mono 16-bit WAV file, skipping any
 * other chunks such as the JUNK chunk `say` writes. */
export function pcmFromWAV(wav: Buffer): Buffer {
  if (
    wav.subarray(0, 4).toString() !== "RIFF" ||
    wav.subarray(8, 12).toString() !== "WAVE"
  )
    throw new Error("The clip is not a WAV file.");
  let format: { channels: number; rate: number; bits: number } | undefined;
  let offset = 12;
  while (offset + 8 <= wav.length) {
    const id = wav.subarray(offset, offset + 4).toString();
    const size = wav.readUInt32LE(offset + 4);
    const body = offset + 8;
    if (id === "fmt ") {
      format = {
        channels: wav.readUInt16LE(body + 2),
        rate: wav.readUInt32LE(body + 4),
        bits: wav.readUInt16LE(body + 14),
      };
    }
    if (id === "data") {
      if (!format) throw new Error("The WAV file has no format chunk.");
      if (
        format.channels !== 1 ||
        format.rate !== SAMPLE_RATE ||
        format.bits !== 16
      )
        throw new Error("The clip must be 16 kHz mono 16-bit audio.");
      return wav.subarray(body, Math.min(body + size, wav.length));
    }
    offset = body + size + (size % 2);
  }
  throw new Error("The WAV file has no data chunk.");
}

export function wavFromPCM(pcm: Buffer): Buffer {
  const header = Buffer.alloc(44);
  header.write("RIFF", 0);
  header.writeUInt32LE(36 + pcm.length, 4);
  header.write("WAVE", 8);
  header.write("fmt ", 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(SAMPLE_RATE, 24);
  header.writeUInt32LE(SAMPLE_RATE * 2, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write("data", 36);
  header.writeUInt32LE(pcm.length, 40);
  return Buffer.concat([header, pcm]);
}

/** Runs the MLX Whisper helper on one WAV file. */
export function mlxWhisperTranscriber(options: {
  python: string;
  helper: string;
  model: string;
  /** Biases recognition toward a name or vocabulary the speaker uses. */
  initialPrompt?: string;
  timeoutMs?: number;
}): WhisperTranscriber {
  return async (pcm) => {
    const folder = await mkdtemp(path.join(os.tmpdir(), "talos-whisper-"));
    const file = path.join(folder, "clip.wav");
    try {
      await writeFile(file, wavFromPCM(pcm), { mode: 0o600 });
      const { stdout } = await exec(
        options.python,
        [
          options.helper,
          file,
          options.model,
          ...(options.initialPrompt ? [options.initialPrompt] : []),
        ],
        { timeout: options.timeoutMs ?? 120_000, maxBuffer: 4 * 1024 * 1024 },
      );
      const parsed = JSON.parse(stdout) as { text?: unknown };
      return typeof parsed.text === "string" ? parsed.text.trim() : "";
    } finally {
      await rm(folder, { recursive: true, force: true });
    }
  };
}

export class LocalWhisperProvider implements ASRProvider {
  constructor(
    private readonly options: {
      readonly transcribe: WhisperTranscriber;
      readonly maxSeconds?: number;
    },
  ) {}

  async open(signal: AbortSignal): Promise<ASRStream> {
    return new LocalWhisperStream(
      this.options.transcribe,
      this.options.maxSeconds ?? MAX_SECONDS,
      signal,
    );
  }
}

class LocalWhisperStream implements ASRStream {
  private chunks: Buffer[] = [];
  private samples = 0;
  private pending: ASREvent[] = [];
  private finished = false;
  private waiter: (() => void) | undefined;

  constructor(
    private readonly transcribe: WhisperTranscriber,
    private readonly maxSeconds: number,
    private readonly signal: AbortSignal,
  ) {}

  async sendPCM(frame: Int16Array): Promise<void> {
    if (this.finished) return;
    this.chunks.push(
      Buffer.from(frame.buffer, frame.byteOffset, frame.byteLength),
    );
    this.samples += frame.length;
    if (this.samples > this.maxSeconds * SAMPLE_RATE)
      throw new Error("The clip is longer than the local Whisper limit.");
  }

  async finish(): Promise<void> {
    if (this.finished) return;
    const durationMs = Math.round((this.samples / SAMPLE_RATE) * 1000);
    try {
      const text = await this.transcribe(Buffer.concat(this.chunks));
      if (text) {
        this.pending.push({
          kind: "final",
          startOffsetMs: 0,
          endOffsetMs: durationMs,
          text,
        });
      }
    } finally {
      this.finished = true;
      this.waiter?.();
    }
  }

  async *events(): AsyncIterable<ASREvent> {
    while (!this.finished || this.pending.length > 0) {
      if (this.signal.aborted) return;
      const next = this.pending.shift();
      if (next) {
        yield next;
        continue;
      }
      await new Promise<void>((resolve) => {
        this.waiter = resolve;
      });
    }
  }

  async close(): Promise<void> {
    this.finished = true;
    this.waiter?.();
  }
}
