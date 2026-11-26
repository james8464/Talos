package app

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"github.com/simplexai-labs/talos/apps/teamhub/internal/store"
)

// recordPresenceReading stores the counts from one frame the Human chose to
// classify in Settings. The server sets the capture time, so a client clock
// cannot make a stale reading look fresh.
func (a *App) recordPresenceReading(c *gin.Context) {
	var request struct {
		People *int `json:"people"`
		Faces  *int `json:"faces"`
	}
	if decodeJSON(c, &request) != nil || request.People == nil || request.Faces == nil {
		a.writeError(c, http.StatusBadRequest, "INVALID_REQUEST", "people and faces are required")
		return
	}
	if *request.People < 0 || *request.People > 100 || *request.Faces < 0 || *request.Faces > 100 {
		a.writeError(c, http.StatusBadRequest, "INVALID_REQUEST", "people and faces must be between 0 and 100")
		return
	}
	session := currentPrincipal(c).Session
	err := a.queries.UpsertPresenceReading(c.Request.Context(), store.UpsertPresenceReadingParams{
		TenantID: session.HumanTenantID, HumanActorID: session.HumanActorID,
		People: int32(*request.People), Faces: int32(*request.Faces),
	})
	if err != nil {
		a.internalError(c, "record presence reading", err)
		return
	}
	c.Status(http.StatusNoContent)
}
