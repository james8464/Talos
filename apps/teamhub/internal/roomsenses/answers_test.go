package roomsenses

import (
	"testing"
	"time"
)

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

func TestCaptureRequestsExplainConsent(t *testing.T) {
	for _, text := range []string{
		"Can you look at me?", "Open the camera", "Turn on your camera",
		"Take a photo", "Talos, take a picture",
	} {
		if got := Answer(text, false, true); got != CameraConsent {
			t.Fatalf("%q => %q", text, got)
		}
	}
	for _, text := range []string{
		"Can you listen to me?", "Record me", "Turn on the microphone",
		"Start recording", "Are you recording?",
	} {
		if got := Answer(text, false, true); got != MicrophoneConsent {
			t.Fatalf("%q => %q", text, got)
		}
	}
	// A plain seeing question still reports that no camera reading exists.
	if got := Answer("Can you see me?", false, true); got != Unknown {
		t.Fatalf("seeing => %q", got)
	}
}

func TestSeeingAnswersUseOnlyFreshPresence(t *testing.T) {
	people := func(count int, age time.Duration) func() *Presence {
		return func() *Presence {
			return &Presence{People: count, Faces: count, CapturedAt: time.Now().Add(-age)}
		}
	}
	cases := []struct {
		text     string
		presence func() *Presence
		want     string
	}{
		{"Can you see me?", people(2, time.Minute), Visible},
		{"Is anyone in the room?", people(0, time.Minute), Empty},
		{"Can you see me?", people(2, PresenceWindow+time.Minute), Unknown},
		{"Can you see me?", func() *Presence { return nil }, Unknown},
		{"Can you see me?", nil, Unknown},
		{"Can you hear me and see me?", people(1, time.Minute), Typed + " " + Visible},
	}
	for _, tc := range cases {
		if got := AnswerWithPresence(tc.text, false, false, tc.presence); got != tc.want {
			t.Fatalf("%q => %q want %q", tc.text, got, tc.want)
		}
	}
}
