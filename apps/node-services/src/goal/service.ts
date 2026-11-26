/** POST /talos/goal/judge runs the local model judge for a Room goal. */
import { serviceCredential } from "@talos/service-kit/node";
import type { JsonLogger } from "@talos/service-kit/node";

import type { HttpHost } from "../http-host.js";
import { hasBearerToken } from "../service-bearer-auth.js";

export type GoalVerdict = "done" | "continue" | "blocked";

export interface GoalDecision {
  readonly verdict: GoalVerdict;
  readonly reason: string;
}

/** Parses one judge JSON object. */
export function parseGoalDecision(value: unknown): GoalDecision {
  if (typeof value !== "object" || value === null)
    throw new Error("The goal judge returned no object.");
  const record = value as Record<string, unknown>;
  if (
    record.verdict !== "done" &&
    record.verdict !== "continue" &&
    record.verdict !== "blocked"
  )
    throw new Error("The goal judge returned no supported verdict.");
  return {
    verdict: record.verdict,
    reason:
      typeof record.reason === "string" ? record.reason.slice(0, 500) : "",
  };
}

export async function judgeGoal(options: {
  baseUrl: string;
  model: string;
  objective: string;
  reply: string;
  timeoutMs?: number;
  fetch?: typeof fetch;
}): Promise<GoalDecision> {
  const response = await (options.fetch ?? fetch)(
    `${options.baseUrl.replace(/\/+$/, "")}/api/chat`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      signal: AbortSignal.timeout(options.timeoutMs ?? 120_000),
      body: JSON.stringify({
        model: options.model,
        stream: false,
        think: false,
        format: "json",
        messages: [
          {
            role: "system",
            content:
              "Judge whether an autonomous agent achieved a goal. Return one JSON object with verdict (done, continue, blocked) and reason. done only when the reply shows the deliverable exists; blocked when it explains an unachievable block or needs user input; otherwise continue.",
          },
          {
            role: "user",
            content: `Goal: ${options.objective}\n\nLatest reply:\n${options.reply}`,
          },
        ],
        options: { temperature: 0, num_predict: 300 },
      }),
    },
  );
  if (!response.ok)
    throw new Error(`The goal judge answered ${response.status}.`);
  const body = (await response.json()) as { message?: { content?: unknown } };
  const content =
    typeof body.message?.content === "string" ? body.message.content : "";
  return parseGoalDecision(JSON.parse(content));
}

export async function startGoalService(options: {
  http: HttpHost;
  logger: JsonLogger;
  environment?: NodeJS.ProcessEnv;
}): Promise<{ stop(): Promise<void> }> {
  const env = options.environment ?? process.env;
  const token = serviceCredential("node-services-internal", env);
  const baseUrl = env.TALOS_OLLAMA_URL ?? "http://127.0.0.1:11434";
  const model = env.TALOS_GOAL_MODEL ?? "qwen3:4b";
  const unmount = options.http.mount({
    prefixes: ["/talos/goal"],
    fetch: async (request) => {
      if (
        !token ||
        !hasBearerToken(
          request.headers.get("authorization") ?? undefined,
          token,
        )
      )
        return Response.json(
          { error: "A service token is required." },
          { status: 401 },
        );
      if (request.method !== "POST")
        return new Response(null, { status: 405, headers: { allow: "POST" } });
      try {
        const body = (await request.json()) as Record<string, unknown>;
        if (
          typeof body.objective !== "string" ||
          typeof body.reply !== "string"
        )
          throw new Error("Provide an objective and reply.");
        return Response.json(
          await judgeGoal({
            baseUrl,
            model,
            objective: body.objective.slice(0, 2000),
            reply: body.reply.slice(0, 4000),
          }),
        );
      } catch (error) {
        return Response.json(
          { error: error instanceof Error ? error.message : "Judge failed." },
          { status: 400 },
        );
      }
    },
  });
  options.logger.log("info", "goal.listening", {
    prefix: "/talos/goal",
    model,
  });
  return { stop: async () => unmount() };
}
