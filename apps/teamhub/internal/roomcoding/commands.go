// Package roomcoding parses typed Room commands for bounded coding work.
// TeamHub stores accepted commands durably; the Local Computer executes them.
package roomcoding

import (
	"path"
	"regexp"
	"strings"
)

// Command is one accepted coding request.
type Command struct {
	Kind    string // job or plan
	Target  string
	Request string
}

var (
	jobCommand    = regexp.MustCompile(`(?i)^coding job ([^:]{1,300}):\s*(.{1,800})$`)
	planCommand   = regexp.MustCompile(`(?i)^coding plan:\s*(.{1,800})$`)
	listCommand   = regexp.MustCompile(`(?i)^coding jobs$`)
	showCommand   = regexp.MustCompile(`(?i)^coding job (codingjob_[a-z0-9]+)$`)
	cancelCommand = regexp.MustCompile(`(?i)^coding cancel (codingjob_[a-z0-9]+)$`)
)

// Parse returns a command, true, or false when text is not a coding command.
func Parse(text string) (Command, bool, error) {
	words := strings.Join(strings.Fields(strings.TrimSpace(text)), " ")
	if match := jobCommand.FindStringSubmatch(words); match != nil {
		target := strings.TrimSpace(match[1])
		clean := path.Clean(target)
		if strings.HasPrefix(clean, "/") || clean == ".." || strings.HasPrefix(clean, "../") || clean != target {
			return Command{}, true, ErrUnsafeTarget
		}
		return Command{Kind: "job", Target: target, Request: strings.TrimSpace(match[2])}, true, nil
	}
	if match := planCommand.FindStringSubmatch(words); match != nil {
		return Command{Kind: "plan", Request: strings.TrimSpace(match[1])}, true, nil
	}
	if match := showCommand.FindStringSubmatch(words); match != nil {
		return Command{Kind: "show", Target: match[1]}, true, nil
	}
	if match := cancelCommand.FindStringSubmatch(words); match != nil {
		return Command{Kind: "cancel", Target: match[1]}, true, nil
	}
	return Command{}, false, nil
}

// IsList reports the one read-only coding command.
func IsList(text string) bool {
	return listCommand.MatchString(strings.Join(strings.Fields(strings.TrimSpace(text)), " "))
}

// ErrUnsafeTarget rejects absolute and traversal paths.
var ErrUnsafeTarget = &safeTargetError{}

type safeTargetError struct{}

func (*safeTargetError) Error() string {
	return "Coding job rejected: use a relative path inside the project."
}
