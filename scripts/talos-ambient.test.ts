import { describe, expect, it } from "vitest";

import { parseAddressDecision, parseAmbientAction } from "./talos-ambient.js";

describe("ambient decision", () => {
  it("accepts the three addressed values and trims the reply", () => {
    expect(
      parseAddressDecision({ addressed: "yes", tone: "menacing" }),
    ).toEqual({ addressed: "yes", tone: "menacing" });
    expect(parseAddressDecision({ addressed: "no" })).toEqual({
      addressed: "no",
      tone: "calm",
    });
    expect(parseAddressDecision({ addressed: "other" }).addressed).toBe(
      "other",
    );
    for (const value of [{}, { addressed: "maybe" }, null, "yes"]) {
      expect(() => parseAddressDecision(value)).toThrow();
    }
  });
});

describe("ambient computer actions", () => {
  it("accepts only the two supported tools", () => {
    expect(
      parseAmbientAction({
        action: { tool: "launch_app", arguments: { name: "TextEdit" } },
      }),
    ).toEqual({ tool: "launch_app", arguments: { name: "TextEdit" } });
    expect(parseAmbientAction({ action: null })).toBeNull();
    expect(
      parseAmbientAction({ action: { tool: "delete_file", arguments: {} } }),
    ).toBeNull();
    expect(parseAmbientAction({})).toBeNull();
  });
});
