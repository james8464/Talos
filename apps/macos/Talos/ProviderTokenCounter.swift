import Foundation

enum TokenCounterProvider: String, Sendable {
    case anthropic
    case gemini
    case ollama

    var method: String {
        switch self {
        case .anthropic: return "Anthropic count_tokens"
        case .gemini: return "Gemini countTokens"
        case .ollama: return "Ollama prompt_eval"
        }
    }
}

struct ProviderTokenCount: Sendable {
    let count: Int
    let method: String
}

enum ProviderTokenCounter {
    static func count(
        messages: [ChatMessage],
        provider: TokenCounterProvider,
        model: String,
        baseURL: String,
        apiKey: String,
        isOpenCode: Bool = false,
        openCodeSession: String? = nil
    ) async -> ProviderTokenCount? {
        guard messages.allSatisfy({
            $0.attachments.isEmpty && $0.attachmentPath == nil
        }) else {
            return nil
        }
        switch provider {
        case .anthropic:
            return await anthropicCount(
                messages: messages,
                model: model,
                baseURL: baseURL,
                apiKey: apiKey,
                isOpenCode: isOpenCode,
                openCodeSession: openCodeSession
            )
        case .gemini:
            return await geminiCount(
                messages: messages,
                model: model,
                baseURL: baseURL,
                apiKey: apiKey
            )
        case .ollama:
            return await ollamaCount(messages: messages, model: model, baseURL: baseURL)
        }
    }

    private static func anthropicCount(
        messages: [ChatMessage],
        model: String,
        baseURL: String,
        apiKey: String,
        isOpenCode: Bool,
        openCodeSession: String?
    ) async -> ProviderTokenCount? {
        guard !apiKey.isEmpty else { return nil }
        var root = baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        if root.hasSuffix("/v1") { root.removeLast(3) }
        guard let url = URL(string: root + "/v1/messages/count_tokens") else { return nil }

        let system = messages
            .filter { $0.role == .system }
            .map(\.text)
            .joined(separator: "\n\n")
        let turns = mergedTurns(messages.filter { $0.role != .system })
        guard !turns.isEmpty else { return nil }

        var payload: [String: Any] = [
            "model": model,
            "messages": turns.map { ["role": $0.role, "content": $0.text] },
        ]
        if !system.isEmpty { payload["system"] = system }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 20
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(apiKey, forHTTPHeaderField: "x-api-key")
        request.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")
        if isOpenCode {
            request.setValue("opencode/2.4.0", forHTTPHeaderField: "User-Agent")
            request.setValue("https://opencode.ai", forHTTPHeaderField: "Origin")
            request.setValue("https://opencode.ai/", forHTTPHeaderField: "Referer")
            request.setValue(openCodeSession ?? UUID().uuidString, forHTTPHeaderField: "x-opencode-session")
        }
        request.httpBody = try? JSONSerialization.data(withJSONObject: payload)

        guard let (data, response) = try? await URLSession.shared.data(for: request),
              let http = response as? HTTPURLResponse,
              http.statusCode == 200,
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let count = object["input_tokens"] as? Int else {
            return nil
        }
        return ProviderTokenCount(count: count, method: TokenCounterProvider.anthropic.method)
    }

    private static func geminiCount(
        messages: [ChatMessage],
        model: String,
        baseURL: String,
        apiKey: String
    ) async -> ProviderTokenCount? {
        guard !apiKey.isEmpty else { return nil }
        var root = baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        if root.hasSuffix("/openai") { root.removeLast(7) }
        if !root.contains("/v1beta") && !root.contains("/v1") {
            root += "/v1beta"
        }
        let cleanModel = model.hasPrefix("models/") ? String(model.dropFirst(7)) : model
        guard let url = URL(string: root + "/models/" + cleanModel + ":countTokens") else {
            return nil
        }

        let system = messages
            .filter { $0.role == .system }
            .map(\.text)
            .joined(separator: "\n\n")
        let turns = mergedTurns(messages.filter { $0.role != .system })
        guard !turns.isEmpty else { return nil }

        var payload: [String: Any] = [
            "contents": turns.map { turn in
                [
                    "role": turn.role == "assistant" ? "model" : "user",
                    "parts": [["text": turn.text]],
                ]
            },
        ]
        if !system.isEmpty {
            payload["systemInstruction"] = ["parts": [["text": system]]]
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 20
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(apiKey, forHTTPHeaderField: "x-goog-api-key")
        request.httpBody = try? JSONSerialization.data(withJSONObject: payload)

        guard let (data, response) = try? await URLSession.shared.data(for: request),
              let http = response as? HTTPURLResponse,
              http.statusCode == 200,
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let count = object["totalTokens"] as? Int else {
            return nil
        }
        return ProviderTokenCount(count: count, method: TokenCounterProvider.gemini.method)
    }

    private static func ollamaCount(
        messages: [ChatMessage],
        model: String,
        baseURL: String
    ) async -> ProviderTokenCount? {
        var root = baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard let url = URL(string: root + "/api/chat") else { return nil }
        let payloadMessages = messages.map { message -> [String: Any] in
            [
                "role": message.role.chatRole,
                "content": message.text,
            ]
        }
        let payload: [String: Any] = [
            "model": model,
            "stream": false,
            "messages": payloadMessages,
            "options": ["num_predict": 1],
        ]
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 120
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: payload)

        guard let (data, response) = try? await URLSession.shared.data(for: request),
              let http = response as? HTTPURLResponse,
              http.statusCode == 200,
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let count = object["prompt_eval_count"] as? Int else {
            return nil
        }
        return ProviderTokenCount(count: count, method: TokenCounterProvider.ollama.method)
    }

    private static func mergedTurns(_ messages: [ChatMessage]) -> [(role: String, text: String)] {
        var turns: [(role: String, text: String)] = []
        for message in messages {
            let text = message.text.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty else { continue }
            let role = message.role == .assistant ? "assistant" : "user"
            if let last = turns.last, last.role == role {
                turns[turns.count - 1].text += "\n\n" + text
            } else {
                turns.append((role: role, text: text))
            }
        }
        return turns
    }
}
