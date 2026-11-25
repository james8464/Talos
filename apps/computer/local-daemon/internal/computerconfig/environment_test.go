package computerconfig

import "testing"

func TestRuntimeEnvironmentRejectsControlPlaneCredentials(t *testing.T) {
	source := map[string]string{
		"PATH":                            "/usr/bin",
		"ANTHROPIC_API_KEY":               "provider-key",
		"CODEX_AGENT_TIMEOUT":             "30m",
		"TALOS_SERVER_URL":               "https://talos.example",
		"talos_token":                    "talos-token",
		"TALOS_LLM_API_KEY":              "talos-llm-key",
		"TALOS_GATEWAY_TOKEN":            "talos-token",
		"UNRELATED_GATEWAY_TOKEN_COPY":    "talos-token",
		"TALOS_COMPUTER_SERVICE_COMMAND": `["/bin/talos-computer","--config","/c.json"]`,
	}
	got := RuntimeEnvironment(source, "talos-token")
	for _, key := range []string{"TALOS_SERVER_URL", "talos_token", "TALOS_LLM_API_KEY", "TALOS_GATEWAY_TOKEN", "UNRELATED_GATEWAY_TOKEN_COPY", "TALOS_COMPUTER_SERVICE_COMMAND"} {
		if _, exists := got[key]; exists {
			t.Fatalf("control value %q reached Runtime environment", key)
		}
	}
	if got["PATH"] != "/usr/bin" || got["ANTHROPIC_API_KEY"] != "provider-key" || got["CODEX_AGENT_TIMEOUT"] != "30m" {
		t.Fatalf("Runtime environment lost allowed values: %#v", got)
	}
}
