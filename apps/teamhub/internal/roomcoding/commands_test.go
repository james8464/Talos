package roomcoding

import "testing"

func TestParseJobAndPlan(t *testing.T) {
	job, ok, err := Parse("coding job internal/app/app.go: add a short comment")
	if err != nil || !ok || job.Kind != "job" || job.Target != "internal/app/app.go" {
		t.Fatalf("job=%+v ok=%v err=%v", job, ok, err)
	}
	plan, ok, err := Parse("coding plan: clarify the helper")
	if err != nil || !ok || plan.Kind != "plan" || plan.Request != "clarify the helper" {
		t.Fatalf("plan=%+v ok=%v err=%v", plan, ok, err)
	}
}

func TestParseCancel(t *testing.T) {
	command, ok, err := Parse("coding cancel codingjob_abc123")
	if err != nil || !ok || command.Kind != "cancel" || command.Target != "codingjob_abc123" {
		t.Fatalf("cancel=%+v ok=%v err=%v", command, ok, err)
	}
	for _, text := range []string{"coding cancel", "coding cancel ../etc"} {
		if _, ok, err := Parse(text); ok || err != nil {
			t.Fatalf("%q ok=%v err=%v", text, ok, err)
		}
	}
}

func TestParseRejectsUnsafeTargets(t *testing.T) {
	for _, text := range []string{
		"coding job /etc/passwd: read this",
		"coding job ../outside.go: edit this",
	} {
		if _, ok, err := Parse(text); !ok || err == nil {
			t.Fatalf("%q ok=%v err=%v", text, ok, err)
		}
	}
	if _, ok, err := Parse("Please clarify the helper."); ok || err != nil {
		t.Fatalf("ordinary text ok=%v err=%v", ok, err)
	}
	if !IsList("coding jobs") || IsList("coding job x: y") {
		t.Fatal("IsList mismatch")
	}
}
