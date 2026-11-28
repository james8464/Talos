import SwiftUI

struct AmbientSuggestionBar: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        if model.settings.chronicleSuggestionsEnabled,
           let suggestion = model.chronicle.activeSuggestions.first {
            HStack(alignment: .center, spacing: 10) {
                Image(systemName: "sparkles")
                    .foregroundStyle(Brand.bronze)
                VStack(alignment: .leading, spacing: 2) {
                    Text(suggestion.title)
                        .font(.caption.weight(.semibold))
                    Text(suggestion.detail)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
                Spacer(minLength: 8)
                if model.chronicle.activeSuggestions.count > 1 {
                    Text("1 of \(model.chronicle.activeSuggestions.count)")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
                Button(suggestion.action.title) {
                    model.performSuggestion(suggestion)
                }
                .font(.caption)
                Button {
                    model.chronicle.dismiss(suggestion)
                } label: {
                    Image(systemName: "xmark")
                }
                .buttonStyle(.borderless)
                .help("Dismiss suggestion")
                .accessibilityLabel("Dismiss suggestion")
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 7)
            .background(.thinMaterial)
            .overlay(alignment: .top) {
                Divider()
            }
        }
    }
}

struct ChronicleSettingsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore

    var body: some View {
        Form {
            Section("Ambient suggestions") {
                Toggle("Record local activity history", isOn: $settings.chronicleEnabled)
                    .accessibilityLabel("Record local activity history")
                Toggle("Generate ambient suggestions", isOn: $settings.chronicleSuggestionsEnabled)
                    .accessibilityLabel("Generate ambient suggestions")
                    .disabled(!settings.chronicleEnabled)
                Text("Chronicle stays on this Mac. It stores short activity summaries and rule-based suggestions only; nothing is sent to a server. Suggestions appear as a dismissible strip above the composer.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                HStack {
                    Button("Generate Suggestions Now") {
                        model.chronicle.generateSuggestions()
                    }
                    .disabled(!settings.chronicleEnabled)
                    Button("Clear History") {
                        model.chronicle.clearHistory()
                    }
                    .disabled(model.chronicle.events.isEmpty)
                    Button("Clear Suggestions") {
                        model.chronicle.clearSuggestions()
                    }
                    .disabled(model.chronicle.suggestions.isEmpty)
                }
            }

            Section("Active suggestions") {
                let active = model.chronicle.activeSuggestions
                if active.isEmpty {
                    Text("No active suggestions.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                ForEach(active) { suggestion in
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: "sparkles")
                            .foregroundStyle(Brand.bronze)
                            .frame(width: 18)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(suggestion.title)
                                .font(.callout.weight(.medium))
                            Text(suggestion.detail)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                            Text(suggestion.createdAt.formatted(date: .abbreviated, time: .shortened))
                                .font(.caption2)
                                .foregroundStyle(.tertiary)
                        }
                        Spacer()
                        Button(suggestion.action.title) {
                            model.performSuggestion(suggestion)
                        }
                        Button("Dismiss") {
                            model.chronicle.dismiss(suggestion)
                        }
                    }
                    .padding(.vertical, 3)
                }
            }

            Section("Recent activity") {
                if model.chronicle.events.isEmpty {
                    Text("No events recorded yet.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                ForEach(model.chronicle.events.prefix(20)) { event in
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: event.kind.symbol)
                            .foregroundStyle(.secondary)
                            .frame(width: 16)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(event.kind.title)
                                .font(.caption.weight(.medium))
                            Text(event.summary)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                        Spacer()
                        Text(event.date.formatted(date: .omitted, time: .shortened))
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                    .padding(.vertical, 2)
                }
            }
        }
        .formStyle(.grouped)
        .padding()
    }
}
