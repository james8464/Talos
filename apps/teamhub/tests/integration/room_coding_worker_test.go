package integration_test

import (
	"context"
	"strings"
	"testing"

	"github.com/simplexai-labs/talos/apps/teamhub/internal/messagecreation"
	"github.com/simplexai-labs/talos/apps/teamhub/internal/roomcoding"
	"github.com/simplexai-labs/talos/apps/teamhub/internal/store"
)

type stubExecutor struct {
	outcome roomcoding.Outcome
}

func (s stubExecutor) Execute(ctx context.Context, job store.RoomCodingJob) (roomcoding.Outcome, error) {
	return s.outcome, nil
}

// TestRoomCodingWorkerReportsAsAgent proves the durable queue is consumed and
// the completion reply is written as the Room's Agent.
func TestRoomCodingWorkerReportsAsAgent(t *testing.T) {
	allowParallelIntegrationTest(t)
	env, alice, _, roomID := newAgentCLIIdentity(t)
	ctx := context.Background()
	createHumanMessage(t, env, alice, roomID,
		"coding job internal/app/app.go: add a short comment", "worker-job")
	queries := store.New(env.pool)
	agents, err := queries.ListRoomAgentActors(ctx, roomID)
	if err != nil || len(agents) == 0 {
		t.Fatalf("room agents=%d err=%v", len(agents), err)
	}
	published := ""
	publish := func(ctx context.Context, targetRoom, text, key string) error {
		published = text
		content, err := messagecreation.Normalize([]messagecreation.Segment{{Type: "text", Content: text}})
		if err != nil {
			return err
		}
		tx, err := env.pool.Begin(ctx)
		if err != nil {
			return err
		}
		defer tx.Rollback(ctx)
		if _, err := messagecreation.Create(ctx, queries.WithTx(tx), messagecreation.Draft{
			TenantID: acmeTenant, RoomID: targetRoom, Author: agents[0],
			Content: content, Kind: "conversation", Validated: true,
		}); err != nil {
			return err
		}
		return tx.Commit(ctx)
	}
	worker := &roomcoding.Worker{
		Queries: queries,
		Executor: stubExecutor{outcome: roomcoding.Outcome{
			Status: "verified", Message: "sandbox passed",
			Receipt: &roomcoding.Receipt{Files: []roomcoding.FileReceipt{{
				Target: "internal/app/app.go", BytesBefore: 120, BytesAfter: 132,
				BeforeSHA256: "2c8b08da5ce60398e1f19af0e5dccc744df274b826abe585eaba68c525434806",
				AfterSHA256:  "27dd8ed44a83ff94d557f9fd0412ed5a8cbca69ea04922d88c01184a07300a5a",
			}}, RegressionSHA256: "f8f90421ddaf88273f5020fa1981e09e1cac39807406d3aa80dc52986e195dd2"},
		}},
		Publish:   publish,
		ClaimedBy: "test-computer",
	}
	processed, err := worker.RunOnce(ctx)
	if err != nil || !processed {
		t.Fatalf("worker processed=%v err=%v", processed, err)
	}
	if !strings.Contains(published, "verified in a sandbox") || !strings.Contains(published, "No change was applied.") {
		t.Fatalf("published=%q", published)
	}
	if !strings.Contains(published, "Receipt: internal/app/app.go 120->132 bytes") {
		t.Fatalf("published receipt=%q", published)
	}
	var receipt []byte
	if err := env.pool.QueryRow(ctx, `SELECT receipt FROM room_coding_jobs WHERE room_id=$1`, roomID).Scan(&receipt); err != nil {
		t.Fatalf("stored receipt: %v", err)
	}
	if !strings.Contains(string(receipt), `"target": "internal/app/app.go"`) || !strings.Contains(string(receipt), `"bytes_after": 132`) {
		t.Fatalf("stored receipt=%s", receipt)
	}
	var status, result string
	if err := env.pool.QueryRow(ctx, `SELECT status,result FROM room_coding_jobs WHERE room_id=$1`, roomID).
		Scan(&status, &result); err != nil || status != "done" || result != "sandbox passed" {
		t.Fatalf("job status=%q result=%q err=%v", status, result, err)
	}
	var replies int
	if err := env.pool.QueryRow(ctx, `
		SELECT count(*) FROM messages message
		JOIN actors actor ON actor.id=message.author_actor_id AND actor.tenant_id=message.tenant_id
		WHERE message.room_id=$1 AND actor.kind='agent' AND message.body LIKE 'Coding job % verified in a sandbox%'`,
		roomID).Scan(&replies); err != nil || replies != 1 {
		t.Fatalf("agent replies=%d err=%v", replies, err)
	}
	if processed, err := worker.RunOnce(ctx); err != nil || processed {
		t.Fatalf("empty queue processed=%v err=%v", processed, err)
	}
}
