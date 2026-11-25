/** Session-local, self-reported names. Never a biometric identity claim. */
export const IDENTITY_TTL_MS = 30 * 60 * 1000;

interface Claim {
  readonly name: string;
  readonly at: number;
}

export class IdentityClaims {
  readonly #ttlMs: number;
  readonly #claims = new Map<string, Claim>();

  constructor(ttlMs = IDENTITY_TTL_MS) {
    this.#ttlMs = ttlMs;
  }

  /** A deterministic reply, or null when the message is not an identity request. */
  answer(room: string, text: string, now = Date.now()): string | null {
    const compact = text
      .replace(/[^\p{L}\p{N} '’-]+/gu, " ")
      .replace(/^(?:(?:hey|ok|okay) )?(?:talos|tell us) /iu, "")
      .replace(/\s+/gu, " ")
      .trim();
    const words = compact.toLowerCase();
    const introduced = /^(?:my name is|call me) ([^\p{N}]{1,60})$/iu.exec(
      compact,
    );
    if (introduced) {
      const name = introduced[1]!.trim();
      this.#claims.set(room, { name, at: now });
      return `Nice to meet you, ${name}.`;
    }
    if (
      /^(?:forget|clear) (?:my name|what i told you about my name)$/u.test(
        words,
      )
    ) {
      const had = this.#current(room, now) !== undefined;
      this.#claims.delete(room);
      return had
        ? "I forgot the name you introduced. I still can't verify identity."
        : "I don't have a self-reported name to forget.";
    }
    const claim = this.#current(room, now);
    if (
      /^(?:who am i|do you (?:recognize|remember) me|can you tell who i am|do you know who is speaking)$/u.test(
        words,
      )
    ) {
      return claim
        ? `You introduced yourself as ${claim.name}. That is self-reported; I can't verify identity from the camera.`
        : "I haven't confirmed who I'm speaking with.";
    }
    return null;
  }

  #current(room: string, now: number): Claim | undefined {
    const claim = this.#claims.get(room);
    if (!claim) return undefined;
    if (now - claim.at >= this.#ttlMs) {
      this.#claims.delete(room);
      return undefined;
    }
    return claim;
  }
}
