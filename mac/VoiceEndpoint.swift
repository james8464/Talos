import Accelerate
import AVFoundation
import Foundation

// Partial transcripts can stall mid-sentence. Follow the input level between updates.
final class VoiceEndpoint {
    private let lock = NSLock()
    private var lastAudible: TimeInterval?

    func observe(_ buffer: AVAudioPCMBuffer, at time: TimeInterval) {
        guard buffer.frameLength > 0, let samples = buffer.floatChannelData?[0] else { return }
        var rms: Float = 0
        vDSP_rmsqv(samples, vDSP_Stride(buffer.stride), &rms, vDSP_Length(buffer.frameLength))
        if rms >= 0.006 {
            lock.lock()
            lastAudible = time
            lock.unlock()
        }
    }

    var lastActivity: TimeInterval? {
        lock.lock()
        defer { lock.unlock() }
        return lastAudible
    }

    func remaining(afterPartial partial: TimeInterval, now: TimeInterval) -> TimeInterval {
        // Allow thinking pauses. A steady background sound must not hold the room open indefinitely.
        let lastInput = max(partial, lastActivity ?? partial)
        return max(0, min(lastInput + 1.35, partial + 6) - now)
    }
}

// Only a successful final recognition result may become a room turn.
enum VoiceRecognitionUpdate: Equatable {
    case caption(String), submit(String), retry, pending

    init(text: String?, final: Bool, failed: Bool) {
        let words = text?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if failed || (final && words.isEmpty) {
            self = .retry
        } else if final {
            self = .submit(words)
        } else {
            self = words.isEmpty ? .pending : .caption(words)
        }
    }
}

// Require an explicit salutation at the beginning of a hands-free request.
func roomRequest(_ transcript: String) -> String? {
    let phrase = #"(?i)^\s*(?:(?:hey|ok|okay|hello)[\s,]+talos\b|talos(?=[,:]))[\s,:.!?–—-]*"#
    guard let address = transcript.range(of: phrase, options: .regularExpression) else { return nil }
    return String(transcript[address.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
}

enum VoiceCaptureMode {
    case room, dictation, finishingDraft

    mutating func appendFinalToDraft(_ words: String, draft: inout String) -> Bool {
        guard self != .room else { return false }
        draft = draft.isEmpty ? words : draft + " " + words
        if self == .finishingDraft { self = .room }
        return true
    }
}
