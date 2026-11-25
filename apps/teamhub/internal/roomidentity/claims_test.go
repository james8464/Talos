package roomidentity

import (
	"testing"
	"time"
)

func TestClaimsRememberExpireAndForget(t *testing.T) {
	c := New()
	start := time.Date(2026, 11, 25, 10, 0, 0, 0, time.UTC)
	if got := c.Answer("r1", "My name is Ada.", start); got != "Nice to meet you, Ada." {
		t.Fatalf("intro=%q", got)
	}
	if got := c.Answer("r1", "Do you recognize me?", start.Add(time.Minute)); got == "" ||
		!contains(got, "self-reported") {
		t.Fatalf("recognize=%q", got)
	}
	if got := c.Answer("r2", "Do you recognize me?", start); got != "I haven't confirmed who I'm speaking with." {
		t.Fatalf("other room=%q", got)
	}
	if got := c.Answer("r1", "Do you recognize me?", start.Add(TTL)); got != "I haven't confirmed who I'm speaking with." {
		t.Fatalf("expired=%q", got)
	}
	c.Answer("r1", "My name is Ada.", start)
	if got := c.Answer("r1", "Forget my name.", start); !contains(got, "forgot") {
		t.Fatalf("forget=%q", got)
	}
	if got := c.Answer("r1", "Do you recognize me?", start); got != "I haven't confirmed who I'm speaking with." {
		t.Fatalf("after forget=%q", got)
	}
}

func TestClaimsIgnoreOrdinaryText(t *testing.T) {
	if got := New().Answer("r1", "Play music.", time.Now()); got != "" {
		t.Fatalf("reply=%q", got)
	}
}

func contains(value, part string) bool {
	for i := 0; i+len(part) <= len(value); i++ {
		if value[i:i+len(part)] == part {
			return true
		}
	}
	return false
}
