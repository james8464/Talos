import { useState } from "react";

import { Button } from "@/components/ui/button";
import { teamHubDone } from "@/features/teamhub/teamhub-api";
import { useTeamHubRuntime } from "@/features/teamhub/teamhub-runtime";
import { useT } from "@/i18n/use-translation";

import { settingsMessages } from "./settings.messages";

// The desktop app's one-frame/one-clip capture check. The app handles the
// camera and microphone permissions and deletes the temporary media.
interface DesktopCapture {
  check(): Promise<{
    frameBytes: number;
    audioBytes: number;
    mediaDeleted: boolean;
    /** Counts from the captured frame; absent when classification was unavailable. */
    scene?: { people: number; faces: number } | null;
  }>;
}

function desktopCapture(): DesktopCapture | undefined {
  return typeof window === "undefined"
    ? undefined
    : (window as { talosDesktop?: { capture?: DesktopCapture } }).talosDesktop
        ?.capture;
}

export function CaptureCheckButton() {
  const t = useT();
  const { client } = useTeamHubRuntime();
  const [api] = useState(desktopCapture);
  const [running, setRunning] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  if (typeof api?.check !== "function") return null;

  const run = () => {
    setFailed(false);
    setMessage(null);
    setRunning(true);
    api
      .check()
      .then(async (result) => {
        // Presence stays optional: a failed reading never hides the capture
        // result the person asked for.
        if (result.scene)
          await teamHubDone(
            client.api.POST("/v0/presence-readings", {
              body: {
                people: result.scene.people,
                faces: result.scene.faces,
              },
            }),
          ).catch(() => undefined);
        setMessage(
          t(settingsMessages.captureCheckDone, {
            frame: result.frameBytes,
            audio: result.audioBytes,
          }),
        );
      })
      .catch(() => setFailed(true))
      .finally(() => setRunning(false));
  };

  return (
    <div className="mt-3 flex flex-col items-start gap-1">
      <Button loading={running} onClick={run} size="compact" tone="ghost">
        {t(settingsMessages.captureCheck)}
      </Button>
      {message ? (
        <p className="px-2.5 text-xs text-app-text-muted">{message}</p>
      ) : null}
      {failed ? (
        <p className="px-2.5 text-xs text-app-danger" role="alert">
          {t(settingsMessages.captureCheckFailed)}
        </p>
      ) : null}
    </div>
  );
}
