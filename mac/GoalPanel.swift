import AppKit
import SwiftUI

struct GoalRecord: Decodable, Identifiable {
    struct Document: Decodable { let path, excerpt, updated: String }
    struct Evidence: Decodable { let state: String; let files: [Document] }
    struct Item: Decodable { let id: Int; let kind, text: String }
    struct Quote: Decodable { let id: Int; let text: String }
    struct Observation: Decodable { let evidence: Evidence; let items: [Item]; let excerpts: [Quote]; let risks: [String] }
    struct Change: Decodable { let at, reason: String }
    let id, status, outcome, topic, required_doc, folder, deadline, preference, event_id, calendar_state, checked: String
    let revision, lead: Int
    let observation: Observation
    let history: [Change]
}

struct GoalForm {
    var id = UUID().uuidString.lowercased()
    var revision = 0
    var outcome = "", topic = "", document = "", folder = "", eventID = ""
    var date = Date().addingTimeInterval(86400)
    var lead = 15
    var preference = "risk"

    init() {}
    init(_ g: GoalRecord) {
        id = g.id; revision = g.revision; outcome = g.outcome; topic = g.topic
        document = g.required_doc; folder = g.folder; eventID = g.event_id
        date = Self.date(g.deadline) ?? Date()
        lead = g.lead; preference = g.preference
    }
    static func date(_ stamp: String) -> Date? {
        let parser = ISO8601DateFormatter()
        parser.formatOptions.insert(.withFractionalSeconds)
        return parser.date(from: stamp) ?? ISO8601DateFormatter().date(from: stamp)
    }
    var body: [String: Any] {
        ["id": id, "revision": revision, "outcome": outcome, "topic": topic, "required_doc": document,
         "folder": folder, "event_id": eventID, "deadline": ISO8601DateFormatter().string(from: date),
         "lead": lead, "preference": preference]
    }
}

@MainActor
final class GoalController: ObservableObject {
    @Published var records: [GoalRecord] = []
    @Published var form = GoalForm()
    @Published var message = ""
    @Published var showing = false
    @Published var working = false
    var client: RoomClient?
    private var lastCalendarRead = Date.distantPast
    private var refreshing = false

    func reload() async throws {
        guard let client else { throw RoomError("The local service is unavailable.") }
        let response = try await client.request("goals")
        let data = try JSONSerialization.data(withJSONObject: response["goals"] ?? [])
        records = try JSONDecoder().decode([GoalRecord].self, from: data)
    }

    func open() {
        showing = true
        Task { do { try await reload() } catch { message = error.localizedDescription } }
    }

    func prepare(_ meeting: ScheduledMeeting, outcome: String) {
        var draft = GoalForm()
        if let existing = records.first(where: { !$0.event_id.isEmpty && $0.event_id == meeting.binding }) {
            draft = GoalForm(existing)
        } else {
            draft.outcome = outcome; draft.topic = meeting.title
            draft.eventID = meeting.binding; draft.date = meeting.start
        }
        form = draft; open()
    }

    func save() {
        let body = form.body
        perform { client in _ = try await client.request("goals/save", body: body) }
    }
    func close(_ status: String) {
        let id = form.id
        perform { client in _ = try await client.request("goals/close", body: ["id": id, "status": status]) }
    }
    func setPaused(_ paused: Bool) {
        let id = form.id
        perform { client in _ = try await client.request(paused ? "goals/pause" : "goals/resume", body: ["id": id]) }
    }
    func check() {
        let id = form.id
        perform { client in _ = try await client.request("goals/check", body: ["id": id]) }
    }
    private func perform(_ action: @escaping (RoomClient) async throws -> Void) {
        guard let client, !working else { return }
        working = true
        Task {
            defer { working = false }
            do {
                try await action(client); try await reload()
                form = records.first(where: { $0.id == form.id }).map(GoalForm.init) ?? GoalForm()
                message = ""
            } catch { message = error.localizedDescription }
        }
    }

    func reconcile(_ calendar: MeetingCalendar) async {
        guard let client, !refreshing, Date().timeIntervalSince(lastCalendarRead) >= 30 else { return }
        refreshing = true; lastCalendarRead = Date()
        defer { refreshing = false }
        do {
            try await reload()
            for goal in records where goal.status == "active" && !goal.event_id.isEmpty {
                let update = calendar.observation(binding: goal.event_id, deadline: GoalForm.date(goal.deadline))
                if update.state != goal.calendar_state || (update.start != nil && update.start != GoalForm.date(goal.deadline)) {
                    var body: [String: Any] = ["id": goal.id, "revision": goal.revision, "event_id": goal.event_id, "state": update.state]
                    if let start = update.start { body["deadline"] = ISO8601DateFormatter().string(from: start) }
                    _ = try await client.request("goals/calendar", body: body)
                }
            }
        } catch { if showing { message = error.localizedDescription } }
    }
}

struct GoalPanel: View {
    @ObservedObject var goals: GoalController
    @Environment(\.dismiss) var dismiss
    var body: some View {
        VStack(alignment: .leading) {
            HStack { Text("Goals").font(.title); Spacer(); Button("Done") { dismiss() } }
            HStack(alignment: .top) {
                VStack(alignment: .leading) {
                    Button("New goal") { goals.form = GoalForm() }
                    ForEach(goals.records) { g in Button(g.outcome) { goals.form = GoalForm(g) } }
                }.frame(width: 175, alignment: .leading)
                Divider()
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        TextField("Outcome", text: $goals.form.outcome)
                        TextField("Project or meeting topic", text: $goals.form.topic)
                        TextField("Required document name (optional)", text: $goals.form.document)
                        HStack {
                            Button("Choose project folder") {
                                let panel = NSOpenPanel()
                                panel.canChooseDirectories = true; panel.canChooseFiles = false
                                if panel.runModal() == .OK, let url = panel.url {
                                    goals.form.folder = url.resolvingSymlinksInPath().path
                                }
                            }
                            Text(goals.form.folder).lineLimit(1).truncationMode(.head)
                            if !goals.form.folder.isEmpty { Button("Clear") { goals.form.folder = "" } }
                        }
                        DatePicker("Deadline", selection: $goals.form.date).disabled(!goals.form.eventID.isEmpty)
                        if !goals.form.eventID.isEmpty { Button("Use manual deadline") { goals.form.eventID = "" } }
                        Stepper("Remind \(goals.form.lead) minutes before (0 = off)", value: $goals.form.lead, in: 0...1440)
                        Picker("Notify me", selection: $goals.form.preference) {
                            Text("Only for risks or deadline changes").tag("risk")
                            Text("When blockers change").tag("changes")
                            Text("Keep updates in the goal").tag("quiet")
                        }
                        HStack {
                            Button("Save goal", action: goals.save).disabled(goals.form.outcome.isEmpty || goals.form.topic.isEmpty)
                            if goals.form.revision > 0 {
                                Button("Check now", action: goals.check)
                                Button("Complete") { goals.close("completed") }
                                Button("Cancel goal") { goals.close("cancelled") }
                            }
                        }
                        if let g = goals.records.first(where: { $0.id == goals.form.id }) {
                            Button(g.status == "paused" ? "Resume goal" : "Pause goal") { goals.setPaused(g.status != "paused") }
                            Text("Last checked \(g.checked)").font(.caption)
                            Text(g.observation.risks.isEmpty ? "No current blockers" : g.observation.risks.joined(separator: "\n"))
                            ForEach(g.observation.evidence.files, id: \.path) { f in
                                Text(f.path).bold(); Text(f.excerpt).textSelection(.enabled); Text(f.updated).font(.caption)
                            }
                            ForEach(g.observation.items, id: \.id) { item in Text("\(item.kind) #\(item.id) · \(item.text)") }
                            ForEach(g.observation.excerpts, id: \.id) { q in Text("Transcript #\(q.id) · \(q.text)") }
                            Text("Why Talos is tracking this").bold()
                            ForEach(Array(g.history.enumerated()), id: \.offset) { _, entry in Text("\(entry.at) · \(entry.reason)") }
                        }
                        if !goals.message.isEmpty { Text(goals.message) }
                    }
                }
            }.disabled(goals.working)
        }.padding().frame(width: 790, height: 550)
    }
}
