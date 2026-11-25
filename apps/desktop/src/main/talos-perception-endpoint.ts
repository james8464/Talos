import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import type {
  SceneResult,
  SoundEvent,
  TalosPerception,
} from "./talos-perception.js";

const JPEG = Buffer.from([0xff, 0xd8, 0xff]);
const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47]);
const WAV = Buffer.from("RIFF");

/** Classify a base64 image through a temporary file that is always removed. */
export async function classifySceneBase64(
  perception: Pick<TalosPerception, "scene">,
  image: string,
): Promise<SceneResult> {
  const bytes = decode(image, "image");
  if (!bytes.subarray(0, 3).equals(JPEG) && !bytes.subarray(0, 4).equals(PNG))
    throw new Error("Send a JPEG or PNG image.");
  const folder = await mkdtemp(path.join(os.tmpdir(), "talos-scene-"));
  const file = path.join(folder, "frame");
  try {
    await writeFile(file, bytes, { mode: 0o600 });
    return await perception.scene(file);
  } finally {
    await rm(folder, { recursive: true, force: true });
  }
}

/** Classify a base64 WAV through a temporary file that is always removed. */
export async function classifySoundBase64(
  perception: Pick<TalosPerception, "sound">,
  audio: string,
): Promise<SoundEvent[]> {
  const bytes = decode(audio, "audio");
  if (
    !bytes.subarray(0, 4).equals(WAV) ||
    bytes.subarray(8, 12).toString() !== "WAVE"
  )
    throw new Error("Send WAV audio.");
  const folder = await mkdtemp(path.join(os.tmpdir(), "talos-sound-"));
  const file = path.join(folder, "clip.wav");
  try {
    await writeFile(file, bytes, { mode: 0o600 });
    return await perception.sound(file);
  } finally {
    await rm(folder, { recursive: true, force: true });
  }
}

function decode(value: string, kind: string): Buffer {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > 5_500_000
  )
    throw new Error(`Provide base64 ${kind} under 4 MB.`);
  try {
    return Buffer.from(value, "base64");
  } catch {
    throw new Error(`The ${kind} is not valid base64.`);
  }
}
