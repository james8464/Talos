//go:build windows

package runtimehistory

import (
	"os"

	"github.com/simplexai-labs/talos/apps/computer/local-daemon/internal/platform/privatefs"
)

func openNoFollow(path string) (*os.File, error) { return privatefs.OpenRead(path, false) }
