import { serviceCredential, type JsonLogger } from "@talos/service-kit/node";
import { describe, expect, it, vi } from "vitest";

import type { HttpHost, HttpRoute } from "../src/http-host.js";
import { startIdentityService } from "../src/identity/service.js";

const environment = {
  TALOS_SECRET_SEED: "identity-test-seed-0123456789abcdef",
};
const token = serviceCredential("node-services-internal", environment)!;

async function fixture() {
  let route: HttpRoute | undefined;
  const service = await startIdentityService({
    http: {
      mount: (mounted: HttpRoute) => {
        route = mounted;
        return vi.fn();
      },
    } as unknown as HttpHost,
    logger: { log: vi.fn() } as unknown as JsonLogger,
    environment,
  });
  return { route: route!, service };
}

const send = (body: unknown, auth = `Bearer ${token}`) =>
  new Request("http://x/talos/identity", {
    method: "POST",
    headers: { authorization: auth, "content-type": "application/json" },
    body: JSON.stringify(body),
  });

describe("identity route", () => {
  it("answers introduction, recognition and forgetting", async () => {
    const { route } = await fixture();
    const intro = await (
      await route.fetch(send({ room: "r1", text: "My name is Ada." }))
    ).json();
    expect(intro).toEqual({ reply: "Nice to meet you, Ada." });
    const recognized = await (
      await route.fetch(send({ room: "r1", text: "Do you recognize me?" }))
    ).json();
    expect(recognized.reply).toContain("self-reported");
    const forgot = await (
      await route.fetch(send({ room: "r1", text: "Forget my name." }))
    ).json();
    expect(forgot.reply).toContain("forgot");
    const after = await (
      await route.fetch(send({ room: "r1", text: "Do you recognize me?" }))
    ).json();
    expect(after.reply).toBe("I haven't confirmed who I'm speaking with.");
  });

  it("requires the service token and a room and text", async () => {
    const { route } = await fixture();
    expect(
      (await route.fetch(send({ room: "r1", text: "x" }, "Bearer nope")))
        .status,
    ).toBe(401);
    expect((await route.fetch(send({ text: "x" }))).status).toBe(400);
  });
});
