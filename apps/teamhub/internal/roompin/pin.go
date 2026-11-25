// Package roompin parses the typed Room pin commands.
package roompin

import "strings"

// Parse reports a pin request and whether it pins or unpins.
func Parse(text string) (pinned bool, ok bool) {
	words := strings.Join(strings.Fields(strings.ToLower(strings.TrimSpace(text))), " ")
	words = strings.TrimRight(words, ".!?")
	switch words {
	case "pin this room", "pin room":
		return true, true
	case "unpin this room", "unpin room":
		return false, true
	default:
		return false, false
	}
}
