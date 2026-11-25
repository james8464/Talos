package computercli

import "regexp"

var environmentCommand = regexp.MustCompile(`^talos-computer-[a-z0-9][a-z0-9-]*$`)

// commandName is the command that runs this Computer. Each environment's
// launcher sets TALOS_COMPUTER_COMMAND to talos-computer-<environment>, so
// hints stay right when Computers of several environments share one machine.
func commandName(environment map[string]string) string {
	if command := environment["TALOS_COMPUTER_COMMAND"]; environmentCommand.MatchString(command) {
		return command
	}
	return "talos-computer"
}
