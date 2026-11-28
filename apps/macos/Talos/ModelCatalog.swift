import Foundation

enum ModelCatalog {
    static func capabilities(
        for choice: ModelChoice,
        ollama: OllamaModelInfo?,
        openCode: OpenCodeModel? = nil
    ) -> ModelCapabilities {
        switch choice.provider {
        case .local:
            return local(choice: choice, info: ollama)
        case .openCode:
            let options = (openCode?.reasoningEfforts ?? []).map { effort in
                ReasoningOption(
                    id: effort,
                    title: effort.capitalized,
                    detail: "OpenCode Go reasoning effort."
                )
            }
            let defaultEffort = options.contains(where: { $0.id == "medium" })
                ? "medium"
                : options.first?.id
            return ModelCapabilities(
                reasoningOptions: options,
                defaultReasoning: defaultEffort,
                contextWindow: openCode?.contextLimit,
                supportsVision: openCode?.supportsVision ?? openCodeSupportsVision(choice.id),
                summary: openCode.map {
                    "OpenCode Go · \($0.name)" + ($0.supportsVision ? " · vision" : "")
                } ?? "OpenCode Go model metadata is loading."
            )
        case .openAI:
            return openAI(choice: choice)
        case .anthropic:
            return anthropic(choice: choice)
        case .gemini:
            let id = choice.id.lowercased()
            let context = id.contains("gemini-2.5") ? 1_048_576 : (id.contains("gemini-1.5") ? 1_000_000 : nil)
            return ModelCapabilities(
                reasoningOptions: [],
                defaultReasoning: nil,
                contextWindow: context,
                supportsVision: id.contains("gemini"),
                summary: "Gemini's OpenAI-compatible endpoint does not expose a reasoning control in this client."
            )
        }
    }

    private static func local(choice: ModelChoice, info: OllamaModelInfo?) -> ModelCapabilities {
        guard let info else {
            return ModelCapabilities(
                reasoningOptions: [],
                defaultReasoning: nil,
                contextWindow: nil,
                summary: "Capabilities could not be read from Ollama for this model."
            )
        }

        let thinking = info.capabilities.contains("thinking")
        let tools = info.capabilities.contains("tools")
        let vision = info.capabilities.contains("vision")
        let options: [ReasoningOption] = thinking
            ? [
                ReasoningOption(id: "off", title: "Off", detail: "Answer without a thinking pass."),
                ReasoningOption(id: "on", title: "On", detail: "Use the model's thinking mode before answering."),
            ]
            : []

        var parts: [String] = []
        if thinking { parts.append("thinking") }
        if tools { parts.append("tool-aware") }
        if vision { parts.append("vision") }
        if let context = info.contextLength {
            parts.append("\(context.formatted()) context")
        }
        let summary = parts.isEmpty
            ? "This local model does not advertise thinking or tools."
            : parts.joined(separator: " · ")

        return ModelCapabilities(
            reasoningOptions: options,
            defaultReasoning: thinking ? "off" : nil,
            contextWindow: info.contextLength,
            supportsVision: vision,
            summary: summary
        )
    }

    private static func openAI(choice: ModelChoice) -> ModelCapabilities {
        let id = choice.id.lowercased()
        let supportsReasoning = id.hasPrefix("gpt-5")
            || id.hasPrefix("gpt-6")
            || id.hasPrefix("o1")
            || id.hasPrefix("o3")
            || id.hasPrefix("o4")
        guard supportsReasoning else {
            return ModelCapabilities(
                reasoningOptions: [],
                defaultReasoning: nil,
                contextWindow: openAIContextWindow(id),
                supportsVision: openAISupportsVision(id),
                summary: "This OpenAI model does not expose a reasoning control through Chat Completions."
            )
        }
        return ModelCapabilities(
            reasoningOptions: [
                ReasoningOption(id: "low", title: "Low", detail: "Quick reasoning for well-scoped work."),
                ReasoningOption(id: "medium", title: "Medium", detail: "Balanced reasoning for most tasks."),
                ReasoningOption(id: "high", title: "High", detail: "More reasoning for difficult work."),
            ],
            defaultReasoning: "medium",
            contextWindow: openAIContextWindow(id),
            supportsVision: openAISupportsVision(id),
            summary: "OpenAI reasoning effort is available for this model."
        )
    }

    private static func anthropic(choice: ModelChoice) -> ModelCapabilities {
        let id = choice.id.lowercased()
        let supportsThinking = id.contains("claude-sonnet-4")
            || id.contains("claude-opus-4")
            || id.contains("claude-3-7")
        guard supportsThinking else {
            return ModelCapabilities(
                reasoningOptions: [],
                defaultReasoning: nil,
                contextWindow: 200_000,
                supportsVision: true,
                summary: "This Claude model does not expose extended thinking."
            )
        }
        return ModelCapabilities(
            reasoningOptions: [
                ReasoningOption(id: "off", title: "Off", detail: "Answer without extended thinking."),
                ReasoningOption(id: "1024", title: "1K tokens", detail: "A small thinking budget."),
                ReasoningOption(id: "2048", title: "2K tokens", detail: "A moderate thinking budget."),
                ReasoningOption(id: "4096", title: "4K tokens", detail: "A larger thinking budget for difficult work."),
            ],
            defaultReasoning: "off",
            contextWindow: 200_000,
            supportsVision: true,
            summary: "Anthropic extended thinking uses a token budget."
        )
    }

    private static func openAIContextWindow(_ id: String) -> Int? {
        if id.hasPrefix("gpt-5") || id.hasPrefix("gpt-6") { return 400_000 }
        if id.hasPrefix("gpt-4.1") { return 1_047_576 }
        if id.hasPrefix("gpt-4o") { return 128_000 }
        if id.hasPrefix("o1") || id.hasPrefix("o3") || id.hasPrefix("o4") { return 200_000 }
        return nil
    }

    private static func openAISupportsVision(_ id: String) -> Bool {
        id.hasPrefix("gpt-4o")
            || id.hasPrefix("gpt-4.1")
            || id.hasPrefix("gpt-5")
            || id.hasPrefix("gpt-6")
    }

    private static func openCodeSupportsVision(_ id: String) -> Bool {
        let value = id.lowercased()
        return value.contains("vision")
            || value.contains("omni")
            || value.contains("gemini")
            || value.contains("gpt-5")
            || value.contains("gpt-6")
            || value.contains("claude")
            || value.contains("grok")
    }
}
