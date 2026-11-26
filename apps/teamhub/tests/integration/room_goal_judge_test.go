package integration_test

import (
	"context"
	"testing"

	"github.com/simplexai-labs/talos/apps/teamhub/internal/roomgoal"
	"github.com/simplexai-labs/talos/apps/teamhub/internal/store"
)

type sequenceJudge struct {
	decisions []roomgoal.Decision
	calls     int
}

func (j *sequenceJudge) Judge(ctx context.Context, objective, lastReply string) (roomgoal.Decision, error) {
	index := j.calls
	j.calls++
	if index >= len(j.decisions) {
		index = len(j.decisions) - 1
	}
	return j.decisions[index], nil
}

func TestRoomGoalJudgeLoop(t *testing.T) {
	allowParallelIntegrationTest(t)
	env, alice, _, roomID := newAgentCLIIdentity(t)
	ctx := context.Background()
	createHumanMessage(t, env, alice, roomID, "goal: ship the weekly report", "goal-loop-set")
	queries := store.New(env.pool)
	var continued, notices int
	judge := &sequenceJudge{decisions: []roomgoal.Decision{
		{Verdict: roomgoal.Continue, Reason: "not shipped"},
		{Verdict: roomgoal.Done, Reason: "shipped"},
	}}
	worker := &roomgoal.Worker{
		Queries: queries, Judge: judge,
		Continue: func(ctx context.Context, goal store.RoomGoal) error {
			continued++
			return nil
		},
		Notice: func(ctx context.Context, roomID, text, key string) error {
			notices++
			return nil
		},
	}
	if processed, err := worker.RunOnce(ctx); err != nil || !processed {
		t.Fatalf("continue processed=%v err=%v", processed, err)
	}
	var turns int32
	var status string
	if err := env.pool.QueryRow(ctx, `SELECT turns_used,status FROM room_goals WHERE room_id=$1`, roomID).Scan(&turns, &status); err != nil || turns != 1 || status != "active" {
		t.Fatalf("after continue turns=%d status=%q err=%v", turns, status, err)
	}
	if continued != 1 {
		t.Fatalf("continuations=%d", continued)
	}
	if processed, err := worker.RunOnce(ctx); err != nil || !processed {
		t.Fatalf("done processed=%v err=%v", processed, err)
	}
	if err := env.pool.QueryRow(ctx, `SELECT status FROM room_goals WHERE room_id=$1`, roomID).Scan(&status); err != nil || status != "done" {
		t.Fatalf("done status=%q err=%v", status, err)
	}
	if notices != 1 {
		t.Fatalf("notices=%d", notices)
	}
	// A goal with one turn left pauses instead of waking the Agent again.
	createHumanMessage(t, env, alice, roomID, "goal: ship the weekly report", "goal-loop-reset")
	if _, err := env.pool.Exec(ctx, `UPDATE room_goals SET max_turns=1 WHERE room_id=$1`, roomID); err != nil {
		t.Fatal(err)
	}
	worker.Judge = &sequenceJudge{decisions: []roomgoal.Decision{{Verdict: roomgoal.Continue, Reason: "keep going"}}}
	if processed, err := worker.RunOnce(ctx); err != nil || !processed {
		t.Fatalf("budget processed=%v err=%v", processed, err)
	}
	if err := env.pool.QueryRow(ctx, `SELECT status FROM room_goals WHERE room_id=$1`, roomID).Scan(&status); err != nil || status != "paused" {
		t.Fatalf("paused status=%q err=%v", status, err)
	}
}
