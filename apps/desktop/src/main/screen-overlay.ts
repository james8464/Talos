import { OVERLAY_HTML } from "./screen-overlay-html.js";

/** One screen region to outline while Talos acts. Coordinates are screen
 * points, as the computer tools report them. */
export interface OverlayTarget {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly label?: string;
}

export interface OverlayRect {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

/** The BrowserWindow calls the overlay needs; tests use a fake. */
export interface OverlayWindowHandle {
  setBounds(bounds: OverlayRect): void;
  setIgnoreMouseEvents(ignore: boolean, options?: { forward: boolean }): void;
  setAlwaysOnTop(flag: boolean, level?: string): void;
  setVisibleOnAllWorkspaces(
    visible: boolean,
    options?: { visibleOnFullScreen: boolean },
  ): void;
  loadURL(url: string): Promise<void>;
  showInactive(): void;
  hide(): void;
  destroy(): void;
  isDestroyed(): boolean;
}

export interface ScreenOverlayDeps {
  createWindow(options: {
    x: number;
    y: number;
    width: number;
    height: number;
    frame: boolean;
    transparent: boolean;
    hasShadow: boolean;
    resizable: boolean;
    focusable: boolean;
    skipTaskbar: boolean;
    show: boolean;
    backgroundColor: string;
    webPreferences: {
      sandbox: boolean;
      contextIsolation: boolean;
      nodeIntegration: boolean;
    };
  }): OverlayWindowHandle;
  /** The work area of the display that contains the target. */
  workAreaFor(target: OverlayTarget): OverlayRect;
}

export interface ScreenOverlayOptions {
  readonly autoHideMs?: number;
  readonly deps: ScreenOverlayDeps;
}

function overlayURL(target: OverlayTarget, area: OverlayRect): string {
  const relative = {
    x: target.x - area.x,
    y: target.y - area.y,
    width: target.width,
    height: target.height,
    label: target.label ?? "",
  };
  return (
    "data:text/html;charset=utf-8," +
    encodeURIComponent(OVERLAY_HTML) +
    "#" +
    encodeURIComponent(JSON.stringify(relative))
  );
}

/**
 * A transparent, click-through, always-on-top window that outlines one screen
 * region with a ring and an optional label. It never takes focus, never
 * receives input and never speaks.
 */
export class ScreenOverlay {
  readonly #deps: ScreenOverlayDeps;
  readonly #autoHideMs: number;
  #window: OverlayWindowHandle | undefined;
  #timer: ReturnType<typeof setTimeout> | undefined;
  #visible = false;

  constructor(options: ScreenOverlayOptions) {
    this.#deps = options.deps;
    this.#autoHideMs = options.autoHideMs ?? 4_000;
  }

  async show(target: OverlayTarget): Promise<void> {
    const area = this.#deps.workAreaFor(target);
    if (!this.#window || this.#window.isDestroyed()) {
      this.#window = this.#deps.createWindow({
        ...area,
        frame: false,
        transparent: true,
        hasShadow: false,
        resizable: false,
        focusable: false,
        skipTaskbar: true,
        show: false,
        backgroundColor: "#00000000",
        webPreferences: {
          sandbox: true,
          contextIsolation: true,
          nodeIntegration: false,
        },
      });
      this.#window.setIgnoreMouseEvents(true, { forward: true });
      this.#window.setAlwaysOnTop(true, "screen-saver");
      this.#window.setVisibleOnAllWorkspaces(true, {
        visibleOnFullScreen: true,
      });
    }
    this.#window.setBounds(area);
    await this.#window.loadURL(overlayURL(target, area));
    if (this.#window.isDestroyed()) return;
    this.#window.showInactive();
    this.#visible = true;
    clearTimeout(this.#timer);
    this.#timer = setTimeout(() => this.hide(), this.#autoHideMs);
  }

  hide(): void {
    clearTimeout(this.#timer);
    this.#timer = undefined;
    if (this.#visible && this.#window && !this.#window.isDestroyed())
      this.#window.hide();
    this.#visible = false;
  }

  dispose(): void {
    clearTimeout(this.#timer);
    this.#timer = undefined;
    if (this.#window && !this.#window.isDestroyed()) this.#window.destroy();
    this.#window = undefined;
    this.#visible = false;
  }
}
