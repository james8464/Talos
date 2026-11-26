import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  desktopPermissions,
  type DesktopPermissions,
  type TalosSystemPermission,
} from "@/features/local-computer/desktop-computer";
import { useT } from "@/i18n/use-translation";

import { settingsMessages } from "./settings.messages";

/** Reads the macOS camera and microphone state without capturing anything. */
export function MediaPermissionReadiness() {
  const t = useT();
  const [api] = useState<DesktopPermissions | undefined>(desktopPermissions);
  const [states, setStates] = useState<TalosSystemPermission[] | null>(null);

  useEffect(() => {
    if (!api?.status) return;
    let active = true;
    void api
      .status()
      .then((next) => {
        if (active) setStates(next);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [api]);

  if (!api?.status || !states) return null;
  const media = states.filter(
    (permission) =>
      permission.id === "camera" || permission.id === "microphone",
  );
  if (media.length === 0) return null;

  const allow = (id: "camera" | "microphone") => {
    void api
      .act({ action: "prompt", subject: "talos", permission: id })
      .then(() => api.status?.())
      .then((next) => {
        if (next) setStates(next);
      })
      .catch(() => undefined);
  };

  return (
    <div className="mt-3 flex flex-col gap-1">
      <p className="text-xs text-app-text-muted">
        {t(settingsMessages.mediaReadiness)}
      </p>
      {media.map((permission) => (
        <div className="flex items-center gap-2 text-xs" key={permission.id}>
          <span>
            {t(
              permission.id === "camera"
                ? settingsMessages.cameraPermission
                : settingsMessages.microphonePermission,
            )}
            : {t(permissionLabel(permission.state))}
          </span>
          {permission.state === "granted" ? null : (
            <Button
              onClick={() => allow(permission.id as "camera" | "microphone")}
              size="compact"
              tone="ghost"
            >
              {t(settingsMessages.allowPermission)}
            </Button>
          )}
        </div>
      ))}
    </div>
  );
}

function permissionLabel(state: TalosSystemPermission["state"]) {
  switch (state) {
    case "granted":
      return settingsMessages.permissionGranted;
    case "denied":
    case "not_determined":
      return settingsMessages.permissionNotGranted;
    default:
      return settingsMessages.permissionUnknown;
  }
}
