package app

import (
	"context"

	"github.com/simplexai-labs/talos/apps/teamhub/internal/messagecreation"
	"github.com/simplexai-labs/talos/apps/teamhub/internal/roomgoal"
	"github.com/simplexai-labs/talos/apps/teamhub/internal/store"
)

// continueGoal posts the Hermes continuation prompt as the goal creator,
// mentioning the Room's Agent so its next turn starts.
func (a *App) continueGoal(ctx context.Context, goal store.RoomGoal) error {
	creator, err := a.queries.GetActor(ctx, store.GetActorParams{ID: goal.CreatedByActorID, TenantID: goal.TenantID})
	if err != nil {
		return err
	}
	agents, err := a.queries.ListRoomAgentActors(ctx, goal.RoomID)
	if err != nil || len(agents) == 0 {
		return err
	}
	content, err := messagecreation.Normalize([]messagecreation.Segment{{
		Type: "text", Content: roomgoal.ContinuationPrompt(goal.Objective, int(goal.TurnsUsed), int(goal.MaxTurns)),
	}})
	if err != nil {
		return err
	}
	tx, err := a.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if _, err := messagecreation.Create(ctx, a.queries.WithTx(tx), messagecreation.Draft{
		TenantID: goal.TenantID, RoomID: goal.RoomID, Author: creator,
		Content: content, Kind: "conversation", Mentions: []store.Actor{agents[0]},
	}); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

// startGoalWorker builds the judge worker when the local judge is reachable.
func (a *App) startGoalWorker() {
	if a.cfg.NodeServicesURL == "" || a.cfg.NodeServicesInternalToken == "" {
		return
	}
	a.roomGoalWorker = &roomgoal.Worker{
		Queries:  a.queries,
		Judge:    roomgoal.NewHTTPJudge(a.cfg.NodeServicesURL, a.cfg.NodeServicesInternalToken),
		Continue: a.continueGoal,
		Notice:   a.publishAgentMessage,
		Logger:   a.logger,
	}
}
