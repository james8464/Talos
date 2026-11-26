import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { runCodingJob, type PatchDraft } from "./executor.js";

const dirs: string[] = [];
afterEach(async () => {
  await Promise.all(
    dirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })),
  );
});

async function fixture(): Promise<string> {
  const root = await mkdtemp(path.join(os.tmpdir(), "talos-job-test-"));
  dirs.push(root);
  await mkdir(path.join(root, "src"), { recursive: true });
  await writeFile(path.join(root, "src", "value.txt"), "one\n", "utf8");
  return root;
}

const draft = (after: string): PatchDraft => ({
  patches: [{ target: "src/value.txt", before: "one\n", after }],
  regressionPath: "tests/test_value.txt",
  regression: "expect two\n",
});

const verify = {
  file: "/bin/sh",
  args: ["-c", 'test "$(cat src/value.txt)" = "two"'],
};

describe("runCodingJob", () => {
  it("verifies in a sandbox without touching the source", async () => {
    const root = await fixture();
    const result = await runCodingJob({
      root,
      request: "change one to two",
      targets: ["src/value.txt"],
      draft: async () => draft("two\n"),
      verify,
    });
    expect(result).toMatchObject({ status: "verified", attempts: 1 });
    expect(await readFile(path.join(root, "src/value.txt"), "utf8")).toBe(
      "one\n",
    );
  });

  it("reports hashes and sizes without the file contents", async () => {
    const root = await fixture();
    const result = await runCodingJob({
      root,
      request: "change one to two",
      targets: ["src/value.txt"],
      draft: async () => draft("two\n"),
      verify,
    });
    const receipt = result.receipt;
    expect(receipt?.files).toEqual([
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
    expect(receipt?.regression_sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(JSON.stringify(receipt)).not.toContain("two");
  });

  it("retries once with the verification output after a failed sandbox", async () => {
    const root = await fixture();
    const prompts: string[] = [];
    const result = await runCodingJob({
      root,
      request: "change one",
      targets: ["src/value.txt"],
      draft: async (prompt) => {
        prompts.push(prompt);
        return draft(prompts.length === 1 ? "wrong\n" : "two\n");
      },
      verify,
    });
    expect(result).toMatchObject({ status: "verified", attempts: 2 });
    expect(prompts[1]).toContain("failed verification");
  });

  it("applies a verified patch and writes the regression", async () => {
    const root = await fixture();
    const result = await runCodingJob({
      root,
      request: "change one",
      targets: ["src/value.txt"],
      draft: async () => draft("two\n"),
      verify,
      apply: true,
    });
    expect(result).toMatchObject({ status: "applied", attempts: 1 });
    expect(await readFile(path.join(root, "src/value.txt"), "utf8")).toBe(
      "two\n",
    );
    expect(
      await readFile(path.join(root, "tests", "test_value.txt"), "utf8"),
    ).toBe("expect two\n");
  });

  it("rolls back when live verification fails after sandbox success", async () => {
    const root = await fixture();
    const counterDir = await mkdtemp(path.join(os.tmpdir(), "talos-counter-"));
    dirs.push(counterDir);
    const counter = path.join(counterDir, "counter");
    await writeFile(counter, "0", "utf8");
    const flakyVerify = {
      file: "/bin/sh",
      args: [
        "-c",
        `n=$(cat '${counter}'); n=$((n+1)); echo $n > '${counter}'; test $n -gt 1 && exit 1; test "$(cat src/value.txt)" = "two"`,
      ],
    };
    const result = await runCodingJob({
      root,
      request: "change one",
      targets: ["src/value.txt"],
      draft: async () => draft("two\n"),
      verify: flakyVerify,
      apply: true,
    });
    expect(result.status).toBe("rolled_back");
    expect(await readFile(path.join(root, "src/value.txt"), "utf8")).toBe(
      "one\n",
    );
  });

  it("rejects a patch whose before text is not unique", async () => {
    const root = await fixture();
    const result = await runCodingJob({
      root,
      request: "change one",
      targets: ["src/value.txt"],
      draft: async () => draft("two\n"),
      verify: { file: "/bin/sh", args: ["-c", "exit 0"] },
    });
    expect(result.status).toBe("verified");
    const bad = await runCodingJob({
      root,
      request: "change one",
      targets: ["src/value.txt"],
      draft: async () => ({
        ...draft("two\n"),
        patches: [
          { target: "src/value.txt", before: "missing\n", after: "x\n" },
        ],
      }),
      verify: { file: "/bin/sh", args: ["-c", "exit 0"] },
    });
    expect(bad.status).toBe("declined");
  });
});
