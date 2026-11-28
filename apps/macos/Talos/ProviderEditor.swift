import SwiftUI
import AppKit

struct ProviderEditor: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    @Environment(\.dismiss) private var dismiss

    let provider: Provider

    @State private var key = ""
    @State private var found: [String] = []
    @State private var busy = false
    @State private var notice: String?

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: provider.symbol)
                    .font(.title2)
                    .foregroundStyle(Brand.bronze)
                VStack(alignment: .leading, spacing: 2) {
                    Text(provider.title)
                        .font(.title3.weight(.semibold))
                    Text(status)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Done") { dismiss() }
                    .keyboardShortcut(.defaultAction)
            }
            .padding(16)

            Divider()

            Form {
                if provider == .local {
                    Section("Local server") {
                        TextField("Server", text: baseBinding)
                            .textFieldStyle(.roundedBorder)
                            .accessibilityLabel("Server")
                        HStack {
                            Text("\(model.localModels.count) models available")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Spacer()
                            Button("Refresh") {
                                Task { await model.refreshChoices() }
                            }
                        }
                        ForEach(model.localModels) { local in
                            HStack {
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
                    }
                } else {
                    Section("Credential") {
                        HStack(spacing: 8) {
                            SecureField("API key", text: $key)
                                .textFieldStyle(.roundedBorder)
                                .accessibilityLabel("API key")
                            Button(settings.savedKeys.contains(provider) ? "Replace" : "Save") {
                                saveKey()
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
                        if provider == .openCode {
                            HStack {
                                Button("Import from OpenCode") { importOpenCode() }
                                Text("Reads your local OpenCode login.")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        if let apiKeyURL = provider.apiKeyURL {
                            Link("Get an API key", destination: apiKeyURL)
                                .font(.caption)
                        }
                    }

                    Section("Endpoint") {
                        TextField("Base URL", text: baseBinding)
                            .textFieldStyle(.roundedBorder)
                            .accessibilityLabel("Base URL")
                        TextField("Model", text: modelBinding)
                            .textFieldStyle(.roundedBorder)
                            .accessibilityLabel("Model")
                            .onSubmit {
                                if model.choice.provider == provider {
                                    model.choose(ModelChoice(provider: provider, id: settings.model(for: provider)))
                                }
                                Task { await model.refreshChoices() }
                            }
                        HStack {
                            Button("Discover Models") { Task { await discover() } }
                                .disabled(!settings.savedKeys.contains(provider) || busy)
                            if busy {
                                ProgressView().controlSize(.small)
                            }
                            Spacer()
                        }
                    }

                    if !found.isEmpty {
                        Section("Found models") {
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
                }

                if let notice {
                    Section {
                        Text(notice)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                Section {
                    Text(provider.providerNote)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .formStyle(.grouped)
        }
        .frame(width: 600, height: 580)
    }

    private var status: String {
        if provider == .local {
            return model.localModels.isEmpty ? "Server offline" : "\(model.localModels.count) models"
        }
        if settings.savedKeys.contains(provider) {
            return "Key saved · \(settings.model(for: provider))"
        }
        return "Key missing"
    }

    private var baseBinding: Binding<String> {
        Binding(
            get: { settings.baseURL(for: provider) },
            set: { value in
                switch provider {
                case .local: settings.localURL = value
                case .openCode: settings.openCodeBase = value
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

    private func saveKey() {
        do {
            try settings.saveKey(key, for: provider)
            key = ""
            notice = "\(provider.title) key saved in Keychain."
            Task { await model.refreshChoices() }
        } catch {
            notice = "Could not save the \(provider.title) key."
        }
    }

    private func importOpenCode() {
        do {
            let imported = try OpenCodeAuth.readKey()
            try settings.saveKey(imported, for: .openCode)
            notice = "OpenCode key imported into Keychain."
            model.choose(ModelChoice(provider: .openCode, id: settings.openCodeModel))
            Task { await model.refreshChoices() }
        } catch {
            notice = error.localizedDescription
        }
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
            case .openCode:
                ids = try await OpenCodeCatalog.fetch(token: key)
                    .filter(\.isRouted)
                    .map(\.id)
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
            notice = String(localized: "\(ids.count) models found")
        } catch {
            notice = error.localizedDescription
        }
    }
}
