package roomgoal

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"time"
)

// Verdict is the judge's decision for one goal turn.
type Verdict string

const (
	// Done closes the goal as achieved.
	Done Verdict = "done"
	// Continue keeps the goal active for another turn.
	Continue Verdict = "continue"
	// Blocked closes the goal as unachievable or waiting for input.
	Blocked Verdict = "blocked"
)

// Decision is one judge result.
type Decision struct {
	Verdict Verdict `json:"verdict"`
	Reason  string  `json:"reason"`
}

// ParseDecision validates the judge JSON.
func ParseDecision(raw []byte) (Decision, error) {
	var decision Decision
	if err := json.Unmarshal(raw, &decision); err != nil {
		return Decision{}, err
	}
	switch decision.Verdict {
	case Done, Continue, Blocked:
		return decision, nil
	default:
		return Decision{}, fmt.Errorf("unknown goal verdict %q", decision.Verdict)
	}
}

// Judge decides whether one goal turn is done, should continue, or is blocked.
type Judge interface {
	Judge(ctx context.Context, objective, lastReply string) (Decision, error)
}

// HTTPJudge asks node-services to run the local model judge.
type HTTPJudge struct {
	BaseURL string
	Token   string
	Client  *http.Client
}

// NewHTTPJudge returns a judge for the local goal endpoint.
func NewHTTPJudge(baseURL, token string) *HTTPJudge {
	return &HTTPJudge{
		BaseURL: strings.TrimRight(baseURL, "/"),
		Token:   token,
		Client:  &http.Client{Timeout: 3 * time.Minute},
	}
}

// Judge sends the objective and the latest Room reply.
func (j *HTTPJudge) Judge(ctx context.Context, objective, lastReply string) (Decision, error) {
	body, err := json.Marshal(map[string]string{"objective": objective, "reply": lastReply})
	if err != nil {
		return Decision{}, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, j.BaseURL+"/talos/goal/judge", bytes.NewReader(body))
	if err != nil {
		return Decision{}, err
	}
	request.Header.Set("content-type", "application/json")
	request.Header.Set("authorization", "Bearer "+j.Token)
	response, err := j.Client.Do(request)
	if err != nil {
		return Decision{}, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return Decision{}, fmt.Errorf("goal judge answered %d", response.StatusCode)
	}
	var decision Decision
	if err := json.NewDecoder(response.Body).Decode(&decision); err != nil {
		return Decision{}, err
	}
	if decision.Verdict != Done && decision.Verdict != Continue && decision.Verdict != Blocked {
		return Decision{}, errors.New("goal judge returned no verdict")
	}
	return decision, nil
}
