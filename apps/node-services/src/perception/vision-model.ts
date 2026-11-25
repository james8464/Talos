/**
 * Local vision answers through Ollama, so image questions cost nothing and
 * run on this machine. The model is sent one image, never a recording, and
 * the answer is bounded.
 */
export interface VisionModel {
  describe(
    image: string,
    question: string,
  ): Promise<{ answer: string; model: string }>;
}

export interface OllamaVisionOptions {
  readonly baseUrl: string;
  readonly model: string;
  readonly timeoutMs?: number;
  readonly fetch?: typeof fetch;
}

export class OllamaVision implements VisionModel {
  readonly #baseUrl: string;
  readonly #model: string;
  readonly #timeoutMs: number;
  readonly #fetch: typeof fetch;

  constructor(options: OllamaVisionOptions) {
    this.#baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.#model = options.model;
    this.#timeoutMs = options.timeoutMs ?? 90_000;
    this.#fetch = options.fetch ?? fetch;
  }

  async describe(
    image: string,
    question: string,
  ): Promise<{ answer: string; model: string }> {
    const response = await this.#fetch(`${this.#baseUrl}/api/chat`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      signal: AbortSignal.timeout(this.#timeoutMs),
      body: JSON.stringify({
        model: this.#model,
        stream: false,
        messages: [
          {
            role: "system",
            content:
              "Answer briefly using only details visible in the supplied image. If unclear, say so. Do not identify people.",
          },
          { role: "user", content: question, images: [image] },
        ],
        options: { temperature: 0, num_predict: 160 },
      }),
    });
    if (!response.ok)
      throw new Error(`The local vision model answered ${response.status}.`);
    const body = (await response.json()) as {
      message?: { content?: unknown };
      error?: unknown;
    };
    const answer =
      typeof body.message?.content === "string"
        ? body.message.content.trim()
        : "";
    if (answer === "")
      throw new Error("The local vision model returned no answer.");
    return { answer: answer.slice(0, 600), model: this.#model };
  }
}
