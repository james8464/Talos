// Sends one message to the Workspace Agent in a Direct Room and waits for its
// model-generated reply. Requires a running TeamHub, Agent Service and
// connected Local Computer with a model.
import { readFileSync } from "node:fs";

const base = process.env.TEAMHUB_BASE_URL ?? "http://127.0.0.1:8080";
const owner = JSON.parse(
  readFileSync(
    process.env.TALOS_LOCAL_OWNER_FILE ?? ".talos-runtime/local-owner.json",
    "utf8",
  ),
);
const text =
  process.env.TALOS_LIVE_AGENT_MESSAGE ??
  "Hello Talos. Introduce yourself in one short sentence.";
const timeoutMs = Number(process.env.TALOS_LIVE_AGENT_TIMEOUT_MS ?? 600_000);
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
  const raw = await response.text();
  if (!response.ok)
    throw new Error(
      `${method} ${path} answered ${response.status}: ${raw.slice(0, 200)}`,
    );
  return raw === "" ? undefined : JSON.parse(raw);
}

const login = await call("POST", "/v0/auth/login", {
  identifier: owner.owner_email,
  password: owner.owner_password,
});
const human = login.identity.actors.find((actor) => actor.kind === "human");
if (!human) throw new Error("The local owner has no human actor.");
const tenant = human.tenant_id;
const actors = await call("GET", `/v0/tenants/${tenant}/actors`);
const list = Array.isArray(actors) ? actors : actors.items;
const agent =
  list.find(
    (actor) => actor.kind === "agent" && actor.display_name === "Talos",
  ) ?? list.find((actor) => actor.kind === "agent");
if (!agent) throw new Error("The Workspace has no Agent actor.");

const room = await call(
  "POST",
  `/v0/tenants/${tenant}/direct-rooms`,
  { peer_actor_id: agent.id },
  `agent-live-room-${Date.now()}`,
);
const before = await call("GET", `/v0/rooms/${room.id}/messages?limit=50`);
const existing = new Set(
  (before.items ?? before.messages ?? []).map((item) => item.id),
);
const message = await call(
  "POST",
  `/v0/rooms/${room.id}/messages`,
  { raw_message: text, segments: [{ type: "text", content: text }] },
  `agent-live-message-${Date.now()}`,
);
console.log(`room=${room.id} agent=${agent.id} message=${message.id ?? ""}`);

const deadline = Date.now() + timeoutMs;
let reply;
while (Date.now() < deadline) {
  await new Promise((resolve) => setTimeout(resolve, 5_000));
  const page = await call("GET", `/v0/rooms/${room.id}/messages?limit=50`);
  for (const item of page.items ?? page.messages ?? []) {
    if (item.author?.id !== agent.id) continue;
    if (existing.has(item.id) || item.id === message.id) continue;
    const body = item.raw_message ?? item.body ?? "";
    if (typeof body === "string" && body.trim() !== "") reply = body;
  }
  if (reply) break;
}
if (!reply) {
  console.error("TIMEOUT: the Agent did not answer before the deadline.");
  process.exit(1);
}
console.log(`reply[${agent.display_name}]: ${reply}`);
console.log("AGENT_REPLY_FOUND");
