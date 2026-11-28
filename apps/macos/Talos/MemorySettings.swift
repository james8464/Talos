import SwiftUI

struct MemorySettingsView: View {
    @EnvironmentObject private var model: AppModel
    @State private var editing: MemoryItem?
    @State private var isNew = false

    var body: some View {
        Form {
            Section("Memories") {
                HStack {
                    Text(String(localized: "\(model.memories.memories.count) saved · injected into every task"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Add Memory…") {
                        isNew = true
                        editing = MemoryItem(kind: .memory, text: "")
                    }
                }

                if model.memories.memories.isEmpty {
                    Text("Save durable preferences and facts the model should remember across tasks.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                ForEach(model.memories.memories) { item in
                    row(item)
                }
            }

            Section("Goals") {
                HStack {
                    Text(String(localized: "\(model.memories.activeGoals.count) active · \(model.memories.goals.count - model.memories.activeGoals.count) completed"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Add Goal…") {
                        isNew = true
                        editing = MemoryItem(kind: .goal, text: "", goalStatus: .active)
                    }
                }

                if model.memories.goals.isEmpty {
                    Text("Goals are durable objectives that stay visible to the model until completed.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                ForEach(model.memories.goals) { item in
                    row(item)
                }

                if model.memories.goals.contains(where: { $0.goalStatus == .completed }) {
                    Button("Clear Completed Goals") {
                        model.memories.clearCompletedGoals()
                    }
                }
            }

            Section {
                Text("Enabled memories and active goals are added to the system prompt for every model. Disabled and completed items stay visible here but are not sent.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .padding()
        .sheet(item: $editing) { item in
            MemoryEditor(item: item, isNew: isNew) { saved in
                if isNew {
                    model.memories.add(
                        kind: saved.kind,
                        text: saved.text,
                        goalStatus: saved.goalStatus
                    )
                } else {
                    model.memories.update(saved)
                }
            }
            .environmentObject(model)
        }
    }

    @ViewBuilder
    private func row(_ item: MemoryItem) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: item.kind.symbol)
                .foregroundStyle(item.enabled ? Brand.bronze : .secondary)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 3) {
                Text(item.text)
                    .font(.callout)
                    .foregroundStyle(item.enabled ? .primary : .secondary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 8) {
                    Text(item.kind.title)
                    if item.kind == .goal {
                        Text(item.goalStatus?.title ?? "Active")
                    }
                    if !item.enabled {
                        Text("off")
                    }
                }
                .font(.caption2)
                .foregroundStyle(.secondary)
            }
            Spacer()
            if item.kind == .goal {
                Menu(item.goalStatus?.title ?? "Active") {
                    ForEach(GoalStatus.allCases) { status in
                        Button(status.title) {
                            model.memories.setGoalStatus(item, status)
                        }
                    }
                }
                .font(.caption)
                .fixedSize()
            }
            Toggle("Enabled", isOn: Binding(
                get: { item.enabled },
                set: { model.memories.setEnabled(item, $0) }
            ))
            .labelsHidden()
            .help(item.enabled ? "Included in prompts" : "Not included in prompts")
            Button("Edit") {
                isNew = false
                editing = item
            }
            Button(role: .destructive) {
                model.memories.remove(item)
            } label: {
                Image(systemName: "trash")
            }
            .buttonStyle(.borderless)
        }
        .padding(.vertical, 3)
    }
}

private struct MemoryEditor: View {
    @Environment(\.dismiss) private var dismiss
    let isNew: Bool
    let onSave: (MemoryItem) -> Void
    @State private var draft: MemoryItem
    @State private var status: GoalStatus

    init(item: MemoryItem, isNew: Bool, onSave: @escaping (MemoryItem) -> Void) {
        self.isNew = isNew
        self.onSave = onSave
        _draft = State(initialValue: item)
        _status = State(initialValue: item.goalStatus ?? .active)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(isNew ? "New \(draft.kind.title)" : "Edit \(draft.kind.title)")
                .font(.title3.weight(.semibold))

            Form {
                Section("Kind") {
                    Picker("Kind", selection: $draft.kind) {
                        ForEach(MemoryKind.allCases) { kind in
                            Text(kind.title).tag(kind)
                        }
                    }
                    .pickerStyle(.segmented)
                    if draft.kind == .goal {
                        Picker("Status", selection: $status) {
                            ForEach(GoalStatus.allCases) { value in
                                Text(value.title).tag(value)
                            }
                        }
                    }
                }

                Section("Text") {
                    TextEditor(text: $draft.text)
                        .font(.body)
                        .frame(minHeight: 100)
                    Text("Be concise and specific. This text is sent to the model on every task.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Section {
                    Toggle("Enabled", isOn: $draft.enabled)
                }
            }
            .formStyle(.grouped)

            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Save") {
                    var value = draft
                    value.goalStatus = value.kind == .goal ? status : nil
                    onSave(value)
                    dismiss()
                }
                .keyboardShortcut(.defaultAction)
                .disabled(draft.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 560, height: 460)
    }
}
