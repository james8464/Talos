import { createServer } from "node:http";
import type { AddressInfo } from "node:net";

import { afterEach, describe, expect, it } from "vitest";

import { ollamaDrafter } from "../src/coding/service.js";

const source = "package main\n\nfunc Add(a, b int) int {\n\treturn a + b\n}\n";
const draft = {
  before: "func Add(a, b int) int {",
  after: "// Add returns a plus b.\nfunc Add(a, b int) int {",
  regression_path: "tests/add_test.go",
  regression: "package tests\n",
};

const servers: Array<{ close(callback: () => void): void }> = [];
afterEach(async () => {
  await Promise.all(
    servers
      .splice(0)
      .map(
        (server) =>
          new Promise<void>((resolve) => server.close(() => resolve())),
      ),
  );
});

async function ollama(
  answers: unknown[],
): Promise<{ url: string; bodies: Record<string, unknown>[] }> {
  const bodies: Record<string, unknown>[] = [];
  const server = createServer((incoming, outgoing) => {
    let raw = "";
    incoming.on("data", (chunk) => (raw += chunk));
    incoming.on("end", () => {
      bodies.push(JSON.parse(raw) as Record<string, unknown>);
      outgoing.writeHead(200, { "content-type": "application/json" });
      outgoing.end(
        JSON.stringify({
          message: { content: JSON.stringify(answers.shift()) },
        }),
      );
    });
  });
  servers.push(server);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as AddressInfo).port;
  return { url: `http://127.0.0.1:${port}`, bodies };
}

describe("ollamaDrafter", () => {
  it("returns one validated patch and disables thinking", async () => {
    const { url, bodies } = await ollama([draft]);
    const result = await ollamaDrafter({ baseUrl: url, model: "qwen3:4b" })({
      target: "main.go",
      request: "add a comment",
      source,
    });
    expect(result).toEqual({
      draft: {
        patches: [
          { target: "main.go", before: draft.before, after: draft.after },
        ],
        regressionPath: "tests/add_test.go",
        regression: "package tests\n",
      },
      model: "qwen3:4b",
    });
    expect(bodies[0]?.think).toBe(false);
    expect(bodies[0]?.format).toBe("json");
  });

  it("repairs a rejected answer once", async () => {
    const { url, bodies } = await ollama([
      { ...draft, after: draft.before },
      draft,
    ]);
    const result = await ollamaDrafter({ baseUrl: url, model: "qwen3:4b" })({
      target: "main.go",
      request: "add a comment",
      source,
    });
    expect(result.draft.patches[0]?.after).toBe(draft.after);
    expect(bodies).toHaveLength(2);
    const second = (bodies[1]?.messages as Array<{ content: string }>)[1];
    expect(second?.content).toContain("rejected");
  });

  it("fails after two rejected answers", async () => {
    const { url } = await ollama([
      { ...draft, before: "missing text" },
      { ...draft, after: draft.before },
    ]);
    await expect(
      ollamaDrafter({ baseUrl: url, model: "qwen3:4b" })({
        target: "main.go",
        request: "add a comment",
        source,
      }),
    ).rejects.toThrow("before and after must differ.");
  });
});
