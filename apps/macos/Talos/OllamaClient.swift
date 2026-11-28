import Foundation

struct OllamaModelInfo: Equatable, Sendable {
    let capabilities: [String]
    let contextLength: Int?
    let parameterSize: String?
}

struct LocalModel: Identifiable, Hashable {
    let name: String
    let size: Int64?
    let parameterSize: String?

    var id: String { name }

    var detail: String {
        [parameterSize, sizeDescription].compactMap { $0 }.joined(separator: " · ")
    }

    private var sizeDescription: String? {
        guard let size else { return nil }
        let formatter = ByteCountFormatter()
        formatter.countStyle = .file
        return formatter.string(fromByteCount: size)
    }
}

/// Drops qwen's inline reasoning so only the answer is shown.
private struct ThinkStripper {
    private var buffer = ""
    private var inside = false

    mutating func push(_ chunk: String) -> String {
        buffer += chunk
        var output = ""
        while !buffer.isEmpty {
            if inside {
                if let end = buffer.range(of: "</think") {
                    buffer = String(buffer[end.upperBound...])
                    inside = false
                } else {
                    buffer = ""
                    break
                }
            } else if let start = buffer.range(of: "<think") {
                output += String(buffer[..<start.lowerBound])
                buffer = String(buffer[start.upperBound...])
                inside = true
            } else {
                // Hold a possible partial tag at the end.
                let hold = 9
                if buffer.count > hold {
                    let cut = buffer.index(buffer.endIndex, offsetBy: -hold)
                    output += String(buffer[..<cut])
                    buffer = String(buffer[cut...])
                }
                break
            }
        }
        return output
    }

    mutating func finish() -> String {
        defer { buffer = ""; inside = false }
        return inside ? "" : buffer
    }
}

struct OllamaClient {
    let baseURL: URL

    init(baseURL: URL = URL(string: "http://127.0.0.1:11434")!) {
        self.baseURL = baseURL
    }

    func models() async throws -> [LocalModel] {
        var request = URLRequest(url: baseURL.appendingPathComponent("api/tags"))
        request.timeoutInterval = 5
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw TalosError.message("Ollama is not answering at \(baseURL.absoluteString).")
        }
        struct Tags: Decodable {
            struct Entry: Decodable {
                struct Details: Decodable { let parameter_size: String? }
                let name: String
                let size: Int64?
                let details: Details?
            }
            let models: [Entry]
        }
        return try JSONDecoder().decode(Tags.self, from: data).models.map {
            LocalModel(name: $0.name, size: $0.size, parameterSize: $0.details?.parameter_size)
        }
    }

    func show(model: String) async throws -> OllamaModelInfo {
        var request = URLRequest(url: baseURL.appendingPathComponent("api/show"))
        request.httpMethod = "POST"
        request.timeoutInterval = 10
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["model": model])
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw TalosError.message("Ollama could not describe \(model).")
        }
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw TalosError.message("Ollama returned an unreadable model description.")
        }
        let capabilities = object["capabilities"] as? [String] ?? []
        let details = object["details"] as? [String: Any]
        let parameterSize = details?["parameter_size"] as? String
        let modelInfo = object["model_info"] as? [String: Any] ?? [:]
        let contextLength = modelInfo
            .first { $0.key.lowercased().hasSuffix("context_length") }
            .flatMap { $0.value as? Int }
        return OllamaModelInfo(
            capabilities: capabilities,
            contextLength: contextLength,
            parameterSize: parameterSize
        )
    }

    func stream(model: String, messages: [ChatMessage], settings: GenerationSettings) -> AsyncThrowingStream<ModelStreamEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var request = URLRequest(url: baseURL.appendingPathComponent("api/chat"))
                    request.httpMethod = "POST"
                    request.timeoutInterval = 600
                    request.setValue("application/json", forHTTPHeaderField: "Content-Type")

                    let messagePayload = messages.map { message -> [String: Any] in
                        var value: [String: Any] = [
                            "role": message.role.chatRole,
                            "content": message.text,
                        ]
                        if settings.allowImages, message.role == .user || message.role == .tool {
                            let images = ImageLoader.ollamaBase64(paths: message.attachments)
                            if !images.isEmpty {
                                value["images"] = images
                            }
                        }
                        return value
                    }
                    var payload: [String: Any] = [
                        "model": model,
                        "stream": true,
                        "keep_alive": settings.keepAlive,
                        "messages": messagePayload,
                        "options": [
                            "temperature": settings.temperature,
                            "num_predict": settings.maxTokens,
                        ],
                    ]
                    if supportsThinking(model) {
                        payload["think"] = settings.reasoning == "on"
                    }
                    request.httpBody = try JSONSerialization.data(withJSONObject: payload)

                    let (bytes, response) = try await URLSession.shared.bytes(for: request)
                    guard let http = response as? HTTPURLResponse else {
                        throw TalosError.message("Ollama returned no response.")
                    }
                    guard http.statusCode == 200 else {
                        throw TalosError.message("Ollama answered \(http.statusCode).")
                    }
                    var stripper = ThinkStripper()
                    for try await line in bytes.lines {
                        if Task.isCancelled { break }
                        guard let data = line.data(using: .utf8), !data.isEmpty else { continue }
                        struct Event: Decodable {
                            struct Message: Decodable { let content: String? }
                            let message: Message?
                            let done: Bool?
                            let prompt_eval_count: Int?
                            let eval_count: Int?
                        }
                        if let event = try? JSONDecoder().decode(Event.self, from: data) {
                            if let chunk = event.message?.content, !chunk.isEmpty {
                                let visible = stripper.push(chunk)
                                if !visible.isEmpty { continuation.yield(.text(visible)) }
                            }
                            if event.done == true {
                                if let prompt = event.prompt_eval_count, let completion = event.eval_count {
                                    continuation.yield(.usage(TokenUsage(
                                        prompt: prompt,
                                        completion: completion,
                                        estimated: false
                                    )))
                                }
                                break
                            }
                        }
                    }
                    let tail = stripper.finish()
                    if !tail.isEmpty { continuation.yield(.text(tail)) }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private func supportsThinking(_ model: String) -> Bool {
        let name = model.lowercased()
        return name.contains("qwen3") || name.contains("deepseek-r1") || name.contains("reason")
    }
}

enum TalosError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let value): return value
        }
    }
}
