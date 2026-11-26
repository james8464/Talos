package main

import (
	"bytes"
	"encoding/json"
	"testing"

	"github.com/simplexai-labs/talos/apps/computer/local-daemon/internal/talos/localtools"
)

func TestOverlayFeatureWritesOneHighlight(t *testing.T) {
	var output bytes.Buffer
	channel := newDesktopChannel(&output)
	// Without the hello feature the request is skipped.
	channel.Overlay(localtools.OverlayTarget{X: 1, Y: 2, Width: 72, Height: 72})
	if output.Len() != 0 {
		t.Fatalf("overlay written before hello: %s", output.String())
	}
	channel.receive(desktopLine{Type: "hello", Features: []string{"overlay.v1"}})
	channel.Overlay(localtools.OverlayTarget{X: 10, Y: 20, Width: 72, Height: 72})
	var line struct {
		Type   string  `json:"type"`
		X      float64 `json:"x"`
		Y      float64 `json:"y"`
		Width  float64 `json:"width"`
		Height float64 `json:"height"`
	}
	if err := json.Unmarshal(output.Bytes(), &line); err != nil {
		t.Fatal(err)
	}
	if line.Type != "overlay" || line.X != 10 || line.Y != 20 || line.Width != 72 || line.Height != 72 {
		t.Fatalf("line=%+v", line)
	}
}
