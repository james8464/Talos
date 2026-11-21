import Foundation
import Vision

enum VisitorFrame {
    static func observe(_ handler: VNImageRequestHandler, people: Int, faces: VNDetectFaceRectanglesRequest,
                        sample: Bool) -> (facing: Bool, sample: VNFeaturePrintObservation?) {
        guard people == 1, (try? handler.perform([faces])) != nil, let found = faces.results,
              let face = found.first, face.confidence >= 0.65,
              FacingAttention.singleFacing(persons: people, faces: found.count, width: face.boundingBox.width,
                                           yaw: face.yaw?.doubleValue, pitch: face.pitch?.doubleValue) else { return (false, nil) }
        guard sample else { return (true, nil) }
        let feature = VNGenerateImageFeaturePrintRequest()
        feature.revision = VNGenerateImageFeaturePrintRequestRevision2
        feature.regionOfInterest = face.boundingBox
        guard (try? handler.perform([feature])) != nil else { return (true, nil) }
        return (true, feature.results?.first)
    }
}

struct VisitProfile<Sample> {
    let id: String
    let name: String
    let sample: Sample
}

// Vision image descriptors suggest a candidate. The visitor confirms each new visit.
typealias VisitorProfile = VisitProfile<VNFeaturePrintObservation>

struct VisitorConversation<Sample> {
    typealias Profile = VisitProfile<Sample>
    let compare: (Sample, Sample) -> Float?

    enum Stage {
        case idle, askingName, askingConsent(String, String), askingReturn(Profile), finished
    }
    enum Answer {
        case prompt(String), enroll(String, String), returning(Profile), decline
    }
    init(compare: @escaping (Sample, Sample) -> Float?) { self.compare = compare }

    private(set) var stage = Stage.idle
    private(set) var sample: Sample?
    private var expires = -Double.infinity
    private var lastGreeting = -Double.infinity
    private(set) var confirmed: Profile?

    mutating func leave() {
        confirmed = nil
        sample = nil
        stage = .idle
        expires = -Double.infinity
    }

    mutating func expire(at time: TimeInterval) {
        if waiting && time > expires { stage = .finished }
    }

    mutating func begin(_ face: Sample, profiles: [Profile], now: TimeInterval) -> String? {
        guard case .idle = stage, now - lastGreeting >= 60 else { return nil }
        lastGreeting = now
        sample = face
        expires = now + 45
        let candidates = profiles.compactMap { p in compare(face, p.sample).map { (p, $0) } }
            .sorted { $0.1 < $1.1 }
        if let best = candidates.first, best.1 <= 0.32,
           candidates.count == 1 || candidates[1].1 - best.1 >= 0.12 {
            stage = .askingReturn(best.0)
            return "Are you \(best.0.name)? May I use your saved profile and remember this conversation?"
        }
        stage = .askingName
        return "Hello. I'm Talos. What name should I use for you? You can say skip."
    }

    var waiting: Bool {
        switch stage {
        case .askingName, .askingConsent, .askingReturn: return true
        case .idle, .finished: return false
        }
    }

    static func name(in words: String) -> String? {
        let first = String(words.prefix { !".!?;,".contains($0) }).trimmingCharacters(in: .whitespaces)
        let pattern = #"(?i)^(?:(?:my name is|call me|i am|i['’]m|it['’]s) )?([\p{L}][\p{L}’'-]*(?: [\p{L}][\p{L}’'-]*){0,2})$"#
        guard let regex = try? NSRegularExpression(pattern: pattern),
              let match = regex.firstMatch(in: first, range: NSRange(first.startIndex..., in: first)),
              let range = Range(match.range(at: 1), in: first) else { return nil }
        let name = String(first[range])
        let reserved = Set(["no", "yes", "okay", "ok", "please", "skip", "thanks", "you", "your", "not", "why",
                            "what", "can", "could", "don't", "forget", "cancel", "fine", "tired", "unknown", "someone"])
        guard name.count <= 60, reserved.isDisjoint(with: name.lowercased().split(separator: " ").map(String.init)) else { return nil }
        return name
    }

    mutating func answer(_ text: String, at now: TimeInterval) -> Answer? {
        expire(at: now)
        guard waiting, text.count <= 800 else { return nil }
        let clean = text.lowercased().trimmingCharacters(in: .punctuationCharacters.union(.whitespacesAndNewlines))
        let negative = ["no", "no thanks", "skip", "cancel", "not now", "no thank you", "never mind", "leave me alone", "rather not", "i would rather not"]
        if negative.contains(clean) { stage = .finished; return .decline }
        let positive = ["yes", "yes please", "that's right", "that’s right", "yes you may", "yes i agree"]
        switch stage {
        case .askingName:
            guard let name = Self.name(in: text) else { return nil }
            stage = .askingConsent(name, text)
            expires = now + 45
            return .prompt("May I remember your name as \(name), a visual reference, and the words you say to me?")
        case let .askingConsent(name, introduction):
            guard positive.contains(clean) else { return nil }
            stage = .finished
            return .enroll(name, introduction)
        case let .askingReturn(person):
            guard positive.contains(clean) else { return nil }
            stage = .finished
            return .returning(person)
        default: return nil
        }
    }

    mutating func use(_ person: Profile) {
        stage = .finished
        confirmed = person
    }
}

extension VisitorConversation where Sample == VNFeaturePrintObservation {
    static func distance(_ first: VNFeaturePrintObservation, _ second: VNFeaturePrintObservation) -> Float? {
        var value: Float = 0
        guard (try? first.computeDistance(&value, to: second)) != nil, value.isFinite else { return nil }
        return value
    }

}
