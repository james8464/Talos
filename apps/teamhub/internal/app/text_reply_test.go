package app

import (
	"strings"
	"testing"
)

func TestReviewedFallbackTextQuietAcknowledgements(t *testing.T) {
	for _, value := range []any{"HEARTBEAT_OK", "NO_REPLY", "HEARTBEAT_OK all clear"} {
		if text, ok := reviewedFallbackText(value); ok {
			t.Fatalf("%v => %q", value, text)
		}
	}
	long := strings.Repeat("a", 301)
	if text, ok := reviewedFallbackText("HEARTBEAT_OK " + long); !ok || text != long {
		t.Fatalf("long=%q ok=%v", text, ok)
	}
}

func TestReviewedAgentTextReply(t *testing.T) {
	if text, ok := reviewedAgentTextReply("  Hello from Talos.  "); !ok || text != "Hello from Talos." {
		t.Fatalf("text=%q ok=%v", text, ok)
	}
	if text, ok := reviewedAgentTextReply("<response>\nHello!\n</response>"); !ok || text != "Hello!" {
		t.Fatalf("wrapped text=%q ok=%v", text, ok)
	}
	for _, value := range []any{"", "   ", strings.Repeat("a", 2001), "bad\x00text", 42, nil} {
		if text, ok := reviewedAgentTextReply(value); ok {
			t.Fatalf("%v => %q", value, text)
		}
	}
	if text, ok := reviewedAgentTextReply(strings.Repeat("a", 2000)); !ok || len(text) != 2000 {
		t.Fatalf("boundary text=%d ok=%v", len(text), ok)
	}
}
