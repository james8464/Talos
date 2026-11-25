package integration_test

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5"

	"github.com/simplexai-labs/talos/apps/teamhub/internal/roomcoding"
	"github.com/simplexai-labs/talos/apps/teamhub/internal/store"
)

type transportFailureExecutor struct{}

func (transportFailureExecutor) Execute(ctx context.Context, job store.RoomCodingJob) (roomcoding.Outcome, error) {
	return roomcoding.Outcome{}, errors.New("connection refused")
}

// TestRoomCodingClaimsRecoverAfterTheLease proves a claimed job whose worker
// disappears is taken by the next worker instead of staying claimed forever.
func TestRoomCodingClaimsRecoverAfterTheLease(t *testing.T) {
	allowParallelIntegrationTest(t)
	env, _, _, roomID := newAgentCLIIdentity(t)
	ctx := context.Background()
	queries := store.New(env.pool)
	agents, err := queries.ListRoomAgentActors(ctx, roomID)
	if err != nil || len(agents) == 0 {
		t.Fatalf("room agents=%d err=%v", len(agents), err)
	}
	if _, err := queries.CreateRoomCodingJob(ctx, store.CreateRoomCodingJobParams{
		ID: "codingjob_recovery", TenantID: acmeTenant, RoomID: roomID,
		RequesterActorID: agents[0].ID, Kind: "plan", Request: "clarify the helper",
	}); err != nil {
		t.Fatalf("create job: %v", err)
	}
	first, err := queries.ClaimNextRoomCodingJob(ctx, store.ClaimNextRoomCodingJobParams{
		ClaimedBy: "dead-computer", LeaseSeconds: 60,
	})
	if err != nil || first.Attempts != 1 {
		t.Fatalf("first claim=%+v err=%v", first, err)
	}
	if _, err := env.pool.Exec(ctx, `UPDATE room_coding_jobs
		SET lease_expires_at=clock_timestamp() - interval '1 second' WHERE id=$1`, first.ID); err != nil {
		t.Fatalf("expire lease: %v", err)
	}
	second, err := queries.ClaimNextRoomCodingJob(ctx, store.ClaimNextRoomCodingJobParams{
		ClaimedBy: "live-computer", LeaseSeconds: 60,
	})
	if err != nil || second.ID != first.ID || second.Attempts != 2 || second.ClaimedBy != "live-computer" {
		t.Fatalf("second claim=%+v err=%v", second, err)
	}
	if err := queries.CompleteRoomCodingJob(ctx, store.CompleteRoomCodingJobParams{
		ID: second.ID, Status: "done", Result: "planned",
	}); err != nil {
		t.Fatalf("complete: %v", err)
	}
	if _, err := queries.ClaimNextRoomCodingJob(ctx, store.ClaimNextRoomCodingJobParams{
		ClaimedBy: "live-computer", LeaseSeconds: 60,
	}); !errors.Is(err, pgx.ErrNoRows) {
		t.Fatalf("claim after completion err=%v", err)
	}
}

// TestRoomCodingWorkerRetriesTransportFailures proves a Computer that cannot
// be reached is retried, then reported to the Room once attempts run out.
func TestRoomCodingWorkerRetriesTransportFailures(t *testing.T) {
	allowParallelIntegrationTest(t)
	env, _, _, roomID := newAgentCLIIdentity(t)
	ctx := context.Background()
	queries := store.New(env.pool)
	agents, err := queries.ListRoomAgentActors(ctx, roomID)
	if err != nil || len(agents) == 0 {
		t.Fatalf("room agents=%d err=%v", len(agents), err)
	}
	if _, err := queries.CreateRoomCodingJob(ctx, store.CreateRoomCodingJobParams{
		ID: "codingjob_retry", TenantID: acmeTenant, RoomID: roomID,
		RequesterActorID: agents[0].ID, Kind: "job", TargetPath: "internal/app/app.go",
		Request: "add a comment",
	}); err != nil {
		t.Fatalf("create job: %v", err)
	}
	published := 0
	worker := &roomcoding.Worker{
		Queries:      queries,
		Executor:     transportFailureExecutor{},
		ClaimedBy:    "test-computer",
		LeaseSeconds: 60,
		MaxAttempts:  2,
		Publish: func(ctx context.Context, roomID, text, key string) error {
			published++
			if !strings.Contains(text, "could not run the job") {
				t.Errorf("reply=%q", text)
			}
			return nil
		},
	}
	if processed, err := worker.RunOnce(ctx); err != nil || !processed {
		t.Fatalf("first attempt processed=%v err=%v", processed, err)
	}
	var status, result string
	var attempts int
	if err := env.pool.QueryRow(ctx, `SELECT status,result,attempts FROM room_coding_jobs WHERE id=$1`, "codingjob_retry").
		Scan(&status, &result, &attempts); err != nil || status != "queued" || attempts != 1 ||
		!strings.Contains(result, "connection refused") {
		t.Fatalf("after retry status=%q result=%q attempts=%d err=%v", status, result, attempts, err)
	}
	if published != 0 {
		t.Fatalf("retry published %d replies", published)
	}
	if processed, err := worker.RunOnce(ctx); err != nil || !processed {
		t.Fatalf("second attempt processed=%v err=%v", processed, err)
	}
	if err := env.pool.QueryRow(ctx, `SELECT status,attempts FROM room_coding_jobs WHERE id=$1`, "codingjob_retry").
		Scan(&status, &attempts); err != nil || status != "failed" || attempts != 2 {
		t.Fatalf("after failure status=%q attempts=%d err=%v", status, attempts, err)
	}
	if published != 1 {
		t.Fatalf("failure published %d replies", published)
	}
}
