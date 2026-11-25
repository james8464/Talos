import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CaptureCheckButton } from "./capture-check-button";

function desktop(talosDesktop: unknown) {
  Object.assign(window, { talosDesktop });
}

afterEach(() => {
  delete (window as { talosDesktop?: unknown }).talosDesktop;
});

describe("CaptureCheckButton", () => {
  it("is absent in a browser and in an app without capture", () => {
    expect(render(<CaptureCheckButton />).container).toBeEmptyDOMElement();
    desktop({ version: "0.1.0" });
    expect(render(<CaptureCheckButton />).container).toBeEmptyDOMElement();
  });

  it("runs one capture and reports the deleted media", async () => {
    const check = vi.fn().mockResolvedValue({
      frameBytes: 12,
      audioBytes: 34,
      mediaDeleted: true,
    });
    desktop({ capture: { check } });
    render(<CaptureCheckButton />);

    fireEvent.click(
      screen.getByRole("button", { name: "Test camera and microphone" }),
    );
    await waitFor(() =>
      expect(screen.getByText(/12-byte frame/)).toBeVisible(),
    );
    expect(check).toHaveBeenCalledOnce();
  });

  it("reports a failed capture", async () => {
    const check = vi.fn().mockRejectedValue(new Error("denied"));
    desktop({ capture: { check } });
    render(<CaptureCheckButton />);

    fireEvent.click(
      screen.getByRole("button", { name: "Test camera and microphone" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Capture failed. Check the macOS camera and microphone permissions.",
    );
  });
});
