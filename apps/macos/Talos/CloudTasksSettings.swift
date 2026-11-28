import SwiftUI
import AppKit

struct CloudTasksSettingsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    @State private var prompt = ""
    @State private var busy = false

    var body: some View {
        Form {
            Section("Task server") {
                Toggle("Enable the self-hosted task server", isOn: Binding(
                    get: { settings.cloudServerEnabled },
                    set: { model.setCloudServerEnabled($0) }
                ))
                .accessibilityLabel("Enable the self-hosted task server")
                TextField("Port", value: $settings.cloudPort, format: .number)
                    .textFieldStyle(.roundedBorder)
                    .accessibilityLabel("Task server port")
                    .disabled(settings.cloudServerEnabled)
                Toggle("Allow connections from the local network", isOn: $settings.cloudAllowLAN)
                    .accessibilityLabel("Allow connections from the local network")
                    .disabled(settings.cloudServerEnabled)
                LabeledContent("Status", value: model.cloudServerStatus)
                if let endpoint = model.cloudEndpoint {
                    LabeledContent("Endpoint") {
                        Text(endpoint)
                            .font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled)
                    }
                }
                HStack {
                    Text(settings.cloudToken)
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer()
                    Button("Copy Token") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(settings.cloudToken, forType: .string)
                    }
                }
                Text("Workers authenticate with Authorization: Bearer <token>. The server is bound to localhost unless LAN access is enabled.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Local worker") {
                Toggle("Run queued tasks on this Mac", isOn: Binding(
                    get: { model.cloudWorkerStatus != "Off" && model.cloudWorkerStatus != "Stopping…" },
                    set: { enabled in
                        if enabled { model.startCloudWorker() } else { model.stopCloudWorker() }
                    }
                ))
                .accessibilityLabel("Run queued tasks on this Mac")
                .disabled(model.cloudEndpoint == nil)
                LabeledContent("Worker", value: model.cloudWorkerStatus)
                Text("The worker claims one task at a time, creates an isolated git worktree, and runs the task with shell and file tools. Cancelling a task takes effect before it starts or when the worker finishes its current step.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("New task") {
                TextEditor(text: $prompt)
                    .font(.body)
                    .frame(minHeight: 70)
                    .accessibilityLabel("Cloud task prompt")
                    .overlay(alignment: .topLeading) {
                        if prompt.isEmpty {
                            Text("Describe the change to make in \(model.projectURL.lastPathComponent)…")
                                .font(.callout)
                                .foregroundStyle(.tertiary)
                                .padding(.top, 7)
                                .padding(.leading, 5)
                                .allowsHitTesting(false)
                        }
                    }
                HStack {
                    Text(model.choice.display)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button(busy ? "Enqueueing…" : "Enqueue Task") {
                        busy = true
                        Task {
                            _ = await model.enqueueCloudTask(prompt)
                            prompt = ""
                            busy = false
                        }
                    }
                    .keyboardShortcut(.defaultAction)
                    .disabled(prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || busy)
                }
            }

            Section("Tasks") {
                HStack {
                    Text("\(model.cloudTasks.count) task\(model.cloudTasks.count == 1 ? "" : "s")")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Refresh") {
                        Task { await model.refreshCloudTasks() }
                    }
                }

                if model.cloudTasks.isEmpty {
                    Text("No cloud tasks yet.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                ForEach(model.cloudTasks) { task in
                    taskRow(task)
                }
            }
        }
        .formStyle(.grouped)
        .padding()
        .onAppear {
            Task { await model.refreshCloudTasks() }
        }
    }

    @ViewBuilder
    private func taskRow(_ task: CloudTask) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Circle()
                    .fill(color(for: task.status))
                    .frame(width: 8, height: 8)
                Text(task.title.isEmpty ? task.prompt : task.title)
                    .font(.body.weight(.medium))
                    .lineLimit(1)
                Text(task.status.rawValue)
                    .font(.caption2)
                    .foregroundStyle(color(for: task.status))
                Spacer()
                if !task.status.isTerminal {
                    Button("Cancel") {
                        Task { await model.cancelCloudTask(task.id) }
                    }
                    .font(.caption)
                }
                Button(role: .destructive) {
                    Task { await model.removeCloudTask(task.id) }
                } label: {
                    Image(systemName: "trash")
                }
                .buttonStyle(.borderless)
            }
            HStack(spacing: 10) {
                Text(task.createdAt.formatted(date: .abbreviated, time: .shortened))
                if let branch = task.branch {
                    Text(branch)
                }
                if let worker = task.worker {
                    Text(worker)
                }
            }
            .font(.caption2)
            .foregroundStyle(.secondary)

            if let error = task.error, !error.isEmpty {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .textSelection(.enabled)
            }
            if let result = task.result, !result.isEmpty {
                DisclosureGroup("Result") {
                    Text(result)
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.top, 4)
                }
                .font(.caption)
            }
            if let worktree = task.worktree {
                Text(worktree)
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
        }
        .padding(.vertical, 4)
    }

    private func color(for status: CloudTaskStatus) -> Color {
        switch status {
        case .queued: return .secondary
        case .running: return .blue
        case .succeeded: return .green
        case .failed: return .red
        case .cancelled: return .orange
        }
    }
}
