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
            AdvancedSettingsView()
                .tabItem { Label("Advanced", systemImage: "slider.horizontal.3") }
            AboutSettingsView()
                .tabItem { Label("About", systemImage: "info.circle") }
        }
        .frame(width: 620, height: 640)
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
                Picker("Reasoning effort", selection: $settings.reasoning) {
                    ForEach(ReasoningEffort.allCases) { effort in
                        Text(effort.title).tag(effort)
                    }
                }
                Text(settings.reasoning.summary)
                    .font(.caption)
                    .foregroundStyle(.secondary)
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
        }
        .formStyle(.grouped)
        .padding()
    }
}

private struct ModelsSettingsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore

    var body: some View {
        Form {
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

private struct AdvancedSettingsView: View {
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
                .frame(maxWidth: 420)

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
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.1"
    }
}
