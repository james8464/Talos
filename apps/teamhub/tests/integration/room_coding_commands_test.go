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
	claimed, err := queries.ClaimNextRoomCodingJob(ctx, store.ClaimNextRoomCodingJobParams{
		ClaimedBy: "computer_test", LeaseSeconds: 900,
	})
	if err != nil {
		t.Fatalf("claim coding job: %v", err)
	}
	if claimed.Status != "claimed" || claimed.ClaimedBy != "computer_test" || claimed.RoomID != roomID {
		t.Fatalf("claimed=%+v", claimed)
	}
	if completed, err := queries.CompleteRoomCodingJob(ctx, store.CompleteRoomCodingJobParams{
		ID: claimed.ID, Status: "done", Result: "sandbox passed", AttemptHashes: []string{},
	}); err != nil || completed != 1 {
		t.Fatalf("complete coding job=%d err=%v", completed, err)
	}
	var status, result string
	if err := env.pool.QueryRow(ctx, `SELECT status,result FROM room_coding_jobs WHERE id=$1`, claimed.ID).Scan(&status, &result); err != nil || status != "done" || result != "sandbox passed" {
		t.Fatalf("completed status=%q result=%q err=%v", status, result, err)
	}
	var queued int
	if err := env.pool.QueryRow(ctx, `SELECT count(*) FROM room_coding_jobs WHERE room_id=$1 AND status='queued'`, roomID).Scan(&queued); err != nil || queued != 1 {
		t.Fatalf("remaining queued=%d err=%v", queued, err)
	}
	// The requester can cancel a job that is still queued.
	var remaining string
	if err := env.pool.QueryRow(ctx, `SELECT id FROM room_coding_jobs WHERE room_id=$1 AND status='queued'`, roomID).Scan(&remaining); err != nil {
		t.Fatalf("remaining job: %v", err)
	}
	createHumanMessage(t, env, alice, roomID, "coding cancel "+remaining, "coding-cancel")
	var cancelled int
	if err := env.pool.QueryRow(ctx, `SELECT count(*) FROM room_coding_jobs WHERE id=$1 AND status='cancelled'`, remaining).Scan(&cancelled); err != nil || cancelled != 1 {
		t.Fatalf("cancelled=%d err=%v", cancelled, err)
	}
	createHumanMessage(t, env, alice, roomID, "coding jobs", "coding-list-cancelled")
	var listAfter string
	if err := env.pool.QueryRow(ctx, `
		SELECT message.body FROM messages message
		JOIN actors actor ON actor.id=message.author_actor_id AND actor.tenant_id=message.tenant_id
		WHERE message.room_id=$1 AND actor.kind='agent' AND message.body LIKE 'Coding jobs: %'
		ORDER BY message.created_at DESC LIMIT 1`, roomID).Scan(&listAfter); err != nil {
		t.Fatalf("list after cancel: %v", err)
	}
	if !strings.Contains(listAfter, remaining+" plan cancelled") {
		t.Fatalf("list after cancel=%q", listAfter)
	}
}
