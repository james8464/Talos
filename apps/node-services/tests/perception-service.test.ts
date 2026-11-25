import { access } from "node:fs/promises";
import { serviceCredential, type JsonLogger } from "@talos/service-kit/node";
import { describe, expect, it, vi } from "vitest";

import { startPerceptionService } from "../src/perception/service.js";
import type { HttpHost, HttpRoute } from "../src/http-host.js";

const environment = {
  TALOS_SECRET_SEED: "perception-test-seed-0123456789abcdef",
};
const token = serviceCredential("node-services-internal", environment)!;
const JPEG = Buffer.from([0xff, 0xd8, 0xff, 0x00]).toString("base64");
const WAV = Buffer.concat([
  Buffer.from("RIFF"),
  Buffer.alloc(4),
  Buffer.from("WAVE"),
]).toString("base64");

async function fixture() {
  let route: HttpRoute | undefined;
  const unmount = vi.fn();
  const client = {
    scene: vi.fn(async (file: string) => {
      await access(file);
      return { people: 1, faces: 1, empty: false };
    }),
    sound: vi.fn(async (file: string) => {
      await access(file);
      return [{ label: "Beep", confidence: 0.9 }];
    }),
  };
  const service = await startPerceptionService({
    http: {
      mount: (mounted: HttpRoute) => {
        route = mounted;
        return unmount;
      },
    } as unknown as HttpHost,
    logger: { log: vi.fn() } as unknown as JsonLogger,
    environment,
    client,
  });
  return { route: route!, client, unmount, service };
}

const request = (path: string, body: unknown, auth = `Bearer ${token}`) =>
  new Request(`http://x${path}`, {
    method: "POST",
    headers: { authorization: auth, "content-type": "application/json" },
    body: JSON.stringify(body),
  });

describe("perception routes", () => {
  it("classifies a JPEG and a WAV", async () => {
    const { route, client } = await fixture();
    const scene = await route.fetch(
      request("/talos/vision/scene", { image: JPEG }),
    );
    expect(scene.status).toBe(200);
    expect(await scene.json()).toEqual({
      scene: { people: 1, faces: 1, empty: false },
      description: "I see one person.",
    });
    expect(client.scene).toHaveBeenCalledOnce();

    const sound = await route.fetch(request("/talos/sound", { audio: WAV }));
    expect(sound.status).toBe(200);
    expect(await sound.json()).toEqual({
      events: [{ label: "Beep", confidence: 0.9 }],
      description: "I heard Beep.",
    });
    expect(client.sound).toHaveBeenCalledOnce();
  });

  it("requires the service token and POST", async () => {
    const { route } = await fixture();
    expect(
      (
        await route.fetch(
          request("/talos/sound", { audio: WAV }, "Bearer nope"),
        )
      ).status,
    ).toBe(401);
    expect(
      (await route.fetch(new Request("http://x/talos/sound"))).status,
    ).toBe(401);
    const get = new Request("http://x/talos/sound", {
      headers: { authorization: `Bearer ${token}` },
    });
    expect((await route.fetch(get)).status).toBe(405);
  });

  it("rejects wrong media before classification", async () => {
    const { route, client } = await fixture();
    const bad = await route.fetch(
      request("/talos/vision/scene", {
        image: Buffer.from("nope").toString("base64"),
      }),
    );
    expect(bad.status).toBe(400);
    expect(await bad.json()).toEqual({ error: "Send a JPEG or PNG image." });
    expect(client.scene).not.toHaveBeenCalled();
  });
});
