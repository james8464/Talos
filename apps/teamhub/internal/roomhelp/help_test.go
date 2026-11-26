package roomhelp

import (
	"strings"
	"testing"
)

func TestReadMatchesOnlyHelpQuestions(t *testing.T) {
	for _, text := range []string{
		"help", "Talos help", "commands", "What can you do?", "what can I ask you",
	} {
		if !Read(text) {
			t.Fatalf("%q not read as help", text)
		}
	}
	for _, text := range []string{
		"help me code", "who am I", "can you hear me", "what can you do tomorrow",
	} {
		if Read(text) {
			t.Fatalf("%q read as help", text)
		}
	}
}

func TestReplyNamesCommandsAndMute(t *testing.T) {
	muted := Reply(true)
	for _, part := range []string{"Audio is muted", "reminder in 10m", "pin this room", "goal status", "coding job <id>", "camera or microphone"} {
		if !strings.Contains(muted, part) {
			t.Fatalf("muted reply missing %q: %s", part, muted)
		}
	}
	ready := Reply(false)
	if !strings.Contains(ready, "Audio is not muted") || strings.Contains(ready, "will not speak") {
		t.Fatalf("ready reply=%q", ready)
	}
}
