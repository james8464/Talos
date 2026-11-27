import Foundation

enum OpenCodeAuth {
    static func readKey() throws -> String {
        if let token = exportToken(), !token.isEmpty {
            return token
        }
        return try readAuthFile()
    }

    private static func exportToken() -> String? {
        let candidates = [
            "/opt/homebrew/bin/opencode",
            "/usr/local/bin/opencode",
            "/usr/bin/opencode",
        ]
        for executable in candidates where FileManager.default.isExecutableFile(atPath: executable) {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: executable)
            process.arguments = ["auth", "export"]
            let output = Pipe()
            process.standardOutput = output
            process.standardError = FileHandle.nullDevice
            do {
                try process.run()
            } catch {
                continue
            }
            let data = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            guard process.terminationStatus == 0,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else {
                continue
            }
            for entry in json {
                guard let value = entry["value"] as? [String: Any] else { continue }
                if let access = value["access"] as? String, !access.isEmpty {
                    return access
                }
            }
        }
        return nil
    }

    private static func readAuthFile() throws -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let candidates = [
            home.appendingPathComponent(".local/share/opencode/auth.json"),
            home.appendingPathComponent(".config/opencode/auth.json"),
        ]
        for url in candidates {
            guard let data = try? Data(contentsOf: url),
                  let entries = try? JSONDecoder().decode([String: Entry].self, from: data) else {
                continue
            }
            for provider in ["opencode-go", "opencode", "opencode-zen"] {
                if let value = entries[provider]?.credential {
                    return value
                }
            }
        }
        throw OpenCodeAuthError.message("No OpenCode credential found. Sign in with OpenCode or paste a key from opencode.ai/auth.")
    }

    private struct Entry: Decodable {
        let type: String?
        let key: String?
        let access: String?
        let accessToken: String?

        enum CodingKeys: String, CodingKey {
            case type, key, access
            case accessToken = "access_token"
        }

        var credential: String? {
            [key, accessToken, access]
                .compactMap { $0 }
                .first { !$0.isEmpty }
        }
    }
}

enum OpenCodeAuthError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let value): return value
        }
    }
}
