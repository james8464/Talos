import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { serviceCredential, type JsonLogger } from "@talos/service-kit/node";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { HttpHost, HttpRoute } from "../src/http-host.js";
import { startCodingService } from "../src/coding/service.js";

const environment = {
  TALOS_SECRET_SEED: "coding-test-seed-0123456789abcdef",
};
const token = serviceCredential("node-services-internal", environment)!;

const dirs: string[] = [];
afterEach(async () => {
  await Promise.all(
    dirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })),
  );
});

async function workspace(): Promise<string> {
  const root = await mkdtemp(path.join(os.tmpdir(), "talos-coding-service-"));
  dirs.push(root);
  await mkdir(path.join(root, "src"), { recursive: true });
  await writeFile(path.join(root, "src", "value.txt"), "one\n", "utf8");
  return root;
}

async function fixture(root: string) {
  let route: HttpRoute | undefined;
  const service = await startCodingService({
    http: {
      mount: (mounted: HttpRoute) => {
        route = mounted;
        return vi.fn();
      },
    } as unknown as HttpHost,
    logger: { log: vi.fn() } as unknown as JsonLogger,
    environment,
    workspace: root,
    verify: {
      file: "sh",
      args: ["-c", 'test "$(cat src/value.txt)" = "two"'],
    },
  });
  return { route: route!, service };
}

const send = (body: unknown, auth = `Bearer ${token}`) =>
  new Request("http://x/talos/coding/execute", {
    method: "POST",
    headers: { authorization: auth, "content-type": "application/json" },
    body: JSON.stringify(body),
  });

describe("coding route", () => {
  it("plans a job without inventing a patch", async () => {
    const { route } = await fixture(await workspace());
    const response = await route.fetch(
      send({
        kind: "job",
        target: "src/value.txt",
        request: "add a short comment",
      }),
    );
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.status).toBe("planned");
    expect(body.steps).toEqual([
      { id: "step_1", target: "src/value.txt", request: "add a short comment" },
    ]);
  });

  it("verifies a drafted patch in a sandbox and leaves the source alone", async () => {
    const root = await workspace();
    const { route } = await fixture(root);
    const response = await route.fetch(
      send({
        kind: "job",
        target: "src/value.txt",
        request: "change one to two",
        draft: {
          patches: [
            { target: "src/value.txt", before: "one\n", after: "two\n" },
          ],
          regressionPath: "tests/test_value.txt",
          regression: "expect two\n",
        },
      }),
    );
    const body = await response.json();
    expect(body.status).toBe("verified");
    expect(body.message).toBe("sandbox passed");
    expect(await readFile(path.join(root, "src", "value.txt"), "utf8")).toBe(
      "one\n",
    );
  });

  it("rejects an unsafe target and a missing file", async () => {
    const { route } = await fixture(await workspace());
    const unsafe = await route.fetch(
      send({ kind: "job", target: "../outside.txt", request: "read" }),
    );
    expect(unsafe.status).toBe(400);
    const missing = await route.fetch(
      send({ kind: "job", target: "src/missing.txt", request: "read" }),
    );
    expect((await missing.json()).status).toBe("declined");
  });

  it("requires the service token", async () => {
    const { route } = await fixture(await workspace());
    const response = await route.fetch(
      send({ kind: "plan", request: "clarify" }, "Bearer nope"),
    );
    expect(response.status).toBe(401);
  });
});
