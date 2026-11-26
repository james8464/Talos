package localtools

import "testing"

func TestOutlineInputUsesPointerCoordinates(t *testing.T) {
	var got []OverlayTarget
	manager := &Manager{options: Options{Overlay: func(target OverlayTarget) {
		got = append(got, target)
	}}}
	manager.outlineInput(map[string]any{"x": 200.0, "y": 120.0})
	if len(got) != 1 {
		t.Fatalf("overlays=%+v", got)
	}
	if got[0].X != 164 || got[0].Y != 84 || got[0].Width != 72 || got[0].Height != 72 {
		t.Fatalf("target=%+v", got[0])
	}
	// Element-targeted inputs have no point to outline.
	manager.outlineInput(map[string]any{"element_token": "tok"})
	if len(got) != 1 {
		t.Fatalf("element input outlined: %+v", got)
	}
}
