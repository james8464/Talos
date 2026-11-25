import { describe, expect, it } from "vitest";

import { IdentityClaims } from "../src/identity/claims.js";

describe("IdentityClaims", () => {
  it("remembers a self-reported name without claiming verification", () => {
    const claims = new IdentityClaims();
    expect(claims.answer("r1", "My name is Ada.", 1000)).toBe(
      "Nice to meet you, Ada.",
    );
    expect(claims.answer("r1", "Do you recognize me?", 2000)).toContain(
      "self-reported",
    );
    expect(claims.answer("r2", "Do you recognize me?", 2000)).toBe(
      "I haven't confirmed who I'm speaking with.",
    );
  });

  it("expires and can be forgotten", () => {
    const claims = new IdentityClaims(1000);
    claims.answer("r1", "My name is Ada.", 1000);
    expect(claims.answer("r1", "Do you recognize me?", 2500)).toBe(
      "I haven't confirmed who I'm speaking with.",
    );
    claims.answer("r1", "My name is Ada.", 3000);
    expect(claims.answer("r1", "Forget my name.", 3100)).toContain("forgot");
    expect(claims.answer("r1", "Do you recognize me?", 3200)).toBe(
      "I haven't confirmed who I'm speaking with.",
    );
  });

  it("leaves ordinary text alone", () => {
    expect(new IdentityClaims().answer("r1", "Play music.")).toBeNull();
  });
});
