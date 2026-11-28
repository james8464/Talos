import SwiftUI
import AppKit
import CoreImage
import CoreImage.CIFilterBuiltins

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        NavigationSplitView {
            List(SettingsSection.allCases, selection: Binding(
                get: { model.settingsSection },
                set: { model.settingsSection = $0 }
            )) { section in
                Label(section.title, systemImage: section.symbol)
                    .tag(section)
            }
            .listStyle(.sidebar)
            .navigationSplitViewColumnWidth(min: 180, ideal: 200, max: 230)
        } detail: {
            detail
                .navigationTitle(model.settingsSection.title)
        }
        .frame(width: 880, height: 660)
    }

    @ViewBuilder
    private var detail: some View {
        switch model.settingsSection {
        case .general:
            GeneralSettingsView()
        case .projects:
            ProjectsSettingsView()
        case .models:
            ModelsSettingsView()
        case .providers:
            ProvidersSettingsView()
        case .tools:
            ToolsSettingsView()
        case .profiles:
            ProfilesSettingsView()
        case .hooks:
            HooksSettingsView()
        case .policy:
            ManagedPolicySettingsView()
        case .cloud:
            CloudTasksSettingsView()
        case .skills:
            SkillsSettingsView()
        case .plugins:
            PluginsSettingsView()
        case .mcp:
            MCPSettingsView()
        case .remote:
            RemoteSettingsView()
        case .appearance:
            AppearanceSettingsView()
        case .usage:
            UsageSettingsView()
        case .advanced:
            AdvancedSettingsView()
        case .about:
            AboutSettingsView()
        }
    }
}

enum SettingsSection: String, CaseIterable, Identifiable {
    case general
    case projects
    case models
    case providers
    case tools
    case profiles
    case hooks
    case policy
    case cloud
    case skills
    case plugins
    case mcp
    case remote
    case appearance
    case usage
    case advanced
    case about

    var id: String { rawValue }

    var title: String {
        switch self {
        case .general: return "General"
        case .projects: return "Projects"
        case .models: return "Models"
        case .providers: return "Providers"
        case .tools: return "Tools"
        case .profiles: return "Profiles"
        case .hooks: return "Hooks"
        case .policy: return "Policy"
        case .cloud: return "Cloud Tasks"
        case .skills: return "Skills"
        case .plugins: return "Plugins"
        case .mcp: return "MCP"
        case .remote: return "Remote"
        case .appearance: return "Appearance"
        case .usage: return "Usage"
        case .advanced: return "Advanced"
        case .about: return "About"
        }
    }

    var symbol: String {
        switch self {
        case .general: return "gearshape"
        case .projects: return "folder"
        case .models: return "cpu"
        case .providers: return "key"
        case .tools: return "wrench.and.screwdriver"
        case .profiles: return "person.crop.rectangle.stack"
        case .hooks: return "bolt.badge.clock"
        case .policy: return "checkmark.shield"
        case .cloud: return "cloud"
        case .skills: return "sparkles"
        case .plugins: return "puzzlepiece.extension"
        case .mcp: return "point.3.connected.trianglepath.dotted"
        case .remote: return "iphone"
        case .appearance: return "paintbrush"
        case .usage: return "chart.bar.xaxis"
        case .advanced: return "slider.horizontal.3"
        case .about: return "info.circle"
        }
    }
}

private struct GeneralSettingsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore

    var body: some View {
        Form {
            Section("Model") {
                Picker("Active model", selection: Binding(
                    get: { model.choice },
                    set: { model.choose($0) }
                )) {
                    ForEach(model.choices) { choice in
                        Text(choice.display).tag(choice)
                    }
                }
                .disabled(model.choices.isEmpty || model.managedPolicy?.isModelLocked == true)
                if model.managedPolicy?.isModelLocked == true {
                    Text("The model is fixed by the managed policy.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                HStack {
                    Text(model.status)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Refresh") {
                        Task { await model.refreshChoices() }
                    }
                }

                if model.choices.isEmpty {
                    Text("Start Ollama or add a provider key in Providers.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Section("Reasoning") {
                if model.modelCapabilities.reasoningOptions.isEmpty {
                    Text(model.modelCapabilities.summary)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    Picker("Reasoning", selection: Binding(
                        get: { model.reasoningSelection ?? model.modelCapabilities.reasoningOptions[0].id },
                        set: { model.setReasoning($0) }
                    )) {
                        ForEach(model.modelCapabilities.reasoningOptions) { option in
                            Text(option.title).tag(option.id)
                        }
                    }
                    .disabled(model.managedPolicy?.isReasoningLocked == true)
                    Text(selectedDetail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Section("Response") {
                Picker("Speed", selection: $settings.speed) {
                    ForEach(ResponseSpeed.allCases) { speed in
                        Text(speed.title).tag(speed)
                    }
                }
                Text(settings.speed.summary)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                LabeledContent("Output budget", value: "\(settings.speed.maxTokens) tokens")
            }

            Section("Window") {
                Toggle("Show details inspector", isOn: $model.showInspector)
            }

            Section {
                Text("Shell, file, web, plan, and computer tools are available with every model. Configure them in Tools.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .padding()
    }

    private var selectedDetail: String {
        model.modelCapabilities.reasoningOptions
            .first(where: { $0.id == model.reasoningSelection })?
            .detail ?? model.modelCapabilities.summary
    }
}

private struct ModelsSettingsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    @State private var editingProvider: Provider?

    var body: some View {
        Form {
            Section("Selected model") {
                LabeledContent("Model", value: model.choice.display)
                LabeledContent("Reasoning", value: model.reasoningTitle)
                LabeledContent("Vision", value: model.modelCapabilities.supportsVision ? "Images" : "Text only")
                LabeledContent("Context window", value: model.contextLimit.map { $0.formatted() } ?? "Unknown")
                LabeledContent("Context used", value: model.contextUsage.label)
                Text(model.modelCapabilities.summary)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                TextField("Context window override", text: contextOverrideBinding)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { Task { await model.refreshCapabilities() } }
            }

            Section("Local") {
                TextField("Server", text: $settings.localURL)
                    .textFieldStyle(.roundedBorder)
                HStack {
                    Text("\(model.localModels.count) models found")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Refresh") {
                        Task { await model.refreshChoices() }
                    }
                }
                ForEach(model.localModels) { local in
                    HStack(spacing: 10) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(local.name)
                            if !local.detail.isEmpty {
                                Text(local.detail)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        Spacer()
                        if model.choice == ModelChoice(provider: .local, id: local.name) {
                            Text("Selected")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        } else {
                            Button("Use") {
                                model.choose(ModelChoice(provider: .local, id: local.name))
                            }
                        }
                    }
                }
                if model.localModels.isEmpty {
                    Text("No local models found. Start Ollama, then refresh.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Section("Cloud models") {
                ForEach(Provider.allCases) { provider in
                    if provider != .local {
                    HStack(spacing: 10) {
                        Image(systemName: provider.symbol)
                            .frame(width: 18)
                            .foregroundStyle(.secondary)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(provider.title)
                            Text(settings.model(for: provider))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                        }
                        Spacer()
                        Text(settings.savedKeys.contains(provider) ? "Ready" : "No key")
                            .font(.caption)
                            .foregroundStyle(settings.savedKeys.contains(provider) ? Color.secondary : Color.orange)
                        Button("Configure…") {
                            editingProvider = provider
                        }
                        .buttonStyle(.borderless)
                    }
                    }
                }
            }
        }
        .formStyle(.grouped)
        .padding()
        .sheet(item: $editingProvider) { provider in
            ProviderEditor(provider: provider)
                .environmentObject(model)
                .environmentObject(settings)
        }
    }

    private var contextOverrideBinding: Binding<String> {
        Binding(
            get: {
                settings.contextWindowOverride(for: model.choice).map(String.init) ?? ""
            },
            set: { value in
                settings.setContextWindowOverride(Int(value.trimmingCharacters(in: .whitespaces)), for: model.choice)
            }
        )
    }
}

private struct ProvidersSettingsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    @State private var editingProvider: Provider?

    var body: some View {
        Form {
            Section {
                Text("Everything provider-specific is in this compact list. Click a row to edit its key, endpoint, and model.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Cloud") {
                ForEach(Provider.allCases) { provider in
                    if provider != .local {
                        ProviderSettingsRow(provider: provider) {
                            editingProvider = provider
                        }
                    }
                }
            }

            Section("Local") {
                ProviderSettingsRow(provider: .local) {
                    editingProvider = .local
                }
            }

            Section("Subscriptions") {
                Text("ChatGPT Plus and Claude Pro subscriptions do not include API access for third-party apps. OpenCode Go can be imported from the local OpenCode login. Other hosted providers use API keys.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .padding()
        .sheet(item: $editingProvider) { provider in
            ProviderEditor(provider: provider)
                .environmentObject(model)
                .environmentObject(settings)
        }
    }
}

private struct ProviderSettingsRow: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    let provider: Provider
    var onConfigure: () -> Void

    var body: some View {
        Button {
            onConfigure()
        } label: {
            HStack(spacing: 10) {
                Image(systemName: provider.symbol)
                    .frame(width: 18)
                    .foregroundStyle(.secondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text(provider.title)
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer()
                Text(status)
                    .font(.caption)
                    .foregroundStyle(status == "Ready" ? Color.secondary : Color.orange)
                Image(systemName: "chevron.right")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private var subtitle: String {
        if provider == .local {
            return model.localModels.isEmpty ? "Ollama · offline" : "Ollama · \(model.localModels.count) models"
        }
        return "\(settings.model(for: provider)) · \(settings.baseURL(for: provider))"
    }

    private var status: String {
        if provider == .local {
            return model.localModels.isEmpty ? "Offline" : "Ready"
        }
        return settings.savedKeys.contains(provider) ? "Ready" : "No key"
    }
}

private struct ProjectsSettingsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore

    var body: some View {
        Form {
            Section("Current project") {
                HStack(spacing: 10) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(model.currentProject?.name ?? "Project")
                            .font(.headline)
                        Text(model.projectURL.path)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    }
                    Spacer()
                    Button("Browse Files…") { model.showProjectBrowser = true }
                }
                HStack {
                    Button("Open Folder…") { model.chooseProject() }
                    Button("Reveal in Finder") {
                        NSWorkspace.shared.activateFileViewerSelecting([model.projectURL])
                    }
                }
                Text("The project folder is the workspace for shell, file, search, and document tools.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Recent projects") {
                if model.projects.projects.isEmpty {
                    Text("No projects yet.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(model.projects.sorted) { project in
                        HStack(spacing: 10) {
                            Button {
                                model.openProject(project)
                            } label: {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(project.name)
                                    Text(project.path)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(1)
                                        .truncationMode(.middle)
                                }
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            Spacer()
                            if project.path == model.currentProject?.path {
                                Image(systemName: "checkmark")
                                    .foregroundStyle(.secondary)
                            }
                            Button(role: .destructive) {
                                model.projects.remove(project)
                            } label: {
                                Image(systemName: "trash")
                            }
                            .buttonStyle(.borderless)
                        }
                    }
                }
            }

            Section("Workspace") {
                LabeledContent("Path") {
                    Text(settings.workspacePath)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
                Button("Sync Workspace to Current Project") {
                    settings.workspacePath = model.projectURL.path
                }
            }
        }
        .formStyle(.grouped)
        .padding()
    }
}

private struct ToolsSettingsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    @State private var accessibility = ToolPermission.accessibility
    @State private var screenRecording = ToolPermission.screenRecording

    var body: some View {
        Form {
            Section("Approvals and sandbox") {
                Picker("Approval policy", selection: $settings.approvalPolicy) {
                    ForEach(ApprovalPolicy.allCases) { policy in
                        Text(policy.title).tag(policy)
                    }
                }
                .disabled(settings.managed?.approval != nil)
                Text(settings.approvalPolicy.summary)
                    .font(.caption)
                    .foregroundStyle(.secondary)

                Picker("Sandbox", selection: $settings.sandboxMode) {
                    ForEach(SandboxMode.allCases) { mode in
                        Text(mode.title).tag(mode)
                    }
                }
                .disabled(settings.managed?.sandbox != nil)
                Text(settings.sandboxMode.summary)
                    .font(.caption)
                    .foregroundStyle(.secondary)

                Toggle("Allow network access for shell commands and web tools", isOn: $settings.networkAccess)
                    .disabled(settings.managed?.network != nil)
                Stepper("Maximum tool steps: \(settings.maxToolSteps)", value: $settings.maxToolSteps, in: 1...24)
                    .disabled(settings.managed?.maxToolSteps != nil)
                Toggle("Review high-impact tools before approval", isOn: $settings.autoReviewTools)
                    .disabled(settings.managed?.autoReview != nil)
                Text("Runs a read-only reviewer in a separate context and shows its recommendation in the approval sheet.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Tool access") {
                Toggle("Shell commands", isOn: $settings.enableShell)
                    .disabled(settings.managed?.enableShell != nil)
                Toggle("File reads and edits", isOn: $settings.enableFileEdits)
                    .disabled(settings.managed?.enableFileEdits != nil)
                Toggle("Computer use", isOn: $settings.enableComputerUse)
                    .disabled(settings.managed?.enableComputerUse != nil)
                Toggle("Browser control", isOn: $settings.enableBrowser)
                    .disabled(settings.managed?.enableBrowser != nil)
                Toggle("Web fetch and search", isOn: $settings.enableWeb)
                    .disabled(settings.managed?.enableWeb != nil)
                Toggle("Task plans", isOn: $settings.enablePlan)
                    .disabled(settings.managed?.enablePlan != nil)
                Text("These tools are provided by Talos and work with every model. The sandbox applies to shell and file tools.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("macOS permissions") {
                permissionRow(
                    "Accessibility",
                    granted: accessibility,
                    open: ToolPermission.openAccessibilitySettings
                )
                permissionRow(
                    "Screen Recording",
                    granted: screenRecording,
                    open: ToolPermission.openScreenRecordingSettings
                )
                HStack {
                    Button("Request Accessibility") {
                        ToolPermission.requestAccessibility()
                        refreshPermissions()
                    }
                    Button("Request Screen Recording") {
                        ToolPermission.requestScreenRecording()
                        refreshPermissions()
                    }
                }
                Text("Computer use needs both permissions. Shell, file, web, and plan tools do not.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .padding()
        .onAppear { refreshPermissions() }
    }

    private func permissionRow(_ name: String, granted: Bool, open: @escaping () -> Void) -> some View {
        HStack {
            Label(name, systemImage: granted ? "checkmark.shield" : "exclamationmark.shield")
            Spacer()
            Text(granted ? "Granted" : "Not granted")
                .font(.caption)
                .foregroundStyle(granted ? .green : .secondary)
            Button("Open Settings") { open() }
                .font(.caption)
        }
    }

    private func refreshPermissions() {
        accessibility = ToolPermission.accessibility
        screenRecording = ToolPermission.screenRecording
    }
}

private struct SkillsSettingsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Form {
            Section {
                HStack {
                    Text("\(model.skills.skills.count) skills")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Reload") { model.reloadSkills() }
                    Button("Open Folder") { model.skills.openFolder() }
                }

                if model.skills.skills.isEmpty {
                    Text("Add a folder containing SKILL.md under \(model.skills.directory.path).")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(model.skills.skills) { skill in
                        Toggle(isOn: Binding(
                            get: { !model.skills.disabled.contains(skill.path) },
                            set: { model.skills.setEnabled(skill, $0) }
                        )) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(skill.name)
                                if !skill.description.isEmpty {
                                    Text(skill.description)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(2)
                                }
                            }
                        }
                    }
                }

                Button("Create Example Skill") {
                    model.skills.createStarter()
                    model.reloadSkills()
                }
            } header: {
                Text("Local skills")
            } footer: {
                Text("Skills are reusable instructions. Every model sees their names and descriptions, and can load one with the read_skill tool.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .padding()
    }
}

private struct PluginsSettingsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Form {
            Section {
                HStack {
                    Text("\(model.plugins.plugins.count) plugins")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Reload") { model.reloadPlugins() }
                    Button("Open Folder") { model.plugins.openFolder() }
                }

                if model.plugins.plugins.isEmpty {
                    Text("Add a folder with plugin.json under \(model.plugins.directory.path).")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(model.plugins.plugins) { plugin in
                        HStack(spacing: 8) {
                            Toggle(isOn: Binding(
                                get: { !model.plugins.disabled.contains(plugin.path) },
                                set: { model.plugins.setEnabled(plugin, $0); model.reloadPlugins() }
                            )) {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(plugin.name)
                                    if !plugin.description.isEmpty {
                                        Text(plugin.description)
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                            .lineLimit(2)
                                    }
                                }
                            }
                            Spacer()
                            Button {
                                NSWorkspace.shared.open(URL(fileURLWithPath: plugin.path))
                            } label: {
                                Image(systemName: "folder")
                            }
                            .buttonStyle(.borderless)
                            .help("Reveal plugin folder")
                        }
                    }
                }

                Button("Create Example Plugin") {
                    model.plugins.createStarter()
                    model.reloadPlugins()
                }
            } header: {
                Text("Local plugins")
            } footer: {
                Text("A plugin can bundle skills and stdio MCP servers. Enabled plugins add their skills and tools to every model.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .padding()
    }
}

private struct MCPSettingsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    @State private var name = ""
    @State private var command = ""
    @State private var arguments = ""
    @State private var url = ""
    @State private var selectedResource: MCPResource?

    var body: some View {
        Form {
            Section("Connected servers") {
                HStack {
                    Text(model.mcpStatus)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Reload") {
                        Task { await model.reloadMCP() }
                    }
                }

                if settings.managedMCPLocked {
                    Text("The managed policy controls this server list.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                ForEach(settings.mcpServers) { server in
                    HStack(spacing: 8) {
                        Toggle(server.name, isOn: Binding(
                            get: { server.enabled },
                            set: { value in
                                settings.setMCPEnabled(server, value)
                                Task { await model.reloadMCP() }
                            }
                        ))
                        Text(server.url ?? ([server.command] + server.args).joined(separator: " "))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .truncationMode(.middle)
                        Spacer()
                        Button(role: .destructive) {
                            settings.removeMCPServer(server)
                            Task { await model.reloadMCP() }
                        } label: {
                            Image(systemName: "trash")
                        }
                        .buttonStyle(.borderless)
                        .disabled(settings.managedMCPLocked)
                    }
                }
                .disabled(settings.managedMCPLocked)
            }

            Section("Resources") {
                HStack {
                    Text("\(model.mcpResources.count) resources · \(model.mcpResourceTemplates.count) templates")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Refresh") {
                        Task { await model.refreshMCPResources() }
                    }
                }

                if model.mcpResources.isEmpty {
                    Text("This server set exposes no standalone resources.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                ForEach(model.mcpResources) { resource in
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: resource.isImage ? "photo" : "doc.text")
                            .foregroundStyle(.tint)
                            .frame(width: 18)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(resource.name)
                                .font(.callout.weight(.medium))
                            Text(resource.uri)
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                                .truncationMode(.middle)
                            if !resource.description.isEmpty {
                                Text(resource.description)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(2)
                            }
                        }
                        Spacer()
                        if let mime = resource.mimeType {
                            Text(mime)
                                .font(.caption2)
                                .foregroundStyle(.tertiary)
                        }
                        Button("Open") { selectedResource = resource }
                    }
                    .padding(.vertical, 2)
                }

                if !model.mcpResourceTemplates.isEmpty {
                    DisclosureGroup("Resource templates") {
                        ForEach(model.mcpResourceTemplates) { template in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(template.name)
                                    .font(.callout)
                                Text(template.uriTemplate)
                                    .font(.system(.caption2, design: .monospaced))
                                    .foregroundStyle(.secondary)
                                    .textSelection(.enabled)
                                if !template.description.isEmpty {
                                    Text(template.description)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            }
                            .padding(.vertical, 2)
                        }
                    }
                }
            }

            Section {
                TextField("Name", text: $name)
                    .textFieldStyle(.roundedBorder)
                TextField("HTTP URL (optional)", text: $url)
                    .textFieldStyle(.roundedBorder)
                TextField("Command (stdio)", text: $command)
                    .textFieldStyle(.roundedBorder)
                TextField("Arguments", text: $arguments)
                    .textFieldStyle(.roundedBorder)
                Button("Add Server") {
                    let values = arguments
                        .split(whereSeparator: { $0.isWhitespace })
                        .map(String.init)
                    settings.addMCPServer(MCPServerConfig(
                        name: name.isEmpty ? "MCP" : name,
                        command: command,
                        args: values,
                        url: url.isEmpty ? nil : url
                    ))
                    name = ""
                    command = ""
                    arguments = ""
                    url = ""
                    Task { await model.reloadMCP() }
                }
                .disabled(
                    settings.managedMCPLocked
                        || (command.trimmingCharacters(in: .whitespaces).isEmpty
                            && url.trimmingCharacters(in: .whitespaces).isEmpty)
                )
            } header: {
                Text("Add server")
            } footer: {
                Text("Talos supports stdio servers and Streamable HTTP MCP endpoints. Their tools are offered to every model, so connector-style servers behave like native tools.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .padding()
        .sheet(item: $selectedResource) { resource in
            MCPResourceView(resource: resource)
                .environmentObject(model)
        }
    }
}

private struct RemoteSettingsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Form {
            Section("Phone control") {
                Toggle("Enable phone control", isOn: Binding(
                    get: { model.remote.enabled },
                    set: { model.remote.setEnabled($0) }
                ))

                HStack {
                    Text("Port")
                    TextField("Port", value: Binding(
                        get: { model.remote.port },
                        set: { model.remote.port = $0 }
                    ), format: .number)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 90)
                    Spacer()
                    Text(model.remote.running ? "Serving" : "Off")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                if let url = model.remote.url {
                    HStack(alignment: .top, spacing: 16) {
                        QRCodeView(text: url)
                            .frame(width: 150, height: 150)
                        VStack(alignment: .leading, spacing: 8) {
                            Text(url)
                                .font(.caption.monospaced())
                                .textSelection(.enabled)
                                .lineLimit(4)
                            HStack {
                                Button("Copy URL") {
                                    NSPasteboard.general.clearContents()
                                    NSPasteboard.general.setString(url, forType: .string)
                                }
                                Button("Open") {
                                    if let url = URL(string: url) { NSWorkspace.shared.open(url) }
                                }
                                Button("New Token") {
                                    model.remote.regenerateToken()
                                }
                            }
                            .font(.caption)
                            Text("Open this on a phone on the same network. The token is the password.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                } else {
                    Text("Turn this on to serve a local control page for sending prompts, stopping, and compacting the current task.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Section {
                Text("The control page stays on this Mac. It is not a cloud relay, and requests require the token shown above.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .padding()
    }
}

private struct QRCodeView: View {
    let text: String

    var body: some View {
        if let image = makeImage() {
            Image(nsImage: image)
                .interpolation(.none)
                .resizable()
                .scaledToFit()
                .padding(8)
                .background(Color.white)
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        } else {
            RoundedRectangle(cornerRadius: 8)
                .fill(Color(nsColor: .controlBackgroundColor))
                .overlay(Image(systemName: "qrcode").foregroundStyle(.secondary))
        }
    }

    private func makeImage() -> NSImage? {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(text.utf8)
        filter.correctionLevel = "M"
        guard let output = filter.outputImage else { return nil }
        let scaled = output.transformed(by: CGAffineTransform(scaleX: 8, y: 8))
        let representation = NSCIImageRep(ciImage: scaled)
        let image = NSImage(size: representation.size)
        image.addRepresentation(representation)
        return image
    }
}

private struct AppearanceSettingsView: View {
    @EnvironmentObject private var settings: SettingsStore

    var body: some View {
        Form {
            Section("Appearance") {
                Picker("Theme", selection: $settings.appearance) {
                    ForEach(AppearanceMode.allCases) { mode in
                        Text(mode.title).tag(mode)
                    }
                }
                LabeledContent("Transcript size") {
                    HStack(spacing: 10) {
                        Slider(value: $settings.transcriptFontSize, in: 12...20, step: 1)
                        Text(settings.transcriptFontSize, format: .number.precision(.fractionLength(0)))
                            .monospacedDigit()
                            .frame(width: 24, alignment: .trailing)
                    }
                }
                Text("Applies to messages and tool output. Code stays monospaced.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Completion") {
                Toggle("Play a sound when a task finishes", isOn: $settings.completionSound)
                Toggle("Notify when the app is in the background", isOn: Binding(
                    get: { settings.notifyOnFinish },
                    set: { value in
                        settings.notifyOnFinish = value
                        if value {
                            NotificationManager.shared.requestAuthorization()
                        }
                    }
                ))
                Text("Notifications use the macOS notification center. The app asks for permission the first time you enable them.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .padding()
    }
}

private struct UsageSettingsView: View {
    @EnvironmentObject private var usage: UsageStore

    var body: some View {
        Form {
            Section("Tokens") {
                HStack(spacing: 18) {
                    metric("Total", usage.totalTokens)
                    metric("Prompt", usage.promptTokens)
                    metric("Completion", usage.completionTokens)
                }
                if usage.hasEstimates {
                    Text("Some counts are estimated from text length because the provider did not report usage.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Section("Last 12 weeks") {
                UsageHeatmap(totals: usage.dailyTotals(days: 84))
                HStack(spacing: 5) {
                    Text("Less")
                    ForEach(1...5, id: \.self) { level in
                        RoundedRectangle(cornerRadius: 2)
                            .fill(Brand.bronze.opacity(0.12 + Double(level) * 0.17))
                            .frame(width: 12, height: 12)
                    }
                    Text("More")
                    Spacer()
                    Text("\(TokenEstimator.format(usage.totalTokens)) total")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }

            Section("By model") {
                if usage.modelTotals.isEmpty {
                    Text("No usage recorded yet.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(Array(usage.modelTotals.enumerated()), id: \.offset) { _, entry in
                        LabeledContent(entry.model, value: TokenEstimator.format(entry.tokens))
                    }
                }
            }

            Section {
                Button("Clear Usage History", role: .destructive) {
                    usage.clear()
                }
            }
        }
        .formStyle(.grouped)
        .padding()
    }

    private func metric(_ title: String, _ value: Int) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(TokenEstimator.format(value))
                .font(.title3.weight(.semibold))
                .monospacedDigit()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct UsageHeatmap: View {
    let totals: [Date: Int]

    private let weeks = 12

    var body: some View {
        let calendar = Calendar.current
        let today = calendar.startOfDay(for: Date())
        let start = calendar.date(byAdding: .day, value: -(weeks * 7 - 1), to: today) ?? today
        let weekday = calendar.component(.weekday, from: start)
        let gridStart = calendar.date(byAdding: .day, value: -(weekday - 1), to: start) ?? start
        let maxValue = totals.values.max() ?? 1

        Grid(horizontalSpacing: 3, verticalSpacing: 3) {
            ForEach(0..<7, id: \.self) { row in
                GridRow {
                    ForEach(0..<weeks, id: \.self) { column in
                        let date = calendar.date(byAdding: .day, value: column * 7 + row, to: gridStart) ?? gridStart
                        let day = calendar.startOfDay(for: date)
                        let value = totals[day] ?? 0
                        RoundedRectangle(cornerRadius: 2)
                            .fill(shade(value: value, maxValue: maxValue, inRange: day >= start && day <= today))
                            .frame(width: 13, height: 13)
                            .help("\(day.formatted(date: .abbreviated, time: .omitted)): \(value) tokens")
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func shade(value: Int, maxValue: Int, inRange: Bool) -> Color {
        guard inRange, value > 0, maxValue > 0 else {
            return Color.secondary.opacity(0.08)
        }
        let fraction = Double(value) / Double(maxValue)
        return Brand.bronze.opacity(0.15 + 0.85 * fraction)
    }
}

private struct AdvancedSettingsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore

    var body: some View {
        Form {
            Section("Requests") {
                LabeledContent("Temperature") {
                    HStack(spacing: 10) {
                        Slider(value: $settings.temperature, in: 0...1.5, step: 0.1)
                        Text(settings.temperature, format: .number.precision(.fractionLength(1)))
                            .monospacedDigit()
                            .frame(width: 28, alignment: .trailing)
                    }
                }
                Text("Applies to local, Anthropic, and Gemini requests. OpenAI uses its model defaults.")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                LabeledContent("Max reply", value: "\(settings.speed.maxTokens) tokens")
                Picker("Keep local model loaded", selection: $settings.keepAlive) {
                    Text("5 minutes").tag("5m")
                    Text("30 minutes").tag("30m")
                    Text("2 hours").tag("2h")
                }
                Text("Longer keeps avoid reloading a local model between messages.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Context") {
                Toggle("Automatically compact long conversations", isOn: $settings.autoCompact)
                LabeledContent("Compact at") {
                    HStack(spacing: 10) {
                        Slider(value: $settings.compactionThreshold, in: 0.5...0.95, step: 0.05)
                        Text(settings.compactionThreshold, format: .percent.precision(.fractionLength(0)))
                            .monospacedDigit()
                            .frame(width: 38, alignment: .trailing)
                    }
                }
                Text("When estimated context reaches this share of the model window, Talos summarizes older messages with the same model before continuing.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                LabeledContent("Current usage", value: model.contextUsage.label)
                Button("Compact This Task Now") {
                    model.compactCurrentSession()
                }
                .disabled(model.isStreaming)
            }

            Section("Git") {
                TextField("Worktree branch prefix", text: $settings.branchPrefix)
                    .textFieldStyle(.roundedBorder)
                Text("New worktrees use this prefix, for example talos/task.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("History") {
                Button("Clear All Tasks", role: .destructive) {
                    model.clearHistory()
                }
                Text("Deletes saved conversation history from this Mac. Provider keys and settings are kept.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Reset") {
                Button("Restore Generation Defaults") {
                    settings.resetGeneration()
                }
            }
        }
        .formStyle(.grouped)
        .padding()
    }
}

private struct AboutSettingsView: View {
    var body: some View {
        VStack(spacing: 12) {
            Group {
                if let icon = Brand.icon {
                    Image(nsImage: icon)
                        .resizable()
                } else {
                    Image(systemName: "circle.hexagongrid.fill")
                        .resizable()
                        .foregroundStyle(Brand.bronze)
                }
            }
            .frame(width: 64, height: 64)

            Text("Talos")
                .font(.title2.weight(.semibold))
            Text("Version \(version)")
                .font(.callout)
                .foregroundStyle(.secondary)

            Text("A native macOS client for the models you choose.")
                .font(.body)

            Text("Local models stay on this Mac. Hosted requests go only to the provider you configure. Talos does not train on your conversations.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 440)

            HStack(spacing: 16) {
                Link("Ollama", destination: URL(string: "https://ollama.com")!)
                Link("OpenAI", destination: URL(string: "https://platform.openai.com")!)
                Link("Anthropic", destination: URL(string: "https://console.anthropic.com")!)
                Link("Gemini", destination: URL(string: "https://aistudio.google.com")!)
            }
            .font(.callout)

            Spacer()
        }
        .padding(28)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var version: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.2"
    }
}
