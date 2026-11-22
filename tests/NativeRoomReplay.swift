import Foundation

@main
struct NativeRoomReplay {
    @MainActor
    static func waitUntil(_ predicate: () -> Bool) async throws {
        let deadline = ContinuousClock.now + .seconds(12)
        while !predicate() {
            if ContinuousClock.now > deadline { throw RoomError("Native replay timed out.") }
            try await Task.sleep(for: .milliseconds(30))
        }
    }

    static func checkCameraCadence() {
        var cadence = CameraCadence()
        precondition(!cadence.hasFreshFrame(at: 0))
        precondition(cadence.next(at: 0, settledEmpty: false) == .analyze)
        precondition(cadence.next(at: 0.3, settledEmpty: true) == .skip)
        precondition(cadence.next(at: 0.75, settledEmpty: true) == .retain)
        precondition(cadence.hasFreshFrame(at: 2.7))
        precondition(!cadence.hasFreshFrame(at: 2.75))
        precondition(cadence.next(at: 3, settledEmpty: true) == .analyze)
        precondition(cadence.next(at: 3.75, settledEmpty: false) == .analyze)
        cadence.requestAnalysis()
        precondition(cadence.next(at: 4.5, settledEmpty: true) == .analyze)

        func requests(settledEmpty: Bool) -> Int {
            var detector = CameraCadence()
            return (0..<3600).reduce(0) { total, index in
                total + (detector.next(at: Double(index) / 30, settledEmpty: settledEmpty) == .analyze ? 1 : 0)
            }
        }
        let active = requests(settledEmpty: false)
        let empty = requests(settledEmpty: true)
        precondition(active > 160 && (80...87).contains(empty))
    }

    @MainActor
    static func main() async throws {
        checkCameraCadence()
        let room = RoomController()
        room.connect()
        try await waitUntil { room.status.contains("Practice room") }
        precondition(!room.microphone && !room.cameraRunning && !room.speaking)
        func send(_ text: String, expect: String) async throws {
            room.send(text)
            try await waitUntil { !room.busy }
            guard room.lines.last(where: { $0.hasPrefix("Talos:") })?.contains(expect) == true else {
                throw RoomError("Unexpected native reply to synthetic turn: " + text)
            }
            precondition(!room.speaking)
        }
        try await send("What am I wearing?", expect: "fresh camera")
        try await send("Remember that my test key is in the blue tin.", expect: "blue tin")
        try await send("Where is my test key?", expect: "blue tin")
        let file = URL(fileURLWithPath: CommandLine.arguments.last!)
        _ = try await room.client?.request("context/select", body: ["path":file.path,"kind":"imessage",
            "owner":"James","topic":"Atlas","person":"Maya"])
        try await room.privateSources.load()
        precondition(room.privateSources.availableApps.contains("Messages") && room.privateSources.enabledApps.isEmpty)
        try await room.privateSources.setApps(["Messages"])
        try await room.privateSources.load()
        precondition(room.privateSources.enabledApps == ["Messages"])
        try await room.privateSources.setApps([])
        try await send("Search my messages for cobalt", expect: "enabled")
        precondition(room.privateSources.sources.count == 1)
        guard let evidence = room.privateSources.links.first?.url,
              evidence.resolvingSymlinksInPath().path == file.resolvingSymlinksInPath().path else {
            throw RoomError("The brief did not link the selected fixture: " + room.privateSources.brief)
        }
        precondition(room.privateSources.brief.contains("forecast"))
        try await send("What do I owe people?", expect: "forecast")
        print("Native practice startup, silent typed turns, camera-off answer, durable fact and private briefing passed.")
    }
}
