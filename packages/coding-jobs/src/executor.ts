/**
 * Bounded coding-job executor: draft, sandbox verify, retry once, and apply
 * with rollback. The model call and verify command are injected so this stays
 * testable and never shells out to a model by itself.
 */

import { execFile } from "node:child_process";
import {
  cp,
  mkdtemp,
  readFile,
  rm,
  writeFile,
  mkdir,
  stat,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";

const exec = promisify(execFile);

export interface FileChange {
  readonly target: string;
  readonly before: string;
  readonly after: string;
}

export interface PatchDraft {
  readonly patches: readonly FileChange[];
  readonly regressionPath: string;
  readonly regression: string;
}

export type DraftFunction = (
  prompt: string,
  feedback: string,
) => Promise<PatchDraft>;

export interface VerifyCommand {
  readonly file: string;
  readonly args: readonly string[];
}

export interface CodingJobOptions {
  readonly root: string;
  readonly request: string;
  readonly targets: readonly string[];
  readonly draft: DraftFunction;
  readonly verify: VerifyCommand;
  readonly apply?: boolean;
  readonly maxAttempts?: number;
  readonly budgetSeconds?: number;
  readonly now?: () => number;
}

export interface CodingJobResult {
  readonly status:
    | "verified"
    | "applied"
    | "rolled_back"
    | "declined"
    | "deferred";
  readonly attempts: number;
  readonly message: string;
  readonly failure?: string;
}

const COPY_SKIP = new Set([
  ".git",
  "node_modules",
  ".talos-runtime",
  "dist",
  "coverage",
]);

async function copyTree(source: string, destination: string): Promise<void> {
  await cp(source, destination, {
    recursive: true,
    filter: (entry) => {
      const relative = path.relative(source, entry);
      return !relative.split(path.sep).some((part) => COPY_SKIP.has(part));
    },
  });
}

function safeTarget(root: string, target: string): string {
  if (path.isAbsolute(target) || target.split(/[\\/]/).includes(".."))
    throw new Error("target must be a relative path inside the project");
  const full = path.resolve(root, target);
  if (!full.startsWith(path.resolve(root) + path.sep))
    throw new Error("target must be a relative path inside the project");
  return full;
}

async function applyDraft(
  folder: string,
  draft: PatchDraft,
  targets: readonly string[],
): Promise<void> {
  if (draft.patches.length !== targets.length)
    throw new Error("draft did not patch every target");
  const seen = new Set<string>();
  for (const patch of draft.patches) {
    if (!targets.includes(patch.target))
      throw new Error(`draft patched an unexpected target: ${patch.target}`);
    if (seen.has(patch.target))
      throw new Error(`draft patched ${patch.target} twice`);
    seen.add(patch.target);
    if (!patch.before.trim() || patch.before === patch.after)
      throw new Error(`no change for ${patch.target}`);
    const file = safeTarget(folder, patch.target);
    const source = await readFile(file, "utf8");
    const count = source.split(patch.before).length - 1;
    if (count !== 1)
      throw new Error(`${patch.target}: before text must occur exactly once`);
    await writeFile(file, source.replace(patch.before, patch.after), "utf8");
  }
  safeTarget(folder, draft.regressionPath);
  await mkdir(path.dirname(path.join(folder, draft.regressionPath)), {
    recursive: true,
  });
  await writeFile(
    path.join(folder, draft.regressionPath),
    draft.regression,
    "utf8",
  );
}

async function runVerify(
  folder: string,
  command: VerifyCommand,
): Promise<{ ok: boolean; output: string }> {
  try {
    const { stdout, stderr } = await exec(command.file, [...command.args], {
      cwd: folder,
      timeout: 120_000,
    });
    return { ok: true, output: `${stdout}${stderr}` };
  } catch (error) {
    const failure = error as {
      stdout?: string;
      stderr?: string;
      message?: string;
    };
    return {
      ok: false,
      output:
        `${failure.stdout ?? ""}${failure.stderr ?? ""}${failure.message ?? ""}`.slice(
          -2000,
        ),
    };
  }
}

export async function runCodingJob(
  options: CodingJobOptions,
): Promise<CodingJobResult> {
  const now = options.now ?? (() => Date.now() / 1000);
  const budget = options.budgetSeconds ?? 300;
  const deadline = now() + budget;
  const maxAttempts = Math.min(2, Math.max(1, options.maxAttempts ?? 2));
  const root = path.resolve(options.root);
  let feedback = "";
  let lastFailure = "";
  let attempts = 1;

  for (; attempts <= maxAttempts; attempts += 1) {
    if (deadline - now() < 1) {
      return {
        status: "deferred",
        attempts: attempts - 1,
        message: "budget exhausted",
      };
    }
    let draft: PatchDraft;
    try {
      draft = await options.draft(
        `Request: ${options.request}\nTargets: ${options.targets.join(", ")}${feedback}`,
        feedback,
      );
    } catch (error) {
      return {
        status: "declined",
        attempts,
        message: `draft failed: ${(error as Error).message}`,
      };
    }
    const sandbox = await mkdtemp(path.join(os.tmpdir(), "talos-job-"));
    try {
      await copyTree(root, path.join(sandbox, "work"));
      const folder = path.join(sandbox, "work");
      try {
        await applyDraft(folder, draft, options.targets);
      } catch (error) {
        return {
          status: "declined",
          attempts,
          message: (error as Error).message,
        };
      }
      const verification = await runVerify(folder, options.verify);
      if (!verification.ok) {
        lastFailure = verification.output;
        feedback = `\nThe previous patch failed verification:\n${lastFailure}`;
        continue;
      }
      if (!options.apply) {
        return {
          status: "verified",
          attempts,
          message: "sandbox passed",
        };
      }

      const originals = new Map<string, string>();
      {
        for (const patch of draft.patches) {
          const file = safeTarget(root, patch.target);
          originals.set(file, await readFile(file, "utf8"));
          const source = await readFile(
            path.join(folder, patch.target),
            "utf8",
          );
          await writeFile(file, source, "utf8");
        }
        const regressionFile = safeTarget(root, draft.regressionPath);
        await mkdir(path.dirname(regressionFile), { recursive: true });
        await writeFile(regressionFile, draft.regression, "utf8");
        const live = await runVerify(root, options.verify);
        if (!live.ok) {
          lastFailure = live.output;
          for (const [file, original] of originals)
            await writeFile(file, original, "utf8");
          await rm(regressionFile, { force: true });
          return {
            status: "rolled_back",
            attempts,
            message: "live verification failed; restored originals",
            failure: lastFailure,
          };
        }
        return { status: "applied", attempts, message: "verified and applied" };
      }
    } finally {
      await rm(sandbox, { recursive: true, force: true });
    }
  }
  return {
    status: "declined",
    attempts: maxAttempts,
    message: "verification failed",
    failure: lastFailure,
  };
}

/** True when a path exists; used by callers before enqueueing work. */
export async function exists(file: string): Promise<boolean> {
  try {
    await stat(file);
    return true;
  } catch {
    return false;
  }
}
