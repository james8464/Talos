package integration_test

import (
	"context"
	"strings"
	"testing"

	"github.com/simplexai-labs/talos/apps/teamhub/internal/roomcoding"
	"github.com/simplexai-labs/talos/apps/teamhub/internal/store"
)

type sandboxFailureExecutor struct{ calls int }

func (e *sandboxFailureExecutor) Execute(ctx context.Context, job store.RoomCodingJob) (roomcoding.Outcome, error) {
	e.calls++
	return roomcoding.Outcome{Status: "failed", Message: "verification failed", Failure: "same output"}, nil
}

// TestRoomCodingWorkerStopsRepeatedSandboxFailures proves the no-progress
// streak stops a second identical verification failure instead of retrying.
func TestRoomCodingWorkerStopsRepeatedSandboxFailures(t *testing.T) {
	allowParallelIntegrationTest(t)
	env, _, _, roomID := newAgentCLIIdentity(t)
	ctx := context.Background()
	queries := store.New(env.pool)
	agents, err := queries.ListRoomAgentActors(ctx, roomID)
	if err != nil || len(agents) == 0 {
		t.Fatalf("room agents=%d err=%v", len(agents), err)
	}
	if _, err := queries.CreateRoomCodingJob(ctx, store.CreateRoomCodingJobParams{
		ID: "codingjob_sandbox", TenantID: acmeTenant, RoomID: roomID,
		RequesterActorID: agents[0].ID, Kind: "job", TargetPath: "main.go",
		Request: "add a comment",
	}); err != nil {
		t.Fatalf("create job: %v", err)
	}
	published := []string{}
	executor := &sandboxFailureExecutor{}
	worker := &roomcoding.Worker{
		Queries: queries, Executor: executor, ClaimedBy: "test-computer",
		LeaseSeconds: 60, MaxAttempts: 4,
		Publish: func(ctx context.Context, roomID, text, key string) error {
			published = append(published, text)
			return nil
		},
	}
	if processed, err := worker.RunOnce(ctx); err != nil || !processed {
		t.Fatalf("first attempt processed=%v err=%v", processed, err)
	}
	var status string
	var attempts, hashes int
	var retryIn float64
	if err := env.pool.QueryRow(ctx, `SELECT status,attempts,cardinality(attempt_hashes),
		EXTRACT(EPOCH FROM (next_attempt_at-clock_timestamp())) FROM room_coding_jobs WHERE id=$1`, "codingjob_sandbox").
		Scan(&status, &attempts, &hashes, &retryIn); err != nil || status != "queued" || attempts != 1 || hashes != 1 || retryIn < 10 {
		t.Fatalf("after first failure status=%q attempts=%d hashes=%d retry_in=%v err=%v", status, attempts, hashes, retryIn, err)
	}
	if len(published) != 1 || !strings.Contains(published[0], "failed the sandbox check; retrying in 15s (attempt 1 of 4)") {
		t.Fatalf("retry notices=%q", published)
	}
	if _, err := env.pool.Exec(ctx, `UPDATE room_coding_jobs SET next_attempt_at=clock_timestamp() - interval '1 second' WHERE id=$1`, "codingjob_sandbox"); err != nil {
		t.Fatalf("release retry: %v", err)
	}
	if processed, err := worker.RunOnce(ctx); err != nil || !processed {
		t.Fatalf("second attempt processed=%v err=%v", processed, err)
	}
	if executor.calls != 2 {
		t.Fatalf("executor calls=%d", executor.calls)
	}
	if err := env.pool.QueryRow(ctx, `SELECT status,cardinality(attempt_hashes) FROM room_coding_jobs WHERE id=$1`, "codingjob_sandbox").
		Scan(&status, &hashes); err != nil || status != "failed" || hashes != 2 {
		t.Fatalf("after repeat status=%q hashes=%d err=%v", status, hashes, err)
	}
	if len(published) != 2 || !strings.Contains(published[1], "same way twice") {
		t.Fatalf("failure notices=%q", published)
	}
}
