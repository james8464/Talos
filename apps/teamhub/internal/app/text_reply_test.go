package app

import (
	"strings"
	"testing"
)

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
