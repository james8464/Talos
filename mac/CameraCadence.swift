import Foundation

/// Keep a fresh image for questions even when empty-room presence checks slow down.
struct CameraCadence {
    enum Work { case skip, retain, analyze }

    private(set) var lastFrame = -TimeInterval.infinity
    private var lastAnalysis = -TimeInterval.infinity

    mutating func next(at now: TimeInterval, settledEmpty: Bool) -> Work {
        guard now - lastFrame >= 0.7 else { return .skip }
        lastFrame = now
        if settledEmpty && now - lastAnalysis < 1.4 { return .retain }
        lastAnalysis = now
        return .analyze
    }

    mutating func requestAnalysis() {
        lastAnalysis = -.infinity
    }

    func hasFreshFrame(at now: TimeInterval) -> Bool {
        return now - lastFrame < 2
    }
}
