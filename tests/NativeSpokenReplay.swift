import Foundation
import AVFoundation

@main
struct NativeSpokenReplay {
    @MainActor
    static func waitFor(_ predicate: () -> Bool) async throws {
        let deadline = ContinuousClock.now + .seconds(5)
        while !predicate() {
            guard ContinuousClock.now < deadline else { throw RoomError("Spoken replay timed out.") }
            try await Task.sleep(for: .milliseconds(5))
        }
    }

    @MainActor
    static func main() async throws {
        let room = RoomController()
        room.connect()
        try await waitFor { room.status.contains("Practice room") }
        let scratch = URL(fileURLWithPath: CommandLine.arguments.last!).appendingPathComponent("spoken.wav")
        var clips = 0
        var first: Double?
        var stopped: Double?
        var interrupt: Double?
        let end = ProcessInfo.processInfo.systemUptime
        room.replayClip = { data in
            try data.write(to: scratch)
            let file = try AVAudioFile(forReading: scratch)
            let buffer = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: 128)!
            var found = false
            while file.framePosition < file.length {
                try file.read(into: buffer, frameCount: 128)
                guard buffer.frameLength > 0 else { break }
                if let pcm = buffer.floatChannelData, (0..<Int(buffer.frameLength)).contains(where: { abs(pcm[0][$0]) > 0.001 }) {
                    found = true
                    break
                }
            }
            guard found else { throw RoomError("No voiced PCM at silent sink.") }
            clips += 1
            if first == nil { first = ProcessInfo.processInfo.systemUptime }
        }
        room.replayStop = { stopped = ProcessInfo.processInfo.systemUptime }
        room.send("Tell me about spring.", source: "voice", speechEnd: end, viaFacing: true)
        try await waitFor { clips > 0 }
        precondition(room.speaking && room.busy)
        interrupt = ProcessInfo.processInfo.systemUptime
        room.stopSpeaking()
        precondition(!room.speaking && !room.busy)
        room.send("What am I wearing?")
        try await waitFor { !room.busy && room.lines.contains(where: { $0.contains("fresh camera") }) }
        try await Task.sleep(for: .milliseconds(100))
        precondition(clips == 1 && !room.speaking && !room.microphone && !room.cameraRunning)
        precondition(!room.lines.contains(where: { $0.hasPrefix("Talos: Spring follows") }))
        print(String(format: "Native silent sink: first voiced PCM %.1f ms, stop dispatch %.3f ms.",
                     (first! - end) * 1000, (stopped! - interrupt!) * 1000))
    }
}
