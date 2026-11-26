package roomgoal

import (
	"strings"
	"testing"
)

func TestParseGoalCommands(t *testing.T) {
	cases := map[string]Command{
		"goal: ship the weekly report":  {Action: Set, Objective: "ship the weekly report"},
		"Goal set finish the migration": {Action: Set, Objective: "finish the migration"},
		"goal status":                   {Action: Status},
		"goal show":                     {Action: Status},
		"goal pause":                    {Action: Pause},
		"goal resume":                   {Action: Resume},
		"goal clear":                    {Action: Clear},
		"goal stop":                     {Action: Clear},
	}
	for text, want := range cases {
		got, ok := Parse(text)
		if !ok || got != want {
			t.Fatalf("%q => %+v ok=%v want=%+v", text, got, ok, want)
		}
	}
}

func TestParseRejectsOtherText(t *testing.T) {
	for _, text := range []string{"goal", "goal:", "goal set ", "goals status", "play music", "goal: " + strings.Repeat("a", 501)} {
		if command, ok := Parse(text); ok {
			t.Fatalf("%q => %+v", text, command)
		}
	}
}

func TestContinuationPromptCarriesBudget(t *testing.T) {
	prompt := ContinuationPrompt("ship the report", 3, 20)
	for _, part := range []string{"[Continuing toward your standing goal]", "Goal: ship the report", "3/20"} {
		if !strings.Contains(prompt, part) {
			t.Fatalf("prompt missing %q: %s", part, prompt)
		}
	}
}
