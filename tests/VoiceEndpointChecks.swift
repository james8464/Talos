import AVFoundation
import Foundation

@main
struct VoiceEndpointChecks {
    static func main() throws {
        let format = AVAudioFormat(standardFormatWithSampleRate: 24_000, channels: 1)!
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 480)!
        buffer.frameLength = 480
        let samples = buffer.floatChannelData![0]
        let endpoint = VoiceEndpoint()
        for index in 0..<480 { samples[index] = 0 }
        endpoint.observe(buffer, at: 1)
        precondition(endpoint.remaining(afterPartial: 1, now: 1.66) == 0)
        for index in 0..<480 { samples[index] = 0.1 * sin(Float(index) / 10) }
        endpoint.observe(buffer, at: 1.64)
        precondition(endpoint.remaining(afterPartial: 1, now: 1.65) > 0.4)
        precondition(endpoint.remaining(afterPartial: 1, now: 2.10) == 0)
        endpoint.observe(buffer, at: 2.99)
        precondition(endpoint.remaining(afterPartial: 1, now: 3) == 0)
        print("Passed silent, ongoing speech, trailing silence and continuous-noise checks.")

        if CommandLine.arguments.count > 1 {
            let file = try AVAudioFile(forReading: URL(fileURLWithPath: CommandLine.arguments[1]))
            let frames = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: 480)!
            let recorded = VoiceEndpoint()
            var time = 0.0
            var extended = false
            while file.framePosition < file.length {
                try file.read(into: frames, frameCount: 480)
                time += Double(frames.frameLength) / file.processingFormat.sampleRate
                recorded.observe(frames, at: time)
                if time >= 0.65, time < 1.0, recorded.remaining(afterPartial: 0, now: time) > 0 {
                    extended = true
                }
            }
            precondition(extended && recorded.remaining(afterPartial: time, now: time + 0.66) == 0)
            print("Passed delayed-caption check on a real speech waveform.")
        }
    }
}
