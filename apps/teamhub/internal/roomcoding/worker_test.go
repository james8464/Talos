package roomcoding

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/simplexai-labs/talos/apps/teamhub/internal/store"
)

func TestReportNamesTheOutcome(t *testing.T) {
	worker := &Worker{}
	job := store.RoomCodingJob{ID: "codingjob_1"}
	cases := map[string]string{
		"verified": "verified in a sandbox",
		"planned":  "planned",
		"declined": "declined",
		"failed":   "failed",
	}
	for status, want := range cases {
		rowStatus, reply := worker.report(job, Outcome{Status: status, Message: "detail"})
		if !strings.Contains(reply, want) || !strings.Contains(reply, "codingjob_1") {
			t.Fatalf("%s reply=%q", status, reply)
		}
		if status == "verified" || status == "planned" {
			if rowStatus != "done" {
				t.Fatalf("%s row status=%q", status, rowStatus)
			}
		} else if rowStatus != "failed" {
			t.Fatalf("%s row status=%q", status, rowStatus)
		}
	}
}

func TestReportAddsAReceiptWithoutFileContents(t *testing.T) {
	worker := &Worker{}
	job := store.RoomCodingJob{ID: "codingjob_1"}
	_, reply := worker.report(job, Outcome{
		Status: "applied", Message: "verified and applied",
		Receipt: &Receipt{Files: []FileReceipt{{
			Target: "src/value.txt", BytesBefore: 4, BytesAfter: 4,
			BeforeSHA256: "2c8b08da5ce60398e1f19af0e5dccc744",
			AfterSHA256:  "27dd8ed44a83ff94d557f9fd0412ed5a",
		}}, RegressionSHA256: "f8f90421ddaf88273f5020fa1981e09e",
			DiffSHA256:             "6c0e17d313d232f978b2d89b5f4d36bae4d25ac0d2fbae07b38dac277905fee3",
			BudgetRemainingSeconds: 243},
	})
	for _, want := range []string{
		"src/value.txt 4->4 bytes",
		"sha256 2c8b08da5ce6->27dd8ed44a83",
		"Diff sha256 6c0e17d313d2.",
		"243s budget left.",
	} {
		if !strings.Contains(reply, want) {
			t.Fatalf("reply=%q missing %q", reply, want)
		}
	}
	if strings.Contains(reply, "one") || strings.Contains(reply, "two") {
		t.Fatalf("reply leaked file text: %q", reply)
	}
}

func TestRetryDelayMatchesTheBoundedSchedule(t *testing.T) {
	cases := map[int32]time.Duration{
		1: 15 * time.Second, 2: 30 * time.Second, 3: 60 * time.Second,
		4: 120 * time.Second, 5: 240 * time.Second, 6: 480 * time.Second,
		7: 600 * time.Second, 20: 600 * time.Second,
	}
	for attempts, want := range cases {
		if got := RetryDelay(attempts); got != want {
			t.Fatalf("attempts=%d delay=%s want=%s", attempts, got, want)
		}
	}
}

func TestRetryNoticeNamesTheAttemptAndDelay(t *testing.T) {
	notice := RetryNotice(store.RoomCodingJob{ID: "codingjob_1", Attempts: 2}, 30*time.Second, 4)
	if notice != "Coding job codingjob_1 could not reach the Computer; retrying in 30s (attempt 2 of 4)." {
		t.Fatalf("notice=%q", notice)
	}
}

func TestSandboxRetryNoticeNamesTheAttemptAndDelay(t *testing.T) {
	notice := SandboxRetryNotice(store.RoomCodingJob{ID: "codingjob_1", Attempts: 1}, 15*time.Second, 4)
	if notice != "Coding job codingjob_1 failed the sandbox check; retrying in 15s (attempt 1 of 4)." {
		t.Fatalf("notice=%q", notice)
	}
}

func TestHTTPExecutorPostsTheJobAndReadsTheOutcome(t *testing.T) {
	var body map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("authorization") != "Bearer token" || r.URL.Path != "/talos/coding/execute" {
			t.Errorf("request path=%q auth=%q", r.URL.Path, r.Header.Get("authorization"))
		}
		payload, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(payload, &body)
		_ = json.NewEncoder(w).Encode(Outcome{Status: "verified", Message: "sandbox passed"})
	}))
	defer server.Close()

	executor := NewHTTPExecutor(server.URL, "token")
	outcome, err := executor.Execute(context.Background(), store.RoomCodingJob{
		ID: "codingjob_1", TenantID: "tenant_acme", RequesterActorID: "actor_human",
		Kind: "job", TargetPath: "internal/app/app.go", Request: "add a comment",
	})
	if err != nil || outcome.Status != "verified" {
		t.Fatalf("outcome=%+v err=%v", outcome, err)
	}
	if body["kind"] != "job" || body["target"] != "internal/app/app.go" || body["request"] != "add a comment" ||
		body["job_id"] != "codingjob_1" || body["tenant_id"] != "tenant_acme" || body["requester_actor_id"] != "actor_human" {
		t.Fatalf("body=%+v", body)
	}
}

func TestHTTPExecutorRejectsAnErrorAnswer(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "no workspace", http.StatusBadRequest)
	}))
	defer server.Close()
	if _, err := NewHTTPExecutor(server.URL, "token").Execute(context.Background(), store.RoomCodingJob{}); err == nil {
		t.Fatal("expected an error for a non-200 answer")
	}
}
