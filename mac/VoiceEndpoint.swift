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
        let audible = lastActivity
        // A late caption needs a short settling period. Bound the hold in a noisy room.
        let deadline = audible.map { min(partial + 2, max(partial + 0.2, $0 + 0.45)) } ?? (partial + 0.65)
        return max(0, deadline - now)
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
