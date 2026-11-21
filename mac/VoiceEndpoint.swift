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

    static func removePlaybackPrefix(_ words: String, playback: String) -> String {
        func token(_ part: Substring) -> String { part.lowercased().trimmingCharacters(in: .punctuationCharacters) }
        let parts = words.split(whereSeparator: { $0.isWhitespace })
        let heard = parts.map(token)
        let said = playback.split(whereSeparator: { $0.isWhitespace }).map(token)
        guard heard.count >= 3, said.count >= 2 else { return words }
        var matched = 0
        for start in said.indices where said[start] == heard[0] {
            var count = 0
            while count < heard.count && start + count < said.count && heard[count] == said[start + count] { count += 1 }
            matched = max(matched, count)
        }
        guard matched >= 2, matched < parts.count else { return words }
        return String(words[parts[matched].startIndex...])
    }

    static func conversationalInterruption(_ words: String, playback: String, allowSpeech: Bool = false) -> Bool {
        func normalize(_ text: String) -> String {
            text.lowercased().split(whereSeparator: { !$0.isLetter && !$0.isNumber }).joined(separator: " ")
        }
        let heard = normalize(words)
        guard !heard.isEmpty, !normalize(playback).contains(heard) else { return false }
        if allowSpeech {
            let tokens = heard.split(separator: " ")
            let output = Set(normalize(playback).split(separator: " "))
            if ["stop", "wait", "pause"].contains(heard) { return true }
            if tokens.count >= 2 && tokens.contains(where: { !output.contains($0) }) { return true }
        }
        return heard.range(of: #"^(?:hold on|hang on|wait a second|one second|stop there|wait (?:i|that|actually)|no (?:i meant|sorry)|actually (?:i|it|what|make|the)|sorry i meant)\b"#,
                           options: .regularExpression) != nil
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

// A facing cue carries no identity. A missing angle or an extra person cancels it.
struct FacingAttention {
    private(set) var since: TimeInterval?
    private var last = -Double.infinity

    mutating func observe(_ facing: Bool, at now: TimeInterval) {
        if !facing { since = nil }
        else if since == nil || now - last > 1.8 { since = now }
        last = now
    }

    func available(from start: TimeInterval, until now: TimeInterval) -> Bool {
        guard let since else { return false }
        return since + 0.6 <= start && now >= last && now - last <= 1.8
    }

    static func singleFacing(persons: Int, faces: Int, width: Double, yaw: Double?, pitch: Double?) -> Bool {
        guard persons == 1, faces == 1, width >= 0.09, let yaw, let pitch else { return false }
        return abs(yaw) <= 0.30 && abs(pitch) <= 0.35
    }

    func accepts(_ words: String, from start: TimeInterval, until now: TimeInterval, hasOffer: Bool, hasContext: Bool = false) -> Bool {
        guard available(from: start, until: now) else { return false }
        return Self.direct(words, hasOffer: hasOffer, hasContext: hasContext)
    }

    static func direct(_ words: String, hasOffer: Bool, hasContext: Bool) -> Bool {
        let phrase = words.lowercased().trimmingCharacters(in: .whitespacesAndNewlines.union(.punctuationCharacters))
        if ["confirm", "cancel", "never mind"].contains(phrase) { return hasOffer }
        if hasContext && phrase.range(of: #"^(?:and (?:what|how|on)|what about|how about|tell me more|go on|actually|wait|no i meant|i think|i meant|that sounds|not really)\b"#, options: .regularExpression) != nil { return true }
        let direct = #"^(?:(?:can|could|would) you (?:please )?(?:hear|see|tell|explain|remind|add|create|save|make a note)|(?:please )?(?:remind me|create a task|add .+ to my (?:task|to.do) list|save a note)|(?:what|who|when|where|how) (?:is|are|does|do|many|much|long)|what (?:still )?needs my attention)\b"#
        // A new clause or vocative after punctuation can turn a question toward another listener.
        guard !phrase.contains(","), !phrase.contains("“"), !phrase.contains("\"") else { return false }
        return phrase.range(of: direct, options: .regularExpression) != nil
    }
}
