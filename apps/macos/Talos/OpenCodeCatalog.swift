import Foundation

struct OpenCodeModel: Identifiable, Equatable, Sendable {
    let id: String
    let name: String
    let recommendedAPI: String?
    let inputModalities: [String]
    let reasoningEfforts: [String]
    let contextLimit: Int?
    let chatBaseURL: String?
    let responsesBaseURL: String?
    let messagesBaseURL: String?

    var supportsVision: Bool {
        inputModalities.contains { $0.hasPrefix("image/") }
    }

    var supportsChatCompletions: Bool {
        recommendedAPI == "chat-completions" && chatBaseURL != nil
    }

    var isRouted: Bool {
        switch recommendedAPI {
        case "chat-completions": return chatBaseURL != nil
        case "responses": return responsesBaseURL != nil
        case "messages": return messagesBaseURL != nil
        default: return false
        }
    }
}

enum OpenCodeCatalog {
    static func fetch(token: String) async throws -> [OpenCodeModel] {
        guard let url = URL(string: "https://opencode.ai/zen/go/v1/models") else {
            throw OpenCodeAuthError.message("The OpenCode model URL is invalid.")
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = 20
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("opencode/2.4.0", forHTTPHeaderField: "User-Agent")
        request.setValue("https://opencode.ai", forHTTPHeaderField: "Origin")
        request.setValue("https://opencode.ai/", forHTTPHeaderField: "Referer")
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw OpenCodeAuthError.message("OpenCode Go model discovery failed.")
        }
        guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let rows = root["data"] as? [[String: Any]] else {
            throw OpenCodeAuthError.message("OpenCode Go returned an unreadable model list.")
        }
        return rows.compactMap { row in
            guard let id = row["id"] as? String, row["type"] as? String == "chat" else { return nil }
            let name = row["name"] as? String ?? id
            let recommended = row["recommended_api"] as? String
            let modalities = row["modalities"] as? [String: Any]
            let inputs = modalities?["input"] as? [String] ?? []
            let capabilities = row["capabilities"] as? [String: Any]
            let reasoning = capabilities?["reasoning"] as? [String: Any]
            let efforts = reasoning?["effort"] as? [String] ?? []
            let limit = row["limit"] as? [String: Any]
            let context = limit?["context"] as? Int
            let api = row["api"] as? [String: Any]
            let chat = api?["chat-completions"] as? [String: Any]
            let responses = api?["responses"] as? [String: Any]
            let messages = api?["messages"] as? [String: Any]
            return OpenCodeModel(
                id: id,
                name: name,
                recommendedAPI: recommended,
                inputModalities: inputs,
                reasoningEfforts: efforts,
                contextLimit: context,
                chatBaseURL: chat?["base_url"] as? String,
                responsesBaseURL: responses?["base_url"] as? String,
                messagesBaseURL: messages?["base_url"] as? String
            )
        }
    }
}
