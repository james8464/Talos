import Foundation

enum Provider: String, CaseIterable, Identifiable {
    case local
    case openAI
    case anthropic
    case gemini

    var id: String { rawValue }

    var title: String {
        switch self {
        case .local: return "Local"
        case .openAI: return "OpenAI"
        case .anthropic: return "Anthropic"
        case .gemini: return "Google Gemini"
        }
    }

    var symbol: String {
        switch self {
        case .local: return "cpu"
        case .openAI: return "sparkle"
        case .anthropic: return "text.book.closed"
        case .gemini: return "diamond"
        }
    }

    var keyAccount: String? {
        switch self {
        case .local: return nil
        case .openAI: return "openai"
        case .anthropic: return "anthropic"
        case .gemini: return "gemini"
        }
    }

    var defaultBaseURL: String {
        switch self {
        case .local: return "http://127.0.0.1:11434"
        case .openAI: return "https://api.openai.com/v1"
        case .anthropic: return "https://api.anthropic.com"
        case .gemini: return "https://generativelanguage.googleapis.com/v1beta/openai"
        }
    }

    var defaultModel: String {
        switch self {
        case .local: return "qwen3:8b-16k"
        case .openAI: return "gpt-5"
        case .anthropic: return "claude-sonnet-4-5"
        case .gemini: return "gemini-2.5-pro"
        }
    }

    var apiKeyURL: URL? {
        switch self {
        case .local: return nil
        case .openAI: return URL(string: "https://platform.openai.com/api-keys")
        case .anthropic: return URL(string: "https://console.anthropic.com/settings/keys")
        case .gemini: return URL(string: "https://aistudio.google.com/app/apikey")
        }
    }

    var providerNote: String {
        switch self {
        case .local:
            return "Ollama runs the model on this Mac. Talos only talks to the server address above."
        case .openAI:
            return "The base URL can point at OpenAI or any OpenAI-compatible service, including a local gateway."
        case .anthropic:
            return "Anthropic requests use the Messages API and your own API key."
        case .gemini:
            return "Gemini uses Google's OpenAI-compatible endpoint."
        }
    }

    var privacyNote: String {
        switch self {
        case .local:
            return "Requests stay on this Mac and go only to your Ollama server."
        case .openAI:
            return "Requests go to OpenAI. Talos keeps the key in the macOS Keychain and does not write prompts to disk."
        case .anthropic:
            return "Requests go to Anthropic. Talos keeps the key in the macOS Keychain and does not write prompts to disk."
        case .gemini:
            return "Requests go to Google. Talos keeps the key in the macOS Keychain and does not write prompts to disk."
        }
    }
}

struct ModelChoice: Identifiable, Hashable {
    let provider: Provider
    let id: String

    var display: String { "\(provider.title) · \(id)" }
}

struct ChatTurn {
    let role: Role
    let text: String
}

enum ProviderError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let value): return value
        }
    }
}

/// OpenAI-compatible streaming chat. Covers OpenAI and Gemini's compatible API.
struct OpenAICompatibleClient {
    let baseURL: String
    let apiKey: String
    var usesCompletionTokens = false
    var sendsTemperature = false
    var sendsReasoningEffort = false

    func models() async throws -> [String] {
        guard let url = URL(string: root + "/models") else {
            throw ProviderError.message("The base URL is invalid.")
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = 15
        request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw ProviderError.message("The provider returned no response.")
        }
        guard http.statusCode == 200 else {
            throw ProviderError.message("The provider answered \(http.statusCode). Check the API key.")
        }
        struct List: Decodable {
            struct Entry: Decodable { let id: String }
            let data: [Entry]
        }
        return try JSONDecoder().decode(List.self, from: data).data.map(\.id)
    }

    func stream(model: String, messages: [ChatTurn], settings: GenerationSettings) -> AsyncThrowingStream<String, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    guard let url = URL(string: root + "/chat/completions") else {
                        throw ProviderError.message("The base URL is invalid.")
                    }
                    var request = URLRequest(url: url)
                    request.httpMethod = "POST"
                    request.timeoutInterval = 600
                    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")

                    var payload: [String: Any] = [
                        "model": model,
                        "stream": true,
                        "messages": messages.map { ["role": $0.role.rawValue, "content": $0.text] },
                    ]
                    let reasoningModel = supportsReasoning(model)
                    let tokenKey = usesCompletionTokens && reasoningModel ? "max_completion_tokens" : "max_tokens"
                    payload[tokenKey] = settings.maxTokens
                    if sendsTemperature {
                        payload["temperature"] = settings.temperature
                    }
                    if sendsReasoningEffort, reasoningModel, let effort = settings.reasoning.openAIValue {
                        payload["reasoning_effort"] = effort
                    }
                    request.httpBody = try JSONSerialization.data(withJSONObject: payload)

                    let (bytes, response) = try await URLSession.shared.bytes(for: request)
                    guard let http = response as? HTTPURLResponse else {
                        throw ProviderError.message("The provider returned no response.")
                    }
                    guard http.statusCode == 200 else {
                        throw ProviderError.message("The provider answered \(http.statusCode). Check the API key and model.")
                    }
                    for try await line in bytes.lines {
                        if Task.isCancelled { break }
                        guard line.hasPrefix("data:") else { continue }
                        let payload = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
                        if payload == "[DONE]" { break }
                        guard let data = payload.data(using: .utf8),
                              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                              let choices = object["choices"] as? [[String: Any]],
                              let delta = choices.first?["delta"] as? [String: Any],
                              let text = delta["content"] as? String,
                              !text.isEmpty else { continue }
                        continuation.yield(text)
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private func supportsReasoning(_ model: String) -> Bool {
        let id = model.lowercased()
        return id.hasPrefix("gpt-5") || id.hasPrefix("o1") || id.hasPrefix("o3") || id.hasPrefix("o4")
    }

    private var root: String {
        baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    }
}

/// Anthropic Messages API streaming.
struct AnthropicClient {
    let baseURL: String
    let apiKey: String

    func models() async throws -> [String] {
        guard let url = URL(string: root + "/v1/models") else {
            throw ProviderError.message("The base URL is invalid.")
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = 15
        request.setValue(apiKey, forHTTPHeaderField: "x-api-key")
        request.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw ProviderError.message("Anthropic returned no response.")
        }
        guard http.statusCode == 200 else {
            throw ProviderError.message("Anthropic answered \(http.statusCode). Check the API key.")
        }
        struct List: Decodable {
            struct Entry: Decodable { let id: String }
            let data: [Entry]
        }
        return try JSONDecoder().decode(List.self, from: data).data.map(\.id)
    }

    func stream(model: String, messages: [ChatTurn], settings: GenerationSettings) -> AsyncThrowingStream<String, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    guard let url = URL(string: root + "/v1/messages") else {
                        throw ProviderError.message("The base URL is invalid.")
                    }
                    var request = URLRequest(url: url)
                    request.httpMethod = "POST"
                    request.timeoutInterval = 600
                    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    request.setValue(apiKey, forHTTPHeaderField: "x-api-key")
                    request.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")

                    let system = messages.first(where: { $0.role == .system })?.text ?? ""
                    let turns = messages.filter { $0.role != .system }
                        .map { ["role": $0.role == .assistant ? "assistant" : "user", "content": $0.text] }

                    var payload: [String: Any] = [
                        "model": model,
                        "max_tokens": settings.maxTokens,
                        "stream": true,
                        "messages": turns,
                    ]
                    if !system.isEmpty {
                        payload["system"] = system
                    }
                    if let budget = settings.reasoning.anthropicBudget, supportsThinking(model) {
                        payload["thinking"] = ["type": "enabled", "budget_tokens": budget]
                        payload["max_tokens"] = max(settings.maxTokens, budget + 1024)
                    } else {
                        payload["temperature"] = settings.temperature
                    }
                    request.httpBody = try JSONSerialization.data(withJSONObject: payload)

                    let (bytes, response) = try await URLSession.shared.bytes(for: request)
                    guard let http = response as? HTTPURLResponse else {
                        throw ProviderError.message("Anthropic returned no response.")
                    }
                    guard http.statusCode == 200 else {
                        throw ProviderError.message("Anthropic answered \(http.statusCode). Check the API key and model.")
                    }
                    for try await line in bytes.lines {
                        if Task.isCancelled { break }
                        guard line.hasPrefix("data:") else { continue }
                        let payload = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
                        guard let data = payload.data(using: .utf8),
                              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                              object["type"] as? String == "content_block_delta",
                              let delta = object["delta"] as? [String: Any],
                              delta["type"] as? String == "text_delta",
                              let text = delta["text"] as? String,
                              !text.isEmpty else { continue }
                        continuation.yield(text)
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private func supportsThinking(_ model: String) -> Bool {
        model.contains("claude-sonnet-4") || model.contains("claude-opus-4") || model.contains("claude-3-7")
    }

    private var root: String {
        baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    }
}
