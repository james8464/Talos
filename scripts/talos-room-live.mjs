// Runs one real Room coding job against a running Talos stack and waits for
// the Agent's completion reply. Use it after a Local Computer or the local
// Ollama model is configured. It prints no credentials.
import { readFileSync } from "node:fs";

const base = process.env.TEAMHUB_BASE_URL ?? "http://127.0.0.1:8080";
const owner = JSON.parse(
  readFileSync(
    process.env.TALOS_LOCAL_OWNER_FILE ?? ".talos-runtime/local-owner.json",
    "utf8",
  ),
);
const target =
  process.env.TALOS_LIVE_CODING_TARGET ??
  "apps/teamhub/internal/roomsenses/answers.go";
const request =
  process.env.TALOS_LIVE_CODING_REQUEST ??
  "add a one-line comment above the Answer function";
const timeoutMs = Number(process.env.TALOS_LIVE_CODING_TIMEOUT_MS ?? 600_000);
let cookie = "";

async function call(method, path, body, key) {
  const response = await fetch(base + path, {
    method,
    headers: {
      "content-type": "application/json",
      ...(cookie ? { cookie } : {}),
      ...(key ? { "idempotency-key": key } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!cookie) {
    const set = response.headers.getSetCookie();
    if (set.length) cookie = set.map((value) => value.split(";")[0]).join("; ");
  }
  const text = await response.text();
  if (!response.ok)
    throw new Error(
      `${method} ${path} answered ${response.status}: ${text.slice(0, 200)}`,
    );
  return text === "" ? undefined : JSON.parse(text);
}

const login = await call("POST", "/v0/auth/login", {
  identifier: owner.owner_email,
  password: owner.owner_password,
});
const human = login.identity.actors.find((actor) => actor.kind === "human");
if (!human) throw new Error("The local owner has no human actor.");
const tenant = human.tenant_id;
const room = await call(
  "POST",
  `/v0/tenants/${tenant}/rooms`,
  {
    name: `Live coding proof ${new Date().toISOString()}`,
    description: "One real Room coding job",
  },
  `live-coding-room-${Date.now()}`,
);
const actors = await call("GET", `/v0/tenants/${tenant}/actors`);
const agents = Array.isArray(actors) ? actors : actors.items;
const agent =
  agents.find(
    (actor) => actor.kind === "agent" && actor.display_name === "Talos",
  ) ?? agents.find((actor) => actor.kind === "agent");
if (!agent) throw new Error("The Workspace has no Agent actor.");
await call("PUT", `/v0/rooms/${room.id}/members/${agent.id}`, {
  role: "member",
});

const text = `coding job ${target}: ${request}`;
await call(
  "POST",
  `/v0/rooms/${room.id}/messages`,
  { raw_message: text, segments: [{ type: "text", content: text }] },
  `live-coding-job-${Date.now()}`,
);
console.log(`room=${room.id} agent=${agent.id} target=${target}`);

const deadline = Date.now() + timeoutMs;
let completion;
while (Date.now() < deadline) {
  await new Promise((resolve) => setTimeout(resolve, 5_000));
  const page = await call("GET", `/v0/rooms/${room.id}/messages`);
  for (const item of page.items ?? page.messages ?? []) {
    const body = item.raw_message ?? item.body ?? "";
    if (
      typeof body === "string" &&
      /^Coding job /.test(body) &&
      !body.includes(" queued for ") &&
      !body.includes(" queued (verify-only)") &&
      !body.includes("retrying in") &&
      !body.includes("could not reach the Computer")
    ) {
      completion = {
        author: item.author?.display_name ?? item.author?.id,
        body,
      };
    }
  }
  if (completion) break;
}
if (!completion) {
  console.error("TIMEOUT: no completion reply before the deadline.");
  process.exit(1);
}
console.log(`reply[${completion.author}]: ${completion.body}`);
process.exit(completion.body.includes("verified in a sandbox") ? 0 : 1);
