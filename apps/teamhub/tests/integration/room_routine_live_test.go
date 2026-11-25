package integration_test

import (
	"context"
	"net/http"
	"strings"
	"testing"

	"github.com/simplexai-labs/talos/apps/teamhub/internal/reminder"
)

// TestRoomRoutineFiresAsAgentMessage drives a one-shot Room routine through
// the public API and the reminder worker, then reads the Agent's wake message.
func TestRoomRoutineFiresAsAgentMessage(t *testing.T) {
	allowParallelIntegrationTest(t)
	env, alice, _, roomID := newAgentCLIIdentity(t)
	status, _, body := env.request(alice, http.MethodPost, "/v0/rooms/"+roomID+"/routines", map[string]any{
		"name":             "Live routine proof",
		"instruction":      "Routine proof instruction",
		"mention_actor_id": "actor_acme_official",
		"cadence": map[string]any{
			"kind": "every_n_minutes", "timezone": "UTC", "interval_minutes": 60,
		},
	}, "routine-live-create")
	if status != http.StatusCreated {
		t.Fatalf("create routine: %d %s", status, body)
	}
	var created reminder.Reminder
	decode(t, body, &created)
	if _, err := env.pool.Exec(context.Background(),
		`UPDATE room_reminders SET next_run_at=now()-interval '5 minutes' WHERE id=$1`, created.ID); err != nil {
		t.Fatalf("make routine due: %v", err)
	}
	fires := 0
	messageID := ""
	for _, result := range runReminderWorker(t, env) {
		if result.ReminderID == created.ID {
			fires++
			messageID = result.MessageID
		}
	}
	if fires != 1 || messageID == "" {
		t.Fatalf("routine fires=%d message=%q want 1", fires, messageID)
	}
	var wakeBody, mentions string
	if err := env.pool.QueryRow(context.Background(),
		`SELECT body, mentioned_actor_ids::text FROM messages WHERE id=$1`, messageID).
		Scan(&wakeBody, &mentions); err != nil {
		t.Fatalf("Routine wake message: %v", err)
	}
	if !strings.Contains(wakeBody, "Routine proof instruction") {
		t.Fatalf("wake body=%q", wakeBody)
	}
	if !strings.Contains(mentions, "actor_acme_official") {
		t.Fatalf("wake mentions=%q", mentions)
	}
}
