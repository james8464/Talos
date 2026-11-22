import AVFoundation
import Foundation

// Measures WAV availability at a silent PCM sink. No audio device is opened.
@main
struct MeasureSpokenPCM {
    @MainActor
    static func main() async throws {
        let args = CommandLine.arguments
        let client = RoomClient(port: Int(args[1])!, token: args[2])
        let file = URL(fileURLWithPath: args[3]).appendingPathComponent("first-clip.wav")
        let samples = Int(args[4])!
        for trial in 0..<samples {
            for streaming in (trial.isMultiple(of: 2) ? [false, true] : [true, false]) {
                let start = ProcessInfo.processInfo.systemUptime
                var elapsed: Double?
                func receive(_ text: String) async throws {
                    let audio = try await client.request("voice", body: ["text": text])
                    guard let encoded = audio["wav"] as? String, let bytes = Data(base64Encoded: encoded) else {
                        throw RoomError("Missing synthetic audio.")
                    }
                    try bytes.write(to: file)
                    let wav = try AVAudioFile(forReading: file)
                    let buffer = AVAudioPCMBuffer(pcmFormat: wav.processingFormat, frameCapacity: 128)!
                    while wav.framePosition < wav.length {
                        try wav.read(into: buffer, frameCount: 128)
                        guard buffer.frameLength > 0 else { break }
                        if let pcm = buffer.floatChannelData, (0..<Int(buffer.frameLength)).contains(where: { abs(pcm[0][$0]) > 0.001 }) {
                            elapsed = (ProcessInfo.processInfo.systemUptime - start) * 1000
                            return
                        }
                    }
                    throw RoomError("No voiced samples in generated clip.")
                }
                let body: [String: Any] = ["source": "voice", "text": "Tell me something about the seasons.",
                                           "remember": false, "request_id": UUID().uuidString]
                if streaming {
                    _ = try await client.spokenTurn(body: body) { event in
                        if elapsed == nil, let sentence = event["sentence"] as? String { try await receive(sentence) }
                    }
                } else {
                    let result = try await client.request("voice/facing", body: body)
                    guard let answer = result["reply"] as? String, let first = RoomController.speechClips(answer).first else {
                        throw RoomError("Missing synthetic model reply.")
                    }
                    try await receive(first)
                }
                guard let elapsed else { throw RoomError("No first audio.") }
                print(String(format: "%@ first voiced PCM ready %.2f ms", streaming ? "stream" : "full", elapsed))
            }
        }
    }
}
