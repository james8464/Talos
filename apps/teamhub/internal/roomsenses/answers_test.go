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
		if got := Answer(text, false, false); got != want {
			t.Fatalf("%q => %q, want %q", text, got, want)
		}
	}
	if got := Answer("Can you hear me?", true, false); got != Heard {
		t.Fatalf("voice hearing=%q", got)
	}
	if got := Answer("Can you hear me and see me?", true, false); got != Heard+" "+Unknown {
		t.Fatalf("voice combined=%q", got)
	}
}

func TestAnswerIgnoresOrdinaryText(t *testing.T) {
	for _, text := range []string{"Play music.", "Who am I?", "Can you hear the rain?", ""} {
		if got := Answer(text, false, false); got != "" {
			t.Fatalf("%q => %q", text, got)
		}
	}
}

func TestSpeakAnswersFollowMute(t *testing.T) {
	for _, text := range []string{
		"Can you speak?", "Can you talk out loud?", "Do you have a voice?",
		"Are you muted?", "Is audio off?", "Why are you silent?",
		"Why don't you talk?", "Talos, are you on mute?",
	} {
		if got := Answer(text, false, true); got != SpeakMuted {
			t.Fatalf("%q muted => %q", text, got)
		}
		if got := Answer(text, false, false); got != SpeakReady {
			t.Fatalf("%q unmuted => %q", text, got)
		}
	}
	if got := Answer("Can you hear my voice?", false, true); got != Typed {
		t.Fatalf("hearing question answered as speaking: %q", got)
	}
	if got := Answer("Can you see me?", false, true); got != Unknown {
		t.Fatalf("seeing question answered as speaking: %q", got)
	}
}
