package integration_test

import (
	"context"
	"testing"
)

// TestRoomGoalCommandLifecycle drives the Hermes-style standing goal commands
// through a Room and checks the persisted status.
func TestRoomGoalCommandLifecycle(t *testing.T) {
	allowParallelIntegrationTest(t)
	env, alice, _, roomID := newAgentCLIIdentity(t)
	ctx := context.Background()
	createHumanMessage(t, env, alice, roomID, "goal: ship the weekly report", "goal-set")
	var status, objective string
	var maxTurns int
	if err := env.pool.QueryRow(ctx, `SELECT status,objective,max_turns FROM room_goals WHERE room_id=$1`, roomID).
		Scan(&status, &objective, &maxTurns); err != nil || status != "active" || objective != "ship the weekly report" || maxTurns != 20 {
		t.Fatalf("goal status=%q objective=%q max=%d err=%v", status, objective, maxTurns, err)
	}
	createHumanMessage(t, env, alice, roomID, "goal status", "goal-status")
	createHumanMessage(t, env, alice, roomID, "goal pause", "goal-pause")
	if err := env.pool.QueryRow(ctx, `SELECT status FROM room_goals WHERE room_id=$1`, roomID).Scan(&status); err != nil || status != "paused" {
		t.Fatalf("paused status=%q err=%v", status, err)
	}
	createHumanMessage(t, env, alice, roomID, "goal resume", "goal-resume")
	if err := env.pool.QueryRow(ctx, `SELECT status FROM room_goals WHERE room_id=$1`, roomID).Scan(&status); err != nil || status != "active" {
		t.Fatalf("resumed status=%q err=%v", status, err)
	}
	createHumanMessage(t, env, alice, roomID, "goal clear", "goal-clear")
	if err := env.pool.QueryRow(ctx, `SELECT status FROM room_goals WHERE room_id=$1`, roomID).Scan(&status); err != nil || status != "cleared" {
		t.Fatalf("cleared status=%q err=%v", status, err)
	}
	createHumanMessage(t, env, alice, roomID, "goal status", "goal-status-cleared")
	var replies int
	if err := env.pool.QueryRow(ctx, `
		SELECT count(*) FROM messages message
		JOIN actors actor ON actor.id=message.author_actor_id AND actor.tenant_id=message.tenant_id
		WHERE message.room_id=$1 AND actor.kind='agent'
		AND message.body IN ('No standing goal is set.','Goal active: ship the weekly report (0/20 turns).','Goal paused: ship the weekly report.','Goal resumed: ship the weekly report.','Goal cleared.')`,
		roomID).Scan(&replies); err != nil || replies != 5 {
		t.Fatalf("goal replies=%d err=%v", replies, err)
	}
	var setReply int
	if err := env.pool.QueryRow(ctx, `SELECT count(*) FROM messages message
		JOIN actors actor ON actor.id=message.author_actor_id AND actor.tenant_id=message.tenant_id
		WHERE message.room_id=$1 AND actor.kind='agent'
		AND message.body='Goal set (20-turn budget): ship the weekly report'`, roomID).Scan(&setReply); err != nil || setReply != 1 {
		t.Fatalf("goal set reply=%d err=%v", setReply, err)
	}
}
