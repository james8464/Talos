// Boots TeamHub, the Agent Service and the Local Computer daemon against the
// prepared live database, then asks Talos one Direct Room question and asserts
// the persisted Agent reply. Everything runs with TALOS_MUTE=1.
//
// Prerequisites on this machine: PostgreSQL on 5433 with the live database,
// Ollama serving the model, the prepared Codex home and Computer state, and the
// local owner file the conversation script logs in with. Builds the bundled
// TeamHub CLI and daemon on every run.

import { spawn, execFile } from "node:child_process";
import { createHmac, hkdfSync } from "node:crypto";
import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import { promisify } from "node:util";

const exec = promisify(execFile);
const ROOT = path.resolve(import.meta.dirname, "..");
const DATABASE_URL =
  process.env.TALOS_AGENT_LIVE_DATABASE_URL ??
  "postgres://simplexai@127.0.0.1:5433/talos_ollama_live?sslmode=disable";
const SEED =
  process.env.TALOS_SECRET_SEED ??
  "local-development-secret-seed-not-for-production";
const TEAMHUB_PORT = Number(process.env.TALOS_AGENT_LIVE_TEAMHUB_PORT ?? 8090);
const AGENT_PORT = Number(process.env.TALOS_AGENT_LIVE_AGENT_PORT ?? 4319);
const CODEX_HOME =
  process.env.TALOS_AGENT_LIVE_CODEX_HOME ?? "/tmp/talos-ollama-codex-8b";
const COMPUTER_STATE =
  process.env.TALOS_AGENT_LIVE_COMPUTER_STATE ??
  "/tmp/talos-ollama-computer-state";
const TEAMHUB = `http://127.0.0.1:${TEAMHUB_PORT}`;
const GATEWAY = `ws://127.0.0.1:${AGENT_PORT}/v1/runner-gateway`;
const OLLAMA = process.env.TALOS_OLLAMA_URL ?? "http://127.0.0.1:11434";
const BUNDLE = path.join(ROOT, "apps/computer/dist");
const children = [];

function say(message) {
  console.log(`[agent-live] ${message}`);
}

function baseEnv(extra = {}) {
  return {
    ...process.env,
    TALOS_MUTE: "1",
    TALOS_SECRET_SEED: SEED,
    ...extra,
  };
}

function start(command, args, options) {
  const child = spawn(command, args, {
    detached: true,
    stdio: ["ignore", "pipe", "pipe"],
    ...options,
  });
  child.stdout?.on("data", (chunk) => process.stderr.write(chunk));
  child.stderr?.on("data", (chunk) => process.stderr.write(chunk));
  children.push(child);
  return child;
}

async function stop(child) {
  if (!child || child.exitCode !== null) return;
  try {
    process.kill(-child.pid, "SIGTERM");
  } catch {
    // Already gone.
  }
  await new Promise((resolve) => {
    const timer = setTimeout(() => {
      try {
        process.kill(-child.pid, "SIGKILL");
      } catch {
        // Already gone.
      }
      resolve();
    }, 10_000);
    child.once("exit", () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

const portOpen = (port) =>
  new Promise((resolve) => {
    const socket = net.connect({ host: "127.0.0.1", port });
    socket.setTimeout(500);
    socket.once("connect", () => {
      socket.destroy();
      resolve(true);
    });
    socket.once("timeout", () => {
      socket.destroy();
      resolve(false);
    });
    socket.once("error", () => resolve(false));
  });

async function waitFor(message, deadlineMs, probe) {
  const deadline = Date.now() + deadlineMs;
  while (Date.now() < deadline) {
    if (await probe()) return;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`timed out waiting for ${message}`);
}

async function httpOk(url) {
  try {
    return (await fetch(url, { signal: AbortSignal.timeout(2_000) })).ok;
  } catch {
    return false;
  }
}

async function buildBundle() {
  say("building the bundled TeamHub CLI and daemon");
  await exec("pnpm", ["--filter", "@talos/teamhub-cli", "build"], {
    cwd: ROOT,
    env: baseEnv(),
    timeout: 600_000,
  });
  await exec(
    process.execPath,
    [path.join(ROOT, "apps/computer/scripts/bundle-teamhub-cli.mjs")],
    { cwd: ROOT, env: baseEnv(), timeout: 600_000 },
  );
  const cli = path.join(BUNDLE, "cli");
  await mkdir(cli, { recursive: true });
  for (const entry of [
    "teamhub",
    "teamhub.js",
    "teamhub-assets.json",
    "teamhub-commands.json",
  ])
    await cp(path.join(BUNDLE, "bundle/cli", entry), path.join(cli, entry));
  await rm(path.join(cli, "teamhub-chunks"), { recursive: true, force: true });
  await cp(
    path.join(BUNDLE, "bundle/cli/teamhub-chunks"),
    path.join(cli, "teamhub-chunks"),
    { recursive: true },
  );
  await mkdir(path.join(BUNDLE, "daemon"), { recursive: true });
  const go = process.env.GO_BIN ?? "go";
  await exec(
    go,
    [
      "build",
      "-o",
      "../dist/daemon/talos-computer-daemon",
      "./cmd/talos-computer-daemon",
    ],
    {
      cwd: path.join(ROOT, "apps/computer/local-daemon"),
      env: baseEnv(),
      timeout: 600_000,
    },
  );
  await exec(
    go,
    ["build", "-o", "../dist/cli/talos-computer", "./cmd/talos-computer"],
    {
      cwd: path.join(ROOT, "apps/computer/local-daemon"),
      env: baseEnv(),
      timeout: 600_000,
    },
  );
}

// Re-signs the stored runner credential with this run's seed. TeamHub verifies
// the signature with the same seed, and a state directory prepared under an
// earlier seed would otherwise be rejected at the WebSocket upgrade.
async function resignRunnerToken() {
  const file = path.join(COMPUTER_STATE, "runner-token");
  const [prefix, payload] = (await readFile(file, "utf8")).trim().split(".");
  if (!prefix || !payload) throw new Error("the runner token is malformed");
  // TeamHub signs with the base64url service-credential string, not the raw
  // HKDF bytes.
  const key = Buffer.from(
    hkdfSync(
      "sha256",
      SEED,
      "simplexai/service-credential/v1",
      "runner-signing",
      32,
    ),
  ).toString("base64url");
  const signature = createHmac("sha256", key)
    .update(`${prefix}.${payload}`)
    .digest("base64url");
  await writeFile(file, `${prefix}.${payload}.${signature}\n`, { mode: 0o600 });
}

async function migrate() {
  say("migrating the live database");
  await exec(process.env.GO_BIN ?? "go", ["run", "./cmd/migrate"], {
    cwd: path.join(ROOT, "apps/teamhub"),
    env: baseEnv({ TEAMHUB_DATABASE_URL: DATABASE_URL }),
    timeout: 600_000,
  });
}

async function main() {
  await readFile(path.join(ROOT, ".talos-runtime/local-owner.json"), "utf8");
  await readFile(path.join(COMPUTER_STATE, "config.json"), "utf8");
  await readFile(path.join(COMPUTER_STATE, "runner-token"), "utf8");
  if (!(await httpOk(`${OLLAMA}/api/version`)))
    throw new Error(`Ollama is not answering at ${OLLAMA}`);
  for (const [name, port] of [
    ["TeamHub", TEAMHUB_PORT],
    ["Agent Service", AGENT_PORT],
  ]) {
    if (await portOpen(port))
      throw new Error(
        `${name} port ${port} is already in use; stop that process first`,
      );
  }

  await buildBundle();
  await migrate();

  say(`starting TeamHub on ${TEAMHUB}`);
  start(process.env.GO_BIN ?? "go", ["run", "./cmd/teamhub"], {
    cwd: path.join(ROOT, "apps/teamhub"),
    env: baseEnv({
      TEAMHUB_DATABASE_URL: DATABASE_URL,
      TEAMHUB_HTTP_ADDRESS: `127.0.0.1:${TEAMHUB_PORT}`,
      TEAMHUB_BASE_URL: TEAMHUB,
      TEAMHUB_NODE_SERVICES_URL: `http://127.0.0.1:${AGENT_PORT}`,
      AGENT_RUNNER_PUBLIC_GATEWAY_URL: GATEWAY,
      // Off by default so the check proves the model used the message command,
      // not the reviewed fallback.
      TALOS_AGENT_TEXT_REPLY: process.env.TALOS_AGENT_TEXT_REPLY ?? "0",
      GIN_MODE: "release",
    }),
  });
  await waitFor("TeamHub health", 120_000, () => httpOk(`${TEAMHUB}/healthz`));

  say(`starting the Agent Service on 127.0.0.1:${AGENT_PORT}`);
  start(process.execPath, ["--import", "tsx", "src/main.ts"], {
    cwd: path.join(ROOT, "apps/node-services"),
    env: baseEnv({
      NODE_SERVICES_ROLES: "agent",
      NODE_SERVICES_HTTP_ADDRESS: `127.0.0.1:${AGENT_PORT}`,
      TEAMHUB_BASE_URL: TEAMHUB,
      AGENT_RUNNER_PUBLIC_GATEWAY_URL: GATEWAY,
    }),
  });
  await waitFor("Agent Service health", 120_000, () =>
    httpOk(`http://127.0.0.1:${AGENT_PORT}/healthz`),
  );

  await resignRunnerToken();
  say("starting the Local Computer daemon");
  const computer = path.join(BUNDLE, "cli/talos-computer");
  await exec(
    computer,
    ["daemon", "start", "--config", path.join(COMPUTER_STATE, "config.json")],
    {
      cwd: ROOT,
      env: baseEnv({ CODEX_HOME, OLLAMA_API_KEY: "dummy" }),
      timeout: 120_000,
    },
  );
  await waitFor("the Computer connection", 120_000, async () => {
    try {
      const { stdout } = await exec(
        computer,
        [
          "daemon",
          "status",
          "--config",
          path.join(COMPUTER_STATE, "config.json"),
        ],
        { env: baseEnv(), timeout: 30_000 },
      );
      return stdout.includes("Connected");
    } catch {
      return false;
    }
  });

  say("asking Talos one Direct Room question");
  const { stdout } = await exec(
    process.execPath,
    [path.join(ROOT, "scripts/talos-agent-live.mjs")],
    {
      cwd: ROOT,
      env: baseEnv({
        TEAMHUB_BASE_URL: TEAMHUB,
        TALOS_LIVE_AGENT_TIMEOUT_MS:
          process.env.TALOS_LIVE_AGENT_TIMEOUT_MS ?? "900000",
      }),
      timeout: 1_000_000,
      maxBuffer: 4 * 1024 * 1024,
    },
  );
  const reply = stdout.split("\n").find((line) => line.startsWith("reply["));
  if (!stdout.includes("AGENT_REPLY_FOUND"))
    throw new Error("the Agent reply was not found");
  say(reply ?? "Agent reply found");
}

let failure;
try {
  await main();
} catch (error) {
  failure = error;
} finally {
  say("stopping the stack");
  try {
    await exec(
      path.join(BUNDLE, "cli/talos-computer"),
      ["daemon", "stop", "--config", path.join(COMPUTER_STATE, "config.json")],
      { env: baseEnv(), timeout: 60_000 },
    );
  } catch {
    // The daemon never started or already stopped.
  }
  for (const child of children.reverse()) await stop(child);
}

if (failure) {
  console.error(`[agent-live] FAILED: ${failure.message}`);
  for (const stream of [failure.stdout, failure.stderr]) {
    if (typeof stream === "string" && stream.trim())
      console.error(stream.trim().split("\n").slice(-20).join("\n"));
  }
  process.exit(1);
}
console.log("[agent-live] PASS");
