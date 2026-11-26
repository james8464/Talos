package processinventory

import (
	"errors"
	"syscall"
	"testing"
)

func TestClassifyMatchesAudioAndStaleProcesses(t *testing.T) {
	cases := map[string]Entry{
		"/usr/bin/afplay /tmp/clip.wav":                          {Label: "audio", Audio: true},
		"/usr/bin/say hello":                                     {Label: "audio", Audio: true},
		"node scripts/talos-live-check.ts":                       {Label: "live harness", Stale: true},
		"/Applications/Chrome --user-data-dir=talos-browser-123": {Label: "headless browser", Stale: true},
	}
	for args, want := range cases {
		got, ok := Classify(args)
		if !ok || got != want {
			t.Fatalf("%q => %+v ok=%v want=%+v", args, got, ok, want)
		}
	}
}

func TestClassifyIgnoresShellsAndUnrelatedProcesses(t *testing.T) {
	for _, args := range []string{
		"zsh -c pnpm talos-live-check",
		"grep talos-live-check",
		"ps -axo pid=,ppid=,args=",
		"node apps/teamhub",
		"/Applications/Talos.app/Contents/MacOS/Talos",
		"",
	} {
		if entry, ok := Classify(args); ok {
			t.Fatalf("%q => %+v", args, entry)
		}
	}
}

func TestParseReadsPIDAndKeepsLabelsOnly(t *testing.T) {
	output := `  101     1 /usr/bin/afplay /Users/person/private/clip.wav
  102    42 node scripts/talos-live-check.ts
  103     1 /Applications/Talos.app/Contents/MacOS/Talos
not a row
  104     1 /bin/zsh -c talos-agent-live
`
	entries := Parse(output)
	if len(entries) != 2 {
		t.Fatalf("entries=%+v", entries)
	}
	if entries[0].PID != 101 || entries[0].Label != "audio" || !entries[0].Audio {
		t.Fatalf("first=%+v", entries[0])
	}
	if entries[1].PID != 102 || entries[1].Label != "live harness" || !entries[1].Stale {
		t.Fatalf("second=%+v", entries[1])
	}
}

func TestStopStaleSignalsOnlyStaleProcesses(t *testing.T) {
	var signalled []int
	stopped := stop([]Entry{
		{PID: 1, Label: "audio", Audio: true},
		{PID: 2, Label: "live harness", Stale: true},
		{PID: 3, Label: "headless browser", Stale: true},
		{PID: 4, Label: "audio", Audio: true, Stale: true},
		{PID: 5, Label: "live harness", Stale: true},
	}, func(pid int, signal syscall.Signal) error {
		if signal != syscall.SIGTERM {
			t.Fatalf("signal=%v", signal)
		}
		if pid == 5 {
			return errors.New("gone")
		}
		signalled = append(signalled, pid)
		return nil
	})
	if len(signalled) != 2 || signalled[0] != 2 || signalled[1] != 3 {
		t.Fatalf("signalled=%v", signalled)
	}
	if len(stopped) != 2 || stopped[0] != 2 || stopped[1] != 3 {
		t.Fatalf("stopped=%v", stopped)
	}
}
