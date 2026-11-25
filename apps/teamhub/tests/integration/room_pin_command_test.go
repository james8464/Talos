package integration_test

import (
	"context"
	"testing"
	"time"
)

// TestRoomPinCommandPinsForTheRequester proves the typed Room command pins and
// unpins the Room for the Human who asked.
func TestRoomPinCommandPinsForTheRequester(t *testing.T) {
	allowParallelIntegrationTest(t)
	env, alice, _, roomID := newAgentCLIIdentity(t)
	ctx := context.Background()
	createHumanMessage(t, env, alice, roomID, "pin this room", "pin-command")
	createHumanMessage(t, env, alice, roomID, "unpin this room", "unpin-command")

	replies := map[string]int{}
	rows, err := env.pool.Query(ctx, `
		SELECT message.body FROM messages message
		JOIN actors actor ON actor.id=message.author_actor_id AND actor.tenant_id=message.tenant_id
		WHERE message.room_id=$1 AND actor.kind='agent'
		AND message.body IN ('Pinned this Room.','Unpinned this Room.')`, roomID)
	if err != nil {
		t.Fatalf("pin replies: %v", err)
	}
	for rows.Next() {
		var body string
		if err := rows.Scan(&body); err != nil {
			t.Fatal(err)
		}
		replies[body]++
	}
	rows.Close()
	if replies["Pinned this Room."] != 1 || replies["Unpinned this Room."] != 1 {
		t.Fatalf("pin replies=%v", replies)
	}
	var pinned *time.Time
	if err := env.pool.QueryRow(ctx, `
		SELECT state.pinned_at FROM room_member_states state
		JOIN actors actor ON actor.id=state.actor_id
		WHERE state.room_id=$1 AND actor.kind='human'`, roomID).Scan(&pinned); err != nil {
		t.Fatalf("pin state: %v", err)
	}
	if pinned != nil {
		t.Fatalf("pin still set at %s after unpin", pinned)
	}
}
