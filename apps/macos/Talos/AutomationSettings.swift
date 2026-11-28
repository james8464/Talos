import SwiftUI

struct ProfilesSettingsView: View {
    @EnvironmentObject private var model: AppModel
    @State private var editing: AgentProfile?
    @State private var isNew = false

    var body: some View {
        Form {
            Section("Profiles") {
                if model.profiles.profiles.isEmpty {
                    Text("No profiles yet. Save the current model, sandbox, approval, and tool settings as a profile.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                ForEach(model.profiles.profiles) { profile in
                    HStack(alignment: .top, spacing: 12) {
                        VStack(alignment: .leading, spacing: 3) {
                            HStack(spacing: 6) {
                                Text(profile.name)
                                    .font(.body.weight(.medium))
                                if model.profiles.activeID == profile.id {
                                    Text("Active")
                                        .font(.caption2)
                                        .padding(.horizontal, 6)
                                        .padding(.vertical, 2)
                                        .background(.tint.opacity(0.15), in: Capsule())
                                }
                            }
                            Text(profile.summary)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            if let reasoning = profile.reasoning, !reasoning.isEmpty {
                                Text("Reasoning: \(reasoning)")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        Spacer()
                        Button("Apply") { model.applyProfile(profile) }
                        Button("Edit") {
                            isNew = false
                            editing = profile
                        }
                        Button(role: .destructive) {
                            model.profiles.remove(profile)
                        } label: {
                            Image(systemName: "trash")
                        }
                        .buttonStyle(.borderless)
                        .help("Delete profile")
                    }
                    .padding(.vertical, 2)
                }
            }

            Section {
                Button("New Profile from Current Settings") {
                    isNew = true
                    editing = model.captureProfile(name: "Profile")
                }
                if model.profiles.activeID != nil {
                    Button("Clear Active Profile") { model.clearActiveProfile() }
                }
                Text("Applying a profile switches the model, reasoning, sandbox, approvals, network, speed, and tool toggles.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .padding()
        .sheet(item: $editing) { profile in
            ProfileEditor(profile: profile, isNew: isNew)
                .environmentObject(model)
        }
    }
}

private struct ProfileEditor: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    let isNew: Bool
    @State private var draft: AgentProfile

    init(profile: AgentProfile, isNew: Bool) {
        self.isNew = isNew
        _draft = State(initialValue: profile)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(isNew ? "New Profile" : "Edit Profile")
                .font(.title3.weight(.semibold))

            Form {
                Section("Identity") {
                    TextField("Name", text: $draft.name)
                        .textFieldStyle(.roundedBorder)
                }

                Section("Model") {
                    Picker("Provider", selection: $draft.provider) {
                        ForEach(Provider.allCases) { provider in
                            Text(provider.title).tag(provider)
                        }
                    }
                    TextField("Model ID", text: $draft.model)
                        .textFieldStyle(.roundedBorder)
                    TextField("Reasoning (optional)", text: Binding(
                        get: { draft.reasoning ?? "" },
                        set: { draft.reasoning = $0.isEmpty ? nil : $0 }
                    ))
                    .textFieldStyle(.roundedBorder)
                }

                Section("Safety") {
                    Picker("Sandbox", selection: $draft.sandbox) {
                        ForEach(SandboxMode.allCases) { mode in
                            Text(mode.title).tag(mode)
                        }
                    }
                    Picker("Approvals", selection: $draft.approval) {
                        ForEach(ApprovalPolicy.allCases) { policy in
                            Text(policy.title).tag(policy)
                        }
                    }
                    Toggle("Network access", isOn: $draft.network)
                }

                Section("Response") {
                    Picker("Speed", selection: $draft.speed) {
                        ForEach(ResponseSpeed.allCases) { speed in
                            Text(speed.title).tag(speed)
                        }
                    }
                }

                Section("Tools") {
                    Toggle("Shell", isOn: $draft.enableShell)
                    Toggle("File edits", isOn: $draft.enableFileEdits)
                    Toggle("Computer use", isOn: $draft.enableComputerUse)
                    Toggle("Web", isOn: $draft.enableWeb)
                    Toggle("Browser", isOn: $draft.enableBrowser)
                    Toggle("Plan", isOn: $draft.enablePlan)
                }
            }
            .formStyle(.grouped)

            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Save") { save(false) }
                    .disabled(!valid)
                Button("Save and Apply") { save(true) }
                    .keyboardShortcut(.defaultAction)
                    .disabled(!valid)
            }
        }
        .padding(20)
        .frame(width: 560, height: 640)
    }

    private var valid: Bool {
        !draft.name.trimmingCharacters(in: .whitespaces).isEmpty
            && !draft.model.trimmingCharacters(in: .whitespaces).isEmpty
    }

    private func save(_ apply: Bool) {
        var value = draft
        value.name = value.name.trimmingCharacters(in: .whitespaces)
        value.model = value.model.trimmingCharacters(in: .whitespaces)
        model.profiles.upsert(value)
        if apply { model.applyProfile(value) }
        dismiss()
    }
}

struct HooksSettingsView: View {
    @EnvironmentObject private var model: AppModel
    @State private var editing: HookDefinition?

    var body: some View {
        Form {
            Section("Hooks") {
                if model.hooks.managedLocked {
                    Text("Hooks are controlled by the managed policy and cannot be edited here.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if model.hooks.hooks.isEmpty {
                    Text("No hooks yet. Hooks run a local command on session or tool events.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                ForEach(model.hooks.hooks) { hook in
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: hook.event.symbol)
                            .foregroundStyle(.tint)
                            .frame(width: 20)
                        VStack(alignment: .leading, spacing: 3) {
                            HStack(spacing: 6) {
                                Text(hook.name)
                                    .font(.body.weight(.medium))
                                if !hook.trusted {
                                    Text("needs trust")
                                        .font(.caption2)
                                        .foregroundStyle(.orange)
                                }
                            }
                            Text(hook.summary)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Text(hook.command)
                                .font(.system(.caption, design: .monospaced))
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                        Spacer()
                        Toggle("On", isOn: Binding(
                            get: { hook.enabled },
                            set: { model.hooks.setEnabled(hook, $0) }
                        ))
                        .labelsHidden()
                        Toggle("Trusted", isOn: Binding(
                            get: { hook.trusted },
                            set: { model.hooks.setTrusted(hook, $0) }
                        ))
                        .labelsHidden()
                        .help("Trusted hooks run without a confirmation")
                        Button("Edit") { editing = hook }
                        Button(role: .destructive) {
                            model.hooks.remove(hook)
                        } label: {
                            Image(systemName: "trash")
                        }
                        .buttonStyle(.borderless)
                    }
                    .padding(.vertical, 2)
                    .disabled(model.hooks.managedLocked)
                }
            }

            Section {
                Button("Add Hook") {
                    editing = HookDefinition(
                        name: "New hook",
                        event: .preToolUse,
                        command: "echo '{\"additional_context\":\"hook ran\"}'"
                    )
                }
                .disabled(model.hooks.managedLocked)
                Text("Hook commands run through zsh in the workspace. They receive event details as JSON on stdin and as TALOS_* environment variables. Untrusted hooks ask before running.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Recent hook runs") {
                if model.hooks.log.isEmpty {
                    Text("No hook runs yet.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                ForEach(model.hooks.log.prefix(10)) { entry in
                    VStack(alignment: .leading, spacing: 2) {
                        HStack {
                            Text(entry.hookName)
                                .font(.caption.weight(.medium))
                            Text(entry.event.title)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                            Spacer()
                            Text(entry.status)
                                .font(.caption2)
                                .foregroundStyle(entry.status == "Blocked" ? .red : .secondary)
                        }
                        if !entry.output.isEmpty {
                            Text(entry.output)
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                    }
                }
            }
        }
        .formStyle(.grouped)
        .padding()
        .sheet(item: $editing) { hook in
            HookEditor(hook: hook, isNew: !model.hooks.hooks.contains { $0.id == hook.id })
                .environmentObject(model)
        }
    }
}

private struct HookEditor: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    let isNew: Bool
    @State private var draft: HookDefinition

    init(hook: HookDefinition, isNew: Bool) {
        self.isNew = isNew
        _draft = State(initialValue: hook)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(isNew ? "New Hook" : "Edit Hook")
                .font(.title3.weight(.semibold))

            Form {
                Section("Hook") {
                    TextField("Name", text: $draft.name)
                        .textFieldStyle(.roundedBorder)
                    Picker("Event", selection: $draft.event) {
                        ForEach(HookEvent.allCases) { event in
                            Text(event.title).tag(event)
                        }
                    }
                    Text(draft.event.detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    if draft.event == .preToolUse || draft.event == .postToolUse {
                        TextField("Tool matcher (comma separated, empty = all)", text: $draft.matcher)
                            .textFieldStyle(.roundedBorder)
                    }
                }

                Section("Command") {
                    TextEditor(text: $draft.command)
                        .font(.system(.body, design: .monospaced))
                        .frame(minHeight: 110)
                    Stepper("Timeout: \(draft.timeout)s", value: $draft.timeout, in: 1...300)
                }

                Section("Behavior") {
                    Toggle("Enabled", isOn: $draft.enabled)
                    Toggle("Trusted (run without confirmation)", isOn: $draft.trusted)
                }
            }
            .formStyle(.grouped)

            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Save") { save() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(!valid)
            }
        }
        .padding(20)
        .frame(width: 580, height: 600)
    }

    private var valid: Bool {
        !draft.name.trimmingCharacters(in: .whitespaces).isEmpty
            && !draft.command.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func save() {
        var value = draft
        value.name = value.name.trimmingCharacters(in: .whitespaces)
        if isNew {
            model.hooks.add(value)
        } else {
            model.hooks.update(value)
        }
        dismiss()
    }
}

struct HookTrustView: View {
    @EnvironmentObject private var model: AppModel
    let request: HookTrustRequest

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 10) {
                Image(systemName: "lock.shield")
                    .foregroundStyle(.tint)
                Text("Trust this hook?")
                    .font(.title3.weight(.semibold))
            }

            Text("**\(request.hook.name)** wants to run on \(request.event.title.lowercased()). Hook commands run shell code in your workspace.")
                .font(.callout)
                .fixedSize(horizontal: false, vertical: true)

            Text(request.hook.command)
                .font(.system(.caption, design: .monospaced))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(10)
                .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))

            if let matcher = Optional(request.hook.matcher), !matcher.isEmpty {
                Text("Matches tools: \(matcher)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            HStack {
                Spacer()
                Button("Skip") { model.resolveHookTrust(.skip) }
                Button("Run Once") { model.resolveHookTrust(.runOnce) }
                Button("Always Trust") { model.resolveHookTrust(.alwaysTrust) }
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 540)
    }
}
