import { execFile as execFileCallback } from "node:child_process";
import { mkdir, stat } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";

const execFile = promisify(execFileCallback);

export interface SceneResult {
  readonly people: number;
  readonly faces: number;
  readonly empty: boolean;
  readonly window?: number;
}

export interface SoundEvent {
  readonly label: string;
  readonly confidence: number;
}

export interface PerceptionOptions {
  /** Directory holding vision_scene.swift and sound_classify.swift. */
  readonly sourceDir: string;
  /** Private directory for compiled helpers. */
  readonly runtimeDir: string;
  readonly compiler?: string;
  readonly sceneBinary?: string;
  readonly soundBinary?: string;
  readonly runner?: (
    binary: string,
    args: readonly string[],
  ) => Promise<{ stdout: string; stderr: string }>;
}

const defaultRunner = async (binary: string, args: readonly string[]) => {
  const { stdout, stderr } = await execFile(binary, [...args], {
    timeout: 120_000,
    windowsHide: true,
  });
  return { stdout, stderr };
};

/** File-based Vision and SoundAnalysis classification, ported from Talos. */
export class TalosPerception {
  readonly #options: PerceptionOptions;

  constructor(options: PerceptionOptions) {
    this.#options = options;
  }

  async #binary(name: "vision_scene" | "sound_classify", override?: string) {
    if (override) return override;
    const compiler = this.#options.compiler;
    if (!compiler) throw new Error("The native compiler is unavailable.");
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

  async scene(file: string): Promise<SceneResult> {
    const binary = await this.#binary(
      "vision_scene",
      this.#options.sceneBinary,
    );
    const { stdout } = await (this.#options.runner ?? defaultRunner)(binary, [
      file,
    ]);
    let parsed: unknown;
    try {
      parsed = JSON.parse(stdout);
    } catch {
      throw new Error("Vision returned invalid JSON.");
    }
    if (!parsed || typeof parsed !== "object")
      throw new Error("Vision returned invalid JSON.");
    const result = parsed as Record<string, unknown>;
    if (
      typeof result.people !== "number" ||
      typeof result.faces !== "number" ||
      typeof result.empty !== "boolean"
    )
      throw new Error("Vision returned an invalid scene.");
    return result as unknown as SceneResult;
  }

  async sound(file: string): Promise<SoundEvent[]> {
    const binary = await this.#binary(
      "sound_classify",
      this.#options.soundBinary,
    );
    const { stdout } = await (this.#options.runner ?? defaultRunner)(binary, [
      file,
    ]);
    let parsed: unknown;
    try {
      parsed = JSON.parse(stdout);
    } catch {
      throw new Error("SoundAnalysis returned invalid JSON.");
    }
    if (!Array.isArray(parsed))
      throw new Error("SoundAnalysis returned invalid events.");
    return parsed.map((item) => {
      if (
        !item ||
        typeof item !== "object" ||
        typeof (item as SoundEvent).label !== "string" ||
        typeof (item as SoundEvent).confidence !== "number"
      )
        throw new Error("SoundAnalysis returned an invalid event.");
      return item as SoundEvent;
    });
  }
}
