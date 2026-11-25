import { serviceCredential } from "@talos/service-kit/node";
import type { JsonLogger } from "@talos/service-kit/node";

import { hasBearerToken } from "../service-bearer-auth.js";
import type { HttpHost } from "../http-host.js";
import { IdentityClaims } from "./claims.js";

/** POST /talos/identity { room, text, now? }; deterministic room commands. */
export async function startIdentityService(options: {
  http: HttpHost;
  logger: JsonLogger;
  environment?: NodeJS.ProcessEnv;
  claims?: IdentityClaims;
}): Promise<{ stop(): Promise<void> }> {
  const env = options.environment ?? process.env;
  const token = serviceCredential("node-services-internal", env);
  const claims = options.claims ?? new IdentityClaims();
  const unmount = options.http.mount({
    prefixes: ["/talos/identity"],
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
        if (typeof body.room !== "string" || typeof body.text !== "string")
          throw new Error("Provide a room and text.");
        const now = typeof body.now === "number" ? body.now : undefined;
        return Response.json({
          reply: claims.answer(body.room, body.text, now),
        });
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
  options.logger.log("info", "identity.listening", {
    prefix: "/talos/identity",
  });
  return { stop: async () => unmount() };
}
