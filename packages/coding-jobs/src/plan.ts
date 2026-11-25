/**
 * Bounded, dependency-ordered coding plans, ported from Talos.
 *
 * This module is pure: it validates and advances plan state. Running a step
 * (drafting a patch, verifying it in a sandbox, applying it) is the caller's
 * job. The guards match the Python implementation: bounded steps and targets,
 * catalog-only targets for generated plans, no independent same-file steps,
 * a shared wall-clock budget, and at most two repairs.
 */

export const MAX_STEPS = 8;
export const MAX_TARGETS = 4;
export const MAX_REVISIONS = 2;
export const MIN_SECONDS = 5;
export const MAX_PLAN_SECONDS = 3600;

export type StepStatus =
  | "queued"
  | "running"
  | "verified"
  | "applied"
  | "declined"
  | "deferred"
  | "blocked"
  | "superseded"
  | "rolled_back"
  | "error";

export interface StepInput {
  readonly id: string;
  readonly target?: string;
  readonly targets?: readonly string[];
  readonly request: string;
  readonly needs?: readonly string[];
}

export interface PlanStep {
  readonly id: string;
  readonly targets: readonly string[];
  readonly request: string;
  readonly needs: readonly string[];
  status: StepStatus;
  attempts: number;
  message: string;
  failure?: string;
}

export interface StepReceipt {
  readonly failed: string;
  readonly dropped: readonly string[];
  readonly replacement: readonly string[];
  readonly revision: number;
  readonly remainingBudget: number;
}

export interface Plan {
  readonly id: string;
  readonly request?: string;
  readonly apply: boolean;
  readonly budget: number;
  readonly totalBudget: number;
  readonly deadline: number;
  order: string[];
  steps: PlanStep[];
  status: "queued" | "running" | "done" | "blocked";
  revision: number;
  repair?: StepReceipt;
  repairs: StepReceipt[];
  repairError?: string;
}

export interface StepResult {
  readonly status: Exclude<
    StepStatus,
    "queued" | "running" | "blocked" | "superseded"
  >;
  readonly attempts?: number;
  readonly message?: string;
  readonly failure?: string;
}

const accepted = new Set<StepStatus>(["verified", "applied", "superseded"]);

function targetsOf(step: StepInput, maxTargets: number): string[] {
  if (step.target !== undefined && step.targets !== undefined)
    throw new Error(`Step ${step.id} cannot set both target and targets.`);
  const raw =
    step.targets !== undefined
      ? step.targets
      : step.target !== undefined
        ? [step.target]
        : [];
  if (!Array.isArray(raw) || raw.length < 1 || raw.length > maxTargets)
    throw new Error(`Step ${step.id} needs 1-${maxTargets} targets.`);
  if (raw.some((target) => typeof target !== "string" || !target.trim()))
    throw new Error(`Step ${step.id} has an invalid target.`);
  const targets = [...raw];
  if (new Set(targets).size !== targets.length)
    throw new Error(`Step ${step.id} has duplicate targets.`);
  return targets;
}

/** Validate inputs and return queued steps. Throws on the first violation. */
export function normalizeSteps(
  inputs: readonly StepInput[],
  options: {
    readonly maxSteps?: number;
    readonly maxTargets?: number;
    readonly allowedTargets?: ReadonlySet<string>;
    readonly completed?: ReadonlySet<string>;
  } = {},
): PlanStep[] {
  const maxSteps = options.maxSteps ?? MAX_STEPS;
  const maxTargets = options.maxTargets ?? MAX_TARGETS;
  if (!Array.isArray(inputs) || inputs.length < 1 || inputs.length > maxSteps)
    throw new Error(`A plan needs between 1 and ${maxSteps} steps.`);
  const steps = inputs.map((input): PlanStep => {
    if (!input || typeof input.id !== "string" || !input.id.trim())
      throw new Error("Each step needs a string id.");
    const request = (input.request ?? "").trim();
    if (!request || request.length > 800)
      throw new Error(`Step ${input.id} needs a request under 800 characters.`);
    const targets = targetsOf(input, maxTargets);
    if (options.allowedTargets)
      for (const target of targets)
        if (!options.allowedTargets.has(target))
          throw new Error(`${target} is not in the source catalog`);
    const needs = [...new Set<string>(input.needs ?? [])];
    if (needs.some((need) => typeof need !== "string" || !need))
      throw new Error(`Step ${input.id} needs a list of step ids.`);
    return {
      id: input.id,
      targets,
      request,
      needs,
      status: "queued",
      attempts: 0,
      message: "",
    };
  });
  orderSteps(steps, options.completed);
  return steps;
}

/** Stable topological order. `external` ids count as already satisfied. */
export function orderSteps(
  steps: readonly Pick<PlanStep, "id" | "needs">[],
  external: ReadonlySet<string> = new Set(),
): string[] {
  const ids = steps.map((step) => step.id);
  if (new Set(ids).size !== ids.length)
    throw new Error("Step ids must be unique.");
  const needs = new Map(steps.map((step) => [step.id, step.needs]));
  for (const [id, dependencies] of needs)
    for (const dependency of dependencies) {
      if (external.has(dependency)) continue;
      if (!needs.has(dependency))
        throw new Error(`Step ${id} needs unknown step ${dependency}.`);
      if (dependency === id)
        throw new Error(`Step ${id} cannot depend on itself.`);
    }
  const order: string[] = [];
  const placed = new Set<string>();
  while (order.length < steps.length) {
    const ready = ids.find(
      (id) =>
        !placed.has(id) &&
        needs.get(id)!.every((dep) => placed.has(dep) || external.has(dep)),
    );
    if (!ready) throw new Error("Step dependencies contain a cycle.");
    order.push(ready);
    placed.add(ready);
  }
  return order;
}

/** Two unrelated steps may not edit the same file; sequential ones may. */
export function assertIndependentTargets(steps: readonly PlanStep[]): void {
  const needs = new Map(steps.map((step) => [step.id, step.needs]));
  const dependsOn = (start: string, target: string): boolean => {
    const seen = new Set<string>();
    const stack = [...(needs.get(start) ?? [])];
    while (stack.length > 0) {
      const current = stack.pop()!;
      if (current === target) return true;
      if (needs.has(current) && !seen.has(current)) {
        seen.add(current);
        stack.push(...needs.get(current)!);
      }
    }
    return false;
  };
  const byTarget = new Map<string, string[]>();
  for (const step of steps)
    for (const target of step.targets) {
      const ids = byTarget.get(target) ?? [];
      ids.push(step.id);
      byTarget.set(target, ids);
    }
  for (const [target, ids] of byTarget)
    for (let left = 0; left < ids.length; left += 1)
      for (let right = left + 1; right < ids.length; right += 1) {
        const first = ids[left]!;
        const second = ids[right]!;
        if (!dependsOn(first, second) && !dependsOn(second, first))
          throw new Error(
            `independent steps ${first} and ${second} both target ${target}`,
          );
      }
}

export function createPlan(
  id: string,
  inputs: readonly StepInput[],
  options: {
    readonly budget?: number;
    readonly now: number;
    readonly apply?: boolean;
    readonly request?: string;
    readonly totalBudget?: number;
    readonly allowedTargets?: ReadonlySet<string>;
  },
): Plan {
  const budget = options.budget ?? 300;
  if (!Number.isInteger(budget) || budget < MIN_SECONDS || budget > 3600)
    throw new Error("Budget must be between 5 and 3600 seconds.");
  const steps = normalizeSteps(inputs, {
    allowedTargets: options.allowedTargets,
  });
  const totalBudget =
    options.totalBudget ??
    Math.min(MAX_PLAN_SECONDS, budget * Math.max(2, steps.length + 1));
  if (
    !Number.isInteger(totalBudget) ||
    totalBudget < MIN_SECONDS ||
    totalBudget > MAX_PLAN_SECONDS
  )
    throw new Error("Total plan budget must be between 5 and 3600 seconds.");
  return {
    id,
    request: options.request,
    apply: options.apply ?? false,
    budget,
    totalBudget,
    deadline: options.now + totalBudget,
    order: orderSteps(steps),
    steps,
    status: "queued",
    revision: 0,
    repairs: [],
  };
}

export function remainingBudget(plan: Plan, now: number): number {
  return Math.max(0, plan.deadline - now);
}

export function stepById(plan: Plan, id: string): PlanStep {
  const step = plan.steps.find((candidate) => candidate.id === id);
  if (!step) throw new Error(`Unknown step ${id}.`);
  return step;
}

/** Move a queued step to running, or to deferred when the plan is out of time. */
export function enterStep(plan: Plan, id: string, now: number): Plan {
  const next = structuredClone(plan);
  const step = stepById(next, id);
  if (step.status !== "queued") return next;
  next.status = "running";
  if (remainingBudget(next, now) < MIN_SECONDS) {
    step.status = "deferred";
    step.message = "plan budget exhausted";
    return next;
  }
  step.status = "running";
  return next;
}

export function recordStepResult(
  plan: Plan,
  id: string,
  result: StepResult,
): Plan {
  const next = structuredClone(plan);
  const step = stepById(next, id);
  step.status = result.status;
  step.attempts = result.attempts ?? step.attempts;
  step.message = (result.message ?? "").slice(0, 400);
  if (result.failure) step.failure = result.failure.slice(0, 1200);
  return next;
}

/**
 * Replace the failed step and all queued steps with a revised plan. Throws
 * when the revision cap, the shared budget, or step identity is violated.
 */
export function applyRepair(
  plan: Plan,
  inputs: readonly StepInput[],
  now: number,
  failedId?: string,
): Plan {
  if (plan.revision >= MAX_REVISIONS)
    throw new Error("plan revision cap reached");
  if (remainingBudget(plan, now) < MIN_SECONDS)
    throw new Error("plan budget exhausted");
  const completed = plan.steps
    .filter((step) => step.status === "verified" || step.status === "applied")
    .map((step) => step.id);
  const revised = normalizeSteps(inputs, {
    completed: new Set(completed),
  });
  const existing = new Set(plan.steps.map((step) => step.id));
  const clash = revised.find((step) => existing.has(step.id));
  if (clash) throw new Error(`revision reuses step id: ${clash.id}`);
  const next = structuredClone(plan);
  const failed =
    next.steps.find((step) => step.id === failedId) ??
    next.steps.find((step) =>
      ["declined", "error", "rolled_back", "deferred"].includes(step.status),
    );
  const dropped = next.steps
    .filter((step) => step.status === "queued")
    .map((step) => step.id);
  const executed = next.order.filter((id) => {
    const step = next.steps.find((candidate) => candidate.id === id);
    return step !== undefined && step.status !== "queued";
  });
  next.steps = [
    ...next.steps.filter((step) => step.status !== "queued"),
    ...revised,
  ];
  next.order = [...executed, ...orderSteps(revised, new Set(completed))];
  next.revision += 1;
  if (failed) failed.status = "superseded";
  const receipt: StepReceipt = {
    failed: failed?.id ?? "",
    dropped,
    replacement: revised.map((step) => step.id),
    revision: next.revision,
    remainingBudget: remainingBudget(next, now),
  };
  next.repair = receipt;
  next.repairs.push(receipt);
  return next;
}

export function recordRepairError(plan: Plan, message: string): Plan {
  const next = structuredClone(plan);
  next.repairError = message.slice(0, 400);
  return next;
}

export function finalizePlan(plan: Plan): Plan {
  const next = structuredClone(plan);
  next.status = next.steps.every((step) => accepted.has(step.status))
    ? "done"
    : "blocked";
  return next;
}
