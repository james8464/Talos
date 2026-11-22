import AVFoundation
import Foundation

@main
struct VoiceEndpointChecks {
    static func main() throws {
        let partial = VoiceRecognitionUpdate(text: "Can you here", final: false, failed: false)
        precondition(partial == .caption("Can you here"))
        let final = VoiceRecognitionUpdate(text: "Can you hear me?", final: true, failed: false)
        precondition(final == .submit("Can you hear me?"))
        precondition(VoiceRecognitionUpdate(text: "", final: true, failed: false) == .retry)
        precondition(VoiceRecognitionUpdate(text: "Can you", final: false, failed: true) == .retry)
        precondition(roomRequest("Hey Talos, add buy paper to my to-do list.") == "add buy paper to my to-do list.")
        precondition(roomRequest("Talos, can you hear me?") == "can you hear me?")
        for chatter in ["Add buy paper to my to-do list.", "She said hey Talos.", "Talos is listening.",
                        "Jamie, remind me in ten minutes.", "Hey Talosaurus, are you there?", "Hey, tell us what happened."] {
            precondition(roomRequest(chatter) == nil)
        }
        var mode = VoiceCaptureMode.dictation
        var draft = ""
        precondition(mode.appendFinalToDraft("Please make a note", draft: &draft))
        precondition(mode == .dictation)
        mode = .finishingDraft
        precondition(mode.appendFinalToDraft("about Thursday's meeting", draft: &draft))
        precondition(mode == .room && draft == "Please make a note about Thursday's meeting")
        precondition(!mode.appendFinalToDraft("Overheard conversation", draft: &draft))
        let format = AVAudioFormat(standardFormatWithSampleRate: 24_000, channels: 1)!
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 480)!
        buffer.frameLength = 480
        let samples = buffer.floatChannelData![0]
        let endpoint = VoiceEndpoint()
        for index in 0..<480 { samples[index] = 0 }
        endpoint.observe(buffer, at: 1)
        precondition(endpoint.remaining(afterPartial: 1, now: 2.25) > 0)
        for index in 0..<480 { samples[index] = 0.1 * sin(Float(index) / 10) }
        endpoint.observe(buffer, at: 1.64)
        precondition(endpoint.remaining(afterPartial: 1, now: 1.65) > 0.4)
        precondition(endpoint.remaining(afterPartial: 1, now: 2.80) > 0)
        endpoint.observe(buffer, at: 2.99)
        precondition(endpoint.remaining(afterPartial: 1, now: 3) > 1)
        precondition(endpoint.remaining(afterPartial: 1, now: 4.35) == 0)
        endpoint.observe(buffer, at: 6.99)
        precondition(endpoint.remaining(afterPartial: 1, now: 7) == 0)
        print("Passed address, mid-sentence pause, stalled-caption and final silence checks.")

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
            precondition(extended && recorded.remaining(afterPartial: time, now: time + 1.36) == 0)
            print("Passed delayed-caption check on a real speech waveform.")
        }
    }
}
