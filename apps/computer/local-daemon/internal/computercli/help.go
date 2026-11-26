package computercli

import "strings"

const runtimePathOptions = `  --codex-path <path>          Use this Codex executable
  --claude-path <path>         Use this Claude Code executable
  --pi-path <path>             Use this Pi executable
  --antigravity-path <path>    Use this Antigravity (agy) executable`

const configOption = `  --config <path>              Use another Computer config file`

var commandHelp = map[string]string{
	"login": `Usage:
  talos-computer login [options]

Authorize this computer in your browser, save its credential, and start the
daemon. When it finishes, the computer is connected.

Options:
  --web-url <origin>           Talos site to sign in to
  --name <name>                Computer name (default: host name)
  --enabled-runtime <type>     Enable only this Runtime; repeat for a subset (default: all)
  --token-stdin                Read the server connection token from standard input (hidden prompt in a terminal)
  --gateway-url <wss-url>      Override the Gateway URL
  --max-concurrent-runs <n>    Limit concurrent runs (1-100)
  --no-max-concurrent-runs     Remove the concurrent run limit
  --codex-auth <path>          Use this Codex auth.json
  --localmodels-python <path>  Run the localmodels tool with this Python; give
                               all four --localmodels-* options together
  --localmodels-module-dir <path>
                               The localmodels tool directory
  --localmodels-state <path>   The localmodels state directory
  --localmodels-base-dir <path>
                               The localmodels model base directory
` + runtimePathOptions + "\n" + configOption + "\n",
	"auth": `Usage:
  talos-computer auth status [--json]

Commands:
  status                       Show the site, Computer, and authorization saved on this computer
`,
	"auth status": `Usage:
  talos-computer auth status [--json] [--config <path>]

Show the site, Computer, workspace, and Gateway this computer is authorized for.
`,
	"daemon": `Usage:
  talos-computer daemon <command>

Commands:
  start                        Start the background daemon
  stop [--if-idle]             Stop the background daemon
  restart                      Restart the background daemon
  status [--json]              Show the process, connection, and runtime status
  logs [-f] [-n <lines>]       Show daemon logs
  runners [--json]             List Agent runners
  runners inspect <agent-id>   Show one Agent runner
  runners stop <agent-id>      Stop one Agent runner
  service list [--json]        List managed services
  service start <name> -- <command> [args...]
                               Start a managed service
  service stop <name>          Stop a managed service
`,
	"daemon start": `Usage:
  talos-computer daemon start [--config <path>]

Start the background daemon and wait for the server to confirm the connection.
`,
	"daemon stop": `Usage:
  talos-computer daemon stop [--if-idle] [--config <path>]

Options:
  --if-idle                    Stop only when no Operation is running
`,
	"daemon restart": `Usage:
  talos-computer daemon restart [--config <path>]
`,
	"daemon status": `Usage:
  talos-computer daemon status [--json] [--config <path>]

Show the daemon process, the server connection, and the runtime summary.
`,
	"daemon logs": `Usage:
  talos-computer daemon logs [-f] [-n <lines>] [--config <path>]

Options:
  -f, --follow                 Keep printing new log lines
  -n <lines>                   Number of lines to show (default: 200)
`,
	"daemon runners": `Usage:
  talos-computer daemon runners [--json]
  talos-computer daemon runners inspect <agent-id>
  talos-computer daemon runners stop <agent-id>
`,
	"daemon service": `Usage:
  talos-computer daemon service list [--json]
  talos-computer daemon service start <name> -- <command> [args...]
  talos-computer daemon service stop <name>
`,
	"runtime": `Usage:
  talos-computer runtime check [codex|claude|pi|antigravity] [options]
  talos-computer runtime login <codex|claude|pi|antigravity> [options]

Commands:
  check                        Check Codex, Claude Code, Pi, and Antigravity on this computer
  login                        Open one of them here so you can sign in to it
`,
	"runtime login": `Usage:
  talos-computer runtime login <codex|claude|pi|antigravity> [options]

Open a runtime on this terminal so you can sign in to it, for example enter
/login in Pi. The runtime starts the way the daemon starts it: its native entry
point and, on Windows, the Node runtime this Computer ships rather than the
Node on your PATH. When the runtime exits, this command checks it again.

Options:
` + runtimePathOptions + "\n" + configOption + "\n",
	"runtime check": `Usage:
  talos-computer runtime check [codex|claude|pi|antigravity] [options]

Check whether each runtime is installed, signed in, and compatible.
Works without login and without a running daemon.

Statuses:
  READY                        Install, version, sign-in, and protocol checks passed
  NOT_INSTALLED                No usable executable was found
  AUTH_REQUIRED                The runtime needs sign-in or credentials
  INCOMPATIBLE                 The version or protocol is not supported
  ERROR                        The check failed; the reason is shown

Exit code: 0 when at least one checked runtime is READY. A single-runtime
check returns 13 for NOT_INSTALLED, 11 for AUTH_REQUIRED, 14 for
INCOMPATIBLE, and 1 for ERROR.

Options:
  --live                       Also ask each READY runtime for a model reply
  --json                       Print machine-readable results
` + runtimePathOptions + "\n" + configOption + "\n",
	"reminders": `Usage:
  talos-computer reminders add --title "<what>" --at <RFC 3339 time> [--list Talos] [--json]

Create one reminder in the macOS Reminders app. It is free and local; macOS
asks for Automation permission the first time.
`,
	"reminders add": `Usage:
  talos-computer reminders add --title "<what>" --at <RFC 3339 time> [--list Talos] [--json]

Options:
  --title <text>               What to be reminded about
  --at <RFC 3339 time>         When to be reminded, such as 2026-11-26T15:00:00Z
  --list <name>                Reminders list (default: Talos)
  --json                       Print machine-readable results
`,
	"doctor": `Usage:
  talos-computer doctor [--json] [--prune] [--stop-stale] [options]

Check the installation, configuration, connection, and runtimes.

Options:
  --json                       Print machine-readable results
  --prune                      Remove this computer's local enrollment
  --stop-stale                 Stop only leftover Talos test processes
` + runtimePathOptions + "\n" + configOption + "\n",
	"version": `Usage:
  talos-computer version
`,
}

const generalHelp = `Talos Computer connects this computer to Talos so agents can run here.

Usage:
  talos-computer <command> [options]

Commands:
  login                        Authorize and connect this computer
  auth status                  Show the site, Computer, and authorization
  daemon start|stop|restart    Manage the background daemon
  daemon status                Show the connection and runtime summary
  daemon logs [-f]             Show daemon logs
  runtime check [name]         Check Codex, Claude Code, Pi, and Antigravity
  runtime login <name>         Open a runtime here to sign in to it
  doctor                       Check installation, connection, and runtimes
  reminders add                Add one reminder to the macOS Reminders app
  version                      Show the CLI version

Get started:
  talos-computer login

Run "talos-computer <command> --help" for options.
`

// usage renders help naming this environment's own command.
func usage(topic, commandName string) string {
	text := generalHelp
	if topic != "" {
		text = commandHelp[topic]
	}
	return strings.ReplaceAll(text, "talos-computer ", commandName+" ")
}
