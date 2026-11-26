// Builds the Codex model catalog for a private Ollama home. The setup script
// runs this after listing installed models, and the tests call it directly.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

/**
 * Reads the context window a model name declares, such as `-8k` or `-16k`.
 * Codex needs a number even when the name carries none.
 */
export function contextWindowForModel(model) {
  const match = /-(\d+)k$/i.exec(model);
  return match ? Number(match[1]) * 1024 : 8192;
}

/** A short label that keeps the model identifiable in Runtime menus. */
export function displayNameForModel(model) {
  return model
    .replace(/^([a-z0-9.]+):/i, (_, family) => family + " ")
    .replace(/-/g, " ")
    .replace(/\b([a-z])/g, (letter) => letter.toUpperCase());
}

/**
 * Builds one catalog entry per model, primary first and duplicates removed.
 * `base` is any existing Codex model entry whose optional fields are reused.
 */
export function buildModelCatalog({ models, primary, base = {} }) {
  const entries = [];
  const seen = new Set();
  for (const model of [primary, ...models]) {
    if (typeof model !== "string" || model.trim() === "" || seen.has(model))
      continue;
    seen.add(model);
    const entry = JSON.parse(JSON.stringify(base));
    entry.slug = model;
    entry.display_name = displayNameForModel(model);
    entry.description = "Local Ollama model served through the Responses API";
    entry.context_window = contextWindowForModel(model);
    if ("max_output_tokens" in entry) entry.max_output_tokens = 2048;
    entry.shell_type = "shell_command";
    delete entry.tool_mode;
    delete entry.apply_patch_tool_type;
    delete entry.web_search_tool_type;
    entry.experimental_supported_tools = [];
    entry.supports_search_tool = false;
    entry.use_responses_lite = false;
    entries.push(entry);
  }
  return { models: entries };
}

/** Reads a base entry from the source Codex home, when it has one. */
export function readBaseEntry(sourceHome) {
  try {
    const cache = JSON.parse(
      fs.readFileSync(path.join(sourceHome, "models_cache.json"), "utf8"),
    );
    return cache.models?.[0] ?? {};
  } catch {
    return {};
  }
}

async function main() {
  const [target, primary, ...models] = process.argv.slice(2);
  if (!target || !primary) {
    console.error(
      "usage: ollama-codex-catalog.mjs <target> <primary> [models...]",
    );
    process.exit(2);
  }
  const source =
    process.env.TALOS_CODEX_SOURCE_HOME ||
    path.join(process.env.HOME ?? ".", ".codex");
  const catalog = buildModelCatalog({
    models,
    primary,
    base: readBaseEntry(source),
  });
  fs.writeFileSync(target, JSON.stringify(catalog), { mode: 0o600 });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  await main();
