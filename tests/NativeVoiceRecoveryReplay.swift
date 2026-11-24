import AVFoundation
import Foundation

@main
struct NativeVoiceRecoveryReplay {
    @MainActor
    static func waitFor(_ condition: () -> Bool) async throws {
        let limit = ContinuousClock.now + .seconds(3)
        while !condition() {
            guard ContinuousClock.now < limit else { throw RoomError("Voice recovery timed out.") }
            try await Task.sleep(for: .milliseconds(3))
        }
    }

    @MainActor
    static func main() async throws {
        let room = RoomController()
        var clock = 100.0
        var requests = 0
        var nativeFallbacks = 0
        var receipts: [String] = []
        room.replayVoiceClock = { clock }
        room.replayConfigureVoice("fixture")
        room.replayVoiceFetch = { _ in
            requests += 1
            return ["wav": "this is malformed audio"]
        }
        room.replayVoiceOutcome = { receipts.append($0) }
        room.replaySystemOutput = { _ in nativeFallbacks += 1 }

        room.replayCheckVoiceOutput()
        try await waitFor { requests == 1 && !room.speaking }
        precondition(nativeFallbacks == 0)
        precondition(receipts == ["kokoro_failed"])
        precondition(room.status.contains("on screen"))
        precondition(room.voiceLabel.contains("text only"))

        clock += 61
        room.replayCheckVoiceOutput()
        try await waitFor { requests == 2 && !room.speaking }
        precondition(nativeFallbacks == 0)
        precondition(receipts.filter { $0 == "kokoro_failed" }.count == 2)
        precondition(room.status.contains("on screen"))

        room.stopSpeaking()
        precondition(!room.microphone && !room.cameraRunning)
        print("Kokoro failure stayed text-only; no system voice fallback was attempted.")
    }
}
