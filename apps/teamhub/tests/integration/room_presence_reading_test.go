package integration_test

import (
	"fmt"
	"net/http"
	"testing"

	"github.com/simplexai-labs/talos/apps/teamhub/internal/roomsenses"
)

// TestPresenceReadingAnswersSeeing drives one consented Settings check into a
// presence reading, then asks the Room about it.
func TestPresenceReadingAnswersSeeing(t *testing.T) {
	allowParallelIntegrationTest(t)
	env, alice, _, roomID := newAgentCLIIdentity(t)
	ask := func(text, key, want string) {
		createHumanMessage(t, env, alice, roomID, text, key)
		var replies int
		if err := env.pool.QueryRow(t.Context(), `
			SELECT count(*) FROM messages message
			JOIN actors actor ON actor.id=message.author_actor_id AND actor.tenant_id=message.tenant_id
			WHERE message.room_id=$1 AND actor.kind='agent' AND message.body=$2`, roomID, want).Scan(&replies); err != nil || replies != 1 {
			t.Fatalf("%q reply=%d err=%v", text, replies, err)
		}
	}
	ask("Can you see me?", "presence-before", roomsenses.Unknown)
	for i, tc := range []struct {
		people int
		want   string
	}{
		{2, roomsenses.Visible},
		{0, roomsenses.Empty},
	} {
		status, _, body := env.request(alice, http.MethodPost, "/v0/presence-readings",
			map[string]any{"people": tc.people, "faces": tc.people}, "")
		if status != http.StatusNoContent {
			t.Fatalf("record people=%d status=%d body=%s", tc.people, status, body)
		}
		ask("Is anyone in the room?", fmt.Sprintf("presence-see-%d", i), tc.want)
	}
	status, _, _ := env.request(alice, http.MethodPost, "/v0/presence-readings",
		map[string]any{"people": -1, "faces": 0}, "")
	if status != http.StatusBadRequest {
		t.Fatalf("invalid reading status=%d", status)
	}
}
