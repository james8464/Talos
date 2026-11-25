package roomsenses

import "testing"

func TestAnswerHearingAndSeeing(t *testing.T) {
	cases := map[string]string{
		"Can you hear me?":                 Typed,
		"Hey Talos, do you hear me?":       Typed,
		"Is my voice coming through?":      Typed,
		"Can you see me?":                  Unknown,
		"Is anyone in the room right now?": Unknown,
		"Can you hear me and see me?":      Typed + " " + Unknown,
	}
	for text, want := range cases {
		if got := Answer(text); got != want {
			t.Fatalf("%q => %q, want %q", text, got, want)
		}
	}
}

func TestAnswerIgnoresOrdinaryText(t *testing.T) {
	for _, text := range []string{"Play music.", "Who am I?", "Can you hear the rain?", ""} {
		if got := Answer(text); got != "" {
			t.Fatalf("%q => %q", text, got)
		}
	}
}
