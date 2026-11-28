# Talos Mobile

Native SwiftUI iOS companion for the Talos remote-control daemon.

## Run

1. Start the macOS app with its app server enabled.
2. Run `TalosRemoteDaemon` with matching app-server credentials and a remote token.
3. Build and run `TalosMobile` from `apps/macos/Talos.xcodeproj`.
4. Enter the daemon URL and token.

The app supports session selection and creation, prompt sending, stop,
transcript polling, and approval allow/deny. It uses the same HTTP API as the
daemon's mobile web client.

For local HTTP daemons, `NSAllowsArbitraryLoads` is enabled in the development
Info.plist. Use HTTPS or a trusted network for anything beyond local testing.
