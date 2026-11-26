import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ScreenHighlightPreview } from "./screen-highlight-preview";

afterEach(() => {
  delete (window as { talosDesktop?: unknown }).talosDesktop;
});

describe("ScreenHighlightPreview", () => {
  it("is absent without the overlay API", () => {
    expect(render(<ScreenHighlightPreview />).container).toBeEmptyDOMElement();
  });

  it("shows a labelled highlight without speaking", () => {
    const show = vi.fn().mockResolvedValue(undefined);
    Object.assign(window, { talosDesktop: { overlay: { show } } });
    render(<ScreenHighlightPreview />);

    fireEvent.click(
      screen.getByRole("button", { name: "Test screen highlight" }),
    );
    expect(show).toHaveBeenCalledWith(
      expect.objectContaining({
        width: 420,
        height: 240,
        label: "Talos highlight preview",
      }),
    );
  });
});
