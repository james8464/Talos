import { describe, expect, it } from "vitest";

import {
  buildModelCatalog,
  contextWindowForModel,
  displayNameForModel,
} from "./ollama-codex-catalog.mjs";

describe("ollama codex catalog", () => {
  it("advertises every installed model with the primary first", () => {
    const catalog = buildModelCatalog({
      models: ["qwen3:8b-16k", "qwen3:4b-8k", "qwen3:8b"],
      primary: "qwen3:4b-8k",
    });
    expect(catalog.models.map((model) => model.slug)).toEqual([
      "qwen3:4b-8k",
      "qwen3:8b-16k",
      "qwen3:8b",
    ]);
  });

  it("reads the declared context from the model name", () => {
    expect(contextWindowForModel("qwen3:4b-8k")).toBe(8192);
    expect(contextWindowForModel("qwen3:8b-16k")).toBe(16384);
    expect(contextWindowForModel("qwen3:4b")).toBe(8192);
  });

  it("keeps each entry usable by Codex", () => {
    const catalog = buildModelCatalog({
      models: ["qwen3:8b-16k"],
      primary: "qwen3:4b-8k",
      base: {
        slug: "gpt-5",
        display_name: "GPT-5",
        context_window: 400000,
        tool_mode: "native",
        shell_type: "local_shell",
      },
    });
    const [primary, second] = catalog.models;
    expect(primary).toMatchObject({
      slug: "qwen3:4b-8k",
      display_name: "Qwen3 4b 8k",
      context_window: 8192,
      shell_type: "shell_command",
      supports_search_tool: false,
      use_responses_lite: false,
    });
    expect(primary).not.toHaveProperty("tool_mode");
    expect(second).toMatchObject({
      slug: "qwen3:8b-16k",
      context_window: 16384,
    });
    // The spelling of the family stays as the model names it.
    expect(displayNameForModel("qwen2.5vl:3b")).toBe("Qwen2.5vl 3b");
  });

  it("does not mutate the base entry and drops empty names", () => {
    const base = { slug: "gpt-5", tool_mode: "native" };
    buildModelCatalog({ models: ["", "qwen3:4b"], primary: "qwen3:4b", base });
    expect(base).toEqual({ slug: "gpt-5", tool_mode: "native" });
  });
});
