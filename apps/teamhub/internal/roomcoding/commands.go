// Package roomcoding parses typed Room commands for bounded coding work. The
// queue is in-memory; execution stays verify-only and is a separate worker.
package roomcoding

import (
	"fmt"
	"path"
	"regexp"
	"strings"
	"sync"
)

type job struct {
	kind    string
	target  string
	request string
	room    string
}

// Queue accepts coding commands and reports what was queued.
type Queue struct {
	mu   sync.Mutex
	next int
	jobs []job
}

// New returns an empty queue.
func New() *Queue { return &Queue{} }

var (
	jobCommand  = regexp.MustCompile(`(?i)^coding job ([^:]{1,300}):\s*(.{1,800})$`)
	planCommand = regexp.MustCompile(`(?i)^coding plan:\s*(.{1,800})$`)
	listCommand = regexp.MustCompile(`(?i)^coding jobs$`)
)

// Handle returns a deterministic reply, or "" when text is not a coding command.
func (q *Queue) Handle(room, text string) string {
	words := strings.Join(strings.Fields(strings.TrimSpace(text)), " ")
	q.mu.Lock()
	defer q.mu.Unlock()
	if listCommand.MatchString(words) {
		if len(q.jobs) == 0 {
			return "No coding jobs have been queued."
		}
		lines := make([]string, 0, len(q.jobs))
		for index, item := range q.jobs {
			lines = append(lines, fmt.Sprintf("#%d %s %s", index+1, item.kind, item.target))
		}
		return "Coding jobs: " + strings.Join(lines, "; ") + "."
	}
	if match := jobCommand.FindStringSubmatch(words); match != nil {
		target := strings.TrimSpace(match[1])
		clean := path.Clean(target)
		if strings.HasPrefix(clean, "/") || clean == ".." || strings.HasPrefix(clean, "../") || clean != target {
			return "Coding job rejected: use a relative path inside the project."
		}
		q.next++
		q.jobs = append(q.jobs, job{kind: "job", target: target, request: strings.TrimSpace(match[2]), room: room})
		return fmt.Sprintf("Coding job #%d queued for %s (verify-only).", q.next, target)
	}
	if match := planCommand.FindStringSubmatch(words); match != nil {
		q.next++
		q.jobs = append(q.jobs, job{kind: "plan", request: strings.TrimSpace(match[1]), room: room})
		return fmt.Sprintf("Coding plan #%d queued (verify-only).", q.next)
	}
	return ""
}
