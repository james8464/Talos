package integration_test

import (
	"context"
	"strings"
	"testing"

	"github.com/simplexai-labs/talos/apps/teamhub/internal/store"
)

func TestCodingCommandsWriteAgentReplies(t *testing.T) {
	allowParallelIntegrationTest(t)
	env, alice, _, roomID := newAgentCLIIdentity(t)
	ctx := context.Background()
	createHumanMessage(t, env, alice, roomID,
		"coding job internal/app/app.go: add a short comment", "coding-job")
	var reply string
	if err := env.pool.QueryRow(ctx, `
		SELECT message.body FROM messages message
		JOIN actors actor ON actor.id=message.author_actor_id AND actor.tenant_id=message.tenant_id
		WHERE message.room_id=$1 AND actor.kind='agent'
		AND message.body LIKE 'Coding job codingjob_% queued for internal/app/app.go (verify-only).'
		ORDER BY message.created_at DESC LIMIT 1`, roomID).Scan(&reply); err != nil {
		t.Fatalf("coding job reply: %v", err)
	}
	createHumanMessage(t, env, alice, roomID, "coding plan: clarify the helper", "coding-plan")
	var plan string
	if err := env.pool.QueryRow(ctx, `
		SELECT message.body FROM messages message
		JOIN actors actor ON actor.id=message.author_actor_id AND actor.tenant_id=message.tenant_id
		WHERE message.room_id=$1 AND actor.kind='agent'
		AND message.body LIKE 'Coding plan codingjob_% queued (verify-only).'
		ORDER BY message.created_at DESC LIMIT 1`, roomID).Scan(&plan); err != nil {
		t.Fatalf("coding plan reply: %v", err)
	}
	createHumanMessage(t, env, alice, roomID, "coding jobs", "coding-list")
	var list string
	if err := env.pool.QueryRow(ctx, `
		SELECT message.body FROM messages message
		JOIN actors actor ON actor.id=message.author_actor_id AND actor.tenant_id=message.tenant_id
		WHERE message.room_id=$1 AND actor.kind='agent' AND message.body LIKE 'Coding jobs: %'
		ORDER BY message.created_at DESC LIMIT 1`, roomID).Scan(&list); err != nil {
		t.Fatalf("coding list reply: %v", err)
	}
	if !strings.Contains(list, "internal/app/app.go") || !strings.Contains(list, "plan") {
		t.Fatalf("coding list=%q", list)
	}
	var jobs int
	if err := env.pool.QueryRow(ctx, `SELECT count(*) FROM room_coding_jobs WHERE room_id=$1 AND status='queued'`, roomID).Scan(&jobs); err != nil || jobs != 2 {
		t.Fatalf("durable coding jobs=%d err=%v", jobs, err)
	}
	// A worker claims one row and completes it; the other stays queued.
	queries := store.New(env.pool)
	claimed, err := queries.ClaimNextRoomCodingJob(ctx, "computer_test")
	if err != nil {
		t.Fatalf("claim coding job: %v", err)
	}
	if claimed.Status != "claimed" || claimed.ClaimedBy != "computer_test" || claimed.RoomID != roomID {
		t.Fatalf("claimed=%+v", claimed)
	}
	if err := queries.CompleteRoomCodingJob(ctx, store.CompleteRoomCodingJobParams{
		ID: claimed.ID, Status: "done", Result: "sandbox passed",
	}); err != nil {
		t.Fatalf("complete coding job: %v", err)
	}
	var status, result string
	if err := env.pool.QueryRow(ctx, `SELECT status,result FROM room_coding_jobs WHERE id=$1`, claimed.ID).Scan(&status, &result); err != nil || status != "done" || result != "sandbox passed" {
		t.Fatalf("completed status=%q result=%q err=%v", status, result, err)
	}
	var queued int
	if err := env.pool.QueryRow(ctx, `SELECT count(*) FROM room_coding_jobs WHERE room_id=$1 AND status='queued'`, roomID).Scan(&queued); err != nil || queued != 1 {
		t.Fatalf("remaining queued=%d err=%v", queued, err)
	}
}
