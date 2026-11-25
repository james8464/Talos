import { execFile as execFileCallback } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, stat } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";

const execFile = promisify(execFileCallback);

export type CaptureRunner = (
  binary: string,
  args: readonly string[],
) => Promise<{ stdout: string; stderr: string }>;

export interface TalosMediaCaptureOptions {
  /** Absolute path to av_capture.swift. */
  readonly source: string;
  /** Private directory for the compiled helper and temporary media. */
  readonly runtimeDir: string;
  /** Test seam: skip compilation and use this helper. */
  readonly binary?: string;
  readonly compiler?: string;
  readonly runner?: CaptureRunner;
}

export interface TalosCaptureReceipt {
  readonly frameBase64: string;
  readonly audioBase64: string;
  readonly seconds: number;
  readonly cameraPermission: string;
  readonly microphonePermission: string;
  /** Temporary media is always removed before this resolves. */
  readonly mediaDeleted: true;
}

const JPEG = Buffer.from([0xff, 0xd8, 0xff]);
const WAV = Buffer.from("RIFF");

const defaultRunner: CaptureRunner = async (binary, args) => {
  const { stdout, stderr } = await execFile(binary, [...args], {
    timeout: 180_000,
    windowsHide: true,
  });
  return { stdout, stderr };
};

/**
 * Capture one frame and one short clip without an application-level consent
 * prompt. macOS still applies its own camera/microphone authorization.
 */
export class TalosMediaCapture {
  readonly #options: TalosMediaCaptureOptions;

  constructor(options: TalosMediaCaptureOptions) {
    this.#options = options;
  }

  available(): boolean {
    return (
      this.#options.binary !== undefined || this.#options.compiler !== undefined
    );
  }

  async #helper(): Promise<string> {
    if (this.#options.binary) return this.#options.binary;
    const compiler = this.#options.compiler;
    if (!compiler)
      throw new Error("The native capture compiler is unavailable.");
    await mkdir(this.#options.runtimeDir, { recursive: true, mode: 0o700 });
    const target = path.join(this.#options.runtimeDir, "talos-av-capture");
    const [sourceStat, targetStat] = await Promise.all([
      stat(this.#options.source).catch(() => undefined),
      stat(target).catch(() => undefined),
    ]);
    if (sourceStat && targetStat && targetStat.mtimeMs >= sourceStat.mtimeMs)
      return target;
    await execFile(compiler, ["-O", this.#options.source, "-o", target], {
      timeout: 180_000,
      windowsHide: true,
    });
    return target;
  }

  async capture(seconds = 1): Promise<TalosCaptureReceipt> {
    if (!(seconds > 0 && seconds <= 10))
      throw new Error("Capture length must be between 0 and 10 seconds.");
    const helper = await this.#helper();
    const folder = await mkdtemp(
      path.join(this.#options.runtimeDir, "capture-"),
    );
    const frame = path.join(folder, "frame.jpg");
    const clip = path.join(folder, "clip.wav");
    try {
      const run = this.#options.runner ?? defaultRunner;
      const { stdout } = await run(helper, [frame, clip, seconds.toFixed(2)]);
      const payload = JSON.parse(stdout || "{}") as Record<string, unknown>;
      if (payload.camera !== true || payload.audio !== true)
        throw new Error("Capture helper did not report both streams.");
      const frameBytes = await readFile(frame);
      const audioBytes = await readFile(clip);
      if (!frameBytes.subarray(0, 3).equals(JPEG))
        throw new Error("Capture helper returned no JPEG frame.");
      if (!audioBytes.subarray(0, 4).equals(WAV))
        throw new Error("Capture helper returned no WAV clip.");
      return {
        frameBase64: frameBytes.toString("base64"),
        audioBase64: audioBytes.toString("base64"),
        seconds,
        cameraPermission: String(payload.camera_permission ?? "unknown"),
        microphonePermission: String(
          payload.microphone_permission ?? "unknown",
        ),
        mediaDeleted: true,
      };
    } finally {
      await rm(folder, { recursive: true, force: true });
    }
  }
}
