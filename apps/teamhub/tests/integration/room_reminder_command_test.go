package integration_test

import (
	"context"
	"strings"
	"testing"
)

// TestRoomReminderCommandCreatesAndFires proves the typed reminder command
// creates a one-shot Room Routine and that it delivers through the worker.
func TestRoomReminderCommandCreatesAndFires(t *testing.T) {
	allowParallelIntegrationTest(t)
	env, alice, _, roomID := newAgentCLIIdentity(t)
	ctx := context.Background()
	createHumanMessage(t, env, alice, roomID, "reminder in 10 minutes: stretch", "reminder-command")

	var reply string
	if err := env.pool.QueryRow(ctx, `
		SELECT message.body FROM messages message
		JOIN actors actor ON actor.id=message.author_actor_id AND actor.tenant_id=message.tenant_id
		WHERE message.room_id=$1 AND actor.kind='agent'
		AND message.body='Okay. I''ll remind you in 10 minutes to stretch.'
		ORDER BY message.created_at DESC LIMIT 1`, roomID).Scan(&reply); err != nil {
		t.Fatalf("reminder reply: %v", err)
	}
	var id, instruction, kind string
	var runIn float64
	if err := env.pool.QueryRow(ctx, `
		SELECT id,instruction,cadence_kind,EXTRACT(EPOCH FROM (run_at-clock_timestamp()))
		FROM room_reminders WHERE room_id=$1`, roomID).Scan(&id, &instruction, &kind, &runIn); err != nil {
		t.Fatalf("reminder row: %v", err)
	}
	if instruction != "stretch" || kind != "once" || runIn < 540 || runIn > 660 {
		t.Fatalf("reminder instruction=%q kind=%q run_in=%v", instruction, kind, runIn)
	}
	if _, err := env.pool.Exec(ctx, `UPDATE room_reminders SET next_run_at=now()-interval '5 minutes' WHERE id=$1`, id); err != nil {
		t.Fatalf("make reminder due: %v", err)
	}
	fires := 0
	messageID := ""
	for _, result := range runReminderWorker(t, env) {
		if result.ReminderID == id {
			fires++
			messageID = result.MessageID
		}
	}
	if fires != 1 || messageID == "" {
		t.Fatalf("reminder fires=%d message=%q", fires, messageID)
	}
	var body string
	if err := env.pool.QueryRow(ctx, `SELECT body FROM messages WHERE id=$1`, messageID).Scan(&body); err != nil {
		t.Fatalf("fired message: %v", err)
	}
	if !strings.Contains(body, "stretch") {
		t.Fatalf("fired body=%q", body)
	}
}
