import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ScreenOverlay,
  type OverlayRect,
  type OverlayTarget,
  type OverlayWindowHandle,
  type ScreenOverlayDeps,
} from "./screen-overlay.js";

const area: OverlayRect = { x: 100, y: 50, width: 1200, height: 800 };
const target: OverlayTarget = {
  x: 250,
  y: 180,
  width: 320,
  height: 160,
  label: "Clicking Save",
};

function fixture(autoHideMs = 4_000) {
  const created: Parameters<ScreenOverlayDeps["createWindow"]>[0][] = [];
  const handle = {
    setBounds: vi.fn(),
    setIgnoreMouseEvents: vi.fn(),
    setAlwaysOnTop: vi.fn(),
    setVisibleOnAllWorkspaces: vi.fn(),
    loadURL: vi.fn(async (_url: string) => undefined),
    showInactive: vi.fn(),
    hide: vi.fn(),
    destroy: vi.fn(),
    isDestroyed: vi.fn(() => false),
  } satisfies OverlayWindowHandle;
  const deps: ScreenOverlayDeps = {
    createWindow: (options) => {
      created.push(options);
      return handle;
    },
    workAreaFor: () => area,
  };
  return { overlay: new ScreenOverlay({ autoHideMs, deps }), created, handle };
}

afterEach(() => vi.useRealTimers());

describe("ScreenOverlay", () => {
  it("creates one click-through, non-focusable window over the work area", async () => {
    const { overlay, created, handle } = fixture();
    await overlay.show(target);
    expect(created).toHaveLength(1);
    expect(created[0]).toMatchObject({
      x: area.x,
      y: area.y,
      width: area.width,
      height: area.height,
      frame: false,
      transparent: true,
      focusable: false,
      skipTaskbar: true,
      show: false,
    });
    expect(handle.setIgnoreMouseEvents).toHaveBeenCalledWith(true, {
      forward: true,
    });
    expect(handle.setAlwaysOnTop).toHaveBeenCalledWith(true, "screen-saver");
    expect(handle.showInactive).toHaveBeenCalledOnce();
    const url = String(handle.loadURL.mock.calls[0]?.[0]);
    const hash = JSON.parse(decodeURIComponent(url.split("#")[1]!));
    expect(hash).toEqual({
      x: target.x - area.x,
      y: target.y - area.y,
      width: target.width,
      height: target.height,
      label: "Clicking Save",
    });
  });

  it("reuses the window and hides itself after the timeout", async () => {
    vi.useFakeTimers();
    const { overlay, created, handle } = fixture(2_000);
    await overlay.show(target);
    await overlay.show({ ...target, x: 400 });
    expect(created).toHaveLength(1);
    expect(handle.setBounds).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(1_999);
    expect(handle.hide).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(handle.hide).toHaveBeenCalledOnce();
    overlay.hide();
    expect(handle.hide).toHaveBeenCalledOnce();
  });

  it("creates a new window after the old one was destroyed", async () => {
    const { overlay, created, handle } = fixture();
    await overlay.show(target);
    handle.isDestroyed.mockReturnValue(true);
    await overlay.show(target);
    expect(created).toHaveLength(2);
  });

  it("destroys the window on dispose", async () => {
    const { overlay, handle } = fixture();
    await overlay.show(target);
    overlay.dispose();
    expect(handle.destroy).toHaveBeenCalledOnce();
  });
});
