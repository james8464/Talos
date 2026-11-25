package app

import (
	"testing"

	"github.com/simplexai-labs/talos/apps/teamhub/internal/roomagent"
	api "github.com/simplexai-labs/talos/contracts-go/teamhubagentapi"
	httpapi "github.com/simplexai-labs/talos/contracts-go/teamhubpublic"
)

// The owner's card may raise a prompt or open a pane only for a named
// permission; a relaunch names none.
func TestLocalComputerPermissionActionNeedsItsPermission(t *testing.T) {
	t.Parallel()
	screen := httpapi.ComputerPermissionId("screen_recording")
	camera := httpapi.ComputerPermissionId("camera")
	for _, test := range []struct {
		request httpapi.ComputerPermissionAction
		ok      bool
	}{
		{httpapi.ComputerPermissionAction{Action: "prompt", Subject: "talos", Permission: &screen}, true},
		{httpapi.ComputerPermissionAction{Action: "open_settings", Subject: "computer_use", Permission: &screen}, true},
		{httpapi.ComputerPermissionAction{Action: "relaunch", Subject: "talos"}, true},
		{httpapi.ComputerPermissionAction{Action: "prompt", Subject: "talos"}, false},
		{httpapi.ComputerPermissionAction{Action: "prompt", Subject: "talos", Permission: &camera}, false},
		{httpapi.ComputerPermissionAction{Action: "relaunch", Subject: "talos", Permission: &screen}, false},
		{httpapi.ComputerPermissionAction{Action: "status", Subject: "talos"}, false},
		{httpapi.ComputerPermissionAction{Action: "prompt", Subject: "cua", Permission: &screen}, false},
	} {
		command, ok := localComputerPermissionAction(test.request)
		if ok != test.ok || ok && command.Action != roomagent.LocalComputerPermissionAction(test.request.Action) {
			t.Fatalf("%+v: command=%+v ok=%v", test.request, command, ok)
		}
	}
}

func TestComputerPermissionDecisionValidatesTheAgentRequest(t *testing.T) {
	t.Parallel()
	reason := " Take a screenshot "
	blank := "  "
	decision, ok := computerPermissionDecision("computer-1", api.RequestAgentComputerPermissionsJSONRequestBody{
		Subject: "talos", Permissions: []api.ComputerPermissionId{"screen_recording", "accessibility"}, Reason: &reason,
	})
	if !ok || decision.Reason != "Take a screenshot" || len(decision.Permissions) != 2 || decision.ComputerID != "computer-1" {
		t.Fatalf("decision=%+v ok=%v", decision, ok)
	}
	for _, request := range []api.RequestAgentComputerPermissionsJSONRequestBody{
		{Subject: "talos"},
		{Subject: "talos", Permissions: []api.ComputerPermissionId{"camera"}},
		{Subject: "talos", Permissions: []api.ComputerPermissionId{"accessibility", "accessibility"}},
		{Subject: "cua", Permissions: []api.ComputerPermissionId{"accessibility"}},
		{Subject: "talos", Permissions: []api.ComputerPermissionId{"accessibility"}, Reason: &blank},
	} {
		if _, ok := computerPermissionDecision("computer-1", request); ok {
			t.Fatalf("accepted %+v", request)
		}
	}
}
