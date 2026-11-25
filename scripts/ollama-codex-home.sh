#!/usr/bin/env bash
# Prepares a private Codex home whose provider is the local Ollama server, so
# the Codex Runtime can serve model calls from qwen3 without a provider bill.
set -Eeuo pipefail

model="${TALOS_OLLAMA_CODEX_MODEL:-qwen3:4b-8k}"
base="${TALOS_OLLAMA_URL:-http://127.0.0.1:11434}"
home="${1:-${TMPDIR:-/tmp}/talos-ollama-codex-home}"
base="${base%/}"

mkdir -p "$home"
cat >"$home/config.toml" <<EOF
model = "$model"
model_provider = "ollama-local"
model_catalog_json = "$home/model_catalog.json"

[model_providers.ollama-local]
name = "Ollama Local"
base_url = "$base/v1"
env_key = "OLLAMA_API_KEY"
wire_api = "responses"
requires_openai_auth = false
EOF
printf '{}\n' >"$home/auth.json"
node - "$model" "$home/model_catalog.json" <<'NODE'
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const [model, target] = process.argv.slice(2);
const source = process.env.TALOS_CODEX_SOURCE_HOME || path.join(os.homedir(), ".codex");
let base = {};
try {
  const cache = JSON.parse(fs.readFileSync(path.join(source, "models_cache.json"), "utf8"));
  base = cache.models?.[0] ?? {};
} catch {
  // Fields below still describe a usable entry.
}
const entry = JSON.parse(JSON.stringify(base));
entry.slug = model;
entry.display_name = "Qwen3 4B (8k)";
entry.description = "Local Ollama model served through the Responses API";
entry.context_window = 8192;
if ("max_output_tokens" in entry) entry.max_output_tokens = 2048;
entry.shell_type = "shell_command";
delete entry.tool_mode;
delete entry.apply_patch_tool_type;
delete entry.web_search_tool_type;
entry.experimental_supported_tools = [];
entry.supports_search_tool = false;
entry.use_responses_lite = false;
fs.writeFileSync(target, JSON.stringify({ models: [entry] }), { mode: 0o600 });
NODE
if ! ollama show "$model" >/dev/null 2>&1; then
  modelfile="$(mktemp "${TMPDIR:-/tmp}/talos-ollama-modelfile.XXXXXX")"
  trap 'rm -f "$modelfile"' EXIT
  printf 'FROM qwen3:4b\nPARAMETER num_ctx 8192\n' >"$modelfile"
  ollama create "$model" -f "$modelfile" >/dev/null
fi

printf '%s\n' "$home"
