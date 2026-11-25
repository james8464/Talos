// Package noprogress counts repeated tool outcomes with unchanged arguments,
// adapted from OpenClaw's tool-loop-no-progress detector.
package noprogress

// OutcomeKind marks records the detector treats specially.
type OutcomeKind string

const (
	// ArgumentValidation is a rejected call that never ran.
	ArgumentValidation OutcomeKind = "argument-validation"
	// TerminalExecFailure is a real terminal failure that may repeat across args.
	TerminalExecFailure OutcomeKind = "terminal-exec-failure"
	// ToolLoopVeto is a provisional signal that needs a concrete outcome anchor.
	ToolLoopVeto OutcomeKind = "tool-loop-veto"
)

// Record is one tool call outcome in a session.
type Record struct {
	ToolName            string
	ArgsHash            string
	ResultHash          string
	FailureIdentityHash string
	OutcomeKind         OutcomeKind
}

// Streak returns how many of the newest outcomes repeated the same result for
// the given tool and arguments, and that result hash.
func Streak(history []Record, toolName, argsHash string) (int, string) {
	repeated, repeatedLatest := count(history, toolName, argsHash, false)
	if toolName != "exec" {
		return repeated, repeatedLatest
	}
	terminal, terminalLatest := count(history, toolName, argsHash, true)
	if terminal > repeated {
		return terminal, terminalLatest
	}
	return repeated, repeatedLatest
}

func count(history []Record, toolName, argsHash string, terminalOnly bool) (int, string) {
	streak := 0
	var latest *Record
	crossedArgumentBoundary := false
	pendingVetoes := 0
	for i := len(history) - 1; i >= 0; i-- {
		record := history[i]
		if terminalOnly && record.OutcomeKind == ArgumentValidation {
			continue
		}
		if record.ToolName != toolName {
			if terminalOnly {
				break
			}
			continue
		}
		if !terminalOnly && record.ArgsHash != argsHash {
			continue
		}
		if record.OutcomeKind == ToolLoopVeto {
			pendingVetoes++
			continue
		}
		if record.ResultHash == "" {
			continue
		}
		if terminalOnly && record.OutcomeKind != TerminalExecFailure {
			break
		}
		if latest == nil {
			copied := record
			latest = &copied
			crossedArgumentBoundary = record.ArgsHash != argsHash
			streak = pendingVetoes + 1
			pendingVetoes = 0
			continue
		}
		if terminalOnly {
			if crossedArgumentBoundary && record.ArgsHash == argsHash {
				break
			}
			if record.ArgsHash != argsHash {
				crossedArgumentBoundary = true
			}
		}
		repeatsSameFailure := terminalOnly &&
			record.FailureIdentityHash != "" &&
			record.FailureIdentityHash == latest.FailureIdentityHash
		if record.ResultHash != latest.ResultHash && !repeatsSameFailure {
			break
		}
		streak += pendingVetoes + 1
		pendingVetoes = 0
	}
	if latest == nil {
		if terminalOnly {
			return 0, ""
		}
		return pendingVetoes, ""
	}
	return streak, latest.ResultHash
}
