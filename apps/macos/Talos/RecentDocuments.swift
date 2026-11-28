import Foundation
import AppKit

struct RecentDocument: Codable, Identifiable, Hashable {
    var path: String
    var lastOpened: Date

    var id: String { path }
    var url: URL { URL(fileURLWithPath: path) }
    var name: String { url.lastPathComponent }
    var exists: Bool { FileManager.default.fileExists(atPath: path) }
}

@MainActor
final class RecentDocumentsStore: ObservableObject {
    @Published private(set) var items: [RecentDocument] = []

    private let defaults = UserDefaults.standard
    private let key = "recentDocuments"
    private let limit = 20

    init() {
        load()
    }

    func record(_ url: URL) {
        let path = url.standardizedFileURL.resolvingSymlinksInPath().path
        guard FileManager.default.fileExists(atPath: path) else { return }
        items.removeAll { $0.path == path }
        items.insert(RecentDocument(path: path, lastOpened: Date()), at: 0)
        if items.count > limit {
            items.removeLast(items.count - limit)
        }
        save()
    }

    func remove(_ document: RecentDocument) {
        items.removeAll { $0.path == document.path }
        save()
    }

    func clear() {
        items = []
        save()
    }

    private func load() {
        guard let data = defaults.data(forKey: key),
              let decoded = try? JSONDecoder().decode([RecentDocument].self, from: data) else {
            return
        }
        items = Array(decoded.prefix(limit))
    }

    private func save() {
        guard let data = try? JSONEncoder().encode(items) else { return }
        defaults.set(data, forKey: key)
    }
}
