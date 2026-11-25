import { createServer } from "node:http";
import type { AddressInfo } from "node:net";

import { serviceCredential, type JsonLogger } from "@talos/service-kit/node";
import { describe, expect, it, vi } from "vitest";

import type { HttpHost, HttpRoute } from "../src/http-host.js";
import { startPerceptionService } from "../src/perception/service.js";
import { OllamaVision } from "../src/perception/vision-model.js";

const environment = {
  TALOS_SECRET_SEED: "vision-describe-seed-0123456789abcdef",
};
const token = serviceCredential("node-services-internal", environment)!;
const JPEG = Buffer.from([0xff, 0xd8, 0xff, 0x00]).toString("base64");

async function fixture(
  describe: (
    image: string,
    question: string,
  ) => Promise<{ answer: string; model: string }>,
) {
  let route: HttpRoute | undefined;
  const vision = { describe: vi.fn(describe) };
  const service = await startPerceptionService({
    http: {
      mount: (mounted: HttpRoute) => {
        route = mounted;
        return vi.fn();
      },
    } as unknown as HttpHost,
    logger: { log: vi.fn() } as unknown as JsonLogger,
    environment,
    client: {
      scene: vi.fn(async () => ({ people: 0, faces: 0, empty: true })),
      sound: vi.fn(async () => []),
    },
    vision,
  });
  return { route: route!, vision, service };
}

const request = (body: unknown, auth = `Bearer ${token}`) =>
  new Request("http://x/talos/vision/describe", {
    method: "POST",
    headers: { authorization: auth, "content-type": "application/json" },
    body: JSON.stringify(body),
  });

describe("vision describe route", () => {
  it("answers through the local vision model", async () => {
    const { route, vision } = await fixture(async () => ({
      answer: "A desk with a lamp.",
      model: "qwen2.5vl:3b",
    }));
    const response = await route.fetch(
      request({ image: JPEG, question: "What is on the desk?" }),
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      answer: "A desk with a lamp.",
      model: "qwen2.5vl:3b",
    });
    expect(vision.describe).toHaveBeenCalledWith(JPEG, "What is on the desk?");
  });

  it("uses a default question and rejects a non-image body", async () => {
    const { route, vision } = await fixture(async () => ({
      answer: "Something.",
      model: "qwen2.5vl:3b",
    }));
    const ok = await route.fetch(request({ image: JPEG }));
    expect(ok.status).toBe(200);
    expect(vision.describe).toHaveBeenCalledWith(JPEG, "What do you see?");
    const bad = await route.fetch(request({ image: "not-an-image" }));
    expect(bad.status).toBe(400);
  });

  it("reports an unavailable model without failing the request shape", async () => {
    const { route } = await fixture(async () => {
      throw new Error("The local vision model answered 500.");
    });
    const response = await route.fetch(request({ image: JPEG }));
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({
      error: "The local vision model answered 500.",
    });
  });

  it("requires the service token", async () => {
    const { route } = await fixture(async () => ({
      answer: "x",
      model: "m",
    }));
    expect(
      (await route.fetch(request({ image: JPEG }, "Bearer nope"))).status,
    ).toBe(401);
  });
});

describe("OllamaVision", () => {
  it("posts one image with a bounded prompt and trims the answer", async () => {
    let body: Record<string, unknown> | undefined;
    const server = createServer((incoming, outgoing) => {
      let raw = "";
      incoming.on("data", (chunk) => (raw += chunk));
      incoming.on("end", () => {
        body = JSON.parse(raw) as Record<string, unknown>;
        outgoing.writeHead(200, { "content-type": "application/json" });
        outgoing.end(JSON.stringify({ message: { content: " A desk. " } }));
      });
    });
    await new Promise<void>((resolve) =>
      server.listen(0, "127.0.0.1", resolve),
    );
    const port = (server.address() as AddressInfo).port;
    try {
      const vision = new OllamaVision({
        baseUrl: `http://127.0.0.1:${port}`,
        model: "qwen2.5vl:3b",
      });
      const result = await vision.describe(JPEG, "What do you see?");
      expect(result).toEqual({ answer: "A desk.", model: "qwen2.5vl:3b" });
      expect(body?.model).toBe("qwen2.5vl:3b");
      expect(body?.stream).toBe(false);
      const messages = body?.messages as Array<{ images?: string[] }>;
      expect(messages[1]?.images).toEqual([JPEG]);
    } finally {
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  });

  it("fails when the model answers with an error", async () => {
    const server = createServer((_incoming, outgoing) => {
      outgoing.writeHead(500, { "content-type": "application/json" });
      outgoing.end(JSON.stringify({ error: "model not found" }));
    });
    await new Promise<void>((resolve) =>
      server.listen(0, "127.0.0.1", resolve),
    );
    const port = (server.address() as AddressInfo).port;
    try {
      const vision = new OllamaVision({
        baseUrl: `http://127.0.0.1:${port}`,
        model: "qwen2.5vl:3b",
      });
      await expect(vision.describe(JPEG, "q")).rejects.toThrow("answered 500");
    } finally {
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  });
});
