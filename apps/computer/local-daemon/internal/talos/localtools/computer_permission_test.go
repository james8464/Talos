package localtools

import (
	"context"
	"errors"
	"slices"
	"testing"
)

type fakeTalosBridge struct {
	calls []string
	err   error
	// granted is the Desktop app's state per permission.
	granted map[string]bool
}

func (f *fakeTalosBridge) TalosPermissions(_ context.Context, op, permission string) ([]TalosPermission, error) {
	f.calls = append(f.calls, op+"/"+permission)
	if f.err != nil {
		return nil, f.err
	}
	state := func(id string) string {
		if f.granted[id] {
			return permissionGranted
		}
		return permissionNotDetermined
	}
	return []TalosPermission{
		{ID: PermissionScreenRecording, State: state(PermissionScreenRecording), RelaunchRequired: op == PermissionActionPrompt},
		{ID: PermissionAccessibility, State: state(PermissionAccessibility), RelaunchRequired: true},
	}, nil
}

type fakeCuaPermissions struct {
	reads, requests int
	restarts        int
	reading         CuaPermissionReading
	err, requestErr error
}

func (f *fakeCuaPermissions) CuaPermissions(context.Context) (CuaPermissionReading, error) {
	f.reads++
	return f.reading, f.err
}

func (f *fakeCuaPermissions) RequestCuaPermissions() error {
	f.requests++
	return f.requestErr
}

func (f *fakeCuaPermissions) RestartCua() error {
	f.restarts++
	return nil
}

type permissionFixture struct {
	talos  *fakeTalosBridge
	cua     *fakeCuaPermissions
	opened  []string
	service *ComputerPermissions
}

func newPermissionFixture(platform string, withTalos, withCua bool) *permissionFixture {
	fixture := &permissionFixture{talos: &fakeTalosBridge{granted: map[string]bool{}}, cua: &fakeCuaPermissions{}}
	options := ComputerPermissionOptions{Platform: platform, Desktop: withTalos, Open: func(_ context.Context, url string) error {
		fixture.opened = append(fixture.opened, url)
		return nil
	}}
	if withTalos {
		options.Talos = fixture.talos
	}
	if withCua {
		options.Cua = fixture.cua
	}
	fixture.service = NewComputerPermissions(options)
	return fixture
}

func permissionState(t *testing.T, status ComputerPermissionStatus, subject, id string) ComputerPermissionState {
	t.Helper()
	for _, group := range status.Subjects {
		if group.Subject != subject {
			continue
		}
		for _, item := range group.Permissions {
			if item.ID == id {
				return item
			}
		}
	}
	t.Fatalf("%s %s missing from %+v", subject, id, status)
	return ComputerPermissionState{}
}

func permissionCode(err error) string {
	var typed *ComputerPermissionError
	if errors.As(err, &typed) {
		return typed.Code
	}
	return ""
}

func TestComputerPermissionsOutsideMacOSReportNoSubjects(t *testing.T) {
	for _, platform := range []string{"linux", "windows"} {
		fixture := newPermissionFixture(platform, true, true)
		status, err := fixture.service.ComputerPermission(t.Context(), "status", "", "")
		if err != nil || status.Platform != platform || status.Host != "desktop" || status.Subjects == nil || len(status.Subjects) != 0 {
			t.Fatalf("%s: %+v %v", platform, status, err)
		}
		if _, err := fixture.service.ComputerPermission(t.Context(), "prompt", "talos", "screen_recording"); permissionCode(err) != "COMPUTER_PERMISSION_UNSUPPORTED" {
			t.Fatalf("%s prompt: %v", platform, err)
		}
		if len(fixture.talos.calls)+fixture.cua.reads+fixture.cua.requests+len(fixture.opened) != 0 {
			t.Fatalf("%s touched macOS mechanisms", platform)
		}
	}
}

func TestComputerPermissionsValidateRequests(t *testing.T) {
	fixture := newPermissionFixture("darwin", true, true)
	for _, request := range [][3]string{
		{"grant", "talos", "screen_recording"},
		{"prompt", "", "screen_recording"},
		{"prompt", "browser", "screen_recording"},
		{"prompt", "talos", ""},
		{"open_settings", "computer_use", "camera"},
		{"relaunch", "", ""},
	} {
		if _, err := fixture.service.ComputerPermission(t.Context(), request[0], request[1], request[2]); permissionCode(err) != "COMPUTER_PERMISSION_INVALID" {
			t.Fatalf("%v: %v", request, err)
		}
	}
	if len(fixture.talos.calls)+fixture.cua.reads+fixture.cua.requests+len(fixture.opened) != 0 {
		t.Fatal("an invalid request reached macOS")
	}
}

func TestComputerPermissionsReportBothSubjectsOnMacOS(t *testing.T) {
	fixture := newPermissionFixture("darwin", true, true)
	fixture.talos.granted[PermissionAccessibility] = true
	fixture.cua.reading = CuaPermissionReading{Accessibility: true, ScreenRecording: true}
	status, err := fixture.service.ComputerPermission(t.Context(), "status", "", "")
	if err != nil {
		t.Fatal(err)
	}
	if status.Platform != "darwin" || status.Host != "desktop" || len(status.Subjects) != 2 || status.Subjects[0].Subject != "talos" || status.Subjects[1].Subject != "computer_use" {
		t.Fatalf("status = %+v", status)
	}
	for _, group := range status.Subjects {
		if len(group.Permissions) != 2 || group.Permissions[0].ID != "screen_recording" || group.Permissions[1].ID != "accessibility" {
			t.Fatalf("group = %+v", group)
		}
	}
	if got := permissionState(t, status, "talos", "screen_recording"); got != (ComputerPermissionState{ID: "screen_recording", State: "not_determined", Prompt: "available"}) {
		t.Fatalf("talos screen = %+v", got)
	}
	// Only Screen Recording waits for a relaunch.
	if got := permissionState(t, status, "talos", "accessibility"); got != (ComputerPermissionState{ID: "accessibility", State: "granted", Prompt: "available"}) {
		t.Fatalf("talos accessibility = %+v", got)
	}
	if got := permissionState(t, status, "computer_use", "screen_recording"); got.State != "granted" || got.Prompt != "available" || got.RelaunchRequired {
		t.Fatalf("computer_use screen = %+v", got)
	}
	if fixture.cua.restarts != 0 || fixture.cua.reads != 1 || fixture.cua.requests != 0 || !slices.Equal(fixture.talos.calls, []string{"status/"}) {
		t.Fatalf("reads=%v restarts=%d talos=%v", fixture.cua.reads, fixture.cua.restarts, fixture.talos.calls)
	}
}

func TestComputerPermissionsRestartCuaAfterMissingScreenRecording(t *testing.T) {
	fixture := newPermissionFixture("darwin", true, true)
	fixture.cua.reading = CuaPermissionReading{Accessibility: true}
	status, err := fixture.service.ComputerPermission(t.Context(), "status", "", "")
	if err != nil {
		t.Fatal(err)
	}
	if got := permissionState(t, status, "computer_use", "screen_recording"); got.State != "not_determined" || got.Prompt != "available" {
		t.Fatalf("before the prompt = %+v", got)
	}
	if fixture.cua.restarts != 1 {
		t.Fatalf("a cached Screen Recording preflight survived: restarts=%d", fixture.cua.restarts)
	}

	// The prompt reads once and starts CuaDriver's grant flow; that reading
	// stands in for the status read, so the service keeps running.
	status, err = fixture.service.ComputerPermission(t.Context(), "prompt", "computer_use", "screen_recording")
	if err != nil {
		t.Fatal(err)
	}
	if fixture.cua.reads != 2 || fixture.cua.requests != 1 || fixture.cua.restarts != 1 {
		t.Fatalf("reads=%d requests=%d restarts=%d", fixture.cua.reads, fixture.cua.requests, fixture.cua.restarts)
	}
	if got := permissionState(t, status, "computer_use", "screen_recording"); got.State != "denied" {
		t.Fatalf("after the prompt = %+v", got)
	}
	if got := permissionState(t, status, "computer_use", "accessibility"); got.State != "granted" {
		t.Fatalf("accessibility = %+v", got)
	}

	fixture.cua.reading.ScreenRecording = true
	if _, err := fixture.service.ComputerPermission(t.Context(), "relaunch", "computer_use", ""); err != nil {
		t.Fatal(err)
	}
	if fixture.cua.restarts != 2 {
		t.Fatalf("relaunch did not stop CuaDriver: restarts=%d", fixture.cua.restarts)
	}

	if _, err := fixture.service.ComputerPermission(t.Context(), "open_settings", "computer_use", "accessibility"); err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(fixture.opened, []string{"x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"}) {
		t.Fatalf("opened = %v", fixture.opened)
	}
}

func TestComputerPermissionsWithoutCuaDriver(t *testing.T) {
	for _, withCua := range []bool{false, true} {
		fixture := newPermissionFixture("darwin", true, withCua)
		fixture.cua.err = errors.Join(ErrCuaUnavailable, errors.New("not installed"))
		status, err := fixture.service.ComputerPermission(t.Context(), "prompt", "computer_use", "screen_recording")
		if err != nil {
			t.Fatal(err)
		}
		for _, id := range permissionIDs {
			if got := permissionState(t, status, "computer_use", id); got.State != "unknown" || got.Prompt != "settings_only" {
				t.Fatalf("cua=%v %s = %+v", withCua, id, got)
			}
		}
		if !slices.Equal(fixture.opened, []string{"x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"}) || fixture.cua.restarts != 0 {
			t.Fatalf("cua=%v opened=%v restarts=%d", withCua, fixture.opened, fixture.cua.restarts)
		}
	}
}

func TestComputerPermissionsAskTheDesktopApp(t *testing.T) {
	fixture := newPermissionFixture("darwin", true, true)
	fixture.cua.reading = CuaPermissionReading{Accessibility: true, ScreenRecording: true}
	status, err := fixture.service.ComputerPermission(t.Context(), "prompt", "talos", "screen_recording")
	if err != nil {
		t.Fatal(err)
	}
	// The app's answer already carries its permissions; no second status
	// request follows.
	if !slices.Equal(fixture.talos.calls, []string{"prompt/screen_recording"}) || len(fixture.opened) != 0 {
		t.Fatalf("talos=%v opened=%v", fixture.talos.calls, fixture.opened)
	}
	if got := permissionState(t, status, "talos", "screen_recording"); !got.RelaunchRequired || got.Prompt != "available" {
		t.Fatalf("screen = %+v", got)
	}
	if _, err := fixture.service.ComputerPermission(t.Context(), "relaunch", "talos", ""); err != nil {
		t.Fatal(err)
	}
	if fixture.talos.calls[len(fixture.talos.calls)-1] != "relaunch/" {
		t.Fatalf("talos=%v", fixture.talos.calls)
	}

	fixture.talos.err = &TalosBridgeError{Code: "prompt_failed"}
	if _, err := fixture.service.ComputerPermission(t.Context(), "prompt", "talos", "accessibility"); permissionCode(err) != "COMPUTER_PERMISSION_UNAVAILABLE" {
		t.Fatalf("refusal: %v", err)
	}
	fixture.talos.err = context.DeadlineExceeded
	if _, err := fixture.service.ComputerPermission(t.Context(), "status", "", ""); permissionCode(err) != "COMPUTER_PERMISSION_TIMEOUT" {
		t.Fatalf("timeout: %v", err)
	}
}

func TestComputerPermissionsWithoutTheDesktopBridge(t *testing.T) {
	for _, standalone := range []bool{true, false} {
		fixture := newPermissionFixture("darwin", !standalone, true)
		fixture.cua.reading = CuaPermissionReading{Accessibility: true, ScreenRecording: true}
		// An app without the permissions.v1 hello answers like no app.
		fixture.talos.err = ErrTalosBridgeUnavailable
		status, err := fixture.service.ComputerPermission(t.Context(), "prompt", "talos", "accessibility")
		if err != nil {
			t.Fatal(err)
		}
		wantHost := "desktop"
		if standalone {
			wantHost = "standalone"
		}
		if status.Host != wantHost {
			t.Fatalf("host = %s", status.Host)
		}
		for _, id := range permissionIDs {
			if got := permissionState(t, status, "talos", id); got.State != "unknown" || got.Prompt != "settings_only" || got.RelaunchRequired {
				t.Fatalf("standalone=%v %s = %+v", standalone, id, got)
			}
		}
		if !slices.Equal(fixture.opened, []string{"x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"}) {
			t.Fatalf("prompt did not fall back to System Settings: %v", fixture.opened)
		}
		if _, err := fixture.service.ComputerPermission(t.Context(), "open_settings", "talos", "screen_recording"); err != nil || len(fixture.opened) != 2 {
			t.Fatalf("open_settings: %v %v", err, fixture.opened)
		}
		if _, err := fixture.service.ComputerPermission(t.Context(), "relaunch", "talos", ""); permissionCode(err) != "COMPUTER_PERMISSION_RELAUNCH_UNAVAILABLE" {
			t.Fatalf("relaunch: %v", err)
		}
	}
}

func TestComputerPermissionsReportAFailedCuaGrantFlow(t *testing.T) {
	fixture := newPermissionFixture("darwin", true, true)
	fixture.cua.requestErr = errors.New("exec failed")
	if _, err := fixture.service.ComputerPermission(t.Context(), "prompt", "computer_use", "accessibility"); permissionCode(err) != "COMPUTER_PERMISSION_UNAVAILABLE" {
		t.Fatalf("failed grant flow: %v", err)
	}
	// Without its own grant flow CuaDriver is listed in System Settings once
	// it asked, so the pane is the next step.
	fixture.cua.requestErr = ErrCuaUnavailable
	if _, err := fixture.service.ComputerPermission(t.Context(), "prompt", "computer_use", "accessibility"); err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(fixture.opened, []string{"x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"}) {
		t.Fatalf("opened = %v", fixture.opened)
	}
}
