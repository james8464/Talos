#!/bin/zsh
# Run the local acceptance suite and report each independent diagnostic's status.
cd -- "${0:A:h}" || exit 1
export PYTHONPATH=.
export TALOS_OLLAMA_PORT="${TALOS_OLLAMA_PORT:-11435}"
export TALOS_TEST_OLLAMA=1 TALOS_TEST_VISION=1 TALOS_TEST_KOKORO=1 TALOS_TEST_REAL_MINUTE=1
python=state/kokoro-env/bin/python
if [[ ! -x "$python" ]]; then
    print -u2 'The local Kokoro environment is missing. Start Talos once to prepare it.'
    exit 1
fi
status_code=0
"$python" -m unittest discover -v -s tests || status_code=1
"$python" tests/measure_idle_voice.py || status_code=1
workspace=$(mktemp -d) || exit 1
trap 'rm -rf -- "$workspace"' EXIT
fixture="${TALOS_PORTRAIT_FIXTURE:-generated_images/exec-7eff918c-5a3d-4445-8a99-1537a45c5cfb.png}"
if [[ -f "$fixture" ]] && swiftc -module-cache-path "$workspace/cache" mac/VoiceEndpoint.swift mac/MeetingBrief.swift mac/VisitorMemory.swift tests/VoiceEndpointChecks.swift -o "$workspace/portrait-check"; then
    "$workspace/portrait-check" --portraits "$fixture" || status_code=1
else
    print -u2 'Portrait replay unavailable (fixture missing or Swift compilation failed).'
    status_code=1
fi
exit "$status_code"
