package computercli

import (
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

// appleReminderScript creates one Reminder in the named list. Arguments come
// through argv so a title never becomes AppleScript source.
const appleReminderScript = `function run(argv) {
  const title = argv[0];
  const epoch = Number(argv[1]);
  const listName = argv[2];
  const app = Application("Reminders");
  let list;
  try {
    list = app.lists.byName(listName);
    list.name();
  } catch {
    list = app.make({ new: "list", withProperties: { name: listName } });
  }
  const due = new Date(epoch * 1000);
  list.reminders.push(
    app.Reminder({ name: title, dueDate: due, remindMeDate: due }),
  );
  return JSON.stringify({
    list: listName,
    title: title,
    due: due.toISOString(),
  });
}`

// runAppleReminder creates one Apple Reminder on this Mac without any paid
// service; macOS asks for Automation permission the first time.
func runAppleReminder(
	ctx context.Context,
	title string,
	due time.Time,
	list string,
) ([]byte, error) {
	return exec.CommandContext(
		ctx,
		"osascript",
		"-l",
		"JavaScript",
		"-e",
		appleReminderScript,
		title,
		strconv.FormatInt(due.Unix(), 10),
		list,
	).Output()
}

type reminderResult struct {
	List  string `json:"list"`
	Title string `json:"title"`
	Due   string `json:"due"`
}

// parseReminderDelay accepts a Go duration or a number of days, up to 30 days.
func parseReminderDelay(value string) (time.Duration, error) {
	delay, err := time.ParseDuration(value)
	if err != nil {
		if days, dayErr := strconv.Atoi(strings.TrimSuffix(value, "d")); dayErr == nil &&
			strings.HasSuffix(value, "d") && days > 0 {
			delay = time.Duration(days) * 24 * time.Hour
		} else {
			return 0, newError(categoryUsage, "Use --in as a delay such as 20m, 2h, or 1d")
		}
	}
	if delay <= 0 || delay > 30*24*time.Hour {
		return 0, newError(categoryUsage, "Use a reminder delay between 1 minute and 30 days")
	}
	return delay, nil
}

// executeReminderAdd writes one personal reminder to Apple Reminders.
func (c *cli) executeReminderAdd(ctx context.Context, parsed command) (int, error) {
	if c.deps.goos != "darwin" {
		return 0, newError(categoryConfiguration, "Apple Reminders is available only on macOS")
	}
	add := c.deps.addReminder
	if add == nil {
		return 0, newError(categoryConfiguration, "Apple Reminders is unavailable")
	}
	due := parsed.reminderAt
	if parsed.reminderIn > 0 {
		due = time.Now().Add(parsed.reminderIn)
	}
	output, err := add(ctx, parsed.reminderTitle, due, parsed.reminderList)
	if err != nil {
		return 0, wrapError(categoryConfiguration, "Unable to add the Apple Reminder: "+err.Error(), err)
	}
	result := reminderResult{
		List: parsed.reminderList, Title: parsed.reminderTitle,
		Due: due.UTC().Format(time.RFC3339),
	}
	if json.Unmarshal(output, &result) != nil || result.Title == "" {
		result = reminderResult{
			List: parsed.reminderList, Title: parsed.reminderTitle,
			Due: due.UTC().Format(time.RFC3339),
		}
	}
	if parsed.json {
		c.out(marshalReport(result))
	} else {
		c.out(fmt.Sprintf("Added to Apple Reminders: %s · %s · list %s\n", result.Title, result.Due, result.List))
	}
	return 0, nil
}
