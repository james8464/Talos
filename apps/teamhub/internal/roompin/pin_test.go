package roompin

import "testing"

func TestParsePinCommands(t *testing.T) {
	cases := map[string]bool{
		"pin this room":   true,
		"Pin Room.":       true,
		"unpin this room": false,
		"Unpin room!":     false,
	}
	for text, want := range cases {
		pinned, ok := Parse(text)
		if !ok || pinned != want {
			t.Fatalf("%q => pinned=%v ok=%v", text, pinned, ok)
		}
	}
}

func TestParseIgnoresOtherText(t *testing.T) {
	for _, text := range []string{"pin the message", "play music", "unpin"} {
		if _, ok := Parse(text); ok {
			t.Fatalf("%q parsed", text)
		}
	}
}
