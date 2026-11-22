import AVFoundation
import Foundation

@main
struct NativeVoiceRecoveryReplay {
    @MainActor
    static func waitFor(_ condition: () -> Bool) async throws {
        let limit = ContinuousClock.now + .seconds(3)
        while !condition() {
            guard ContinuousClock.now < limit else { throw RoomError("Voice output recovery timed out.") }
            try await Task.sleep(for: .milliseconds(3))
        }
    }

    @MainActor
    static func main() async throws {
        let room = RoomController()
        var clock = 100.0
        var lastUtterance: AVSpeechUtterance?
        room.replayVoiceClock = { clock }
        var requests = 0
        var nativeTests = 0
        var receipts: [String] = []
        room.replayConfigureVoice("fixture")
        room.replayVoiceFetch = { _ in
            requests += 1
            return ["wav": "this is malformed audio"]
        }
        room.replayVoiceOutcome = { receipts.append($0) }
        room.replaySystemOutput = { utterance in lastUtterance = utterance; nativeTests += 1 }
        room.replayOutputTimeout = .seconds(1)
        room.replayCheckVoiceOutput()
        try await waitFor { nativeTests == 1 }
        precondition(requests == 1 && receipts == ["kokoro_failed"])
        room.replaySystemStarted(lastUtterance!)
        try await waitFor { receipts.last == "macos_started" }
        room.replaySystemFinished(lastUtterance!)
        try await waitFor { !room.speaking }
        precondition(receipts.last == "macos_finished" && room.status == "Voice test finished")
        room.replayOutputTimeout = .milliseconds(25)
        room.replayCheckVoiceOutput()
        try await waitFor { !room.speaking && receipts.last == "macos_unavailable" }
        precondition(requests == 1 && nativeTests == 2)
        precondition(room.status.contains("on screen"))
        precondition(room.lines.filter { $0.contains("test finished") }.count == 1)
        room.replaySystemFinished(lastUtterance!) // a late callback must not turn the failure into success.
        try await Task.sleep(for: .milliseconds(40))
        precondition(receipts.last == "macos_unavailable")
        room.replayCheckVoiceOutput()
        room.stopSpeaking()
        try await Task.sleep(for: .milliseconds(40))
        precondition(nativeTests == 3 && receipts.filter { $0 == "macos_unavailable" }.count == 1)
        clock += 61
        room.replayCheckVoiceOutput()
        try await waitFor { nativeTests == 4 }
        precondition(requests == 2 && receipts.filter { $0 == "kokoro_failed" }.count == 2)
        room.stopSpeaking()
        precondition(!room.microphone && !room.cameraRunning)
        print("Invalid Kokoro audio led to one macOS fallback. Its startup timeout and user cancellation settled separately.")
    }
}
