import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useT } from "@/i18n/use-translation";

import { settingsMessages } from "./settings.messages";

interface DesktopOverlay {
  show(target: {
    x: number;
    y: number;
    width: number;
    height: number;
    label?: string;
  }): Promise<void>;
}

function desktopOverlay(): DesktopOverlay | undefined {
  return typeof window === "undefined"
    ? undefined
    : (window as { talosDesktop?: { overlay?: DesktopOverlay } }).talosDesktop
        ?.overlay;
}

/** Previews the non-speaking highlight the Agent uses for screen actions. */
export function ScreenHighlightPreview() {
  const t = useT();
  const [api] = useState(desktopOverlay);
  if (!api) return null;

  const show = () => {
    const width = 420;
    const height = 240;
    void api.show({
      x: Math.round(window.screenX + (window.outerWidth - width) / 2),
      y: Math.round(window.screenY + (window.outerHeight - height) / 2),
      width,
      height,
      label: t(settingsMessages.screenHighlightLabel),
    });
  };

  return (
    <div className="mt-3 flex flex-col items-start gap-1">
      <Button onClick={show} size="compact" tone="ghost">
        {t(settingsMessages.screenHighlight)}
      </Button>
      <p className="px-2.5 text-xs text-app-text-muted">
        {t(settingsMessages.screenHighlightHint)}
      </p>
    </div>
  );
}
