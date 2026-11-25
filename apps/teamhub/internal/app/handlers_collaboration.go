package app

import (
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/gin-gonic/gin"
	"github.com/jackc/pgx/v5"
	"github.com/simplexai-labs/talos/apps/teamhub/internal/collaboration"
	"github.com/simplexai-labs/talos/apps/teamhub/internal/idempotency"
	idgen "github.com/simplexai-labs/talos/apps/teamhub/internal/ids"
	"github.com/simplexai-labs/talos/apps/teamhub/internal/reminder"
	"github.com/simplexai-labs/talos/apps/teamhub/internal/roomcoding"
	"github.com/simplexai-labs/talos/apps/teamhub/internal/roomcreation"
	"github.com/simplexai-labs/talos/apps/teamhub/internal/roomreminder"
	"github.com/simplexai-labs/talos/apps/teamhub/internal/roomsenses"
	"github.com/simplexai-labs/talos/apps/teamhub/internal/store"
	"github.com/simplexai-labs/talos/apps/teamhub/internal/voicelive"
	httpapi "github.com/simplexai-labs/talos/contracts-go/teamhubpublic"
)

func (a *App) listTenants(c *gin.Context) {
	c.JSON(http.StatusOK, httpapi.TenantList{Items: []httpapi.Tenant{tenantModel(currentPrincipal(c).Session)}})
}

func (a *App) getTenant(c *gin.Context) {
	if !a.requireSpace(c, c.Param("tenantId")) {
		return
	}
	c.JSON(http.StatusOK, tenantModel(currentPrincipal(c).Session))
}

func (a *App) listActors(c *gin.Context) {
	cursor, limit, ok := parsePage(c)
	if !ok {
		a.writeError(c, http.StatusBadRequest, "INVALID_PAGE", "invalid pagination parameters")
		return
	}
	accountID, tenantID := currentPrincipal(c).Session.AccountID, c.Param("tenantId")
	if !a.requireSpace(c, tenantID) {
		return
	}
	page, err := a.collaboration.ListActors(c.Request.Context(), accountID, tenantID, collaboration.Page{Cursor: cursor, Limit: limit})
	if a.notFoundOrError(c, "list actors", err) {
		return
	}
	c.JSON(http.StatusOK, page)
}

func (a *App) getActor(c *gin.Context) {
	actor, err := a.collaboration.GetActor(c.Request.Context(), currentPrincipal(c).Session.AccountID, c.Param("tenantId"), c.Param("actorId"))
	if a.notFoundOrError(c, "get actor", err) {
		return
	}
	c.JSON(http.StatusOK, actor)
}

func (a *App) listRooms(c *gin.Context) {
	cursor, limit, ok := parsePage(c)
	if !ok {
		a.writeError(c, http.StatusBadRequest, "INVALID_PAGE", "invalid pagination parameters")
		return
	}
	accountID, tenantID := currentPrincipal(c).Session.AccountID, c.Param("tenantId")
	if !a.requireSpace(c, tenantID) {
		return
	}
	page, err := a.collaboration.ListRooms(c.Request.Context(), accountID, tenantID, collaboration.Page{Cursor: cursor, Limit: limit})
	if a.notFoundOrError(c, "list rooms", err) {
		return
	}
	c.JSON(http.StatusOK, page)
}

func (a *App) getRoom(c *gin.Context) {
	room, err := a.collaboration.GetRoom(c.Request.Context(), currentPrincipal(c).Session.AccountID, c.Param("roomId"))
	if a.notFoundOrError(c, "get room", err) {
		return
	}
	c.JSON(http.StatusOK, room)
}

// validRoomText reports whether a Room name and description fit the bounds
// roomcreation enforces: characters, not UTF-8 bytes, counted after trimming so
// surrounding spaces do not spend a name's 120.
func validRoomText(name, description *string) bool {
	if name != nil &&
		(strings.TrimSpace(*name) == "" ||
			utf8.RuneCountInString(strings.TrimSpace(*name)) > roomcreation.MaxNameRunes) {
		return false
	}
	return description == nil ||
		utf8.RuneCountInString(*description) <= roomcreation.MaxDescriptionRunes
}

func (a *App) createRoom(c *gin.Context) {
	var request httpapi.HumanRoomCreateRequest
	if err := decodeJSON(c, &request); err != nil ||
		!validRoomText(&request.Name, request.Description) {
		a.writeError(c, http.StatusBadRequest, "INVALID_REQUEST", "room name and description are invalid")
		return
	}
	request.Name = strings.TrimSpace(request.Name)
	// A repeated creation finds its name taken; only Live tool calls deduplicate.
	result, err := a.collaboration.CreateRoom(c.Request.Context(), currentPrincipal(c).Session.AccountID, c.Param("tenantId"), request, "")
	if errors.Is(err, pgx.ErrNoRows) {
		a.writeError(c, http.StatusNotFound, "TENANT_NOT_FOUND", "tenant was not found")
		return
	}
	if errors.Is(err, collaboration.ErrRoomNameTaken) {
		a.writeError(c, http.StatusConflict, "ROOM_NAME_TAKEN", "a Room with this name already exists")
		return
	}
	if errors.Is(err, voicelive.ErrInvalidTurn) || errors.Is(err, voicelive.ErrTurnConflict) {
		a.writeError(c, http.StatusConflict, "VOICE_TURN_UNAVAILABLE", "The Live turn is no longer available.")
		return
	}
	if errors.Is(err, roomcreation.ErrNotFound) {
		a.writeError(c, http.StatusNotFound, "NOT_FOUND", "the Room creator is not an active Tenant member")
		return
	}
	if errors.Is(err, roomcreation.ErrInvalid) {
		a.writeError(c, http.StatusBadRequest, "INVALID_REQUEST", "room name and description are invalid")
		return
	}
	if err != nil {
		a.internalError(c, "create room", err)
		return
	}
	writeResultResponse(c, result)
}

func (a *App) listMessages(c *gin.Context) {
	cursor, limit, ok := parsePage(c)
	if !ok {
		a.writeError(c, http.StatusBadRequest, "INVALID_PAGE", "invalid pagination parameters")
		return
	}
	timings := startMessageReadTimings(c)
	page, err := a.collaboration.ListMessages(c.Request.Context(), currentPrincipal(c).Session.AccountID, c.Param("roomId"),
		collaboration.Page{Cursor: cursor, Limit: limit}, timings)
	if a.notFoundOrError(c, "authorize message history", err) {
		return
	}
	c.JSON(http.StatusOK, page)
	timings.Stage("response")
}

func (a *App) getMessage(c *gin.Context) {
	message, err := a.collaboration.GetMessage(c.Request.Context(), currentPrincipal(c).Session.AccountID, c.Param("roomId"), c.Param("messageId"))
	if a.notFoundOrError(c, "get message", err) {
		return
	}
	c.JSON(http.StatusOK, message)
}

func (a *App) forwardRoomMessage(c *gin.Context) {
	idempotencyKey := contractParams[httpapi.ForwardRoomMessageParams](c).IdempotencyKey
	var request httpapi.MessageForwardRequest
	if err := c.ShouldBindJSON(&request); err != nil {
		a.writeError(c, http.StatusBadRequest, "INVALID_REQUEST", "Forward target is invalid")
		return
	}
	forwarded, err := a.collaboration.ForwardMessage(c.Request.Context(), currentPrincipal(c).Session.AccountID,
		c.Param("roomId"), c.Param("messageId"), request, idempotencyKey)
	switch {
	case errors.Is(err, collaboration.ErrForwardSourceNotFound):
		a.writeError(c, http.StatusNotFound, "MESSAGE_NOT_FOUND", "Message was not found")
	case errors.Is(err, collaboration.ErrForwardTargetNotFound):
		a.writeError(c, http.StatusNotFound, "FORWARD_TARGET_NOT_FOUND", "Forward target is unavailable")
	case errors.Is(err, collaboration.ErrForwardTargetInvalid):
		a.writeError(c, http.StatusBadRequest, "INVALID_REQUEST", "Forward target is invalid")
	case errors.Is(err, collaboration.ErrForwardNotAllowed):
		a.writeError(c, http.StatusNotFound, "FORWARD_NOT_ALLOWED", "Message could not be forwarded")
	case errors.Is(err, voicelive.ErrInvalidTurn), errors.Is(err, voicelive.ErrTurnConflict):
		a.writeError(c, http.StatusConflict, "VOICE_TURN_UNAVAILABLE", "The Live turn is no longer available.")
	case errors.Is(err, idempotency.ErrConflict):
		a.writeError(c, http.StatusConflict, "IDEMPOTENCY_KEY_REUSED", "idempotency key was used with different input")
	case err != nil:
		a.internalError(c, "forward Message", err)
	default:
		if forwarded.Replay {
			c.Header("Idempotent-Replay", "true")
		}
		c.JSON(http.StatusCreated, forwarded.Result)
	}
}

func (a *App) createMessage(c *gin.Context) {
	key, ok := a.requireIdempotencyKey(c)
	if !ok {
		return
	}
	var request httpapi.MessageCreateRequest
	if err := decodeJSON(c, &request); err != nil {
		a.writeError(c, http.StatusBadRequest, "INVALID_REQUEST", "message content is invalid")
		return
	}
	content, err := collaboration.NormalizeMessageCreateRequest(request)
	if err != nil {
		a.writeError(c, http.StatusBadRequest, "INVALID_REQUEST", "message content is invalid")
		return
	}
	result, err := a.collaboration.CreateMessage(c.Request.Context(), currentPrincipal(c).Session.AccountID, c.Param("roomId"), request, content, key)
	if errors.Is(err, collaboration.ErrRoomParticipationLeft) {
		a.writeError(c, http.StatusForbidden, "ROOM_LEFT", "Rejoin this Room before sending messages.")
		return
	}
	if errors.Is(err, pgx.ErrNoRows) {
		a.writeError(c, http.StatusNotFound, "ROOM_NOT_FOUND", "room was not found")
		return
	}
	if errors.Is(err, voicelive.ErrInvalidTurn) || errors.Is(err, voicelive.ErrTurnConflict) {
		a.writeError(c, http.StatusConflict, "VOICE_TURN_UNAVAILABLE", "The Live turn is no longer available.")
		return
	}
	if errors.Is(err, idempotency.ErrConflict) {
		a.writeError(c, http.StatusConflict, "IDEMPOTENCY_KEY_REUSED", "idempotency key was used with different input")
		return
	}
	if errors.Is(err, collaboration.ErrInvalidMention) {
		a.writeError(c, http.StatusBadRequest, "INVALID_MENTION", "message references must target eligible Tenant Actors")
		return
	}
	if errors.Is(err, collaboration.ErrInvalidMessageSkill) {
		a.writeError(c, http.StatusBadRequest, "INVALID_SKILL", "skill segments must target an eligible Room Agent with the exact enabled Skill binding")
		return
	}
	if errors.Is(err, collaboration.ErrInvalidMessageContext) {
		a.writeError(c, http.StatusBadRequest, "INVALID_MESSAGE_CONTEXT", "context messages must be distinct committed Messages in the same Room")
		return
	}
	if errors.Is(err, collaboration.ErrInvalidDocumentReference) {
		a.writeError(c, http.StatusBadRequest, "INVALID_DOCUMENT_REFERENCE", "Document references must be active in the current message Context")
		return
	}
	if errors.Is(err, collaboration.ErrAttachmentUploadUnavailable) {
		a.writeError(c, http.StatusConflict, "ATTACHMENT_UPLOAD_UNAVAILABLE", "message attachment upload is unavailable")
		return
	}
	if errors.Is(err, collaboration.ErrInvalidMessageAttachment) {
		a.writeError(c, http.StatusBadRequest, "INVALID_ATTACHMENT", "message context must reference currently authorized published Artifact Versions")
		return
	}
	if errors.Is(err, collaboration.ErrDirectRoomPeerInactive) {
		a.writeError(c, http.StatusConflict, "DIRECT_ROOM_PEER_INACTIVE", "the Direct Room Agent is unavailable")
		return
	}
	if err != nil {
		a.internalError(c, "create message", err)
		return
	}
	a.recordMessageCommitTiming(c)
	a.replyToRoomCommand(c, c.Param("roomId"), content.PlainText(), content.FromVoice(), key)
	writeResultResponse(c, result)
}

// replyToRoomCommand writes one Agent-authored reply for a deterministic
// identity, sensing or coding command. A failure never changes the accepted
// Human message.
func (a *App) replyToRoomCommand(c *gin.Context, roomID, text string, voice bool, key string) {
	if a.roomIdentity == nil || strings.TrimSpace(text) == "" || key == "" {
		return
	}
	reply := a.roomIdentity.Answer(roomID, text, time.Now())
	if reply == "" {
		reply = roomsenses.Answer(text, voice)
	}
	if reply == "" {
		reply = a.roomReminderReply(c, roomID, text)
	}
	if reply == "" {
		reply = a.roomCodingReply(c, roomID, text)
	}
	if reply == "" {
		return
	}
	agents, err := a.queries.ListRoomAgentActors(c.Request.Context(), roomID)
	if err != nil || len(agents) == 0 {
		if a.logger != nil {
			a.logger.Warn("room_command.no_agent", "room_id", roomID, "error", err)
		}
		return
	}
	if _, err := a.collaboration.CreateAgentMessage(
		c.Request.Context(), roomID, agents[0].ID, reply, "identity:"+key,
	); err != nil && a.logger != nil {
		a.logger.Warn("room_command.failed", "room_id", roomID, "error", err)
	}
}

// roomReminderReply creates a one-shot Room reminder from the typed command.
func (a *App) roomReminderReply(c *gin.Context, roomID, text string) string {
	command, ok := roomreminder.Parse(text)
	if !ok {
		return ""
	}
	runAt := time.Now().UTC().Add(command.Delay)
	if _, err := a.reminders.Create(c.Request.Context(),
		reminder.HumanCaller(currentPrincipal(c).Session.AccountID), roomID, "", nil,
		reminder.CreateInput{
			Name: command.Task, Instruction: command.Task,
			Cadence: reminder.Cadence{Kind: "once", Timezone: "UTC", RunAt: &runAt},
		}); err != nil {
		if a.logger != nil {
			a.logger.Warn("room_command.reminder_failed", "room_id", roomID, "error", err)
		}
		return "I couldn't create that reminder."
	}
	return fmt.Sprintf("Okay. I'll remind you in %s to %s.", roomreminder.Describe(command.Delay), command.Task)
}

func (a *App) roomCodingReply(c *gin.Context, roomID, text string) string {
	ctx := c.Request.Context()
	if roomcoding.IsList(text) {
		jobs, err := a.queries.ListRoomCodingJobsForRoom(ctx, roomID)
		if err != nil {
			return ""
		}
		if len(jobs) == 0 {
			return "No coding jobs have been queued."
		}
		lines := make([]string, 0, len(jobs))
		for _, item := range jobs {
			label := item.Kind
			if item.TargetPath != "" {
				label += " " + item.TargetPath
			}
			line := fmt.Sprintf("%s %s %s", item.ID, label, item.Status)
			if item.Attempts > 1 {
				line += fmt.Sprintf(" (attempt %d)", item.Attempts)
			}
			lines = append(lines, line)
		}
		return "Coding jobs: " + strings.Join(lines, "; ") + "."
	}
	command, ok, err := roomcoding.Parse(text)
	if !ok {
		return ""
	}
	if err != nil {
		return err.Error()
	}
	tenantID, err := a.queries.GetRoomTenantID(ctx, roomID)
	if err != nil {
		return ""
	}
	actor, err := a.queries.GetHumanActor(ctx, store.GetHumanActorParams{
		TenantID: tenantID, AccountID: textValue(currentPrincipal(c).Session.AccountID),
	})
	if err != nil {
		return ""
	}
	if command.Kind != "cancel" {
		existing, err := a.queries.FindActiveRoomCodingJob(ctx, store.FindActiveRoomCodingJobParams{
			RoomID: roomID, TargetPath: command.Target, Request: command.Request,
		})
		if err == nil {
			return fmt.Sprintf("Coding job %s is already queued for %s; not adding a duplicate. Say \"coding cancel %s\" to stop it.", existing.ID, command.Target, existing.ID)
		}
		if !errors.Is(err, pgx.ErrNoRows) {
			return ""
		}
	}
	if command.Kind == "cancel" {
		cancelled, err := a.queries.CancelRoomCodingJob(ctx, store.CancelRoomCodingJobParams{
			ID: command.Target, RoomID: roomID, RequesterActorID: actor.ID,
			Result: "cancelled from the Room",
		})
		if err != nil || cancelled == 0 {
			return "I couldn't cancel " + command.Target + "; it may be finished or belong to another request."
		}
		return "Coding job " + command.Target + " cancelled."
	}
	id := idgen.New("codingjob")
	if _, err := a.queries.CreateRoomCodingJob(ctx, store.CreateRoomCodingJobParams{
		ID: id, TenantID: tenantID, RoomID: roomID, RequesterActorID: actor.ID,
		Kind: command.Kind, TargetPath: command.Target, Request: command.Request,
	}); err != nil {
		return ""
	}
	if command.Kind == "plan" {
		return fmt.Sprintf("Coding plan %s queued (verify-only).", id)
	}
	return fmt.Sprintf("Coding job %s queued for %s (verify-only).", id, command.Target)
}

func (a *App) recordMessageCommitTiming(c *gin.Context) {
	correlationID := c.GetHeader("X-Talos-Latency-ID")
	if !idempotencyKeyPattern.MatchString(correlationID) {
		return
	}
	committedAt := time.Now().UTC()
	c.Header("X-Talos-Message-Commit-Unix-Ms", strconv.FormatInt(committedAt.UnixMilli(), 10))
	if a.logger != nil {
		a.logger.Info("local_agent_ingress.message_committed",
			"correlation_id", correlationID,
			"room_id", c.Param("roomId"),
			"committed_at_unix_ms", committedAt.UnixMilli(),
		)
	}
}

func (a *App) notFoundOrError(c *gin.Context, action string, err error) bool {
	if err == nil {
		return false
	}
	// A failure after access was established is never a hidden resource.
	var internal *collaboration.InternalError
	if errors.As(err, &internal) {
		a.internalError(c, internal.Action, internal.Err)
		return true
	}
	// For protected resources, callers use authorization queries with Account/Tenant/Room conditions and always return 404, so the response does not reveal whether the resource exists.
	if errors.Is(err, pgx.ErrNoRows) {
		a.writeError(c, http.StatusNotFound, "NOT_FOUND", "resource was not found")
		return true
	}
	a.internalError(c, action, err)
	return true
}
