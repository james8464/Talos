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
if ! ollama show "$model" >/dev/null 2>&1; then
  modelfile="$(mktemp "${TMPDIR:-/tmp}/talos-ollama-modelfile.XXXXXX")"
  trap 'rm -f "$modelfile"' EXIT
  printf 'FROM qwen3:4b\nPARAMETER num_ctx 8192\n' >"$modelfile"
  ollama create "$model" -f "$modelfile" >/dev/null
fi

# Every installed model is advertised, so an Agent configured for another
# Ollama model still finds a placement on this Computer.
installed="$(ollama list 2>/dev/null | tail -n +2 | awk '{print $1}' | grep -v '^$' || true)"
# shellcheck disable=SC2086
node "$(dirname "$0")/ollama-codex-catalog.mjs" "$home/model_catalog.json" "$model" $installed

printf '%s\n' "$home"
