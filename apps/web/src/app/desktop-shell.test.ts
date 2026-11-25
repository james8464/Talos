// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";

import { isDesktopShell, markDesktopShell } from "./desktop-shell";

const desktop = window as { talosDesktop?: unknown };

describe("desktop shell", () => {
  afterEach(() => {
    delete desktop.talosDesktop;
  });

  it("leaves the web layout alone in a browser", () => {
    const root = document.createElement("html");
    markDesktopShell(root);
    expect(isDesktopShell()).toBe(false);
    expect(root).not.toHaveAttribute("data-desktop-shell");
  });

  it("marks the page inside the desktop app", () => {
    desktop.talosDesktop = {};
    const root = document.createElement("html");
    markDesktopShell(root);
    expect(isDesktopShell()).toBe(true);
    expect(root).toHaveAttribute("data-desktop-shell", "");
  });
});
