package roomcoding

import (
	"strings"
	"testing"
)

func TestQueueHandlesJobPlanAndList(t *testing.T) {
	q := New()
	if got := q.Handle("r1", "coding job internal/app/app.go: add a short comment"); got != "Coding job #1 queued for internal/app/app.go (verify-only)." {
		t.Fatalf("job=%q", got)
	}
	if got := q.Handle("r1", "coding plan: clarify the helper"); got != "Coding plan #2 queued (verify-only)." {
		t.Fatalf("plan=%q", got)
	}
	if got := q.Handle("r1", "coding jobs"); !strings.Contains(got, "#1 job internal/app/app.go") ||
		!strings.Contains(got, "#2 plan") {
		t.Fatalf("list=%q", got)
	}
}

func TestQueueRejectsUnsafeTargetsAndIgnoresText(t *testing.T) {
	q := New()
	for _, text := range []string{
		"coding job /etc/passwd: read this",
		"coding job ../outside.go: edit this",
	} {
		if got := q.Handle("r1", text); !strings.Contains(got, "rejected") {
			t.Fatalf("%q => %q", text, got)
		}
	}
	if got := q.Handle("r1", "Please clarify the helper."); got != "" {
		t.Fatalf("ordinary text=%q", got)
	}
	if got := q.Handle("r1", "coding jobs"); got != "No coding jobs have been queued." {
		t.Fatalf("empty list=%q", got)
	}
}
