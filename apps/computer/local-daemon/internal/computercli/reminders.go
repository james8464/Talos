package computercli

import (
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"strconv"
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

// executeReminderAdd writes one personal reminder to Apple Reminders.
func (c *cli) executeReminderAdd(ctx context.Context, parsed command) (int, error) {
	if c.deps.goos != "darwin" {
		return 0, newError(categoryConfiguration, "Apple Reminders is available only on macOS")
	}
	add := c.deps.addReminder
	if add == nil {
		return 0, newError(categoryConfiguration, "Apple Reminders is unavailable")
	}
	output, err := add(ctx, parsed.reminderTitle, parsed.reminderAt, parsed.reminderList)
	if err != nil {
		return 0, wrapError(categoryConfiguration, "Unable to add the Apple Reminder: "+err.Error(), err)
	}
	result := reminderResult{
		List: parsed.reminderList, Title: parsed.reminderTitle,
		Due: parsed.reminderAt.UTC().Format(time.RFC3339),
	}
	if json.Unmarshal(output, &result) != nil || result.Title == "" {
		result = reminderResult{
			List: parsed.reminderList, Title: parsed.reminderTitle,
			Due: parsed.reminderAt.UTC().Format(time.RFC3339),
		}
	}
	if parsed.json {
		c.out(marshalReport(result))
	} else {
		c.out(fmt.Sprintf("Added to Apple Reminders: %s · %s · list %s\n", result.Title, result.Due, result.List))
	}
	return 0, nil
}
