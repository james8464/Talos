package roomreminder

import (
	"testing"
	"time"
)

func TestParseTypedReminder(t *testing.T) {
	cases := map[string]Command{
		"reminder in 10m: stretch":       {Delay: 10 * time.Minute, Task: "stretch"},
		"reminder in 2 hours: call Ada":  {Delay: 2 * time.Hour, Task: "call Ada"},
		"reminder in 1 day: renew lease": {Delay: 24 * time.Hour, Task: "renew lease"},
		"Reminder in 90 minutes: water":  {Delay: 90 * time.Minute, Task: "water"},
	}
	for text, want := range cases {
		got, ok := Parse(text)
		if !ok || got != want {
			t.Fatalf("%q => %+v ok=%v, want %+v", text, got, ok, want)
		}
	}
}

func TestParseRejectsInvalidCommands(t *testing.T) {
	for _, text := range []string{
		"reminder in 0m: nothing",
		"reminder in 31d: too far",
		"reminder in 10m:",
		"remind me in 10m: stretch",
		"reminder in ten minutes: stretch",
		"play music",
	} {
		if command, ok := Parse(text); ok {
			t.Fatalf("%q => %+v", text, command)
		}
	}
}

func TestDescribeUsesWholeUnits(t *testing.T) {
	cases := map[time.Duration]string{
		10 * time.Minute:   "10 minutes",
		time.Minute:        "1 minute",
		2 * time.Hour:      "2 hours",
		24 * time.Hour:     "1 day",
		3 * 24 * time.Hour: "3 days",
		90 * time.Minute:   "90 minutes",
	}
	for delay, want := range cases {
		if got := Describe(delay); got != want {
			t.Fatalf("%s => %q, want %q", delay, got, want)
		}
	}
}
