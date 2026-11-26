import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { serviceCredential, type JsonLogger } from "@talos/service-kit/node";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { HttpHost, HttpRoute } from "../src/http-host.js";
import type { ModelCall } from "../src/runtime-model-call.js";
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

async function fixture(
  root: string,
  modelCall?: () => ModelCall,
  localDrafter?: (input: {
    target: string;
    request: string;
    source: string;
  }) => Promise<{
    draft: {
      patches: { target: string; before: string; after: string }[];
      regressionPath: string;
      regression: string;
    };
    model: string;
  }>,
  verify: { file: string; args: readonly string[] } | null = {
    file: "sh",
    args: ["-c", 'test "$(cat src/value.txt)" = "two"'],
  },
) {
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
    ...(verify ? { verify } : {}),
    ...(modelCall ? { modelCall } : {}),
    localDrafter:
      localDrafter ??
      (async () => {
        throw new Error("no local drafter in tests");
      }),
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
  it("declines a patch when no verify command is configured", async () => {
    const { route } = await fixture(
      await workspace(),
      undefined,
      undefined,
      null,
    );
    const response = await route.fetch(
      send({
        kind: "job",
        target: "src/value.txt",
        request: "add a short comment",
      }),
    );
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.status).toBe("declined");
    expect(body.message).toContain("No verify command");
  });

  it("plans a request that has no target", async () => {
    const { route } = await fixture(await workspace());
    const response = await route.fetch(
      send({ kind: "plan", request: "clarify the helper" }),
    );
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.status).toBe("planned");
    expect(body.steps).toEqual([
      { id: "step_1", target: "", request: "clarify the helper" },
    ]);
  });

  it("verifies a patch from the local drafter", async () => {
    const root = await workspace();
    const { route } = await fixture(root, undefined, async () => ({
      draft: {
        patches: [{ target: "src/value.txt", before: "one\n", after: "two\n" }],
        regressionPath: "tests/test_value.txt",
        regression: "expect two\n",
      },
      model: "qwen3:4b",
    }));
    const response = await route.fetch(
      send({
        kind: "job",
        target: "src/value.txt",
        request: "change one to two",
      }),
    );
    const body = await response.json();
    expect(body.status).toBe("verified");
    expect(body.model).toBe("qwen3:4b");
    expect(body.receipt.files).toEqual([
      {
        target: "src/value.txt",
        before_sha256:
          "2c8b08da5ce60398e1f19af0e5dccc744df274b826abe585eaba68c525434806",
        after_sha256:
          "27dd8ed44a83ff94d557f9fd0412ed5a8cbca69ea04922d88c01184a07300a5a",
        bytes_before: 4,
        bytes_after: 4,
      },
    ]);
    expect(body.receipt.regression_sha256).toBe(
      "f8f90421ddaf88273f5020fa1981e09e1cac39807406d3aa80dc52986e195dd2",
    );
    expect(body.receipt.diff_sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(body.receipt.budget_remaining_seconds).toBeGreaterThanOrEqual(0);
    expect(await readFile(path.join(root, "src", "value.txt"), "utf8")).toBe(
      "one\n",
    );
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

  it("drafts a patch with the model seam and verifies it in a sandbox", async () => {
    const root = await workspace();
    const calls: Record<string, unknown>[] = [];
    const model = {
      call: async (request: Record<string, unknown>) => {
        calls.push(request);
        const output = {
          before: "one\n",
          after: "two\n",
          regression_path: "tests/test_value.txt",
          regression: "expect two\n",
        };
        const reason = await (
          request.validate as (
            value: Record<string, unknown>,
          ) => Promise<string | undefined>
        )(output);
        if (reason) throw new Error(reason);
        return { output, model: "fake-model" };
      },
    } as unknown as ModelCall;
    const { route } = await fixture(root, () => model);
    const response = await route.fetch(
      send({
        kind: "job",
        job_id: "codingjob_1",
        tenant_id: "tenant_acme",
        requester_actor_id: "actor_human",
        target: "src/value.txt",
        request: "change one to two",
      }),
    );
    const body = await response.json();
    expect(body.status).toBe("verified");
    expect(body.model).toBe("fake-model");
    const first = calls[0]!;
    expect(first.kind).toBe("coding");
    expect(first.key).toBe("coding:codingjob_1");
    expect(first.owner).toEqual({
      tenantId: "tenant_acme",
      ownerActorId: "actor_human",
    });
    expect(await readFile(path.join(root, "src", "value.txt"), "utf8")).toBe(
      "one\n",
    );
  });

  it("falls back to the local drafter when the Agent model fails", async () => {
    const root = await workspace();
    const model = {
      call: async () => {
        throw new Error("no Computer is online");
      },
    } as unknown as ModelCall;
    const { route } = await fixture(
      root,
      () => model,
      async () => ({
        draft: {
          patches: [
            { target: "src/value.txt", before: "one\n", after: "two\n" },
          ],
          regressionPath: "tests/test_value.txt",
          regression: "expect two\n",
        },
        model: "qwen3:4b",
      }),
    );
    const response = await route.fetch(
      send({
        kind: "job",
        target: "src/value.txt",
        request: "change one to two",
      }),
    );
    const body = await response.json();
    expect(body.status).toBe("verified");
    expect(body.model).toBe("qwen3:4b");
    expect(await readFile(path.join(root, "src", "value.txt"), "utf8")).toBe(
      "one\n",
    );
  });

  it("declines a draft whose before text does not match the file", async () => {
    const root = await workspace();
    const model = {
      call: async (request: Record<string, unknown>) => {
        const output = {
          before: "missing\n",
          after: "two\n",
          regression_path: "tests/test_value.txt",
          regression: "expect two\n",
        };
        const reason = await (
          request.validate as (
            value: Record<string, unknown>,
          ) => Promise<string | undefined>
        )(output);
        if (reason) throw new Error(reason);
        return { output, model: "fake-model" };
      },
    } as unknown as ModelCall;
    const { route } = await fixture(root, () => model);
    const response = await route.fetch(
      send({ kind: "job", target: "src/value.txt", request: "change one" }),
    );
    const body = await response.json();
    expect(body.status).toBe("declined");
    expect(body.message).toContain("could not draft a patch");
    expect(body.message).toContain("exactly once");
  });

  it("requires the service token", async () => {
    const { route } = await fixture(await workspace());
    const response = await route.fetch(
      send({ kind: "plan", request: "clarify" }, "Bearer nope"),
    );
    expect(response.status).toBe(401);
  });
});
