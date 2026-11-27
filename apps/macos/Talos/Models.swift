import Foundation

enum Role: String, Codable {
    case system
    case user
    case assistant
    case tool

    var chatRole: String {
        self == .tool ? "user" : rawValue
    }
}

enum ToolStatus: String, Codable {
    case succeeded
    case failed
    case denied
}

enum ApprovalDecision {
    case deny
    case allowOnce
    case allowForTask
}

enum PlanStatus: String, Codable, CaseIterable {
    case pending
    case inProgress
    case completed

    var title: String {
        switch self {
        case .pending: return "Pending"
        case .inProgress: return "In progress"
        case .completed: return "Completed"
        }
    }

    var symbol: String {
        switch self {
        case .pending: return "circle"
        case .inProgress: return "circle.dotted"
        case .completed: return "checkmark.circle.fill"
        }
    }
}

struct PlanStep: Identifiable, Codable, Equatable {
    var id = UUID()
    var title: String
    var status: PlanStatus = .pending
}

struct ChatMessage: Identifiable, Codable, Equatable {
    var id = UUID()
    let role: Role
    var text: String
    var isStreaming = false
    var model: String? = nil
    var toolName: String? = nil
    var toolStatus: ToolStatus? = nil
    var attachmentPath: String? = nil
    var attachments: [String] = []
    var createdAt = Date()
}

struct QueuedPrompt: Identifiable, Codable, Equatable {
    var id = UUID()
    var text: String
    var attachments: [String] = []
    var createdAt = Date()
}

struct Session: Identifiable, Codable, Equatable {
    var id = UUID()
    var title: String
    var messages: [ChatMessage] = []
    var plan: [PlanStep] = []
    var queue: [QueuedPrompt] = []
    var approvedTools: [String] = []
    var createdAt = Date()
    var updatedAt = Date()

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

struct ReasoningOption: Identifiable, Hashable {
    let id: String
    let title: String
    let detail: String
}

struct ModelCapabilities: Equatable {
    var reasoningOptions: [ReasoningOption] = []
    var defaultReasoning: String?
    var contextWindow: Int?
    var summary: String = "Reasoning not exposed by this model."

    static let none = ModelCapabilities()
}

struct GenerationSettings {
    var temperature: Double
    var maxTokens: Int
    var reasoning: String?
    var keepAlive: String
}

struct TokenUsage: Codable, Equatable, Sendable {
    var prompt: Int
    var completion: Int
    var estimated: Bool

    var total: Int { prompt + completion }
}

enum ModelStreamEvent: Sendable {
    case text(String)
    case usage(TokenUsage)
}

enum TokenEstimator {
    static func estimate(_ text: String) -> Int {
        guard !text.isEmpty else { return 0 }
        return max(1, (text.count + 3) / 4)
    }

    static func estimate(_ messages: [ChatMessage]) -> Int {
        messages.reduce(0) { total, message in
            total + estimate(message.text) + 4
        }
    }

    static func estimatedUsage(prompt: [ChatMessage], completion: String) -> TokenUsage {
        TokenUsage(
            prompt: estimate(prompt),
            completion: estimate(completion),
            estimated: true
        )
    }

    static func format(_ count: Int) -> String {
        if count >= 1_000_000 {
            return String(format: "%.1fm", Double(count) / 1_000_000)
        }
        if count >= 1_000 {
            return String(format: "%.1fk", Double(count) / 1_000)
        }
        return "\(count)"
    }
}

struct SlashCommand: Identifiable {
    let id: String
    let detail: String
}

enum SlashCommands {
    static let all: [SlashCommand] = [
        SlashCommand(id: "/new", detail: "Start a new task"),
        SlashCommand(id: "/clear", detail: "Clear this task"),
        SlashCommand(id: "/compact", detail: "Summarize earlier messages"),
        SlashCommand(id: "/stop", detail: "Stop the current response"),
        SlashCommand(id: "/model", detail: "Open the model picker"),
        SlashCommand(id: "/queue", detail: "Show the queue"),
        SlashCommand(id: "/status", detail: "Show model and tool state"),
        SlashCommand(id: "/settings", detail: "Open Settings"),
        SlashCommand(id: "/usage", detail: "Show token usage"),
        SlashCommand(id: "/help", detail: "Show commands"),
    ]
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

enum ApprovalPolicy: String, CaseIterable, Identifiable, Codable {
    case onRequest
    case never

    var id: String { rawValue }

    var title: String {
        switch self {
        case .onRequest: return "Ask before high-impact tools"
        case .never: return "Never ask"
        }
    }

    var summary: String {
        switch self {
        case .onRequest:
            return "Running commands, editing files, and controlling the desktop require approval."
        case .never:
            return "Tools run automatically inside the sandbox you choose."
        }
    }
}

enum SandboxMode: String, CaseIterable, Identifiable, Codable {
    case readOnly
    case workspaceWrite
    case fullAccess

    var id: String { rawValue }

    var title: String {
        switch self {
        case .readOnly: return "Read only"
        case .workspaceWrite: return "Workspace write"
        case .fullAccess: return "Full access"
        }
    }

    var summary: String {
        switch self {
        case .readOnly:
            return "Read files and run read-only commands. Writes are blocked."
        case .workspaceWrite:
            return "Read and write inside the workspace and temporary folders."
        case .fullAccess:
            return "No filesystem sandbox. Only use this when you trust the task."
        }
    }
}

enum AppearanceMode: String, CaseIterable, Identifiable, Codable {
    case system
    case light
    case dark

    var id: String { rawValue }

    var title: String {
        switch self {
        case .system: return "System"
        case .light: return "Light"
        case .dark: return "Dark"
        }
    }
}
