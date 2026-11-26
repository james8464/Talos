// Package roomgoal parses the typed Room goal commands and builds the
// continuation prompt adapted from Hermes' persistent goals.
package roomgoal

import (
	"fmt"
	"strings"
)

// Action is one goal command.
type Action string

const (
	// Set creates or replaces the standing goal.
	Set Action = "set"
	// Status reports the current goal.
	Status Action = "status"
	// Pause stops automatic continuation.
	Pause Action = "pause"
	// Resume restarts automatic continuation.
	Resume Action = "resume"
	// Clear drops the goal.
	Clear Action = "clear"
)

// Command is one parsed goal request.
type Command struct {
	Action    Action
	Objective string
}

// DefaultMaxTurns bounds the Hermes goal budget.
const DefaultMaxTurns = 20

// Parse accepts `goal: <objective>` and the status/pause/resume/clear commands.
func Parse(text string) (Command, bool) {
	words := strings.Join(strings.Fields(strings.TrimSpace(text)), " ")
	lower := strings.ToLower(words)
	switch lower {
	case "goal status", "goal show":
		return Command{Action: Status}, true
	case "goal pause":
		return Command{Action: Pause}, true
	case "goal resume":
		return Command{Action: Resume}, true
	case "goal clear", "goal stop", "goal done":
		return Command{Action: Clear}, true
	}
	if strings.HasPrefix(lower, "goal:") || strings.HasPrefix(lower, "goal set ") {
		objective := strings.TrimSpace(words[len("goal:"):])
		if strings.HasPrefix(lower, "goal set ") {
			objective = strings.TrimSpace(words[len("goal set "):])
		}
		if objective == "" || len([]rune(objective)) > 500 {
			return Command{}, false
		}
		return Command{Action: Set, Objective: objective}, true
	}
	return Command{}, false
}

// ContinuationPrompt is fed back when a goal is still open.
func ContinuationPrompt(objective string, turnsUsed, maxTurns int) string {
	return fmt.Sprintf(`[Continuing toward your standing goal]
Goal: %s

Continue working toward this goal. Take the next concrete step. If you believe the goal is complete, state so explicitly and stop. If you are blocked and need input from the user, say so clearly and stop.

Turns used: %d/%d.`, objective, turnsUsed, maxTurns)
}
