import { createServer } from "node:http";
import type { AddressInfo } from "node:net";

import { serviceCredential, type JsonLogger } from "@talos/service-kit/node";
import { describe, expect, it, vi } from "vitest";

import type { HttpHost, HttpRoute } from "../src/http-host.js";
import {
  judgeGoal,
  parseGoalDecision,
  startGoalService,
} from "../src/goal/service.js";

const environment = {
  TALOS_SECRET_SEED: "goal-test-seed-0123456789abcdef01234567",
};
const token = serviceCredential("node-services-internal", environment)!;

describe("goal decision parsing", () => {
  it("accepts the three verdicts and rejects others", () => {
    expect(parseGoalDecision({ verdict: "done", reason: "shipped" })).toEqual({
      verdict: "done",
      reason: "shipped",
    });
    expect(parseGoalDecision({ verdict: "continue" }).verdict).toBe("continue");
    expect(parseGoalDecision({ verdict: "blocked" }).verdict).toBe("blocked");
    for (const value of [{}, { verdict: "maybe" }, null, "done"]) {
      expect(() => parseGoalDecision(value)).toThrow();
    }
  });
});

describe("judgeGoal", () => {
  it("asks the local model for one JSON verdict", async () => {
    let body: Record<string, unknown> | undefined;
    const server = createServer((incoming, outgoing) => {
      let raw = "";
      incoming.on("data", (chunk) => (raw += chunk));
      incoming.on("end", () => {
        body = JSON.parse(raw) as Record<string, unknown>;
        outgoing.writeHead(200, { "content-type": "application/json" });
        outgoing.end(
          JSON.stringify({
            message: { content: '{"verdict":"done","reason":"shipped"}' },
          }),
        );
      });
    });
    await new Promise<void>((resolve) =>
      server.listen(0, "127.0.0.1", resolve),
    );
    const port = (server.address() as AddressInfo).port;
    try {
      const decision = await judgeGoal({
        baseUrl: `http://127.0.0.1:${port}`,
        model: "qwen3:4b",
        objective: "ship the report",
        reply: "Report is shipped.",
      });
      expect(decision).toEqual({ verdict: "done", reason: "shipped" });
      expect(body?.model).toBe("qwen3:4b");
      expect(body?.format).toBe("json");
      expect(body?.think).toBe(false);
    } finally {
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  });
});

describe("goal route", () => {
  it("requires the service token and both fields", async () => {
    let route: HttpRoute | undefined;
    await startGoalService({
      http: {
        mount: (mounted: HttpRoute) => {
          route = mounted;
          return vi.fn();
        },
      } as unknown as HttpHost,
      logger: { log: vi.fn() } as unknown as JsonLogger,
      environment,
    });
    const send = (body: unknown, auth = `Bearer ${token}`) =>
      new Request("http://x/talos/goal/judge", {
        method: "POST",
        headers: { authorization: auth, "content-type": "application/json" },
        body: JSON.stringify(body),
      });
    expect(
      (await route!.fetch(send({ objective: "x", reply: "y" }, "Bearer nope")))
        .status,
    ).toBe(401);
    expect((await route!.fetch(send({ objective: "x" }))).status).toBe(400);
  });
});
