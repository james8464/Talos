import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { serviceCredential } from "@talos/service-kit/node";
import { hasBearerToken } from "../service-bearer-auth.js";
import type { HttpHost } from "../http-host.js";
import type { JsonLogger } from "@talos/service-kit/node";
import { NativePerception } from "./native.js";
import { OllamaVision, type VisionModel } from "./vision-model.js";

const PREFIXES = [
  "/talos/vision/scene",
  "/talos/vision/describe",
  "/talos/sound",
] as const;
const JPEG = [0xff, 0xd8, 0xff];
const PNG = [0x89, 0x50, 0x4e, 0x47];

export function describeScene(scene: {
  people: number;
  faces: number;
  empty: boolean;
}): string {
  if (scene.empty) return "The scene looks empty.";
  if (scene.people === 1) return "I see one person.";
  return `I see ${scene.people} people.`;
}

export function describeSound(events: readonly { label: string }[]): string {
  return events.length === 0
    ? "I couldn't identify a distinct sound in that clip."
    : `I heard ${events.map((event) => event.label).join(", ")}.`;
}

interface PerceptionClient {
  scene(
    file: string,
  ): Promise<{ people: number; faces: number; empty: boolean }>;
  sound(file: string): Promise<Array<{ label: string; confidence: number }>>;
}

/** HTTP routes for file-based Vision and SoundAnalysis classification. */
export async function startPerceptionService(options: {
  http: HttpHost;
  logger: JsonLogger;
  environment?: NodeJS.ProcessEnv;
  client?: PerceptionClient;
  vision?: VisionModel;
}): Promise<{ stop(): Promise<void> }> {
  const env = options.environment ?? process.env;
  const token = serviceCredential("node-services-internal", env);
  const vision =
    options.vision ??
    new OllamaVision({
      baseUrl: env.TALOS_OLLAMA_URL ?? "http://127.0.0.1:11434",
      model: env.TALOS_VISION_MODEL ?? "qwen2.5vl:3b",
    });
  const client =
    options.client ??
    new NativePerception({
      sourceDir:
        env.TALOS_NATIVE_HELPERS_DIR ??
        path.resolve(process.cwd(), "apps/desktop/resources/talos"),
      runtimeDir: path.resolve(process.cwd(), ".talos-runtime/perception"),
    });
  const unmount = options.http.mount({
    prefixes: [...PREFIXES],
    fetch: async (request) => {
      const url = new URL(request.url);
      if (
        !token ||
        !hasBearerToken(
          request.headers.get("authorization") ?? undefined,
          token,
        )
      )
        return Response.json(
          { error: "A service token is required." },
          { status: 401 },
        );
      if (request.method !== "POST")
        return new Response(null, { status: 405, headers: { allow: "POST" } });
      try {
        const body = (await request.json()) as Record<string, unknown>;
        if (url.pathname === "/talos/vision/describe") {
          const bytes = decode(body.image, "image");
          if (!startsWith(bytes, JPEG) && !startsWith(bytes, PNG))
            throw new Error("Send a JPEG or PNG image.");
          const question =
            typeof body.question === "string" && body.question.trim() !== ""
              ? body.question.trim().slice(0, 500)
              : "What do you see?";
          try {
            const described = await vision.describe(
              bytes.toString("base64"),
              question,
            );
            return Response.json(described);
          } catch (error) {
            return Response.json(
              {
                error:
                  error instanceof Error
                    ? error.message
                    : "The local vision model is unavailable.",
              },
              { status: 503 },
            );
          }
        }
        if (url.pathname === "/talos/vision/scene") {
          const bytes = decode(body.image, "image");
          if (!startsWith(bytes, JPEG) && !startsWith(bytes, PNG))
            throw new Error("Send a JPEG or PNG image.");
          const scene = await withTemp(bytes, "jpg", (file) =>
            client.scene(file),
          );
          return Response.json({ scene, description: describeScene(scene) });
        }
        const bytes = decode(body.audio, "audio");
        if (
          startsWith(bytes, [0x52, 0x49, 0x46, 0x46]) === false ||
          bytes.subarray(8, 12).toString() !== "WAVE"
        )
          throw new Error("Send WAV audio.");
        const events = await withTemp(bytes, "wav", (file) =>
          client.sound(file),
        );
        return Response.json({ events, description: describeSound(events) });
      } catch (error) {
        return Response.json(
          {
            error: error instanceof Error ? error.message : "Invalid request.",
          },
          { status: 400 },
        );
      }
    },
  });
  options.logger.log("info", "perception.listening", { prefixes: PREFIXES });
  return { stop: async () => unmount() };
}

function decode(value: unknown, kind: string): Buffer {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > 5_500_000
  )
    throw new Error(`Provide base64 ${kind} under 4 MB.`);
  const bytes = Buffer.from(value, "base64");
  if (bytes.length === 0) throw new Error(`The ${kind} is not valid base64.`);
  return bytes;
}

function startsWith(bytes: Buffer, magic: readonly number[]): boolean {
  return magic.every((value, index) => bytes[index] === value);
}

async function withTemp<T>(
  bytes: Buffer,
  suffix: string,
  run: (file: string) => Promise<T>,
): Promise<T> {
  const folder = await mkdtemp(path.join(os.tmpdir(), "talos-perception-"));
  const file = path.join(folder, `media.${suffix}`);
  try {
    await writeFile(file, bytes, { mode: 0o600 });
    return await run(file);
  } finally {
    await rm(folder, { recursive: true, force: true });
  }
}
