// Package roomhelp answers one short question about what the Room Agent can
// do, including the deterministic commands it accepts.
package roomhelp

import "strings"

// Read reports whether text asks for the Room capability list.
func Read(text string) bool {
	words := strings.Join(strings.Fields(strings.ToLower(strings.TrimSpace(text))), " ")
	words = strings.TrimRight(words, " ?!.,")
	switch words {
	case "help", "talos help", "commands", "talos commands",
		"what can you do", "what can you do talos",
		"what commands do you have", "what can i ask you":
		return true
	default:
		return false
	}
}

// Reply lists the deterministic commands and the current audio state.
func Reply(muted bool) string {
	audio := "Audio is not muted, so voice mode can use the speaker when it is available."
	if muted {
		audio = "Audio is muted here, so I answer in text and will not speak out loud."
	}
	return audio + " In a Room I can: remember the name you introduce; answer whether I can hear or see you; " +
		"set `reminder in 10m: <task>`; `pin this room` or `unpin this room`; " +
		"set and control `goal: <objective>`, `goal status`, `goal pause`, `goal resume`, `goal clear`; " +
		"queue `coding plan: <request>` or `coding job <path>: <request>`, then `coding jobs`, `coding job <id>` and `coding cancel <id>`. " +
		"I never open the camera or microphone without your explicit Settings capture check."
}
