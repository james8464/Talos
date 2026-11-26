import { describe, expect, it } from "vitest";

import { chooseTone, parseAmbientDecision } from "./talos-ambient.js";

describe("ambient decision", () => {
  it("accepts the three addressed values and trims the reply", () => {
    expect(
      parseAmbientDecision({ addressed: "yes", reply: "  On it.  " }),
    ).toEqual({ addressed: "yes", reply: "On it." });
    expect(parseAmbientDecision({ addressed: "no" })).toEqual({
      addressed: "no",
      reply: "",
    });
    expect(parseAmbientDecision({ addressed: "other" }).addressed).toBe(
      "other",
    );
    for (const value of [{}, { addressed: "maybe" }, null, "yes"]) {
      expect(() => parseAmbientDecision(value)).toThrow();
    }
  });

  it("keeps today's answers calm", () => {
    expect(chooseTone({ addressed: "yes", reply: "Hi" })).toBe("calm");
  });
});
