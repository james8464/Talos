// Package roomidentity holds session-local, self-reported names. It never
// claims biometric verification.
package roomidentity

import (
	"regexp"
	"strings"
	"sync"
	"time"
)

// TTL bounds how long a self-reported name is used.
const TTL = 30 * time.Minute

type claim struct {
	name string
	at   time.Time
}

// Claims is safe for concurrent Room handlers.
type Claims struct {
	mu     sync.Mutex
	ttl    time.Duration
	byRoom map[string]claim
}

// New returns claims with the default TTL.
func New() *Claims { return &Claims{ttl: TTL, byRoom: map[string]claim{}} }

var (
	introduce = regexp.MustCompile(`(?i)^(?:my name is|call me) ([^\d]{1,60})$`)
	forget    = regexp.MustCompile(`(?i)^(?:forget|clear) (?:my name|what i told you about my name)$`)
	recognize = regexp.MustCompile(`(?i)^(?:who am i|do you (?:recognize|remember) me|can you tell who i am|do you know who is speaking)$`)
)

// Answer returns a deterministic reply, or "" when text is not an identity request.
func (c *Claims) Answer(room, text string, now time.Time) string {
	words := normalize(text)
	c.mu.Lock()
	defer c.mu.Unlock()
	if match := introduce.FindStringSubmatch(words); match != nil {
		name := strings.TrimSpace(match[1])
		c.byRoom[room] = claim{name: name, at: now}
		return "Nice to meet you, " + name + "."
	}
	if forget.MatchString(words) {
		_, had := c.byRoom[room]
		delete(c.byRoom, room)
		if had {
			return "I forgot the name you introduced. I still can't verify identity."
		}
		return "I don't have a self-reported name to forget."
	}
	current, ok := c.byRoom[room]
	if ok && now.Sub(current.at) >= c.ttl {
		delete(c.byRoom, room)
		ok = false
	}
	if recognize.MatchString(words) {
		if ok {
			return "You introduced yourself as " + current.name + ". That is self-reported; I can't verify identity from the camera."
		}
		return "I haven't confirmed who I'm speaking with."
	}
	return ""
}

func normalize(text string) string {
	trimmed := strings.TrimSpace(text)
	lower := strings.ToLower(trimmed)
	for _, prefix := range []string{"hey talos ", "ok talos ", "okay talos ", "talos ", "tell us "} {
		if strings.HasPrefix(lower, prefix) {
			trimmed = trimmed[len(prefix):]
			break
		}
	}
	return strings.Join(strings.Fields(strings.Trim(trimmed, " .!?")), " ")
}
