// Package roomsenses answers short questions about what Talos can sense.
// It never claims a camera reading the Room does not have.
package roomsenses

import (
	"regexp"
	"strings"
)

// The fixed answers match the old Talos room dialogue.
const (
	Heard   = "Yes, I can hear you."
	Typed   = "That message arrived as text."
	Visible = "I can see someone in the camera view."
	Empty   = "The camera isn't detecting anyone right now."
	Unknown = "I don't have a current camera reading."
)

var (
	prefixes = []string{"hey talos ", "ok talos ", "okay talos ", "talos ", "tell us "}
	hearing  = regexp.MustCompile(`^(?:can|do) you hear me$|^did you hear me$|^am i coming through$|^can you hear my voice$|^is my voice coming through$`)
	seeing   = regexp.MustCompile(`^(?:can|do) you see me$|^(?:can|do) you see anyone$|^is anyone in the room$|^is there anyone in the room$|^am i in the room$`)
	combined = regexp.MustCompile(`^(?:can|do) you (hear|see)(?: me)? and (?:can you |do you )?(hear|see) me$`)
)

// Answer returns a deterministic reply, or "" when text asks nothing about
// hearing or seeing.
func Answer(text string) string {
	words := normalize(text)
	if words == "" {
		return ""
	}
	if match := combined.FindStringSubmatch(words); match != nil && match[1] != match[2] {
		answers := map[string]string{"hear": Typed, "see": Unknown}
		return answers[match[1]] + " " + answers[match[2]]
	}
	if hearing.MatchString(words) {
		return Typed
	}
	if seeing.MatchString(words) {
		return Unknown
	}
	return ""
}

func normalize(text string) string {
	lower := strings.ToLower(strings.TrimSpace(text))
	lower = strings.Map(func(r rune) rune {
		switch {
		case r >= 'a' && r <= 'z', r >= '0' && r <= '9', r == ' ':
			return r
		default:
			return ' '
		}
	}, lower)
	words := strings.Join(strings.Fields(lower), " ")
	for _, prefix := range prefixes {
		if strings.HasPrefix(words, prefix) {
			words = strings.TrimPrefix(words, prefix)
			break
		}
	}
	words = strings.TrimSuffix(words, " talos")
	words = strings.TrimSuffix(words, " tell us")
	words = strings.TrimSuffix(words, " right now")
	words = strings.TrimSuffix(words, " now")
	return strings.TrimSpace(words)
}
