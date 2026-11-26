/** Energy-based utterance gate for the ambient loop. */

export type GateEvent = "none" | "start" | "utterance";

export interface GateOptions {
  /** Normalized RMS above which a chunk counts as speech. */
  readonly threshold?: number;
  /** Silence that ends an utterance. */
  readonly silenceMs?: number;
  /** Speech shorter than this is ignored. */
  readonly minSpeechMs?: number;
  /** Hard cap so a noisy room cannot hold one utterance forever. */
  readonly maxUtteranceMs?: number;
}

const DEFAULTS = {
  threshold: 0.001,
  silenceMs: 1_000,
  minSpeechMs: 400,
  maxUtteranceMs: 15_000,
} as const;

/**
 * Feed one chunk's RMS and length; it reports when speech starts and when the
 * accumulated utterance ends. Pure and synchronous so it is testable.
 */
export class UtteranceGate {
  readonly #options: Required<GateOptions>;
  #speaking = false;
  #speechMs = 0;
  #silenceMs = 0;
  #utteranceMs = 0;

  constructor(options: GateOptions = {}) {
    this.#options = { ...DEFAULTS, ...options };
  }

  push(rms: number, samples: number, sampleRate = 16_000): GateEvent {
    const ms = (samples / sampleRate) * 1_000;
    if (rms >= this.#options.threshold) {
      this.#silenceMs = 0;
      if (!this.#speaking) {
        this.#speaking = true;
        this.#speechMs = ms;
        this.#utteranceMs = ms;
        return "start";
      }
      this.#speechMs += ms;
      this.#utteranceMs += ms;
      return this.#finishIfFull() ? "utterance" : "none";
    }
    if (!this.#speaking) return "none";
    this.#silenceMs += ms;
    this.#utteranceMs += ms;
    if (
      this.#silenceMs < this.#options.silenceMs &&
      this.#utteranceMs < this.#options.maxUtteranceMs
    )
      return "none";
    const heardSpeech = this.#speechMs >= this.#options.minSpeechMs;
    this.#reset();
    return heardSpeech ? "utterance" : "none";
  }

  #finishIfFull(): boolean {
    if (this.#utteranceMs < this.#options.maxUtteranceMs) return false;
    const heardSpeech = this.#speechMs >= this.#options.minSpeechMs;
    this.#reset();
    return heardSpeech;
  }

  #reset(): void {
    this.#speaking = false;
    this.#speechMs = 0;
    this.#silenceMs = 0;
    this.#utteranceMs = 0;
  }
}

/** Normalized RMS of 16-bit mono samples. */
export function pcmRms(samples: Int16Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (const sample of samples) {
    const value = sample / 32768;
    sum += value * value;
  }
  return Math.sqrt(sum / samples.length);
}
