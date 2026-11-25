package noprogress

import "testing"

func records(tool, args string, results ...string) []Record {
	history := make([]Record, 0, len(results))
	for _, result := range results {
		history = append(history, Record{ToolName: tool, ArgsHash: args, ResultHash: result})
	}
	return history
}

func TestRepeatedArgumentsAndResult(t *testing.T) {
	history := records("search", "a1", "r1", "r1", "r1")
	if count, latest := Streak(history, "search", "a1"); count != 3 || latest != "r1" {
		t.Fatalf("count=%d latest=%q", count, latest)
	}
}

func TestChangedResultEndsTheStreak(t *testing.T) {
	history := records("search", "a1", "r1", "r2", "r2")
	if count, latest := Streak(history, "search", "a1"); count != 2 || latest != "r2" {
		t.Fatalf("count=%d latest=%q", count, latest)
	}
}

func TestOtherArgumentsAndToolsAreSkipped(t *testing.T) {
	history := []Record{
		{ToolName: "search", ArgsHash: "a1", ResultHash: "r1"},
		{ToolName: "search", ArgsHash: "other", ResultHash: "r2"},
		{ToolName: "read", ArgsHash: "a1", ResultHash: "r3"},
		{ToolName: "search", ArgsHash: "a1", ResultHash: "r1"},
	}
	if count, latest := Streak(history, "search", "a1"); count != 2 || latest != "r1" {
		t.Fatalf("count=%d latest=%q", count, latest)
	}
}

func TestVetoesNeedAnAnchor(t *testing.T) {
	history := []Record{
		{ToolName: "search", ArgsHash: "a1", ResultHash: "r1"},
		{ToolName: "search", ArgsHash: "a1", OutcomeKind: ToolLoopVeto},
		{ToolName: "search", ArgsHash: "a1", ResultHash: "r1"},
	}
	if count, _ := Streak(history, "search", "a1"); count != 3 {
		t.Fatalf("anchored count=%d", count)
	}
	onlyVetoes := []Record{
		{ToolName: "search", ArgsHash: "a1", OutcomeKind: ToolLoopVeto},
		{ToolName: "search", ArgsHash: "a1", OutcomeKind: ToolLoopVeto},
	}
	if count, latest := Streak(onlyVetoes, "search", "a1"); count != 2 || latest != "" {
		t.Fatalf("unanchored count=%d latest=%q", count, latest)
	}
}

func TestExecTerminalFailureTail(t *testing.T) {
	history := []Record{
		{ToolName: "exec", ArgsHash: "a1", ResultHash: "r1", FailureIdentityHash: "f1", OutcomeKind: TerminalExecFailure},
		{ToolName: "exec", ArgsHash: "a2", ResultHash: "r2", FailureIdentityHash: "f1", OutcomeKind: TerminalExecFailure},
		{ToolName: "exec", ArgsHash: "a3", ResultHash: "r3", FailureIdentityHash: "f1", OutcomeKind: TerminalExecFailure},
	}
	count, latest := Streak(history, "exec", "a3")
	if count != 3 || latest != "r3" {
		t.Fatalf("exec tail count=%d latest=%q", count, latest)
	}
}

func TestExecStopsAtArgumentBoundary(t *testing.T) {
	// The repeated-arguments branch ignores other arguments; the terminal tail
	// stops when it crosses back to the requested command.
	history := []Record{
		{ToolName: "exec", ArgsHash: "a1", ResultHash: "r1", FailureIdentityHash: "f1", OutcomeKind: TerminalExecFailure},
		{ToolName: "exec", ArgsHash: "a2", ResultHash: "r2", FailureIdentityHash: "f1", OutcomeKind: TerminalExecFailure},
		{ToolName: "exec", ArgsHash: "a1", ResultHash: "r0", FailureIdentityHash: "f1", OutcomeKind: TerminalExecFailure},
	}
	count, _ := Streak(history, "exec", "a1")
	if count != 2 {
		t.Fatalf("count=%d", count)
	}
}

func TestExecSkipsArgumentValidationAndMatchesFailureIdentity(t *testing.T) {
	history := []Record{
		{ToolName: "exec", ArgsHash: "a1", ResultHash: "r1", FailureIdentityHash: "f1", OutcomeKind: TerminalExecFailure},
		{ToolName: "exec", ArgsHash: "a2", OutcomeKind: ArgumentValidation},
		{ToolName: "exec", ArgsHash: "a2", ResultHash: "r2", FailureIdentityHash: "f1", OutcomeKind: TerminalExecFailure},
	}
	count, latest := Streak(history, "exec", "a2")
	if count != 2 || latest != "r2" {
		t.Fatalf("count=%d latest=%q", count, latest)
	}
}

func TestMissingResultHashDoesNotCount(t *testing.T) {
	history := []Record{
		{ToolName: "search", ArgsHash: "a1", ResultHash: "r1"},
		{ToolName: "search", ArgsHash: "a1"},
		{ToolName: "search", ArgsHash: "a1", ResultHash: "r1"},
	}
	if count, _ := Streak(history, "search", "a1"); count != 2 {
		t.Fatalf("count=%d", count)
	}
}

func TestEmptyHistory(t *testing.T) {
	if count, latest := Streak(nil, "search", "a1"); count != 0 || latest != "" {
		t.Fatalf("count=%d latest=%q", count, latest)
	}
	if count, _ := Streak(records("exec", "a1", "r1"), "exec", "a1"); count != 1 {
		t.Fatalf("exec count=%d", count)
	}
}
