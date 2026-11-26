/**
 * POST /talos/coding/execute runs one Room coding job on this Computer.
 *
 * TeamHub owns the durable queue; this service owns execution. It never applies
 * a patch unless the caller explicitly asks, and every path stays inside the
 * configured workspace. A job without a drafted patch returns a bounded plan
 * so the Room still gets a real answer instead of an invented change.
 */
import { readFile, stat } from "node:fs/promises";
import path from "node:path";

import {
  createPlan,
  runCodingJob,
  type CodingJobReceipt,
  type PatchDraft,
} from "@talos/coding-jobs";
import { serviceCredential } from "@talos/service-kit/node";
import type { JsonLogger } from "@talos/service-kit/node";

import type { HttpHost } from "../http-host.js";
import type { ModelCall, ModelOwner } from "../runtime-model-call.js";
import { hasBearerToken } from "../service-bearer-auth.js";

export interface CodingJobRequest {
  readonly kind: "job" | "plan";
  readonly target?: string;
  readonly request: string;
  readonly draft?: PatchDraft;
}

export interface CodingJobOutcome {
  readonly status: "planned" | "verified" | "declined" | "deferred" | "failed";
  readonly message: string;
  readonly steps?: readonly {
    readonly id: string;
    readonly target: string;
    readonly request: string;
  }[];
  readonly failure?: string;
  /** The model that drafted the verified patch, when one did. */
  readonly model?: string;
  /** Hashes and sizes of the verified change, when one was drafted. */
  readonly receipt?: CodingJobReceipt;
}

/** What a drafter sees: one target file and the request for it. */
export interface CodingDraftInput {
  readonly target: string;
  readonly request: string;
  readonly source: string;
}

/** A drafter returns one bounded patch and the model that produced it. */
export type CodingDrafter = (
  input: CodingDraftInput,
) => Promise<{ readonly draft: PatchDraft; readonly model: string }>;

/** Larger files are declined rather than sent to a model wholesale. */
export const MAX_DRAFT_SOURCE_CHARS = 200_000;

const draftSchema = {
  type: "object",
  additionalProperties: false,
  required: ["before", "after", "regression_path", "regression"],
  properties: {
    before: { type: "string", minLength: 1 },
    after: { type: "string", minLength: 1 },
    regression_path: { type: "string", minLength: 1 },
    regression: { type: "string", minLength: 1 },
  },
} as const;

/**
 * Drafts one exact-substring patch for a target file. The model never writes
 * the workspace: the sandbox verifier applies and checks the returned patch.
 */
export function modelDrafter(
  model: ModelCall,
  options: { readonly key: string; readonly owner?: ModelOwner },
): CodingDrafter {
  return async ({ target, request, source }) => {
    const answer = await model.call({
      kind: "coding",
      key: options.key,
      effort: "medium",
      ...(options.owner ? { owner: options.owner } : {}),
      instructions:
        "Edit exactly one file. Return `before`, an exact substring that occurs once in the file, and `after`, its replacement. Add a small regression test file under a relative path. Make the smallest change that satisfies the request. Do not include unrelated edits.",
      input: `Request: ${request}\nTarget: ${target}\n\n<file path="${target}">\n${source}\n</file>`,
      schemaName: "coding_patch",
      outputSchema: draftSchema,
      validate: (output) => validateDraft(output, target, source),
    });
    return {
      draft: {
        patches: [
          {
            target,
            before: answer.output.before as string,
            after: answer.output.after as string,
          },
        ],
        regressionPath: answer.output.regression_path as string,
        regression: answer.output.regression as string,
      },
      model: answer.model,
    };
  };
}

/**
 * Drafts the same one-file patch through a local Ollama model. This keeps
 * Room coding jobs free and available when no Agent Runtime can serve them.
 */
export function ollamaDrafter(options: {
  readonly baseUrl: string;
  readonly model: string;
  readonly timeoutMs?: number;
  readonly fetch?: typeof fetch;
}): CodingDrafter {
  const baseUrl = options.baseUrl.replace(/\/+$/, "");
  const doFetch = options.fetch ?? fetch;
  const timeoutMs = options.timeoutMs ?? 180_000;
  return async ({ target, request, source }) => {
    let feedback = "";
    let lastReason = "";
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const response = await doFetch(`${baseUrl}/api/chat`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        signal: AbortSignal.timeout(timeoutMs),
        body: JSON.stringify({
          model: options.model,
          stream: false,
          think: false,
          format: "json",
          messages: [
            {
              role: "system",
              content:
                "You edit one file. Return one JSON object with string keys before, after, regression_path and regression. `before` must occur exactly once in the file; `after` replaces it. Add a small regression file under a relative path. Make the smallest change that satisfies the request.",
            },
            {
              role: "user",
              content: `Request: ${request}\nTarget: ${target}\n\n<file path="${target}">\n${source}\n</file>${feedback}`,
            },
          ],
          options: { temperature: 0, num_predict: 900 },
        }),
      });
      if (!response.ok)
        throw new Error(`The local coding model answered ${response.status}.`);
      const body = (await response.json()) as {
        message?: { content?: unknown };
      };
      const raw =
        typeof body.message?.content === "string" ? body.message.content : "";
      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(raw) as Record<string, unknown>;
      } catch {
        lastReason = "The answer was not a JSON object.";
        feedback = `\nYour previous answer was rejected: ${lastReason}`;
        continue;
      }
      const reason = validateDraft(parsed, target, source);
      if (reason) {
        lastReason = reason;
        feedback = `\nYour previous answer was rejected: ${reason}`;
        continue;
      }
      return {
        draft: {
          patches: [
            {
              target,
              before: parsed.before as string,
              after: parsed.after as string,
            },
          ],
          regressionPath: parsed.regression_path as string,
          regression: parsed.regression as string,
        },
        model: options.model,
      };
    }
    throw new Error(lastReason || "The local coding model returned no patch.");
  };
}

function validateDraft(
  output: Record<string, unknown>,
  target: string,
  source: string,
): string | undefined {
  const before = output.before;
  const after = output.after;
  if (typeof before !== "string" || typeof after !== "string")
    return "before and after must be strings.";
  if (before === after) return "before and after must differ.";
  if (source.split(before).length - 1 !== 1)
    return `before must occur exactly once in ${target}.`;
  const regressionPath = output.regression_path;
  if (
    typeof regressionPath !== "string" ||
    path.isAbsolute(regressionPath) ||
    regressionPath.split(/[\\/]/).includes("..")
  )
    return "regression_path must be a relative path inside the workspace.";
  return undefined;
}

/** Resolves a relative target inside the workspace or throws. */
export function workspaceTarget(workspace: string, target: string): string {
  if (
    !target ||
    path.isAbsolute(target) ||
    target.split(/[\\/]/).includes("..")
  )
    throw new Error("target must be a relative path inside the workspace");
  const root = path.resolve(workspace);
  const full = path.resolve(root, target);
  if (!full.startsWith(root + path.sep))
    throw new Error("target must be a relative path inside the workspace");
  return full;
}

/** Plans a job; a draft additionally asks for a sandbox verification. */
export async function executeCodingJob(
  workspace: string,
  job: CodingJobRequest,
  options: {
    readonly now?: number;
    readonly verify?: { file: string; args: readonly string[] };
    readonly drafter?: CodingDrafter;
    readonly localDrafter?: CodingDrafter;
  } = {},
): Promise<CodingJobOutcome> {
  const now = options.now ?? Math.floor(Date.now() / 1000);
  const verify = options.verify;
  if (job.request.trim().length === 0 || job.request.length > 800)
    return {
      status: "declined",
      message: "A job needs a request under 800 characters.",
    };
  if (job.target) {
    const full = workspaceTarget(workspace, job.target);
    const entry = await stat(full).catch(() => undefined);
    if (!entry?.isFile())
      return {
        status: "declined",
        message: `${job.target} is not a file in the workspace.`,
      };
  }
  // A request-only plan has no file to guard; it still names its one step.
  const steps = job.target
    ? createPlan(
        job.kind === "plan" ? "plan_room" : "job_room",
        [{ id: "step_1", target: job.target, request: job.request }],
        { now, apply: false, request: job.request },
      ).steps.map((step) => ({
        id: step.id,
        target: step.targets.join(", "),
        request: step.request,
      }))
    : [{ id: "step_1", target: "", request: job.request }];
  let draft = job.draft;
  let draftedBy = "";
  if (!draft && job.kind === "job" && job.target && !verify)
    return {
      status: "declined",
      message: "No verify command is configured on this Computer.",
      steps,
    };
  const primaryDrafter = options.drafter ?? options.localDrafter;
  if (!draft && job.kind === "job" && job.target && primaryDrafter) {
    const source = await readFile(
      workspaceTarget(workspace, job.target),
      "utf8",
    );
    if (source.length > MAX_DRAFT_SOURCE_CHARS)
      return {
        status: "declined",
        message: `${job.target} is too large to draft in one patch.`,
        steps,
      };
    const input = { target: job.target, request: job.request, source };
    try {
      const drafted = await primaryDrafter(input);
      draft = drafted.draft;
      draftedBy = drafted.model;
    } catch (error) {
      const primaryReason =
        error instanceof Error ? error.message : "unknown error";
      if (options.drafter && options.localDrafter) {
        try {
          const drafted = await options.localDrafter(input);
          draft = drafted.draft;
          draftedBy = drafted.model;
        } catch (fallbackError) {
          return {
            status: "declined",
            message: `The model could not draft a patch: ${primaryReason}; local fallback: ${fallbackError instanceof Error ? fallbackError.message : "unknown error"}`,
            steps,
          };
        }
      } else {
        return {
          status: "declined",
          message: `The model could not draft a patch: ${primaryReason}`,
          steps,
        };
      }
    }
  }
  if (!draft)
    return {
      status: "planned",
      message: `Bounded plan prepared for ${job.target ?? "the request"}; no patch was applied.`,
      steps,
    };
  if (!verify)
    return {
      status: "declined",
      message: "No verify command is configured on this Computer.",
      steps,
    };
  const result = await runCodingJob({
    root: workspace,
    request: job.request,
    targets: draft.patches.map((patch) => patch.target),
    draft: async () => draft,
    verify,
    apply: false,
  });
  return {
    status:
      result.status === "verified"
        ? "verified"
        : result.status === "declined"
          ? "declined"
          : "failed",
    message: result.message,
    steps,
    ...(result.failure ? { failure: result.failure } : {}),
    ...(result.receipt ? { receipt: result.receipt } : {}),
    ...(draftedBy ? { model: draftedBy } : {}),
  };
}

/** Serves the coding prefix with a bearer token shared with TeamHub. */
export async function startCodingService(options: {
  http: HttpHost;
  logger: JsonLogger;
  environment?: NodeJS.ProcessEnv;
  workspace?: string;
  verify?: { file: string; args: readonly string[] };
  /** The Agent role's model seam; absent, the local model drafts. */
  modelCall?: () => ModelCall | undefined;
  /** Overrides the local model drafter in tests. */
  localDrafter?: CodingDrafter;
}): Promise<{ stop(): Promise<void> }> {
  const env = options.environment ?? process.env;
  const verify = options.verify ?? readVerify(env);
  const token = serviceCredential("node-services-internal", env);
  const workspace = path.resolve(
    options.workspace ?? env.TALOS_CODING_ROOT ?? process.cwd(),
  );
  const unmount = options.http.mount({
    prefixes: ["/talos/coding"],
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
        if (body.kind !== "job" && body.kind !== "plan")
          throw new Error('kind must be "job" or "plan".');
        if (typeof body.request !== "string")
          throw new Error("Provide a request.");
        if (body.target !== undefined && typeof body.target !== "string")
          throw new Error("target must be a string.");
        if (body.draft !== undefined && !isDraft(body.draft))
          throw new Error(
            "draft must carry patches, regression_path and regression.",
          );
        const model = options.modelCall?.();
        const owner = readOwner(body);
        const drafter = model
          ? modelDrafter(model, {
              key:
                typeof body.job_id === "string"
                  ? `coding:${body.job_id}`
                  : `coding:${body.target ?? "request"}`,
              ...(owner ? { owner } : {}),
            })
          : undefined;
        const localDrafter =
          options.localDrafter ??
          ollamaDrafter({
            baseUrl: env.TALOS_OLLAMA_URL ?? "http://127.0.0.1:11434",
            model: env.TALOS_CODING_MODEL ?? "qwen3:4b",
          });
        return Response.json(
          await executeCodingJob(
            workspace,
            {
              kind: body.kind,
              request: body.request,
              ...(typeof body.target === "string"
                ? { target: body.target }
                : {}),
              ...(body.draft !== undefined
                ? { draft: body.draft as PatchDraft }
                : {}),
            },
            {
              ...(verify ? { verify } : {}),
              localDrafter,
              ...(drafter ? { drafter } : {}),
            },
          ),
        );
      } catch (error) {
        return Response.json(
          {
            error: error instanceof Error ? error.message : "Invalid request.",
          },
          { status: 400 },
        );
      }
    },
  });
  options.logger.log("info", "coding.listening", {
    prefix: "/talos/coding",
    workspace,
  });
  return { stop: async () => unmount() };
}

/** TALOS_CODING_VERIFY is a JSON argv array, never a shell string. */
function readVerify(env: NodeJS.ProcessEnv) {
  const raw = env.TALOS_CODING_VERIFY?.trim();
  if (!raw) return undefined;
  const argv = JSON.parse(raw) as unknown;
  if (
    !Array.isArray(argv) ||
    argv.length < 1 ||
    argv.some((part) => typeof part !== "string")
  )
    throw new Error("TALOS_CODING_VERIFY must be a JSON array of strings.");
  return { file: argv[0] as string, args: argv.slice(1) as string[] };
}

function readOwner(body: Record<string, unknown>): ModelOwner | undefined {
  if (
    typeof body.tenant_id !== "string" ||
    typeof body.requester_actor_id !== "string"
  )
    return undefined;
  return { tenantId: body.tenant_id, ownerActorId: body.requester_actor_id };
}

function isDraft(value: unknown): boolean {
  if (typeof value !== "object" || value === null) return false;
  const draft = value as Record<string, unknown>;
  return (
    Array.isArray(draft.patches) &&
    typeof draft.regressionPath === "string" &&
    typeof draft.regression === "string"
  );
}
