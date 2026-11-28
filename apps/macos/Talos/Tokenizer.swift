import Foundation

enum TokenizerFamily: String, Sendable {
    case cl100k
    case o200k

    var title: String {
        switch self {
        case .cl100k: return "cl100k BPE"
        case .o200k: return "o200k BPE"
        }
    }

    var pattern: String {
        switch self {
        case .cl100k:
            return #"'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}{1,3}| ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+"#
        case .o200k:
            return [
                #"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?"#,
                #"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?"#,
                #"\p{N}{1,3}"#,
                #" ?[^\s\p{L}\p{N}]+[\r\n/]*"#,
                #"\s*[\r\n]+"#,
                #"\s+(?!\S)"#,
                #"\s+"#,
            ].joined(separator: "|")
        }
    }

    var specialTokens: Set<String> {
        switch self {
        case .cl100k:
            return ["<|endoftext|>", "<|fim_prefix|>", "<|fim_middle|>", "<|fim_suffix|>", "<|endofprompt|>"]
        case .o200k:
            return ["<|endoftext|>", "<|endofprompt|>"]
        }
    }
}

final class BPEVocabulary: @unchecked Sendable {
    let ranks: [Data: Int]

    init?(name: String) {
        guard let directory = TokenizerRegistry.resourceDirectory else { return nil }
        let url = directory.appendingPathComponent("\(name).tiktoken")
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return nil }
        var ranks: [Data: Int] = [:]
        ranks.reserveCapacity(200_000)
        for line in text.split(separator: "\n") {
            let parts = line.split(separator: " ")
            guard parts.count >= 2,
                  let data = Data(base64Encoded: String(parts[0])),
                  let rank = Int(parts[1]) else { continue }
            ranks[data] = rank
        }
        guard !ranks.isEmpty else { return nil }
        self.ranks = ranks
    }
}

enum TokenizerRegistry {
    static let cl100k = BPEVocabulary(name: "cl100k_base")
    static let o200k = BPEVocabulary(name: "o200k_base")

    static var resourceDirectory: URL? {
        if let override = ProcessInfo.processInfo.environment["TALOS_TOKENIZER_DIR"],
           !override.isEmpty {
            return URL(fileURLWithPath: override, isDirectory: true)
        }
        return Bundle.main.url(forResource: "o200k_base", withExtension: "tiktoken")?
            .deletingLastPathComponent()
    }

    static func vocabulary(for family: TokenizerFamily) -> BPEVocabulary? {
        switch family {
        case .cl100k: return cl100k
        case .o200k: return o200k
        }
    }
}

private struct TokenPair: Comparable {
    let rank: Int
    let left: Int
    let right: Int
    let leftGeneration: Int
    let rightGeneration: Int

    static func < (lhs: TokenPair, rhs: TokenPair) -> Bool {
        if lhs.rank != rhs.rank { return lhs.rank < rhs.rank }
        return lhs.left < rhs.left
    }
}

private struct TokenPairHeap {
    private var items: [TokenPair] = []

    mutating func push(_ item: TokenPair) {
        items.append(item)
        var index = items.count - 1
        while index > 0 {
            let parent = (index - 1) / 2
            if items[parent] <= items[index] { break }
            items.swapAt(parent, index)
            index = parent
        }
    }

    mutating func pop() -> TokenPair? {
        guard !items.isEmpty else { return nil }
        if items.count == 1 { return items.removeLast() }
        let value = items[0]
        items[0] = items.removeLast()
        var index = 0
        while true {
            let left = index * 2 + 1
            let right = left + 1
            var smallest = index
            if left < items.count, items[left] < items[smallest] { smallest = left }
            if right < items.count, items[right] < items[smallest] { smallest = right }
            if smallest == index { break }
            items.swapAt(index, smallest)
            index = smallest
        }
        return value
    }
}

private final class TokenCountCache: @unchecked Sendable {
    private let cache = NSCache<NSString, NSNumber>()

    func value(_ key: String) -> Int? {
        cache.object(forKey: key as NSString)?.intValue
    }

    func store(_ value: Int, for key: String) {
        cache.setObject(NSNumber(value: value), forKey: key as NSString)
    }
}

enum TokenCounter {
    private static let cache = TokenCountCache()

    static func family(provider: String, model: String) -> TokenizerFamily? {
        let provider = provider.lowercased()
        let model = model.lowercased()
        guard provider == "openai" || provider == "opencode" else { return nil }
        if model.contains("gpt-4o")
            || model.contains("gpt-4.1")
            || model.contains("gpt-5")
            || model.contains("gpt-6")
            || model.hasPrefix("o1")
            || model.hasPrefix("o3")
            || model.hasPrefix("o4")
            || model.contains("chatgpt-4o") {
            return .o200k
        }
        if model.contains("gpt-4")
            || model.contains("gpt-3.5")
            || model.contains("text-embedding-3")
            || model.contains("davinci") {
            return .cl100k
        }
        return nil
    }

    static func count(
        _ text: String,
        provider: String,
        model: String
    ) -> Int? {
        guard let family = family(provider: provider, model: model),
              let vocabulary = TokenizerRegistry.vocabulary(for: family) else {
            return nil
        }
        return count(text, family: family, vocabulary: vocabulary)
    }

    static func count(
        _ texts: [String],
        provider: String,
        model: String
    ) -> Int? {
        guard let family = family(provider: provider, model: model),
              let vocabulary = TokenizerRegistry.vocabulary(for: family) else {
            return nil
        }
        return texts.reduce(0) { total, text in
            total + count(text, family: family, vocabulary: vocabulary)
        }
    }

    static func label(provider: String, model: String) -> String {
        family(provider: provider, model: model)?.title ?? "Estimate"
    }

    static func isExact(provider: String, model: String) -> Bool {
        family(provider: provider, model: model) != nil
    }

    static func count(
        _ text: String,
        family: TokenizerFamily,
        vocabulary: BPEVocabulary
    ) -> Int {
        guard !text.isEmpty else { return 0 }
        let cacheKey = "\(family.rawValue)|\(text)"
        if let cached = cache.value(cacheKey) { return cached }
        let total = encodeSpecialAware(text, family: family, vocabulary: vocabulary)
        cache.store(total, for: cacheKey)
        return total
    }

    private static func encodeSpecialAware(
        _ text: String,
        family: TokenizerFamily,
        vocabulary: BPEVocabulary
    ) -> Int {
        guard let regex = try? NSRegularExpression(pattern: #"<\|[A-Za-z0-9_]+\|>"#) else {
            return encodePlain(text, family: family, vocabulary: vocabulary)
        }
        let nsText = text as NSString
        var total = 0
        var cursor = 0
        for match in regex.matches(in: text, range: NSRange(location: 0, length: nsText.length)) {
            if match.range.location > cursor {
                let piece = nsText.substring(with: NSRange(location: cursor, length: match.range.location - cursor))
                total += encodePlain(piece, family: family, vocabulary: vocabulary)
            }
            let token = nsText.substring(with: match.range)
            total += family.specialTokens.contains(token)
                ? 1
                : encodePlain(token, family: family, vocabulary: vocabulary)
            cursor = match.range.location + match.range.length
        }
        if cursor < nsText.length {
            total += encodePlain(nsText.substring(from: cursor), family: family, vocabulary: vocabulary)
        }
        return total
    }

    private static func encodePlain(
        _ text: String,
        family: TokenizerFamily,
        vocabulary: BPEVocabulary
    ) -> Int {
        guard !text.isEmpty else { return 0 }
        guard let regex = try? NSRegularExpression(pattern: family.pattern) else {
            return text.utf8.count
        }
        let nsText = text as NSString
        var total = 0
        for match in regex.matches(in: text, range: NSRange(location: 0, length: nsText.length)) {
            let piece = nsText.substring(with: match.range)
            total += encodePiece(Data(piece.utf8), ranks: vocabulary.ranks).count
        }
        return total
    }

    private static func encodePiece(_ data: Data, ranks: [Data: Int]) -> [Int] {
        guard !data.isEmpty else { return [] }
        if data.count == 1 {
            return [ranks[data] ?? 0]
        }
        let bytes = [UInt8](data)
        var parts = bytes.map { Data([$0]) }
        var previous = Array<Int?>(repeating: nil, count: parts.count)
        var next = Array<Int?>(repeating: nil, count: parts.count)
        var alive = Array(repeating: true, count: parts.count)
        var generation = Array(repeating: 0, count: parts.count)
        for index in 0..<parts.count {
            previous[index] = index > 0 ? index - 1 : nil
            next[index] = index + 1 < parts.count ? index + 1 : nil
        }

        var heap = TokenPairHeap()
        if parts.count > 1 {
            for index in 0..<(parts.count - 1) {
                guard let rank = ranks[parts[index] + parts[index + 1]] else { continue }
                heap.push(TokenPair(
                    rank: rank,
                    left: index,
                    right: index + 1,
                    leftGeneration: generation[index],
                    rightGeneration: generation[index + 1]
                ))
            }
        }

        while let pair = heap.pop() {
            let left = pair.left
            let right = pair.right
            guard alive[left], alive[right],
                  next[left] == right,
                  generation[left] == pair.leftGeneration,
                  generation[right] == pair.rightGeneration else {
                continue
            }
            parts[left].append(parts[right])
            alive[right] = false
            next[left] = next[right]
            if let after = next[left] {
                previous[after] = left
            }
            generation[left] += 1

            if let before = previous[left], alive[before],
               let rank = ranks[parts[before] + parts[left]] {
                heap.push(TokenPair(
                    rank: rank,
                    left: before,
                    right: left,
                    leftGeneration: generation[before],
                    rightGeneration: generation[left]
                ))
            }
            if let after = next[left], alive[after],
               let rank = ranks[parts[left] + parts[after]] {
                heap.push(TokenPair(
                    rank: rank,
                    left: left,
                    right: after,
                    leftGeneration: generation[left],
                    rightGeneration: generation[after]
                ))
            }
        }

        return (0..<parts.count).compactMap { index in
            alive[index] ? (ranks[parts[index]] ?? 0) : nil
        }
    }
}
