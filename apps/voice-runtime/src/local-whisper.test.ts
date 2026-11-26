import { describe, expect, it, vi } from "vitest";

import {
  LocalWhisperProvider,
  pcmFromWAV,
  wavFromPCM,
} from "./local-whisper.js";

describe("pcmFromWAV", () => {
  it("reads the data chunk past another chunk", () => {
    const junk = Buffer.alloc(8 + 28);
    junk.write("JUNK", 0);
    junk.writeUInt32LE(28, 4);
    const wav = wavFromPCM(Buffer.alloc(640, 7));
    const spliced = Buffer.concat([
      wav.subarray(0, 12),
      junk,
      wav.subarray(12),
    ]);
    const pcm = pcmFromWAV(spliced);
    expect(pcm.length).toBe(640);
    expect(pcm[0]).toBe(7);
  });

  it("rejects stereo, other rates and missing data", () => {
    const wav = wavFromPCM(Buffer.alloc(64));
    const stereo = Buffer.from(wav);
    stereo.writeUInt16LE(2, 22);
    expect(() => pcmFromWAV(stereo)).toThrow();
    const rate = Buffer.from(wav);
    rate.writeUInt32LE(44_100, 24);
    expect(() => pcmFromWAV(rate)).toThrow();
    expect(() => pcmFromWAV(wav.subarray(0, 40))).toThrow();
  });
});

describe("local whisper", () => {
  it("writes a WAV header and emits one final transcript", async () => {
    const pcm = Buffer.alloc(3200);
    const wav = wavFromPCM(pcm);
    expect(wav.subarray(0, 4).toString()).toBe("RIFF");
    expect(wav.subarray(8, 12).toString()).toBe("WAVE");
    expect(wav.readUInt32LE(40)).toBe(pcm.length);

    const transcribe = vi.fn(async () => "the quick brown fox");
    const stream = await new LocalWhisperProvider({ transcribe }).open(
      new AbortController().signal,
    );
    await stream.sendPCM(new Int16Array(1600));
    await stream.finish();
    const events = [];
    for await (const event of stream.events()) events.push(event);
    expect(transcribe).toHaveBeenCalledOnce();
    expect(events).toEqual([
      {
        kind: "final",
        startOffsetMs: 0,
        endOffsetMs: 100,
        text: "the quick brown fox",
      },
    ]);
  });

  it("emits no final for an empty transcript", async () => {
    const stream = await new LocalWhisperProvider({
      transcribe: async () => "",
    }).open(new AbortController().signal);
    await stream.sendPCM(new Int16Array(1600));
    await stream.finish();
    const events = [];
    for await (const event of stream.events()) events.push(event);
    expect(events).toEqual([]);
  });
});
