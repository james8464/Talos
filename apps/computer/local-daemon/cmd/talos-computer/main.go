// Command talos-computer is the Local Computer control CLI.
package main

import (
	"os"

	"github.com/simplexai-labs/talos/apps/computer/local-daemon/internal/computercli"
)

func main() {
	os.Exit(computercli.Main(os.Args[1:]))
}
