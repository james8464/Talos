// Package processinventory reports Talos-related and audio processes without
// their command lines, so diagnostics never echo prompts or private paths.
package processinventory

import (
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
)

// Entry is one matched process.
type Entry struct {
	PID   int
	Label string
	Audio bool
	Stale bool
}

type pattern struct {
	label string
	words []string
	stale bool
	audio bool
}

// Patterns cover test leftovers that survive a crashed run and anything that
// can play sound. The Daemon and TeamHub are expected and stay out.
var patterns = []pattern{
	{label: "live harness", words: []string{"talos-live-check", "talos-agent-live", "talos-room-live"}, stale: true},
	{label: "headless browser", words: []string{"talos-browser-"}, stale: true},
	{label: "audio", words: []string{"afplay", "/usr/bin/say", "kokoro", "AVSpeech"}, audio: true},
}

// Shells and scanners carry other processes' words in their own arguments;
// matching them would report the check itself.
var ignoredExecutables = map[string]bool{
	"sh": true, "bash": true, "zsh": true, "fish": true, "dash": true,
	"ksh": true, "csh": true, "tcsh": true, "ps": true, "pgrep": true,
	"lsof": true, "rg": true, "grep": true,
}

// Classify returns the match for one command line.
func Classify(args string) (Entry, bool) {
	fields := strings.Fields(args)
	if len(fields) == 0 {
		return Entry{}, false
	}
	executable := strings.ToLower(filepath.Base(fields[0]))
	if ignoredExecutables[executable] {
		return Entry{}, false
	}
	for _, candidate := range patterns {
		for _, word := range candidate.words {
			if strings.Contains(args, word) {
				return Entry{Label: candidate.label, Audio: candidate.audio, Stale: candidate.stale}, true
			}
		}
	}
	return Entry{}, false
}

// Parse reads `ps -axo pid=,ppid=,args=` output.
func Parse(output string) []Entry {
	entries := []Entry{}
	for _, line := range strings.Split(output, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 3 {
			continue
		}
		pid, err := strconv.Atoi(fields[0])
		if err != nil {
			continue
		}
		entry, ok := Classify(strings.Join(fields[2:], " "))
		if !ok {
			continue
		}
		entry.PID = pid
		entries = append(entries, entry)
	}
	return entries
}

// Scan lists matching processes on this machine. An unavailable `ps` yields
// no entries instead of failing diagnostics.
func Scan() []Entry {
	output, err := exec.Command("ps", "-axo", "pid=,ppid=,args=").Output()
	if err != nil {
		return nil
	}
	return Parse(string(output))
}
