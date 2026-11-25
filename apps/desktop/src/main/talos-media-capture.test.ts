import { mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { TalosMediaCapture } from "./talos-media-capture.js";

const dirs: string[] = [];

afterEach(async () => {
  await Promise.all(
    dirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })),
  );
});

async function runtime(): Promise<string> {
  const dir = await mkdtemp(path.join(os.tmpdir(), "talos-capture-test-"));
  dirs.push(dir);
  return dir;
}

describe("TalosMediaCapture", () => {
  it("captures both streams and deletes the temporary media", async () => {
    const runtimeDir = await runtime();
    const capture = new TalosMediaCapture({
      source: "/unused/av_capture.swift",
      runtimeDir,
      binary: "/fake/capture",
      runner: async (_binary, args) => {
        await writeFile(args[0]!, Buffer.from([0xff, 0xd8, 0xff, 0x00]));
        await writeFile(args[1]!, Buffer.from("RIFF0000WAVE"));
        return {
          stdout: JSON.stringify({
            camera: true,
            audio: true,
            camera_permission: "authorized",
            microphone_permission: "authorized",
          }),
          stderr: "",
        };
      },
    });
    const receipt = await capture.capture(1.5);
    expect(receipt.frameBase64).toMatch(/^\/9j\//);
    expect(
      Buffer.from(receipt.audioBase64, "base64").subarray(0, 4).toString(),
    ).toBe("RIFF");
    expect(receipt.cameraPermission).toBe("authorized");
    expect(receipt.mediaDeleted).toBe(true);
    expect(await readdir(runtimeDir)).toEqual([]);
  });

  it("rejects a helper that returns no valid media and still cleans up", async () => {
    const runtimeDir = await runtime();
    const capture = new TalosMediaCapture({
      source: "/unused/av_capture.swift",
      runtimeDir,
      binary: "/fake/capture",
      runner: async (_binary, args) => {
        await writeFile(args[0]!, Buffer.from("not a jpeg"));
        await writeFile(args[1]!, Buffer.from("not a wav"));
        return {
          stdout: JSON.stringify({ camera: true, audio: true }),
          stderr: "",
        };
      },
    });
    await expect(capture.capture()).rejects.toThrow("no JPEG frame");
    expect(await readdir(runtimeDir)).toEqual([]);
  });

  it("rejects an invalid duration before running the helper", async () => {
    const runtimeDir = await runtime();
    let ran = false;
    const capture = new TalosMediaCapture({
      source: "/unused/av_capture.swift",
      runtimeDir,
      binary: "/fake/capture",
      runner: async () => {
        ran = true;
        return { stdout: "{}", stderr: "" };
      },
    });
    await expect(capture.capture(0)).rejects.toThrow("between 0 and 10");
    expect(ran).toBe(false);
  });
});
