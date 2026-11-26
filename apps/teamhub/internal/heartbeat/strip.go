// Package heartbeat interprets quiet heartbeat acknowledgements using
// OpenClaw's HEARTBEAT_OK token rules.
package heartbeat

import (
	"strings"
	"unicode"
	"unicode/utf16"
)

const (
	// Token marks a heartbeat acknowledgement.
	Token = "HEARTBEAT_OK"
	// SilentReply marks an explicit quiet reply.
	SilentReply = "NO_REPLY"
	// MaxAckChars bounds a quiet acknowledgement after the token is removed.
	MaxAckChars = 300
)

// Result is the visible text and whether the reply is quiet.
type Result struct {
	Skip     bool
	Text     string
	Stripped bool
}

// Strip removes HEARTBEAT_OK at the edges and decides whether the reply is a
// quiet acknowledgement. A short remainder after a token stays quiet; a long
// report is surfaced without the token.
func Strip(raw string) Result {
	text := strings.TrimSpace(raw)
	if text == "" {
		return Result{Skip: true}
	}
	if strings.EqualFold(text, SilentReply) {
		return Result{Skip: true, Stripped: true}
	}
	if !strings.Contains(text, Token) {
		return Result{Text: text}
	}
	rest, stripped := stripEdges(text)
	if !stripped {
		return Result{Text: text}
	}
	if strings.TrimSpace(rest) == "" {
		return Result{Skip: true, Stripped: true}
	}
	if utf16Units(rest) <= MaxAckChars {
		return Result{Skip: true, Stripped: true}
	}
	return Result{Text: rest, Stripped: true}
}

func stripEdges(raw string) (string, bool) {
	text := strings.TrimSpace(raw)
	stripped := false
	for {
		next := strings.TrimSpace(text)
		if strings.HasPrefix(next, Token) {
			text = strings.TrimSpace(next[len(Token):])
			stripped = true
			continue
		}
		index := strings.LastIndex(next, Token)
		if index >= 0 {
			before := strings.TrimRightFunc(next[:index], unicode.IsSpace)
			after := strings.TrimLeftFunc(next[index+len(Token):], unicode.IsSpace)
			trailing := strings.TrimFunc(after, func(r rune) bool {
				return !unicode.IsLetter(r) && !unicode.IsDigit(r) && r != '_'
			})
			if trailing == "" && len([]rune(after)) <= 4 {
				text = strings.TrimRightFunc(before, unicode.IsSpace)
				stripped = true
				continue
			}
		}
		break
	}
	return text, stripped
}

func utf16Units(value string) int {
	return len(utf16.Encode([]rune(value)))
}
