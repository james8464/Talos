import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TeamHubRuntimeProvider } from "@/features/teamhub/teamhub-runtime";
import { createTestTeamHubRuntime } from "@/test/teamhub-test-runtime";

import { CaptureCheckButton } from "./capture-check-button";

function desktop(talosDesktop: unknown) {
  Object.assign(window, { talosDesktop });
}

function renderWithRuntime(children: ReactNode) {
  return render(
    <TeamHubRuntimeProvider runtime={createTestTeamHubRuntime()}>
      {children}
    </TeamHubRuntimeProvider>,
  );
}

afterEach(() => {
  delete (window as { talosDesktop?: unknown }).talosDesktop;
});

describe("CaptureCheckButton", () => {
  it("is absent in a browser and in an app without capture", () => {
    expect(
      renderWithRuntime(<CaptureCheckButton />).container,
    ).toBeEmptyDOMElement();
    desktop({ version: "0.1.0" });
    expect(
      renderWithRuntime(<CaptureCheckButton />).container,
    ).toBeEmptyDOMElement();
  });

  it("runs one capture, records the scene and reports the deleted media", async () => {
    const check = vi.fn().mockResolvedValue({
      frameBytes: 12,
      audioBytes: 34,
      mediaDeleted: true,
      scene: { people: 2, faces: 2 },
    });
    desktop({ capture: { check } });
    const runtime = createTestTeamHubRuntime();
    const recorded: unknown[] = [];
    runtime.server.on("POST", "/v0/presence-readings", ({ body }) => {
      recorded.push(body);
      return undefined;
    });
    render(
      <TeamHubRuntimeProvider runtime={runtime}>
        <CaptureCheckButton />
      </TeamHubRuntimeProvider>,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Test camera and microphone" }),
    );
    await waitFor(() =>
      expect(screen.getByText(/12-byte frame/)).toBeVisible(),
    );
    expect(check).toHaveBeenCalledOnce();
    await waitFor(() => expect(recorded).toEqual([{ people: 2, faces: 2 }]));
  });

  it("does not record a reading when classification was unavailable", async () => {
    const check = vi.fn().mockResolvedValue({
      frameBytes: 12,
      audioBytes: 34,
      mediaDeleted: true,
      scene: null,
    });
    desktop({ capture: { check } });
    const runtime = createTestTeamHubRuntime();
    runtime.server.on("POST", "/v0/presence-readings", () => undefined);
    render(
      <TeamHubRuntimeProvider runtime={runtime}>
        <CaptureCheckButton />
      </TeamHubRuntimeProvider>,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Test camera and microphone" }),
    );
    await waitFor(() =>
      expect(screen.getByText(/12-byte frame/)).toBeVisible(),
    );
    expect(
      runtime.server.requestsTo("POST", "/v0/presence-readings"),
    ).toHaveLength(0);
  });

  it("reports a failed capture", async () => {
    const check = vi.fn().mockRejectedValue(new Error("denied"));
    desktop({ capture: { check } });
    renderWithRuntime(<CaptureCheckButton />);

    fireEvent.click(
      screen.getByRole("button", { name: "Test camera and microphone" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Capture failed. Check the macOS camera and microphone permissions.",
    );
  });
});
