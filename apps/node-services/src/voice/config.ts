import type { Region } from "@talos/service-kit";
import { serviceCredential } from "@talos/service-kit/node";
import { teamHubBaseUrl } from "../teamhub-base-url.js";

export interface VoiceConfig {
  readonly teamHubBaseUrl: string;
  /** Reads provider keys from TeamHub; undefined without TALOS_SECRET_SEED. */
  readonly teamHubCredential: string | undefined;
  /** Authenticates TeamHub's reconcile notifications. */
  readonly managementCredential: string | undefined;
  /**
   * The download region the installer resolved (SIMPLEX_REGION); model
   * packages come from its registry first. Global when unset.
   */
  readonly region: Region;
}

export function readVoiceConfig(environment: NodeJS.ProcessEnv): VoiceConfig {
  return Object.freeze({
    teamHubBaseUrl: teamHubBaseUrl(environment),
    teamHubCredential: serviceCredential("voice-runtime", environment),
    managementCredential: serviceCredential(
      "node-services-internal",
      environment,
    ),
    region: environment.SIMPLEX_REGION === "cn" ? "cn" : "global",
  });
}
