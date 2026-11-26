package heartbeat

import (
	"strings"
	"testing"
)

func TestStripQuietAcknowledgements(t *testing.T) {
	for _, raw := range []string{
		"", "   ", "HEARTBEAT_OK", "HEARTBEAT_OK.", "!! HEARTBEAT_OK !!",
		"HEARTBEAT_OK HEARTBEAT_OK", "NO_REPLY", "no_reply",
		"HEARTBEAT_OK all clear", "HEARTBEAT_OK " + strings.Repeat("a", 299),
	} {
		if result := Strip(raw); !result.Skip {
			t.Fatalf("%q => %+v", raw, result)
		}
	}
}

func TestStripSurfacesSubstantiveText(t *testing.T) {
	long := strings.Repeat("a", 301)
	if result := Strip("HEARTBEAT_OK " + long); result.Skip || result.Text != long {
		t.Fatalf("long=%+v", result)
	}
	if result := Strip("Please check the deploy"); result.Skip || result.Text != "Please check the deploy" {
		t.Fatalf("plain=%+v", result)
	}
	if result := Strip("HEARTBEAT_OK: deploy failed on the canary"); !result.Skip {
		t.Fatalf("short report must stay quiet: %+v", result)
	}
}
