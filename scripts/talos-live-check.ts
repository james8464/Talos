/**
 * Safe live acceptance for the Talos replatform.
 *
 * Starts only the identity, perception and coding roles with TALOS_MUTE=1,
 * proves the voice runtime refuses to start, captures one frame/clip and
 * deletes it, classifies both over HTTP, asks the on-device vision model,
 * exercises identity and coding commands, runs the browser Room Document
 * journeys, runs one Codex call backed by local Ollama, optionally runs a
 * live Agent conversation, then verifies no voice/audio process is left
 * behind.
 *
 * Run: pnpm exec tsx scripts/talos-live-check.ts
 */
import { execFile, spawn } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";

import { isMuted } from "../packages/service-kit/src/index.js";
import { serviceCredential } from "../packages/service-kit/src/node.js";
import { TalosMediaCapture } from "../apps/desktop/src/main/talos-media-capture.js";

const exec = promisify(execFile);
const ROOT = path.resolve(import.meta.dirname, "..");
const SEED = "talos-live-check-seed-0123456789abcdef";
const BASE = "http://127.0.0.1:4319";
const TOKEN = serviceCredential("node-services-internal", {
  TALOS_SECRET_SEED: SEED,
})!;
const checks: { name: string; ok: boolean; detail: string }[] = [];

function check(name: string, ok: boolean, detail = "") {
  checks.push({ name, ok, detail: String(detail).slice(0, 300) });
}

// Codex reads stdin when it is open, so run it with stdin closed and collect
// both streams ourselves.
function runCodex(codexHome: string, model: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(
      "codex",
      [
        "exec",
        "--skip-git-repo-check",
        "-m",
        model,
        'Return exactly the JSON {"ok":true} and nothing else.',
      ],
      {
        cwd: os.tmpdir(),
        env: { ...process.env, CODEX_HOME: codexHome, OLLAMA_API_KEY: "dummy" },
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error("codex timed out"));
    }, 300_000);
    child.stdout.on("data", (chunk) => (stdout += chunk));
    child.stderr.on("data", (chunk) => (stderr += chunk));
    child.once("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.once("close", (code) => {
      clearTimeout(timer);
      if (code === 0) resolve(stdout);
      else reject(new Error(`codex exited ${code}: ${stderr.slice(-300)}`));
    });
  });
}

async function noProcess(pattern: string): Promise<boolean> {
  try {
    await exec("pgrep", ["-f", pattern]);
    return false;
  } catch {
    return true;
  }
}

async function waitForHealth(): Promise<boolean> {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const response = await fetch(`${BASE}/healthz`);
      if (response.ok) return true;
    } catch {
      // not listening yet
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return false;
}

async function identity(text: string, room = "live"): Promise<string | null> {
  const response = await fetch(`${BASE}/talos/identity`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${TOKEN}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({ room, text }),
  });
  if (!response.ok) throw new Error(`identity HTTP ${response.status}`);
  const body = (await response.json()) as { reply?: string | null };
  return body.reply ?? null;
}

async function classify(
  route: "/talos/vision/scene" | "/talos/sound",
  body: Record<string, string>,
): Promise<{ status: number; body: Record<string, unknown> }> {
  const response = await fetch(`${BASE}${route}`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${TOKEN}`,
      "content-type": "application/json",
    },
    body: JSON.stringify(body),
  });
  return {
    status: response.status,
    body: (await response.json()) as Record<string, unknown>,
  };
}

async function main(): Promise<number> {
  check("mute-policy", isMuted({ TALOS_MUTE: "1" }) && !isMuted({}));

  const voice = await exec(
    process.execPath,
    ["--import", "tsx", path.join(ROOT, "apps/voice-runtime/src/main.ts")],
    {
      cwd: ROOT,
      env: { ...process.env, TALOS_MUTE: "1" },
      timeout: 60_000,
    },
  ).then(
    () => true,
    (error: { stderr?: string }) => {
      check(
        "voice-refusal-detail",
        false,
        error.stderr ?? "voice runtime failed",
      );
      return false;
    },
  );
  check("voice-refusal", voice);
  check("no-voice-process", await noProcess("voice-runtime|livekit-server"));

  if (!(await noProcess("node-services|src/main.ts")))
    throw new Error("port 4319 is already held by another Talos process");

  const child = spawn("pnpm", ["--filter", "@talos/node-services", "dev"], {
    cwd: ROOT,
    env: {
      ...process.env,
      TALOS_MUTE: "1",
      TALOS_SECRET_SEED: SEED,
      NODE_SERVICES_ROLES: "identity,perception,coding,goal",
      TALOS_NATIVE_HELPERS_DIR: path.join(ROOT, "apps/desktop/resources/talos"),
      TALOS_CODING_ROOT: ROOT,
    },
    detached: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  child.stdout?.on("data", (chunk) => (output += chunk));
  child.stderr?.on("data", (chunk) => (output += chunk));

  const captureDir = await mkdtemp(
    path.join(os.tmpdir(), "talos-live-capture-"),
  );
  try {
    check("service-health", await waitForHealth(), output.slice(-200));
    if (!checks.at(-1)!.ok) return 1;

    const intro = await identity("My name is Ada.");
    const recognize = await identity("Do you recognize me?");
    const forget = await identity("Forget my name.");
    const after = await identity("Do you recognize me?");
    check(
      "identity-commands",
      intro === "Nice to meet you, Ada." &&
        Boolean(recognize?.includes("self-reported")) &&
        Boolean(forget?.includes("forgot")) &&
        after === "I haven't confirmed who I'm speaking with.",
      JSON.stringify({ intro, recognize, forget, after }),
    );

    const media = await new TalosMediaCapture({
      source: path.join(ROOT, "apps/desktop/resources/talos/av_capture.swift"),
      runtimeDir: path.join(captureDir, "native"),
      compiler: "swiftc",
    }).capture(1);
    check(
      "capture-deleted",
      media.mediaDeleted &&
        media.frameBase64.length > 1000 &&
        media.audioBase64.length > 1000,
      `${media.frameBase64.length}/${media.audioBase64.length} base64 chars`,
    );

    const scene = await classify("/talos/vision/scene", {
      image: media.frameBase64,
    });
    const sound = await classify("/talos/sound", { audio: media.audioBase64 });
    check(
      "vision-http",
      scene.status === 200 && typeof scene.body.description === "string",
      JSON.stringify(scene).slice(0, 200),
    );
    check(
      "sound-http",
      sound.status === 200 && Array.isArray(sound.body.events),
      JSON.stringify(sound).slice(0, 200),
    );

    const describeStarted = Date.now();
    const described = await fetch(`${BASE}/talos/vision/describe`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${TOKEN}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({
        image: media.frameBase64,
        question: "What do you see?",
      }),
    });
    const describedBody = (await described.json()) as {
      answer?: string;
      model?: string;
      error?: string;
    };
    check(
      "vision-describe",
      described.status === 200 &&
        typeof describedBody.answer === "string" &&
        describedBody.answer.length > 0,
      `${describedBody.model ?? "model"} ${Date.now() - describeStarted}ms · ${
        describedBody.answer ?? describedBody.error ?? ""
      }`.slice(0, 200),
    );

    const judged = await fetch(`${BASE}/talos/goal/judge`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${TOKEN}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({
        objective: "The task is complete.",
        reply: "I finished the task and the report is ready.",
      }),
    });
    const judgedBody = (await judged.json()) as {
      verdict?: string;
      reason?: string;
      error?: string;
    };
    check(
      "goal-judge",
      judged.status === 200 &&
        ["done", "continue", "blocked"].includes(judgedBody.verdict ?? ""),
      JSON.stringify(judgedBody).slice(0, 160),
    );

    const coding = await fetch(`${BASE}/talos/coding/execute`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${TOKEN}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({
        kind: "plan",
        request: "review the coding route",
      }),
    });
    const codingBody = (await coding.json()) as {
      status?: string;
      steps?: { target?: string }[];
    };
    check(
      "coding-execute",
      coding.status === 200 &&
        codingBody.status === "planned" &&
        codingBody.steps?.[0]?.id === "step_1",
      JSON.stringify(codingBody).slice(0, 200),
    );
  } finally {
    try {
      if (child.pid) process.kill(-child.pid, "SIGTERM");
    } catch {
      // already gone
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
    await rm(captureDir, { recursive: true, force: true });
  }

  if (process.env.TEST_DATABASE_URL && process.env.TEAMHUB_TEST_RUN_TOKEN) {
    try {
      await exec(
        process.env.GO_BIN ?? "go",
        [
          "test",
          "./tests/integration",
          "-run",
          "TestIdentityTextWritesAgentReply|TestSensorQuestionsWriteAgentReplies|TestCodingCommandsWriteAgentReplies|TestRoomCodingWorkerReportsAsAgent|TestRoomCodingClaimsRecoverAfterTheLease|TestRoomCodingWorkerRetriesTransportFailures|TestDuplicateCodingCommandsShareOneJob|TestRoomRoutineFiresAsAgentMessage|TestRoomReminderCommandCreatesAndFires|TestRoomCodingWorkerStopsRepeatedSandboxFailures|TestAgentOneShotPastGraceIsRetired|TestRoomPinCommandPinsForTheRequester",
          "-count=1",
        ],
        {
          cwd: path.join(ROOT, "apps/teamhub"),
          env: process.env,
          timeout: 300_000,
        },
      );
      check(
        "room-commands",
        true,
        "Postgres-backed Room identity and coding replies",
      );
    } catch (error) {
      check("room-commands", false, (error as Error).message);
    }
  }

  if (
    process.env.TALOS_LIVE_DOCUMENT_E2E !== "0" &&
    process.env.TEST_DATABASE_URL &&
    process.env.TEAMHUB_TEST_RUN_TOKEN
  ) {
    try {
      await exec(
        "pnpm",
        [
          "exec",
          "playwright",
          "test",
          "--config",
          "e2e/playwright.room-document.config.ts",
          "-g",
          "persists an anchored comment|renames the Document from its title",
        ],
        {
          cwd: ROOT,
          env: {
            ...process.env,
            TEAMHUB_ALLOW_EXTERNAL_TEST_DATABASE: "1",
          },
          timeout: 900_000,
          maxBuffer: 20 * 1024 * 1024,
        },
      );
      check(
        "room-document-e2e",
        true,
        "create, edit, comment and reopen in a real browser",
      );
    } catch (error) {
      check("room-document-e2e", false, (error as Error).message);
    }
  }

  if (process.env.TEST_DATABASE_URL && process.env.TEAMHUB_TEST_RUN_TOKEN) {
    try {
      await exec(
        process.env.GO_BIN ?? "go",
        [
          "test",
          "./tests/integration",
          "-run",
          "TestRoomThreadJourneyLive",
          "-count=1",
        ],
        {
          cwd: path.join(ROOT, "apps/teamhub"),
          env: process.env,
          timeout: 300_000,
        },
      );
      check(
        "room-thread-journey",
        true,
        "create, reply, parent summary and search",
      );
    } catch (error) {
      check("room-thread-journey", false, (error as Error).message);
    }
  }

  if (process.env.TEST_DATABASE_URL && process.env.TEAMHUB_TEST_RUN_TOKEN) {
    for (const [name, testName, detail] of [
      [
        "room-search-journey",
        "TestGlobalSearchMessagesPublicHTTP",
        "visibility, pagination and case-insensitive search",
      ],
      [
        "room-direct-journey",
        "TestPrivateAgentVisibilityAndDirectRoomLifecycle",
        "direct Room lifecycle and Agent visibility",
      ],
      [
        "room-proactive-journey",
        "TestProactiveWindowsAndQuietWakes",
        "proactive windows, quiet wakes and ordered delivery",
      ],
      [
        "room-memory-journey",
        "TestAgentMemoryScopePublication|TestAgentMemoryReadUsesRevisionColumn",
        "Agent memory publish, revision conflict and read",
      ],
      [
        "room-wiki-journey",
        "TestPersonalWikiInputTreeAndImmutableVersions|TestPersonalWikiMarkdownSourceUploadAdmitsRefresh",
        "Personal Wiki input tree, immutable versions and upload refresh",
      ],
      [
        "room-goal-journey",
        "TestRoomGoalCommandLifecycle",
        "standing goal set, status, pause, resume and clear",
      ],
      [
        "room-goal-judge",
        "TestRoomGoalJudgeLoop",
        "judge continue/done and the turn budget",
      ],
    ] as const) {
      try {
        await exec(
          process.env.GO_BIN ?? "go",
          ["test", "./tests/integration", "-run", testName, "-count=1"],
          {
            cwd: path.join(ROOT, "apps/teamhub"),
            env: process.env,
            timeout: 300_000,
          },
        );
        check(name, true, detail);
      } catch (error) {
        check(name, false, (error as Error).message);
      }
    }
  }

  if (process.env.TALOS_LIVE_AGENT === "1") {
    try {
      await exec("node", [path.join(ROOT, "scripts/talos-agent-live.mjs")], {
        cwd: ROOT,
        env: {
          ...process.env,
          TEAMHUB_BASE_URL:
            process.env.TALOS_LIVE_AGENT_BASE_URL ??
            process.env.TEAMHUB_BASE_URL ??
            "http://127.0.0.1:8080",
        },
        timeout: 900_000,
        maxBuffer: 4 * 1024 * 1024,
      });
      check(
        "agent-conversation",
        true,
        "model-generated Agent reply persisted in a Direct Room",
      );
    } catch (error) {
      check("agent-conversation", false, (error as Error).message);
    }
  } else {
    check(
      "agent-conversation",
      true,
      "skipped: set TALOS_LIVE_AGENT=1 with a running stack",
    );
  }

  try {
    await exec(
      process.env.GO_BIN ?? "go",
      [
        "test",
        "./internal/heartbeat",
        "./internal/app",
        "-run",
        "TestStripQuietAcknowledgements|TestStripSurfacesSubstantiveText|TestReviewedFallbackTextQuietAcknowledgements|TestReviewedAgentTextReply",
        "-count=1",
      ],
      {
        cwd: path.join(ROOT, "apps/teamhub"),
        env: process.env,
        timeout: 300_000,
      },
    );
    check(
      "heartbeat-quiet-guard",
      true,
      "HEARTBEAT_OK/NO_REPLY stays quiet; long reports surface",
    );
  } catch (error) {
    check("heartbeat-quiet-guard", false, (error as Error).message);
  }

  const codexModel = process.env.TALOS_OLLAMA_CODEX_MODEL ?? "qwen3:4b-8k";
  const hasCodex = await exec("which", ["codex"]).then(
    () => true,
    () => false,
  );
  const hasOllama = await exec("which", ["ollama"]).then(
    () => true,
    () => false,
  );
  if (hasCodex && hasOllama) {
    const codexHome = await mkdtemp(
      path.join(os.tmpdir(), "talos-ollama-codex-"),
    );
    try {
      await exec(
        "bash",
        [path.join(ROOT, "scripts/ollama-codex-home.sh"), codexHome],
        { cwd: ROOT, timeout: 120_000 },
      );
      const started = Date.now();
      const output = await runCodex(codexHome, codexModel);
      const compact = output.replace(/\s+/gu, "");
      const catalog = JSON.parse(
        await readFile(path.join(codexHome, "model_catalog.json"), "utf8"),
      ) as { models?: { slug?: string }[] };
      const catalogued = (catalog.models ?? []).some(
        (model) => model.slug === codexModel,
      );
      check(
        "ollama-codex-model",
        catalogued &&
          (compact.includes('"ok":true') || compact.includes("'ok':true")),
        `${codexModel} catalogued and answered in ${Date.now() - started}ms · ${output.slice(-100)}`,
      );
    } catch (error) {
      check("ollama-codex-model", false, (error as Error).message);
    } finally {
      await rm(codexHome, { recursive: true, force: true });
    }
  } else {
    check(
      "ollama-codex-model",
      true,
      "skipped: codex or ollama is not installed",
    );
  }

  check(
    "service-stopped",
    await noProcess("NODE_SERVICES_ROLES=identity,perception,coding"),
  );
  check(
    "no-audio-process",
    await noProcess("afplay|/usr/bin/say|kokoro|AVSpeech"),
  );
  check("no-livekit", await noProcess("livekit-server|voice-runtime"));

  let failed = false;
  for (const item of checks) {
    console.log(`${item.ok ? "PASS" : "FAIL"} ${item.name} · ${item.detail}`);
    failed ||= !item.ok;
  }
  return failed ? 1 : 0;
}

main().then(
  (code) => process.exit(code),
  (error) => {
    console.error(error);
    process.exit(1);
  },
);
