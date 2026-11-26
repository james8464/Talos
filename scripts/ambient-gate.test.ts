import { describe, expect, it } from "vitest";

import { pcmRms, UtteranceGate } from "./ambient-gate.js";

const chunk = (seconds: number) => seconds * 16_000;

describe("UtteranceGate", () => {
  it("starts on speech and ends after enough silence", () => {
    const gate = new UtteranceGate({ silenceMs: 1_000, minSpeechMs: 300 });
    expect(gate.push(0.0002, chunk(1))).toBe("none");
    expect(gate.push(0.05, chunk(1))).toBe("start");
    expect(gate.push(0.04, chunk(1))).toBe("none");
    expect(gate.push(0.0002, chunk(0.5))).toBe("none");
    expect(gate.push(0.0002, chunk(0.5))).toBe("utterance");
  });

  it("ignores a blip shorter than the minimum", () => {
    const gate = new UtteranceGate({ silenceMs: 500, minSpeechMs: 500 });
    expect(gate.push(0.05, chunk(0.2))).toBe("start");
    expect(gate.push(0.0002, chunk(0.5))).toBe("none");
  });

  it("forces an utterance at the maximum length", () => {
    const gate = new UtteranceGate({ maxUtteranceMs: 2_000 });
    expect(gate.push(0.05, chunk(1))).toBe("start");
    expect(gate.push(0.05, chunk(1))).toBe("utterance");
  });
});

describe("pcmRms", () => {
  it("is zero for silence and grows with the signal", () => {
    expect(pcmRms(new Int16Array(1600))).toBe(0);
    const quiet = pcmRms(new Int16Array(1600).fill(330));
    const loud = pcmRms(new Int16Array(1600).fill(3_300));
    expect(quiet).toBeGreaterThan(0);
    expect(loud).toBeGreaterThan(quiet);
  });
});
