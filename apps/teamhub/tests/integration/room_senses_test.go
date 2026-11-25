package integration_test

import (
	"context"
	"testing"
)

// TestSensorQuestionsWriteAgentReplies proves the old Talos hear/see answers
// reach a Room as Agent-authored messages and never invent a camera reading.
func TestSensorQuestionsWriteAgentReplies(t *testing.T) {
	allowParallelIntegrationTest(t)
	env, alice, _, roomID := newAgentCLIIdentity(t)
	ctx := context.Background()
	reply := func(text, key string) string {
		createHumanMessage(t, env, alice, roomID, text, key)
		var body string
		if err := env.pool.QueryRow(ctx, `
			SELECT message.body FROM messages message
			JOIN actors actor ON actor.id=message.author_actor_id AND actor.tenant_id=message.tenant_id
			WHERE message.room_id=$1 AND actor.kind='agent' AND message.body=$2
			ORDER BY message.created_at DESC LIMIT 1`, roomID, text).Scan(&body); err == nil {
			return body
		}
		return ""
	}
	if got := reply("That message arrived as text.", "senses-typed"); got != "" {
		t.Fatalf("seed reply=%q", got)
	}
	createHumanMessage(t, env, alice, roomID, "Can you hear me?", "senses-hear")
	var heard int
	if err := env.pool.QueryRow(ctx, `
		SELECT count(*) FROM messages message
		JOIN actors actor ON actor.id=message.author_actor_id AND actor.tenant_id=message.tenant_id
		WHERE message.room_id=$1 AND actor.kind='agent' AND message.body='That message arrived as text.'`, roomID).Scan(&heard); err != nil || heard != 1 {
		t.Fatalf("hearing reply=%d err=%v", heard, err)
	}
	createHumanMessage(t, env, alice, roomID, "Can you see me?", "senses-see")
	var seen int
	if err := env.pool.QueryRow(ctx, `
		SELECT count(*) FROM messages message
		JOIN actors actor ON actor.id=message.author_actor_id AND actor.tenant_id=message.tenant_id
		WHERE message.room_id=$1 AND actor.kind='agent' AND message.body='I don''t have a current camera reading.'`, roomID).Scan(&seen); err != nil || seen != 1 {
		t.Fatalf("seeing reply=%d err=%v", seen, err)
	}
	var agentMessages int
	if err := env.pool.QueryRow(ctx, `
		SELECT count(*) FROM messages message
		JOIN actors actor ON actor.id=message.author_actor_id AND actor.tenant_id=message.tenant_id
		WHERE message.room_id=$1 AND actor.kind='agent'`, roomID).Scan(&agentMessages); err != nil {
		t.Fatalf("agent messages: %v", err)
	}
	createHumanMessage(t, env, alice, roomID, "Can you hear the rain?", "senses-ordinary")
	var after int
	if err := env.pool.QueryRow(ctx, `
		SELECT count(*) FROM messages message
		JOIN actors actor ON actor.id=message.author_actor_id AND actor.tenant_id=message.tenant_id
		WHERE message.room_id=$1 AND actor.kind='agent'`, roomID).Scan(&after); err != nil || after != agentMessages {
		t.Fatalf("ordinary question drew a reply: %d -> %d err=%v", agentMessages, after, err)
	}
}
