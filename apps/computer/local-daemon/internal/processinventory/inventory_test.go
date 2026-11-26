package processinventory

import "testing"

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
