package integration_test

import (
	"context"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/simplexai-labs/talos/apps/teamhub/internal/reminder"
)

// TestAgentOneShotPastGraceIsRetired proves a stale Agent follow-up does not
// arrive hours late, while a Human reminder still fires.
func TestAgentOneShotPastGraceIsRetired(t *testing.T) {
	allowParallelIntegrationTest(t)
	env, alice, _, roomID := newAgentCLIIdentity(t)
	ctx := context.Background()
	id := "routine_stale_" + strings.ReplaceAll(time.Now().UTC().Format("150405.000000"), ".", "")
	if _, err := env.pool.Exec(ctx, `
		INSERT INTO room_reminders (
			id,tenant_id,room_id,created_by_actor_id,name,instruction,
			cadence_kind,timezone,run_at,enabled,next_run_at
		) VALUES ($1,$2,$3,'actor_acme_official','Stale follow-up','stale instruction',
			'once','UTC',now()-interval '10 minutes',true,now()-interval '10 minutes')`,
		id, acmeTenant, roomID); err != nil {
		t.Fatalf("insert stale follow-up: %v", err)
	}
	for _, result := range runReminderWorker(t, env) {
		if result.ReminderID == id {
			t.Fatalf("stale Agent follow-up fired: %+v", result)
		}
	}
	var next *time.Time
	var enabled bool
	if err := env.pool.QueryRow(ctx, `SELECT next_run_at,enabled FROM room_reminders WHERE id=$1`, id).
		Scan(&next, &enabled); err != nil {
		t.Fatalf("stale follow-up row: %v", err)
	}
	if next != nil {
		t.Fatalf("stale follow-up still scheduled at %s", next)
	}
	var delivered int
	if err := env.pool.QueryRow(ctx, `SELECT count(*) FROM messages WHERE room_id=$1 AND body LIKE '%stale instruction%'`, roomID).
		Scan(&delivered); err != nil || delivered != 0 {
		t.Fatalf("stale follow-up messages=%d err=%v", delivered, err)
	}

	// The same overdue shape from a Human keeps its reminder behavior.
	runAt := time.Now().Add(time.Hour)
	status, _, body := env.request(alice, http.MethodPost, "/v0/rooms/"+roomID+"/routines", map[string]any{
		"name": "Late human reminder", "instruction": "late human instruction",
		"cadence": map[string]any{"kind": "once", "timezone": "UTC", "run_at": runAt.Format(time.RFC3339)},
	}, "late-human-reminder")
	if status != http.StatusCreated {
		t.Fatalf("create human reminder: %d %s", status, body)
	}
	var created reminder.Reminder
	decode(t, body, &created)
	if _, err := env.pool.Exec(ctx, `UPDATE room_reminders SET run_at=now()-interval '10 minutes',next_run_at=now()-interval '10 minutes' WHERE id=$1`, created.ID); err != nil {
		t.Fatalf("backdate human reminder: %v", err)
	}
	fires := 0
	for _, result := range runReminderWorker(t, env) {
		if result.ReminderID == created.ID {
			fires++
		}
	}
	if fires != 1 {
		t.Fatalf("human reminder fires=%d want 1", fires)
	}
}
