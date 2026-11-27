import Foundation

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

    func stream(model: String, messages: [ChatMessage], settings: GenerationSettings) -> AsyncThrowingStream<String, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var request = URLRequest(url: baseURL.appendingPathComponent("api/chat"))
                    request.httpMethod = "POST"
                    request.timeoutInterval = 600
                    request.setValue("application/json", forHTTPHeaderField: "Content-Type")

                    var payload: [String: Any] = [
                        "model": model,
                        "stream": true,
                        "keep_alive": settings.keepAlive,
                        "messages": messages.map { ["role": $0.role.rawValue, "content": $0.text] },
                        "options": [
                            "temperature": settings.temperature,
                            "num_predict": settings.maxTokens,
                        ],
                    ]
                    if supportsThinking(model) {
                        payload["think"] = settings.reasoning != .off
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
                        }
                        if let event = try? JSONDecoder().decode(Event.self, from: data) {
                            if let chunk = event.message?.content, !chunk.isEmpty {
                                let visible = stripper.push(chunk)
                                if !visible.isEmpty { continuation.yield(visible) }
                            }
                            if event.done == true { break }
                        }
                    }
                    let tail = stripper.finish()
                    if !tail.isEmpty { continuation.yield(tail) }
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
