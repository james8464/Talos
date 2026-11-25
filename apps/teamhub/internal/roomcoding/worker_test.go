package roomcoding

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

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
		Kind: "job", TargetPath: "internal/app/app.go", Request: "add a comment",
	})
	if err != nil || outcome.Status != "verified" {
		t.Fatalf("outcome=%+v err=%v", outcome, err)
	}
	if body["kind"] != "job" || body["target"] != "internal/app/app.go" || body["request"] != "add a comment" {
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
