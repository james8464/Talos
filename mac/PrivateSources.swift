import AppKit
import SwiftUI

@MainActor
final class PrivateSourceController: ObservableObject {
    var client: RoomClient?
    @Published var kind = "email"
    @Published var owner = ""
    @Published var selector = ""
    @Published var topic = ""
    @Published var person = ""
    @Published var status = ""
    @Published var speak = false
    @Published var sources: [Entry] = []
    @Published var availableApps: [String] = []
    @Published var enabledApps: Set<String> = []
    @Published var savingApps = false
    @Published var brief = ""
    @Published var activity = ""
    @Published var links: [Evidence] = []
    struct Question: Decodable {
        struct Choice: Decodable { let id: [String]; let text: String }
        let revision: [String]
        let options: [Choice]
    }
    @Published var questions: [Question] = []
    struct Entry: Decodable, Identifiable {
        let id: Int
        let path, topic, person, error: String
        let checked: Double
        let speak: Int
    }
    struct Evidence: Decodable, Hashable { let title: String; let url: URL }
    func setApps(_ apps: Set<String>) async throws {
        guard !savingApps, let client else { return }
        savingApps = true
        defer { savingApps = false }
        let result = try await client.request("context/apps", body: ["apps": apps.sorted()])
        guard let saved = result["enabled_apps"] as? [String] else { throw RoomError("Couldn't confirm the app settings.") }
        enabledApps = Set(saved)
    }

    func load() async throws {
        let data = try await client?.request("context/sources") ?? [:]
        availableApps = data["available_apps"] as? [String] ?? []
        enabledApps = Set(data["enabled_apps"] as? [String] ?? [])
        sources = try JSONDecoder().decode([Entry].self, from: JSONSerialization.data(withJSONObject: data["sources"] ?? []))
        let result = try await client?.request("context/brief") ?? [:]
        brief = result["reply"] as? String ?? ""
        questions = try JSONDecoder().decode([Question].self, from: JSONSerialization.data(withJSONObject: result["questions"] ?? []))
        links = try JSONDecoder().decode([Evidence].self, from: JSONSerialization.data(withJSONObject: result["sources"] ?? []))
    }
}

struct PrivateSources: View {
    @ObservedObject var model: PrivateSourceController
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack { Text("Private sources and commitments").font(.title2); Spacer(); Button("Done") { dismiss() } }
            if !model.activity.isEmpty { Text(model.activity).font(.caption).textSelection(.enabled) }
            if !model.availableApps.isEmpty {
                GroupBox("Search from the private message field") {
                    HStack {
                        ForEach(model.availableApps, id: \.self) { app in
                            Toggle(app, isOn: Binding(get: { model.enabledApps.contains(app) }, set: { enabled in
                                let selected = enabled ? model.enabledApps.union([app]) : model.enabledApps.subtracting([app])
                                Task {
                                    do { try await model.setApps(selected) } catch { model.status = error.localizedDescription }
                                }
                            }))
                        }
                    }.toggleStyle(.checkbox).disabled(model.savingApps)
                }
            }
            Picker("Source", selection: $model.kind) {
                ForEach(["email","imessage","whatsapp","document","calendar"], id: \.self) { Text($0.capitalized) }
            }
            TextField("Your sender name or email in the export", text: $model.owner)
            TextField("Contact", text: $model.person)
            TextField("Chat identifier or sender alias (optional)", text: $model.selector)
            TextField("Project or topic", text: $model.topic)
            Toggle("Allow speaking commitments from this source", isOn: $model.speak)
            Button(model.kind == "document" ? "Choose project file or folder…" : "Choose file to remember…") {
                let panel = NSOpenPanel()
                panel.canChooseDirectories = model.kind == "document"
                guard panel.runModal() == .OK, let file = panel.url else { return }
                Task {
                    do {
                        _ = try await model.client?.request("context/select", body: ["kind":model.kind,"path":file.path,"owner":model.owner,
                            "person":model.person,"topic":model.topic,"speak":model.speak,"selector":model.selector])
                        try await model.load()
                    } catch { model.status = error.localizedDescription }
                }
            }.disabled(model.owner.isEmpty || model.person.isEmpty || model.topic.isEmpty)
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(model.sources) { item in
                        HStack {
                            VStack(alignment: .leading) {
                                Text(URL(fileURLWithPath: item.path).lastPathComponent)
                                Text(item.topic + " · " + item.person + (item.speak == 1 ? " · voice enabled" : " · private"))
                                Text(item.error.isEmpty ? (item.checked > 0 ? "Checked " + Date(timeIntervalSince1970:item.checked).formatted() : "Awaiting first check") : item.error)
                            }.font(.caption)
                            Spacer()
                            Button("Forget") {
                                Task {
                                    do { _ = try await model.client?.request("context/forget",body:["id":item.id]); try await model.load() }
                                    catch { model.status = error.localizedDescription }
                                }
                            }
                        }
                    }
                    Text(model.brief).textSelection(.enabled)
                    if let question = model.questions.first {
                        ForEach(question.options, id: \.id) { choice in
                            Button(choice.text) {
                                Task {
                                    do {
                                        _ = try await model.client?.request("context/resolve",body:["revision":question.revision,"promise":choice.id])
                                        try await model.load()
                                    } catch { model.status = error.localizedDescription }
                                }
                            }
                        }
                    }
                    ForEach(Array(model.links.enumerated()),id: \.offset) { _, link in
                        Button(link.title) { NSWorkspace.shared.activateFileViewerSelecting([link.url]) }
                    }
                }
            }
            HStack { Button("Refresh briefing") { Task { do { try await model.load() } catch { model.status = error.localizedDescription } } }; Text(model.status) }
        }.padding(20).frame(minWidth: 480,minHeight: 520)
        .task {
            while !Task.isCancelled {
                do {
                    let result = try await model.client?.request("context/activity")
                    model.activity = result?["report"] as? String ?? ""
                    try await Task.sleep(nanoseconds: 30_000_000_000)
                } catch { break }
            }
        }
        .task { do { try await model.load() } catch { model.status = error.localizedDescription } }
    }
}
