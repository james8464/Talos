package integration_test

import (
	"net/http"
	"strings"
	"testing"
)

// TestRoomThreadJourneyLive creates a Thread from a Room message, replies in
// it, reads the parent summary, reopens the Thread, and finds the reply by
// search.
func TestRoomThreadJourneyLive(t *testing.T) {
	allowParallelIntegrationTest(t)
	env, alice, _, roomID := newAgentCLIIdentity(t)
	rootID := createHumanMessage(t, env, alice, roomID, "Thread journey root", "thread-journey-root")
	created := requestMessageThread(t, env, alice, roomID, rootID, "thread-journey-create")
	if created.status != http.StatusCreated {
		t.Fatalf("create Thread status=%d body=%s", created.status, created.body)
	}
	reply := "Thread journey reply unique-token"
	status, _, body := env.request(alice, http.MethodPost, "/v0/rooms/"+created.room.ID+"/messages",
		humanTextMessage(reply), "thread-journey-reply")
	if status != http.StatusCreated {
		t.Fatalf("Thread reply status=%d body=%s", status, body)
	}

	status, _, parent := env.request(alice, http.MethodGet, "/v0/rooms/"+roomID+"/messages/"+rootID, nil, "")
	if status != http.StatusOK || !containsJSONText(parent, `"reply_count":1`) ||
		!containsJSONText(parent, created.room.ID) {
		t.Fatalf("parent summary status=%d body=%s", status, parent)
	}
	status, _, thread := env.request(alice, http.MethodGet, "/v0/rooms/"+created.room.ID+"/messages?limit=50", nil, "")
	if status != http.StatusOK || !containsJSONText(thread, reply) {
		t.Fatalf("Thread messages status=%d body=%s", status, thread)
	}
	status, _, found := env.request(alice, http.MethodGet,
		"/v0/tenants/"+acmeTenant+"/messages/search?q=unique-token", nil, "")
	if status != http.StatusOK || !containsJSONText(found, reply) ||
		!strings.Contains(string(found), created.room.ID) {
		t.Fatalf("Thread search status=%d body=%s", status, found)
	}
}
