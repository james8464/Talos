import { readdir } from "node:fs/promises";
import os from "node:os";
import { describe, expect, it, vi } from "vitest";

import {
  classifySceneBase64,
  classifySoundBase64,
} from "./talos-perception-endpoint.js";

const JPEG = Buffer.from([0xff, 0xd8, 0xff, 0x00]).toString("base64");
const WAV = Buffer.concat([
  Buffer.from("RIFF"),
  Buffer.alloc(4),
  Buffer.from("WAVE"),
]).toString("base64");

describe("perception endpoints", () => {
  it("classifies an image and removes the temporary file", async () => {
    const scene = vi
      .fn()
      .mockResolvedValue({ people: 1, faces: 1, empty: false });
    await expect(classifySceneBase64({ scene }, JPEG)).resolves.toMatchObject({
      people: 1,
    });
    expect(scene).toHaveBeenCalledOnce();
    expect(scene.mock.calls[0]![0]).toContain("talos-scene-");
  });

  it("classifies WAV audio", async () => {
    const sound = vi
      .fn()
      .mockResolvedValue([{ label: "Beep", confidence: 0.8 }]);
    await expect(classifySoundBase64({ sound }, WAV)).resolves.toHaveLength(1);
    expect(sound).toHaveBeenCalledOnce();
  });

  it("rejects wrong media before classification", async () => {
    const scene = vi.fn();
    const sound = vi.fn();
    await expect(
      classifySceneBase64({ scene }, Buffer.from("not").toString("base64")),
    ).rejects.toThrow("JPEG or PNG");
    await expect(classifySoundBase64({ sound }, JPEG)).rejects.toThrow("WAV");
    expect(scene).not.toHaveBeenCalled();
    expect(sound).not.toHaveBeenCalled();
  });

  it("leaves no temporary folders after failures", async () => {
    const sound = vi.fn().mockRejectedValue(new Error("boom"));
    await expect(classifySoundBase64({ sound }, WAV)).rejects.toThrow("boom");
    const leftovers = (await readdir(os.tmpdir())).filter((name) =>
      name.startsWith("talos-sound-"),
    );
    expect(leftovers).toEqual([]);
  });
});
