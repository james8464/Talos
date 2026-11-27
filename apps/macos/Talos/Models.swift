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

    private enum CodingKeys: String, CodingKey {
        case id, title, status
    }

    init(id: UUID = UUID(), title: String, status: PlanStatus = .pending) {
        self.id = id
        self.title = title
        self.status = status
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decodeIfPresent(UUID.self, forKey: .id) ?? UUID()
        title = try container.decodeIfPresent(String.self, forKey: .title) ?? ""
        status = try container.decodeIfPresent(PlanStatus.self, forKey: .status) ?? .pending
    }
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

    private enum CodingKeys: String, CodingKey {
        case id, role, text, isStreaming, model, toolName, toolStatus
        case attachmentPath, attachments, createdAt
    }

    init(id: UUID = UUID(), role: Role, text: String, isStreaming: Bool = false,
         model: String? = nil, toolName: String? = nil, toolStatus: ToolStatus? = nil,
         attachmentPath: String? = nil, attachments: [String] = [], createdAt: Date = Date()) {
        self.id = id
        self.role = role
        self.text = text
        self.isStreaming = isStreaming
        self.model = model
        self.toolName = toolName
        self.toolStatus = toolStatus
        self.attachmentPath = attachmentPath
        self.attachments = attachments
        self.createdAt = createdAt
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decodeIfPresent(UUID.self, forKey: .id) ?? UUID()
        role = try container.decode(Role.self, forKey: .role)
        text = try container.decodeIfPresent(String.self, forKey: .text) ?? ""
        isStreaming = try container.decodeIfPresent(Bool.self, forKey: .isStreaming) ?? false
        model = try container.decodeIfPresent(String.self, forKey: .model)
        toolName = try container.decodeIfPresent(String.self, forKey: .toolName)
        toolStatus = try container.decodeIfPresent(ToolStatus.self, forKey: .toolStatus)
        attachmentPath = try container.decodeIfPresent(String.self, forKey: .attachmentPath)
        attachments = try container.decodeIfPresent([String].self, forKey: .attachments) ?? []
        createdAt = try container.decodeIfPresent(Date.self, forKey: .createdAt) ?? Date()
    }
}

struct QueuedPrompt: Identifiable, Codable, Equatable {
    var id = UUID()
    var text: String
    var attachments: [String] = []
    var createdAt = Date()

    private enum CodingKeys: String, CodingKey {
        case id, text, attachments, createdAt
    }

    init(id: UUID = UUID(), text: String, attachments: [String] = [], createdAt: Date = Date()) {
        self.id = id
        self.text = text
        self.attachments = attachments
        self.createdAt = createdAt
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decodeIfPresent(UUID.self, forKey: .id) ?? UUID()
        text = try container.decodeIfPresent(String.self, forKey: .text) ?? ""
        attachments = try container.decodeIfPresent([String].self, forKey: .attachments) ?? []
        createdAt = try container.decodeIfPresent(Date.self, forKey: .createdAt) ?? Date()
    }
}

struct Session: Identifiable, Codable, Equatable {
    var id = UUID()
    var title: String
    var projectPath: String = ""
    var messages: [ChatMessage] = []
    var plan: [PlanStep] = []
    var queue: [QueuedPrompt] = []
    var approvedTools: [String] = []
    var archived = false
    var createdAt = Date()
    var updatedAt = Date()

    private enum CodingKeys: String, CodingKey {
        case id, title, projectPath, messages, plan, queue, approvedTools, archived, createdAt, updatedAt
    }

    init(id: UUID = UUID(), title: String, projectPath: String = "", messages: [ChatMessage] = [],
         plan: [PlanStep] = [], queue: [QueuedPrompt] = [], approvedTools: [String] = [],
         archived: Bool = false, createdAt: Date = Date(), updatedAt: Date = Date()) {
        self.id = id
        self.title = title
        self.projectPath = projectPath
        self.messages = messages
        self.plan = plan
        self.queue = queue
        self.approvedTools = approvedTools
        self.archived = archived
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decodeIfPresent(UUID.self, forKey: .id) ?? UUID()
        title = try container.decodeIfPresent(String.self, forKey: .title) ?? "New Task"
        projectPath = try container.decodeIfPresent(String.self, forKey: .projectPath) ?? ""
        messages = try container.decodeIfPresent([ChatMessage].self, forKey: .messages) ?? []
        plan = try container.decodeIfPresent([PlanStep].self, forKey: .plan) ?? []
        queue = try container.decodeIfPresent([QueuedPrompt].self, forKey: .queue) ?? []
        approvedTools = try container.decodeIfPresent([String].self, forKey: .approvedTools) ?? []
        archived = try container.decodeIfPresent(Bool.self, forKey: .archived) ?? false
        createdAt = try container.decodeIfPresent(Date.self, forKey: .createdAt) ?? Date()
        updatedAt = try container.decodeIfPresent(Date.self, forKey: .updatedAt) ?? createdAt
    }

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
        SlashCommand(id: "/retry", detail: "Retry the last turn"),
        SlashCommand(id: "/fork", detail: "Fork this task"),
        SlashCommand(id: "/archive", detail: "Archive this task"),
        SlashCommand(id: "/rename", detail: "Rename this task"),
        SlashCommand(id: "/model", detail: "Open the model picker"),
        SlashCommand(id: "/queue", detail: "Show or add to the queue"),
        SlashCommand(id: "/context", detail: "Break down context usage"),
        SlashCommand(id: "/status", detail: "Show model and tool state"),
        SlashCommand(id: "/usage", detail: "Show token usage"),
        SlashCommand(id: "/cost", detail: "Show token usage"),
        SlashCommand(id: "/diff", detail: "Open the changes panel"),
        SlashCommand(id: "/review", detail: "Review working tree changes"),
        SlashCommand(id: "/security-review", detail: "Review changes for security risks"),
        SlashCommand(id: "/rewind", detail: "Remove the last turn"),
        SlashCommand(id: "/export", detail: "Export this task as Markdown"),
        SlashCommand(id: "/doctor", detail: "Check local configuration"),
        SlashCommand(id: "/init", detail: "Create AGENTS.md"),
        SlashCommand(id: "/resume", detail: "List recent tasks"),
        SlashCommand(id: "/memory", detail: "Open AGENTS.md project memory"),
        SlashCommand(id: "/permissions", detail: "Open approvals and sandbox"),
        SlashCommand(id: "/config", detail: "Open Settings"),
        SlashCommand(id: "/providers", detail: "Configure providers"),
        SlashCommand(id: "/models", detail: "Configure models"),
        SlashCommand(id: "/skills", detail: "List local skills"),
        SlashCommand(id: "/plugins", detail: "List local plugins"),
        SlashCommand(id: "/mcp", detail: "List connected MCP tools"),
        SlashCommand(id: "/remote", detail: "Show the phone-control URL"),
        SlashCommand(id: "/todos", detail: "Show the current plan"),
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
