/**
 * POST /talos/coding/execute runs one Room coding job on this Computer.
 *
 * TeamHub owns the durable queue; this service owns execution. It never applies
 * a patch unless the caller explicitly asks, and every path stays inside the
 * configured workspace. A job without a drafted patch returns a bounded plan
 * so the Room still gets a real answer instead of an invented change.
 */
import { stat } from "node:fs/promises";
import path from "node:path";

import { createPlan, runCodingJob, type PatchDraft } from "@talos/coding-jobs";
import { serviceCredential } from "@talos/service-kit/node";
import type { JsonLogger } from "@talos/service-kit/node";

import type { HttpHost } from "../http-host.js";
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
  const plan = createPlan(
    job.kind === "plan" ? "plan_room" : "job_room",
    [
      {
        id: "step_1",
        ...(job.target ? { target: job.target } : {}),
        request: job.request,
      },
    ],
    { now, apply: false, request: job.request },
  );
  const steps = plan.steps.map((step) => ({
    id: step.id,
    target: step.targets.join(", "),
    request: step.request,
  }));
  if (!job.draft)
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
    targets: job.draft.patches.map((patch) => patch.target),
    draft: async () => job.draft as PatchDraft,
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
  };
}

/** Serves the coding prefix with a bearer token shared with TeamHub. */
export async function startCodingService(options: {
  http: HttpHost;
  logger: JsonLogger;
  environment?: NodeJS.ProcessEnv;
  workspace?: string;
  verify?: { file: string; args: readonly string[] };
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
            verify ? { verify } : {},
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

function isDraft(value: unknown): boolean {
  if (typeof value !== "object" || value === null) return false;
  const draft = value as Record<string, unknown>;
  return (
    Array.isArray(draft.patches) &&
    typeof draft.regressionPath === "string" &&
    typeof draft.regression === "string"
  );
}
