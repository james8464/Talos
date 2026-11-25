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
}

// RunOnce claims at most one job and reports it. It returns false when the
// queue is empty.
func (w *Worker) RunOnce(ctx context.Context) (bool, error) {
	job, err := w.Queries.ClaimNextRoomCodingJob(ctx, w.ClaimedBy)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	outcome, err := w.Executor.Execute(ctx, job)
	if err != nil {
		outcome = Outcome{Status: "failed", Message: "The Computer could not run the job: " + err.Error()}
	}
	status, reply := w.report(job, outcome)
	if err := w.Queries.CompleteRoomCodingJob(ctx, store.CompleteRoomCodingJobParams{
		ID: job.ID, Status: status, Result: outcome.Message,
	}); err != nil {
		return true, err
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
			if _, err := w.RunOnce(ctx); err != nil && w.Logger != nil {
				w.Logger.Warn("room_coding.worker_failed", "error", err)
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
