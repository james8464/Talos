import { execFile as execFileCallback } from "node:child_process";
import { mkdir, stat } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";

const execFile = promisify(execFileCallback);

export interface NativeScene {
  readonly people: number;
  readonly faces: number;
  readonly empty: boolean;
  readonly window?: number;
}

export interface NativeSoundEvent {
  readonly label: string;
  readonly confidence: number;
}

export interface NativePerceptionOptions {
  readonly sourceDir: string;
  readonly runtimeDir: string;
  readonly compiler?: string;
  readonly runner?: (
    binary: string,
    args: readonly string[],
  ) => Promise<{ stdout: string }>;
}

/** Vision and SoundAnalysis helpers shared with the desktop app. */
export class NativePerception {
  readonly #options: NativePerceptionOptions;

  constructor(options: NativePerceptionOptions) {
    this.#options = options;
  }

  async #binary(name: "vision_scene" | "sound_classify"): Promise<string> {
    const compiler = this.#options.compiler ?? "swiftc";
    await mkdir(this.#options.runtimeDir, { recursive: true, mode: 0o700 });
    const source = path.join(this.#options.sourceDir, `${name}.swift`);
    const target = path.join(this.#options.runtimeDir, name);
    const [sourceStat, targetStat] = await Promise.all([
      stat(source).catch(() => undefined),
      stat(target).catch(() => undefined),
    ]);
    if (sourceStat && targetStat && targetStat.mtimeMs >= sourceStat.mtimeMs)
      return target;
    await execFile(compiler, ["-O", source, "-o", target], {
      timeout: 180_000,
      windowsHide: true,
    });
    return target;
  }

  async scene(file: string): Promise<NativeScene> {
    const binary = await this.#binary("vision_scene");
    const stdout = await this.#run(binary, file);
    const parsed = parse(stdout, "Vision");
    const scene = parsed as Record<string, unknown>;
    if (
      typeof scene.people !== "number" ||
      typeof scene.faces !== "number" ||
      typeof scene.empty !== "boolean"
    )
      throw new Error("Vision returned an invalid scene.");
    return scene as unknown as NativeScene;
  }

  async sound(file: string): Promise<NativeSoundEvent[]> {
    const binary = await this.#binary("sound_classify");
    const stdout = await this.#run(binary, file);
    const parsed = parse(stdout, "SoundAnalysis");
    if (!Array.isArray(parsed))
      throw new Error("SoundAnalysis returned invalid events.");
    return parsed.map((item) => {
      const event = item as Record<string, unknown> | null;
      if (
        !event ||
        typeof event !== "object" ||
        typeof event.label !== "string" ||
        typeof event.confidence !== "number"
      )
        throw new Error("SoundAnalysis returned an invalid event.");
      return event as unknown as NativeSoundEvent;
    });
  }

  async #run(binary: string, file: string): Promise<string> {
    const runner =
      this.#options.runner ??
      (async (helper: string, args: readonly string[]) => {
        const { stdout } = await execFile(helper, [...args], {
          timeout: 120_000,
          windowsHide: true,
        });
        return { stdout };
      });
    return (await runner(binary, [file])).stdout;
  }
}

function parse(
  stdout: string,
  label: string,
): Record<string, unknown> | unknown[] {
  try {
    const parsed: unknown = JSON.parse(stdout);
    if (parsed && typeof parsed === "object")
      return parsed as Record<string, unknown>;
  } catch {
    // handled below
  }
  throw new Error(`${label} returned invalid JSON.`);
}
