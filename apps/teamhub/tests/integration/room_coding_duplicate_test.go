package integration_test

import (
	"context"
	"testing"
)

// TestDuplicateCodingCommandsShareOneJob proves a repeated request does not
// start a second job while the first is still queued or running.
func TestDuplicateCodingCommandsShareOneJob(t *testing.T) {
	allowParallelIntegrationTest(t)
	env, alice, _, roomID := newAgentCLIIdentity(t)
	ctx := context.Background()
	text := "coding job apps/teamhub/internal/roomsenses/answers.go: add a short comment"
	createHumanMessage(t, env, alice, roomID, text, "duplicate-first")
	createHumanMessage(t, env, alice, roomID, text, "duplicate-second")

	var jobs int
	if err := env.pool.QueryRow(ctx, `SELECT count(*) FROM room_coding_jobs WHERE room_id=$1`, roomID).Scan(&jobs); err != nil || jobs != 1 {
		t.Fatalf("duplicate jobs=%d err=%v", jobs, err)
	}
	var duplicateReplies int
	if err := env.pool.QueryRow(ctx, `
		SELECT count(*) FROM messages message
		JOIN actors actor ON actor.id=message.author_actor_id AND actor.tenant_id=message.tenant_id
		WHERE message.room_id=$1 AND actor.kind='agent' AND message.body LIKE 'Coding job % is already queued for %; not adding a duplicate.%'`,
		roomID).Scan(&duplicateReplies); err != nil || duplicateReplies != 1 {
		t.Fatalf("duplicate replies=%d err=%v", duplicateReplies, err)
	}
	var jobID, status string
	if err := env.pool.QueryRow(ctx, `SELECT id,status FROM room_coding_jobs WHERE room_id=$1`, roomID).Scan(&jobID, &status); err != nil {
		t.Fatalf("job: %v", err)
	}
	// Cancelling releases the request: the same command can start again.
	createHumanMessage(t, env, alice, roomID, "coding cancel "+jobID, "duplicate-cancel")
	createHumanMessage(t, env, alice, roomID, text, "duplicate-third")
	var active int
	if err := env.pool.QueryRow(ctx, `SELECT count(*) FROM room_coding_jobs WHERE room_id=$1 AND status IN ('queued','claimed')`, roomID).Scan(&active); err != nil || active != 1 {
		t.Fatalf("active after cancel=%d err=%v", active, err)
	}
	if err := env.pool.QueryRow(ctx, `SELECT count(*) FROM room_coding_jobs WHERE room_id=$1`, roomID).Scan(&jobs); err != nil || jobs != 2 {
		t.Fatalf("all jobs after cancel=%d err=%v", jobs, err)
	}
}
