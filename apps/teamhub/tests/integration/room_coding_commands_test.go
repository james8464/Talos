package integration_test

import (
	"context"
	"strings"
	"testing"
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
		AND message.body LIKE 'Coding job #% queued for internal/app/app.go (verify-only).'
		ORDER BY message.created_at DESC LIMIT 1`, roomID).Scan(&reply); err != nil {
		t.Fatalf("coding job reply: %v", err)
	}
	createHumanMessage(t, env, alice, roomID, "coding plan: clarify the helper", "coding-plan")
	var plan string
	if err := env.pool.QueryRow(ctx, `
		SELECT message.body FROM messages message
		JOIN actors actor ON actor.id=message.author_actor_id AND actor.tenant_id=message.tenant_id
		WHERE message.room_id=$1 AND actor.kind='agent'
		AND message.body LIKE 'Coding plan #% queued (verify-only).'
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
}
