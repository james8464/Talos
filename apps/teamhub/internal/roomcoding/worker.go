package roomcoding

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/simplexai-labs/talos/apps/teamhub/internal/store"
)

// Outcome is what the owner's Computer reports for one job.
type Outcome struct {
	Status  string `json:"status"`
	Message string `json:"message"`
}

// Executor runs one accepted job on the Computer that owns the workspace.
type Executor interface {
	Execute(ctx context.Context, job store.RoomCodingJob) (Outcome, error)
}

// Publish writes one Agent-authored Room message.
type Publish func(ctx context.Context, roomID, text, key string) error

// Worker claims durable Room coding jobs and reports their outcome as the
// Room's Agent. Execution stays on the Computer; TeamHub only owns the queue
// and the reply.
type Worker struct {
	Queries   *store.Queries
	Executor  Executor
	Publish   Publish
	Logger    *slog.Logger
	ClaimedBy string
	// LeaseSeconds bounds how long a claim is trusted before another worker
	// may take the job. Zero uses fifteen minutes.
	LeaseSeconds int
	// MaxAttempts bounds transport failures before the job is reported as
	// failed. Zero uses three.
	MaxAttempts int
}

// RunOnce claims at most one job and reports it. It returns false when the
// queue is empty.
func (w *Worker) RunOnce(ctx context.Context) (bool, error) {
	lease := w.LeaseSeconds
	if lease <= 0 {
		lease = 900
	}
	job, err := w.Queries.ClaimNextRoomCodingJob(ctx, store.ClaimNextRoomCodingJobParams{
		ClaimedBy: w.ClaimedBy, LeaseSeconds: int32(lease),
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	outcome, err := w.Executor.Execute(ctx, job)
	if err != nil {
		maxAttempts := w.MaxAttempts
		if maxAttempts <= 0 {
			maxAttempts = 3
		}
		message := "The Computer could not run the job: " + err.Error()
		if int(job.Attempts) < maxAttempts {
			if _, requeueErr := w.Queries.RequeueRoomCodingJob(ctx, store.RequeueRoomCodingJobParams{
				ID: job.ID, Result: message,
			}); requeueErr != nil {
				return true, requeueErr
			}
			if w.Logger != nil {
				w.Logger.Warn("room_coding.requeued", "job_id", job.ID, "attempts", job.Attempts, "error", err)
			}
			return true, nil
		}
		outcome = Outcome{Status: "failed", Message: message}
	}
	status, reply := w.report(job, outcome)
	completed, err := w.Queries.CompleteRoomCodingJob(ctx, store.CompleteRoomCodingJobParams{
		ID: job.ID, Status: status, Result: outcome.Message,
	})
	if err != nil {
		return true, err
	}
	if completed == 0 {
		// The Room cancelled the job while the Computer ran it.
		return true, nil
	}
	if err := w.Publish(ctx, job.RoomID, reply, "coding:"+job.ID+":"+status); err != nil {
		return true, err
	}
	return true, nil
}

// Run claims one job per interval until ctx is cancelled.
func (w *Worker) Run(ctx context.Context, interval time.Duration) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			// Drain what is queued so a burst does not wait one interval per
			// job, but stop after a bounded batch to leave the tick free.
			for processed := 0; processed < 10; processed++ {
				more, err := w.RunOnce(ctx)
				if err != nil {
					if w.Logger != nil {
						w.Logger.Warn("room_coding.worker_failed", "error", err)
					}
					break
				}
				if !more {
					break
				}
			}
		}
	}
}

func (w *Worker) report(job store.RoomCodingJob, outcome Outcome) (string, string) {
	switch outcome.Status {
	case "verified":
		return "done", fmt.Sprintf("Coding job %s verified in a sandbox: %s. No change was applied.", job.ID, outcome.Message)
	case "planned":
		return "done", fmt.Sprintf("Coding job %s planned; no patch was applied: %s", job.ID, outcome.Message)
	case "declined":
		return "failed", fmt.Sprintf("Coding job %s declined: %s", job.ID, outcome.Message)
	default:
		return "failed", fmt.Sprintf("Coding job %s failed: %s", job.ID, outcome.Message)
	}
}

// HTTPExecutor calls the Computer's POST /talos/coding/execute.
type HTTPExecutor struct {
	BaseURL string
	Token   string
	Client  *http.Client
}

// NewHTTPExecutor returns an executor for the configured Computer.
func NewHTTPExecutor(baseURL, token string) *HTTPExecutor {
	return &HTTPExecutor{
		BaseURL: strings.TrimRight(baseURL, "/"),
		Token:   token,
		Client:  &http.Client{Timeout: 6 * time.Minute},
	}
}

// Execute sends one job; a non-200 answer is an error so the Room learns the
// Computer could not be reached.
func (e *HTTPExecutor) Execute(ctx context.Context, job store.RoomCodingJob) (Outcome, error) {
	body, err := json.Marshal(map[string]any{
		"kind": job.Kind, "target": job.TargetPath, "request": job.Request,
	})
	if err != nil {
		return Outcome{}, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, e.BaseURL+"/talos/coding/execute", bytes.NewReader(body))
	if err != nil {
		return Outcome{}, err
	}
	request.Header.Set("content-type", "application/json")
	request.Header.Set("authorization", "Bearer "+e.Token)
	response, err := e.Client.Do(request)
	if err != nil {
		return Outcome{}, err
	}
	defer response.Body.Close()
	payload, err := io.ReadAll(io.LimitReader(response.Body, 64<<10))
	if err != nil {
		return Outcome{}, err
	}
	if response.StatusCode != http.StatusOK {
		return Outcome{}, fmt.Errorf("coding service answered %d: %s", response.StatusCode, strings.TrimSpace(string(payload)))
	}
	var outcome Outcome
	if err := json.Unmarshal(payload, &outcome); err != nil {
		return Outcome{}, err
	}
	if outcome.Status == "" {
		return Outcome{}, errors.New("coding service returned no status")
	}
	return outcome, nil
}
