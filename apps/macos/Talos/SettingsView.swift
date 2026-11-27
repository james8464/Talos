import SwiftUI
import AppKit

struct SettingsView: View {
    var body: some View {
        TabView {
            GeneralSettingsView()
                .tabItem { Label("General", systemImage: "gearshape") }
            ModelsSettingsView()
                .tabItem { Label("Models", systemImage: "cpu") }
            ProvidersSettingsView()
                .tabItem { Label("Providers", systemImage: "key") }
            ToolsSettingsView()
                .tabItem { Label("Tools", systemImage: "wrench.and.screwdriver") }
            AppearanceSettingsView()
                .tabItem { Label("Appearance", systemImage: "paintbrush") }
            UsageSettingsView()
                .tabItem { Label("Usage", systemImage: "chart.bar.xaxis") }
            AdvancedSettingsView()
                .tabItem { Label("Advanced", systemImage: "slider.horizontal.3") }
            AboutSettingsView()
                .tabItem { Label("About", systemImage: "info.circle") }
        }
        .frame(width: 760, height: 680)
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
                .disabled(model.choices.isEmpty)

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

    var body: some View {
        Form {
            Section("Selected model") {
                LabeledContent("Model", value: model.choice.display)
                LabeledContent("Reasoning", value: model.reasoningTitle)
                LabeledContent("Context window", value: model.contextLimit.map { $0.formatted() } ?? "Unknown")
                LabeledContent("Context used", value: model.contextUsage.label)
                Text(model.modelCapabilities.summary)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                TextField("Context window override", text: contextOverrideBinding)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { Task { await model.refreshCapabilities() } }
                Text("Leave empty to use the model's reported window.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
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

                Text("Local models run through Ollama at the address above.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Cloud models") {
                ForEach(Provider.allCases.filter { $0 != .local }) { provider in
                    CloudModelSettings(provider: provider)
                }
            }
        }
        .formStyle(.grouped)
        .padding()
    }
}

private struct CloudModelSettings: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore

    let provider: Provider
    @State private var found: [String] = []
    @State private var busy = false
    @State private var notice: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label(provider.title, systemImage: provider.symbol)
                Spacer()
                Text(settings.savedKeys.contains(provider) ? "Key saved" : "No key")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            TextField("Base URL", text: baseBinding)
                .textFieldStyle(.roundedBorder)
            TextField("Model", text: modelBinding)
                .textFieldStyle(.roundedBorder)
                .onSubmit {
                    if model.choice.provider == provider {
                        model.choose(ModelChoice(provider: provider, id: settings.model(for: provider)))
                    }
                    Task { await model.refreshChoices() }
                }

            HStack(spacing: 8) {
                Button("Discover Models") {
                    Task { await discover() }
                }
                .disabled(!settings.savedKeys.contains(provider) || busy)

                if busy {
                    ProgressView().controlSize(.small)
                }

                Spacer()

                if let notice {
                    Text(notice)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }

            if !found.isEmpty {
                DisclosureGroup("Found \(found.count) models") {
                    ForEach(found, id: \.self) { id in
                        Button {
                            settings.setModel(id, for: provider)
                            if model.choice.provider == provider {
                                model.choose(ModelChoice(provider: provider, id: id))
                            }
                            notice = "Using \(id)"
                            Task { await model.refreshChoices() }
                        } label: {
                            HStack {
                                Text(id)
                                    .lineLimit(1)
                                Spacer()
                                if id == settings.model(for: provider) {
                                    Image(systemName: "checkmark")
                                }
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                }
            }

            Text(provider.providerNote)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
    }

    private var baseBinding: Binding<String> {
        Binding(
            get: { settings.baseURL(for: provider) },
            set: { value in
                switch provider {
                case .local: break
                case .openAI: settings.openAIBase = value
                case .anthropic: settings.anthropicBase = value
                case .gemini: settings.geminiBase = value
                }
            }
        )
    }

    private var modelBinding: Binding<String> {
        Binding(
            get: { settings.model(for: provider) },
            set: { settings.setModel($0, for: provider) }
        )
    }

    @MainActor
    private func discover() async {
        guard let key = settings.key(for: provider) else {
            notice = "Add an API key first."
            return
        }
        busy = true
        defer { busy = false }
        do {
            let ids: [String]
            switch provider {
            case .openAI:
                ids = try await OpenAICompatibleClient(baseURL: settings.openAIBase, apiKey: key).models()
            case .anthropic:
                ids = try await AnthropicClient(baseURL: settings.anthropicBase, apiKey: key).models()
            case .gemini:
                ids = try await OpenAICompatibleClient(baseURL: settings.geminiBase, apiKey: key).models()
            case .local:
                return
            }
            found = ids.sorted()
            notice = "\(ids.count) models found"
        } catch {
            notice = error.localizedDescription
        }
    }
}

private struct ProvidersSettingsView: View {
    var body: some View {
        Form {
            Section {
                Text("Talos stores API keys in the macOS Keychain. A key is sent only to the provider you select.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            ForEach(Provider.allCases.filter { $0 != .local }) { provider in
                ProviderKeySettings(provider: provider)
            }

            Section("Subscriptions") {
                Text("ChatGPT Plus and Claude Pro subscriptions do not include API access for third-party apps. Add an API key from the provider's developer console; Talos never sees your account password.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .padding()
    }
}

private struct ProviderKeySettings: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore

    let provider: Provider
    @State private var key = ""
    @State private var notice: String?

    var body: some View {
        Section {
            HStack(spacing: 8) {
                SecureField("API key", text: $key)
                    .textFieldStyle(.roundedBorder)
                Button(settings.savedKeys.contains(provider) ? "Replace" : "Save") {
                    save()
                }
                .disabled(key.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                if settings.savedKeys.contains(provider) {
                    Button("Remove", role: .destructive) {
                        settings.clearKey(for: provider)
                        notice = "\(provider.title) key removed."
                        Task { await model.refreshChoices() }
                    }
                }
            }

            LabeledContent("Status") {
                Text(settings.savedKeys.contains(provider) ? "Stored in Keychain" : "Not set")
                    .foregroundStyle(.secondary)
            }

            if let apiKeyURL = provider.apiKeyURL {
                Link("Get an API key", destination: apiKeyURL)
                    .font(.caption)
            }

            if let notice {
                Text(notice)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        } header: {
            Label(provider.title, systemImage: provider.symbol)
        } footer: {
            Text(provider.providerNote)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private func save() {
        do {
            try settings.saveKey(key, for: provider)
            key = ""
            notice = "\(provider.title) key saved in Keychain."
            Task { await model.refreshChoices() }
        } catch {
            notice = "Could not save the \(provider.title) key."
        }
    }
}

private struct ToolsSettingsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    @State private var accessibility = ToolPermission.accessibility
    @State private var screenRecording = ToolPermission.screenRecording

    var body: some View {
        Form {
            Section("Workspace") {
                HStack(spacing: 10) {
                    Text(settings.workspacePath)
                        .font(.callout)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer()
                    Button("Choose…") { chooseWorkspace() }
                }
                Text("Relative paths resolve here and shell commands start here.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Approvals and sandbox") {
                Picker("Approval policy", selection: $settings.approvalPolicy) {
                    ForEach(ApprovalPolicy.allCases) { policy in
                        Text(policy.title).tag(policy)
                    }
                }
                Text(settings.approvalPolicy.summary)
                    .font(.caption)
                    .foregroundStyle(.secondary)

                Picker("Sandbox", selection: $settings.sandboxMode) {
                    ForEach(SandboxMode.allCases) { mode in
                        Text(mode.title).tag(mode)
                    }
                }
                Text(settings.sandboxMode.summary)
                    .font(.caption)
                    .foregroundStyle(.secondary)

                Toggle("Allow network access for shell commands and web tools", isOn: $settings.networkAccess)
                Stepper("Maximum tool steps: \(settings.maxToolSteps)", value: $settings.maxToolSteps, in: 1...24)
            }

            Section("Tool access") {
                Toggle("Shell commands", isOn: $settings.enableShell)
                Toggle("File reads and edits", isOn: $settings.enableFileEdits)
                Toggle("Computer use", isOn: $settings.enableComputerUse)
                Toggle("Web fetch and search", isOn: $settings.enableWeb)
                Toggle("Task plans", isOn: $settings.enablePlan)
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

    private func chooseWorkspace() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.directoryURL = URL(fileURLWithPath: settings.workspacePath, isDirectory: true)
        if panel.runModal() == .OK, let url = panel.url {
            settings.workspacePath = url.path
        }
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
            Image(nsImage: NSApp.applicationIconImage)
                .resizable()
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
