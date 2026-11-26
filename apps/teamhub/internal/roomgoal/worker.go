package roomgoal

import (
	"context"
	"errors"
	"log/slog"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/simplexai-labs/talos/apps/teamhub/internal/store"
)

// Continuation wakes the Agent with the next goal instruction.
type Continuation func(ctx context.Context, goal store.RoomGoal) error

// Notice posts an Agent-authored goal outcome.
type Notice func(ctx context.Context, roomID, text, key string) error

// Worker judges active Room goals and advances or stops their turn budget.
type Worker struct {
	Queries  *store.Queries
	Judge    Judge
	Continue Continuation
	Notice   Notice
	Logger   *slog.Logger
}

// RunOnce judges one active goal. It returns false when no goal is active.
func (w *Worker) RunOnce(ctx context.Context) (bool, error) {
	goal, err := w.Queries.NextActiveRoomGoal(ctx)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	lastReply, err := w.Queries.LatestRoomConversationBody(ctx, goal.RoomID)
	if err != nil && !errors.Is(err, pgx.ErrNoRows) {
		return true, err
	}
	decision, err := w.Judge.Judge(ctx, goal.Objective, lastReply)
	if err != nil {
		if w.Logger != nil {
			w.Logger.Warn("room_goal.judge_failed", "room_id", goal.RoomID, "error", err)
		}
		return true, nil
	}
	if _, err := w.Queries.RecordRoomGoalVerdict(ctx, store.RecordRoomGoalVerdictParams{
		RoomID: goal.RoomID, LastVerdict: string(decision.Verdict), LastReason: decision.Reason,
	}); err != nil {
		return true, err
	}
	switch decision.Verdict {
	case Done, Blocked:
		status := string(decision.Verdict)
		if _, err := w.Queries.SetRoomGoalStatus(ctx, store.SetRoomGoalStatusParams{RoomID: goal.RoomID, Status: status}); err != nil {
			return true, err
		}
		if w.Notice != nil {
			text := "Goal achieved: " + goal.Objective
			if decision.Verdict == Blocked {
				text = "Goal blocked: " + goal.Objective
			}
			if decision.Reason != "" {
				text += " (" + decision.Reason + ")"
			}
			if err := w.Notice(ctx, goal.RoomID, text, "goal:"+goal.RoomID+":"+status); err != nil && w.Logger != nil {
				w.Logger.Warn("room_goal.notice_failed", "room_id", goal.RoomID, "error", err)
			}
		}
	case Continue:
		next, err := w.Queries.IncrementRoomGoalTurns(ctx, goal.RoomID)
		if err != nil {
			return true, err
		}
		if next.TurnsUsed >= next.MaxTurns {
			if _, err := w.Queries.SetRoomGoalStatus(ctx, store.SetRoomGoalStatusParams{RoomID: goal.RoomID, Status: "paused"}); err != nil {
				return true, err
			}
			if w.Notice != nil {
				_ = w.Notice(ctx, goal.RoomID, "Goal paused after its turn budget: "+goal.Objective, "goal:"+goal.RoomID+":paused")
			}
			return true, nil
		}
		if w.Continue != nil {
			if err := w.Continue(ctx, next); err != nil {
				if w.Logger != nil {
					w.Logger.Warn("room_goal.continue_failed", "room_id", goal.RoomID, "error", err)
				}
			}
		}
	}
	return true, nil
}

// Run judges one goal per interval until ctx is cancelled.
func (w *Worker) Run(ctx context.Context, interval time.Duration) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if _, err := w.RunOnce(ctx); err != nil && w.Logger != nil {
				w.Logger.Warn("room_goal.worker_failed", "error", err)
			}
		}
	}
}
