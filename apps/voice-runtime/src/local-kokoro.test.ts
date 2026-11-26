import { writeFile } from "node:fs/promises";
import { describe, expect, it, vi } from "vitest";

import { kokoroSynthesizer } from "./local-kokoro.js";
import { wavFromPCM } from "./local-whisper.js";

describe("kokoroSynthesizer", () => {
  it("runs the helper with the voice and reads the WAV", async () => {
    const seen: string[][] = [];
    const runner = vi.fn(async (file: string, args: readonly string[]) => {
      seen.push([file, ...args]);
      const wav = wavFromPCM(Buffer.alloc(4800));
      await writeFile(args[6]!, wav);
      return {
        stdout: JSON.stringify({
          sample_rate: 24000,
          seconds: 1.2,
          bytes: 4800,
        }),
      };
    });
    const synth = kokoroSynthesizer(
      {
        python: "/repo/apps/voice-runtime/.venv/bin/python",
        helper: "/repo/apps/voice-runtime/scripts/synthesize_kokoro.py",
        model: "/repo/.kokoro/model.onnx",
        voices: "/repo/.kokoro/voices.bin",
        voice: "af_heart",
        speed: 1.05,
      },
      runner,
    );
    const clip = await synth("Hello from Talos.");
    expect(seen[0]).toEqual([
      "/repo/apps/voice-runtime/.venv/bin/python",
      "/repo/apps/voice-runtime/scripts/synthesize_kokoro.py",
      "/repo/.kokoro/model.onnx",
      "/repo/.kokoro/voices.bin",
      "af_heart",
      "1.05",
      "Hello from Talos.",
      expect.any(String),
    ]);
    expect(clip.sampleRate).toBe(24_000);
    expect(clip.seconds).toBe(1.2);
    expect(clip.wav.subarray(0, 4).toString()).toBe("RIFF");
    expect(clip.wav.length).toBeGreaterThanOrEqual(48);
  });

  it("changes voice and pace by tone unless a voice is forced", async () => {
    const seen: (readonly string[])[] = [];
    const runner = async (_file: string, args: readonly string[]) => {
      seen.push(args);
      await writeFile(args[6]!, wavFromPCM(Buffer.alloc(48)));
      return { stdout: JSON.stringify({ sample_rate: 24000, seconds: 1 }) };
    };
    const tones = kokoroSynthesizer(
      {
        python: "/python",
        helper: "/helper.py",
        model: "/model",
        voices: "/voices",
      },
      runner,
    );
    await tones("Alert.", "alert");
    await tones("Threat.", "menacing");
    expect(seen[0]![3]).toBe("am_fenrir");
    expect(seen[0]![4]).toBe("1.03");
    expect(seen[1]![3]).toBe("bm_lewis");
    expect(seen[1]![4]).toBe("0.84");
    const forced = kokoroSynthesizer(
      {
        python: "/python",
        helper: "/helper.py",
        model: "/model",
        voices: "/voices",
        voice: "am_michael",
      },
      runner,
    );
    await forced("Calm.", "menacing");
    expect(seen[2]![3]).toBe("am_michael");
  });
});
