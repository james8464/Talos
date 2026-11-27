import Foundation

enum SessionPersistence {
    struct Snapshot: Codable {
        let sessions: [Session]
        let selectedID: UUID?
    }

    private static var directory: URL {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        return support.appendingPathComponent("Talos", isDirectory: true)
    }

    private static var fileURL: URL {
        directory.appendingPathComponent("sessions.json")
    }

    static func load() -> Snapshot? {
        guard let data = try? Data(contentsOf: fileURL) else { return nil }
        return try? JSONDecoder().decode(Snapshot.self, from: data)
    }

    static func save(sessions: [Session], selectedID: UUID?) {
        let snapshot = Snapshot(sessions: sessions, selectedID: selectedID)
        guard let data = try? JSONEncoder().encode(snapshot) else { return }
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try data.write(to: fileURL, options: .atomic)
        } catch {
            // History is best-effort; the conversation in memory remains authoritative.
        }
    }
}
