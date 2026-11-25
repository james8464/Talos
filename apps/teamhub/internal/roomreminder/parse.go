// Package roomreminder parses the typed Room reminder command.
package roomreminder

import (
	"regexp"
	"strconv"
	"strings"
	"time"
)

// Command is one parsed reminder request.
type Command struct {
	Delay time.Duration
	Task  string
}

var pattern = regexp.MustCompile(`(?i)^reminder in ([0-9]{1,4})\s*(m|min|minute|minutes|h|hour|hours|d|day|days):\s*(.{1,200})$`)

// Parse accepts `reminder in 10m: task` and returns its delay and task.
func Parse(text string) (Command, bool) {
	words := strings.Join(strings.Fields(strings.TrimSpace(text)), " ")
	match := pattern.FindStringSubmatch(words)
	if match == nil {
		return Command{}, false
	}
	amount, unit, task := match[1], strings.ToLower(match[2]), strings.TrimSpace(match[3])
	minutes, err := strconv.Atoi(amount)
	if err != nil {
		return Command{}, false
	}
	switch {
	case strings.HasPrefix(unit, "m"):
	case strings.HasPrefix(unit, "h"):
		minutes *= 60
	default:
		minutes *= 24 * 60
	}
	delay := time.Duration(minutes) * time.Minute
	if delay < time.Minute || delay > 30*24*time.Hour || task == "" {
		return Command{}, false
	}
	return Command{Delay: delay, Task: task}, true
}

// Describe renders a delay as the largest whole unit it fits.
func Describe(delay time.Duration) string {
	switch {
	case delay >= 24*time.Hour && delay%(24*time.Hour) == 0:
		days := int(delay / (24 * time.Hour))
		if days == 1 {
			return "1 day"
		}
		return strconv.Itoa(days) + " days"
	case delay >= time.Hour && delay%time.Hour == 0:
		hours := int(delay / time.Hour)
		if hours == 1 {
			return "1 hour"
		}
		return strconv.Itoa(hours) + " hours"
	default:
		minutes := int(delay / time.Minute)
		if minutes == 1 {
			return "1 minute"
		}
		return strconv.Itoa(minutes) + " minutes"
	}
}
