package integration_test

import (
	"context"
	"testing"
)

func TestIdentityTextWritesAgentReply(t *testing.T) {
	allowParallelIntegrationTest(t)
	env, alice, _, roomID := newAgentCLIIdentity(t)
	ctx := context.Background()
	createHumanMessage(t, env, alice, roomID, "My name is Ada.", "identity-intro")
	var reply, kind string
	if err := env.pool.QueryRow(ctx, `
		SELECT message.body, actor.kind
		FROM messages message
		JOIN actors actor ON actor.id=message.author_actor_id AND actor.tenant_id=message.tenant_id
		WHERE message.room_id=$1 AND message.body='Nice to meet you, Ada.'
		ORDER BY message.created_at DESC LIMIT 1`, roomID).Scan(&reply, &kind); err != nil {
		t.Fatalf("agent identity reply: %v", err)
	}
	if reply != "Nice to meet you, Ada." || kind != "agent" {
		t.Fatalf("reply=%q kind=%q", reply, kind)
	}
	createHumanMessage(t, env, alice, roomID, "Do you recognize me?", "identity-recognize")
	var recognized string
	if err := env.pool.QueryRow(ctx, `
		SELECT message.body FROM messages message
		WHERE message.room_id=$1 AND message.body LIKE 'You introduced yourself as Ada.%'
		ORDER BY message.created_at DESC LIMIT 1`, roomID).Scan(&recognized); err != nil {
		t.Fatalf("recognition reply: %v", err)
	}
	createHumanMessage(t, env, alice, roomID, "Forget my name.", "identity-forget")
	var forgot int
	if err := env.pool.QueryRow(ctx, `
		SELECT count(*) FROM messages message
		WHERE message.room_id=$1 AND message.body='I forgot the name you introduced. I still can''t verify identity.'`, roomID).Scan(&forgot); err != nil || forgot != 1 {
		t.Fatalf("forget reply count=%d err=%v", forgot, err)
	}
	var agentMessages int
	if err := env.pool.QueryRow(ctx, `SELECT count(*) FROM messages message
		JOIN actors actor ON actor.id=message.author_actor_id AND actor.tenant_id=message.tenant_id
		WHERE message.room_id=$1 AND actor.kind='agent'`, roomID).Scan(&agentMessages); err != nil {
		t.Fatalf("agent message count: %v", err)
	}
	createHumanMessage(t, env, alice, roomID, "Play some music.", "identity-ordinary")
	var afterOrdinary int
	if err := env.pool.QueryRow(ctx, `SELECT count(*) FROM messages message
		JOIN actors actor ON actor.id=message.author_actor_id AND actor.tenant_id=message.tenant_id
		WHERE message.room_id=$1 AND actor.kind='agent'`, roomID).Scan(&afterOrdinary); err != nil {
		t.Fatalf("agent message count after ordinary text: %v", err)
	}
	if afterOrdinary != agentMessages {
		t.Fatalf("ordinary text drew an Agent reply: %d -> %d", agentMessages, afterOrdinary)
	}
	createHumanMessage(t, env, alice, roomID, "Do you recognize me?", "identity-after")
	var after int
	if err := env.pool.QueryRow(ctx, `
		SELECT count(*) FROM messages message
		WHERE message.room_id=$1 AND message.body='I haven''t confirmed who I''m speaking with.'
		AND message.created_at > (SELECT created_at FROM messages WHERE body LIKE 'I forgot the name%' ORDER BY created_at DESC LIMIT 1)`, roomID).Scan(&after); err != nil || after != 1 {
		t.Fatalf("after-forget reply count=%d err=%v", after, err)
	}
}
