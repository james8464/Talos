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

struct ConversationFocus {
    private var until = -Double.infinity
    private var awaitingAnswer = false
    private var explicitlyCalled = false

    mutating func open(at now: TimeInterval, question: Bool, called: Bool = false) {
        until = now + 8
        awaitingAnswer = question
        explicitlyCalled = called
    }

    mutating func clear() { until = -Double.infinity; awaitingAnswer = false; explicitlyCalled = false }

    func isFollowUp(_ words: String, at time: TimeInterval) -> Bool {
        roomRequest(words) == nil && !explicitlyCalled && time <= until
    }

    func request(_ words: String, at time: TimeInterval) -> String? {
        if let addressed = roomRequest(words) { return addressed }
        guard time <= until else { return nil }
        if explicitlyCalled { return words }
        let linked = #"(?i)^(?:and (?:what|how|when|where|on|if|for)|what about|how about|actually[, ]+(?:make that|i meant)|i meant|make that|why are you doing that|what (?:still )?needs my attention)\b"#
        if words.range(of: linked, options: .regularExpression) != nil { return words }
        if awaitingAnswer && words.range(of: #"(?i)^(?:in )?[a-z0-9 -]+ (?:minutes?|hours?|days?)[.!? ]*$|^(?:never mind|cancel it)[.!? ]*$"#,
                                         options: .regularExpression) != nil { return words }
        return nil
    }

    static func isInterruption(_ words: String, playback: String) -> Bool {
        guard let command = roomRequest(words) else { return false }
        let clean = command.lowercased().trimmingCharacters(in: .punctuationCharacters)
        guard ["stop", "pause", "wait", "hold on", "stop speaking"].contains(clean) else { return false }
        return !playback.lowercased().contains(words.lowercased().trimmingCharacters(in: .punctuationCharacters))
    }
}

// A caption ending in a function word often precedes the object of a request.
func needsContinuation(_ caption: String) -> Bool {
    caption.range(of: #"(?i)\b(?:to|for|from|with|because|until|about|and then|called|named|the|my|that I)\s*[,–—-]?$"#,
                  options: .regularExpression) != nil
}
