package app

import (
	"strings"
	"unicode/utf8"

	"github.com/simplexai-labs/talos/apps/teamhub/internal/heartbeat"
)

// maxAgentTextReplyRunes bounds the fallback so a runaway model cannot post an
// unbounded message.
const maxAgentTextReplyRunes = 2000

// reviewedAgentTextReply accepts a non-empty, bounded final response with no
// NUL bytes. It trims surrounding whitespace.
// reviewedFallbackText applies the length review, then the heartbeat rules:
// a quiet acknowledgement posts nothing; a token-edged long report is surfaced
// without the token.
func reviewedFallbackText(value any) (string, bool) {
	text, ok := reviewedAgentTextReply(value)
	if !ok {
		return "", false
	}
	result := heartbeat.Strip(text)
	if result.Skip {
		return "", false
	}
	return result.Text, true
}

func reviewedAgentTextReply(value any) (string, bool) {
	text, ok := value.(string)
	if !ok {
		return "", false
	}
	text = strings.TrimSpace(text)
	if strings.HasPrefix(text, "<response>") && strings.HasSuffix(text, "</response>") {
		text = strings.TrimSpace(text[len("<response>") : len(text)-len("</response>")])
	}
	if text == "" || utf8.RuneCountInString(text) > maxAgentTextReplyRunes {
		return "", false
	}
	if strings.ContainsRune(text, '\x00') {
		return "", false
	}
	return text, true
}
