import { describe, expect, it } from "vitest";

import {
  applyRepair,
  assertIndependentTargets,
  createPlan,
  enterStep,
  finalizePlan,
  normalizeSteps,
  orderSteps,
  recordRepairError,
  recordStepResult,
  remainingBudget,
  stepById,
} from "./plan.js";

const first = { id: "first", targets: ["talos_core/example.py"], request: "first" };
const second = {
  id: "second",
  targets: ["talos_core/other.py"],
  request: "second",
  needs: ["first"],
};

describe("normalizeSteps", () => {
  it("accepts target or targets and normalizes them", () => {
    const steps = normalizeSteps([
      { id: "a", target: "talos_core/a.py", request: "a" },
      { id: "b", targets: ["talos_core/b.py"], request: "b" },
    ]);
    expect(steps.map((step) => step.targets)).toEqual([
      ["talos_core/a.py"],
      ["talos_core/b.py"],
    ]);
    expect(steps.every((step) => step.status === "queued")).toBe(true);
  });

  it("rejects both target and targets, duplicates, cycles and unknown needs", () => {
    expect(() =>
      normalizeSteps([
        {
          id: "a",
          target: "talos_core/a.py",
          targets: ["talos_core/a.py"],
          request: "a",
        },
      ]),
    ).toThrow("both target and targets");
    expect(() =>
      normalizeSteps([
        { id: "a", targets: ["talos_core/a.py", "talos_core/a.py"], request: "a" },
      ]),
    ).toThrow("duplicate targets");
    expect(() =>
      normalizeSteps([
        { id: "a", targets: ["talos_core/a.py"], request: "a", needs: ["b"] },
        { id: "b", targets: ["talos_core/b.py"], request: "b", needs: ["a"] },
      ]),
    ).toThrow("cycle");
    expect(() =>
      normalizeSteps([
        { id: "a", targets: ["talos_core/a.py"], request: "a", needs: ["gone"] },
      ]),
    ).toThrow("unknown step");
  });

  it("enforces the source catalog when one is supplied", () => {
    expect(() =>
      normalizeSteps(
        [{ id: "a", targets: ["state/private.py"], request: "a" }],
        { allowedTargets: new Set(["talos_core/a.py"]) },
      ),
    ).toThrow("not in the source catalog");
  });
});

describe("ordering and conflicts", () => {
  it("returns a stable topological order and accepts completed ids", () => {
    const steps = normalizeSteps([second, first]);
    expect(orderSteps(steps)).toEqual(["first", "second"]);
    const secondStep = steps.find((step) => step.id === "second")!;
    expect(orderSteps([secondStep], new Set(["first"]))).toEqual(["second"]);
  });

  it("rejects independent same-file steps but allows sequential ones", () => {
    const shared = "talos_core/shared.py";
    expect(() =>
      assertIndependentTargets(
        normalizeSteps([
          { id: "a", targets: [shared], request: "a" },
          { id: "b", targets: [shared], request: "b" },
        ]),
      ),
    ).toThrow("both target");
    const sequential = normalizeSteps([
      { id: "a", targets: [shared], request: "a" },
      { id: "b", targets: [shared], request: "b", needs: ["a"] },
    ]);
    expect(() => assertIndependentTargets(sequential)).not.toThrow();
  });
});

describe("plan lifecycle", () => {
  const plan = () =>
    createPlan("plan-1", [first, second], { now: 100, budget: 60 });

  it("computes the shared budget and defers work when it is gone", () => {
    const p = plan();
    expect(p.totalBudget).toBe(180);
    expect(remainingBudget(p, 150)).toBe(130);
    const late = enterStep(p, "first", 400);
    expect(stepById(late, "first").status).toBe("deferred");
    expect(late.status).toBe("running");
  });

  it("records results and finalizes done or blocked", () => {
    let p = enterStep(plan(), "first", 101);
    p = recordStepResult(p, "first", { status: "verified", attempts: 1 });
    p = recordStepResult(p, "second", { status: "declined", attempts: 2 });
    expect(finalizePlan(p).status).toBe("blocked");
    let done = enterStep(plan(), "first", 101);
    done = recordStepResult(done, "first", { status: "verified" });
    done = recordStepResult(done, "second", { status: "verified" });
    expect(finalizePlan(done).status).toBe("done");
  });

  it("repairs at most twice and supersedes the failed step", () => {
    let p = enterStep(plan(), "first", 101);
    p = recordStepResult(p, "first", { status: "declined" });
    p = applyRepair(
      p,
      [{ id: "fixed-1", targets: ["talos_core/other.py"], request: "retry" }],
      110,
    );
    expect(p.revision).toBe(1);
    expect(stepById(p, "first").status).toBe("superseded");
    expect(p.repairs[0]).toMatchObject({ failed: "first", replacement: ["fixed-1"] });
    p = enterStep(p, "fixed-1", 111);
    p = recordStepResult(p, "fixed-1", { status: "declined" });
    p = applyRepair(
      p,
      [{ id: "fixed-2", targets: ["talos_core/other.py"], request: "retry again" }],
      120,
    );
    expect(p.revision).toBe(2);
    expect(() =>
      applyRepair(
        p,
        [{ id: "fixed-3", targets: ["talos_core/other.py"], request: "third" }],
        130,
      ),
    ).toThrow("revision cap");
  });

  it("rejects a repair that reuses an id or is out of budget", () => {
    let p = enterStep(plan(), "first", 101);
    p = recordStepResult(p, "first", { status: "declined" });
    expect(() =>
      applyRepair(p, [{ id: "second", targets: ["talos_core/other.py"], request: "x" }], 110),
    ).toThrow("reuses step id");
    expect(() =>
      applyRepair(p, [{ id: "fixed", targets: ["talos_core/other.py"], request: "x" }], 400),
    ).toThrow("budget exhausted");
    expect(recordRepairError(p, "no safe revision").repairError).toBe(
      "no safe revision",
    );
  });
});
