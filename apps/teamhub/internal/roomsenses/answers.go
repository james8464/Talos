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
	// SpeakMuted keeps the host silent and says so; SpeakReady does not claim
	// a speaker or voice session exists.
	SpeakMuted = "Audio is muted in this environment, so I will not speak out loud. I can answer here in text."
	SpeakReady = "Audio is not muted, so voice mode can use the speaker when it is available."
	// CameraConsent and MicrophoneConsent keep capture behind the explicit
	// Settings check, matching the old Talos room dialogue.
	CameraConsent     = "I don't open the camera without your explicit consent. Use Settings, then Capture check, to allow one frame; the frame and clip are deleted afterwards."
	MicrophoneConsent = "I don't record the microphone without your explicit consent. Use Settings, then Capture check, to record one short clip; it is deleted afterwards."
)

var (
	prefixes   = []string{"hey talos ", "ok talos ", "okay talos ", "talos ", "tell us "}
	hearing    = regexp.MustCompile(`^(?:can|do) you hear me$|^did you hear me$|^am i coming through$|^can you hear my voice$|^is my voice coming through$`)
	seeing     = regexp.MustCompile(`^(?:can|do) you see me$|^(?:can|do) you see anyone$|^is anyone in the room$|^is there anyone in the room$|^am i in the room$`)
	speaking   = regexp.MustCompile(`^(?:can|do) you (?:speak|talk)(?: out loud)?$|^do you have a voice$|^are you (?:muted|on mute|silent|quiet)$|^is (?:your voice|audio) (?:off|on|muted|on mute)$|^why (?:are you|do you stay) (?:silent|quiet|muted)$|^why don t you (?:talk|speak)$`)
	camera     = regexp.MustCompile(`^(?:can|could|will|would) you (?:look|watch)(?: at)? (?:me|us|the room|my camera)$|^open (?:the |your )?camera$|^turn on (?:the |your )?camera$|^take (?:a )?(?:photo|picture|snapshot)$`)
	microphone = regexp.MustCompile(`^(?:can|could|will|would) you (?:listen|record)(?: to)? (?:me|us|the room|my voice|audio)?$|^turn on (?:the |your )?microphone$|^record (?:me|audio|the room|a clip)$|^start recording$|^are you recording$`)
	combined   = regexp.MustCompile(`^(?:can|do) you (hear|see)(?: me)? and (?:can you |do you )?(hear|see) me$`)
)

// Answer returns a deterministic reply, or "" when text asks nothing about
// hearing, seeing or speaking. voice reports that the turn arrived as speech;
// muted reports that the host has no audio output.
func Answer(text string, voice, muted bool) string {
	words := normalize(text)
	if words == "" {
		return ""
	}
	hearingAnswer := Typed
	if voice {
		hearingAnswer = Heard
	}
	if match := combined.FindStringSubmatch(words); match != nil && match[1] != match[2] {
		answers := map[string]string{"hear": hearingAnswer, "see": Unknown}
		return answers[match[1]] + " " + answers[match[2]]
	}
	if hearing.MatchString(words) {
		return hearingAnswer
	}
	if seeing.MatchString(words) {
		return Unknown
	}
	if speaking.MatchString(words) {
		if muted {
			return SpeakMuted
		}
		return SpeakReady
	}
	if camera.MatchString(words) {
		return CameraConsent
	}
	if microphone.MatchString(words) {
		return MicrophoneConsent
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
