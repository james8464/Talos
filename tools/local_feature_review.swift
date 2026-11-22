import Foundation
import FoundationModels

if #available(macOS 26, *), SystemLanguageModel.default.availability == .available {
    let input = FileHandle.standardInput.readDataToEndOfFile()
    guard let prompt = String(data: input, encoding: .utf8) else { exit(1) }
    do {
        let session = LanguageModelSession(instructions: "Review source code for a personal desktop assistant. Return a single JSON object with feature and acceptance strings.")
        let response = try await session.respond(to: prompt)
        print(response.content)
    } catch {
        exit(1)
    }
} else {
    exit(1)
}
