import Foundation

enum Role: String, Codable {
    case system
    case user
    case assistant
}

enum ReasoningEffort: String, CaseIterable, Identifiable, Codable {
    case off
    case low
    case medium
    case high

    var id: String { rawValue }

    var title: String {
        switch self {
        case .off: return "Off"
        case .low: return "Low"
        case .medium: return "Medium"
        case .high: return "High"
        }
    }

    var summary: String {
        switch self {
        case .off:
            return "No extra reasoning. Fastest responses."
        case .low:
            return "A short reasoning pass when the model supports it."
        case .medium:
            return "Balanced reasoning for everyday coding and analysis."
        case .high:
            return "Longer reasoning for difficult problems. Slower and uses more tokens."
        }
    }

    var openAIValue: String? {
        switch self {
        case .off: return nil
        case .low: return "low"
        case .medium: return "medium"
        case .high: return "high"
        }
    }

    var anthropicBudget: Int? {
        switch self {
        case .off: return nil
        case .low: return 1024
        case .medium: return 2048
        case .high: return 4096
        }
    }
}

enum ResponseSpeed: String, CaseIterable, Identifiable, Codable {
    case fast
    case balanced
    case thorough

    var id: String { rawValue }

    var title: String {
        switch self {
        case .fast: return "Fast"
        case .balanced: return "Balanced"
        case .thorough: return "Thorough"
        }
    }

    var summary: String {
        switch self {
        case .fast:
            return "Short answers with a small output budget. Quickest turnaround."
        case .balanced:
            return "The default output budget for most conversations."
        case .thorough:
            return "Room for long answers, patches, and explanations."
        }
    }

    var maxTokens: Int {
        switch self {
        case .fast: return 1024
        case .balanced: return 4096
        case .thorough: return 8192
        }
    }
}

struct GenerationSettings: Equatable {
    var temperature: Double
    var maxTokens: Int
    var reasoning: ReasoningEffort
    var keepAlive: String
}

struct ChatMessage: Identifiable, Equatable {
    let id = UUID()
    let role: Role
    var text: String
    var isStreaming = false
    var model: String? = nil
}

struct Session: Identifiable, Equatable {
    let id = UUID()
    var title: String
    var messages: [ChatMessage] = []
    var createdAt = Date()

    var preview: String {
        guard let reply = messages.last(where: { $0.role == .assistant && !$0.text.isEmpty }) else {
            return messages.isEmpty ? "No messages yet" : "Waiting for reply"
        }
        let line = reply.text
            .replacingOccurrences(of: "\n", with: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return line.isEmpty ? "Reply" : String(line.prefix(90))
    }
}
