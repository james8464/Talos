import { describe, expect, it } from "vitest";

import { TalosPerception } from "./talos-perception.js";

const perception = (outputs: Record<string, string>) =>
  new TalosPerception({
    sourceDir: "/unused",
    runtimeDir: "/unused",
    sceneBinary: "/fake/scene",
    soundBinary: "/fake/sound",
    runner: async (binary) => ({
      stdout: outputs[binary] ?? "{}",
      stderr: "",
    }),
  });

describe("TalosPerception", () => {
  it("reads people and faces from a scene", async () => {
    const result = await perception({
      "/fake/scene": JSON.stringify({
        people: 2,
        faces: 1,
        empty: false,
        window: 1,
      }),
    }).scene("/tmp/frame.jpg");
    expect(result).toEqual({ people: 2, faces: 1, empty: false, window: 1 });
  });

  it("reads sound events", async () => {
    const result = await perception({
      "/fake/sound": JSON.stringify([
        { label: "Telephone", confidence: 0.9 },
        { label: "Beep", confidence: 0.4 },
      ]),
    }).sound("/tmp/clip.wav");
    expect(result.map((event) => event.label)).toEqual(["Telephone", "Beep"]);
  });

  it("rejects malformed results", async () => {
    await expect(
      perception({ "/fake/scene": "not json" }).scene("/tmp/frame.jpg"),
    ).rejects.toThrow("invalid JSON");
    await expect(
      perception({ "/fake/scene": JSON.stringify({ people: "two" }) }).scene(
        "/tmp/frame.jpg",
      ),
    ).rejects.toThrow("invalid scene");
    await expect(
      perception({ "/fake/sound": JSON.stringify([{ label: 2 }]) }).sound(
        "/tmp/clip.wav",
      ),
    ).rejects.toThrow("invalid event");
  });
});
