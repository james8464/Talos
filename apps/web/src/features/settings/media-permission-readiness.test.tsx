import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MediaPermissionReadiness } from "./media-permission-readiness";

function desktop(permissions: unknown) {
  Object.assign(window, { talosDesktop: { permissions } });
}

afterEach(() => {
  delete (window as { talosDesktop?: unknown }).talosDesktop;
});

describe("MediaPermissionReadiness", () => {
  it("is absent without the status API", () => {
    expect(
      render(<MediaPermissionReadiness />).container,
    ).toBeEmptyDOMElement();
    desktop({ act: vi.fn() });
    expect(
      render(<MediaPermissionReadiness />).container,
    ).toBeEmptyDOMElement();
  });

  it("shows camera and microphone readiness without capturing", async () => {
    const status = vi.fn().mockResolvedValue([
      { id: "screen_recording", state: "granted", relaunch_required: false },
      { id: "camera", state: "granted", relaunch_required: false },
      { id: "microphone", state: "not_determined", relaunch_required: false },
    ]);
    const act = vi.fn().mockImplementation(async () => {
      status.mockResolvedValue([
        { id: "camera", state: "granted", relaunch_required: false },
        { id: "microphone", state: "granted", relaunch_required: false },
      ]);
    });
    desktop({ act, status });
    render(<MediaPermissionReadiness />);

    expect(await screen.findByText("Camera: Granted")).toBeVisible();
    expect(screen.getByText("Microphone: Not granted")).toBeVisible();
    expect(screen.queryByText("Screen Recording: Granted")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Allow" }));
    await waitFor(() =>
      expect(act).toHaveBeenCalledWith({
        action: "prompt",
        subject: "talos",
        permission: "microphone",
      }),
    );
    expect(await screen.findByText("Microphone: Granted")).toBeVisible();
  });
});
