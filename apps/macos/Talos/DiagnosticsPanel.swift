import SwiftUI
import AppKit

struct DiagnosticsPanel: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    @State private var groups: [(path: String, rows: [LSPDiagnostic])] = []
    @State private var failures: [String] = []
    @State private var busy = false
    @State private var checked = 0
    @State private var ran = false

    private var files: [URL] {
        model.gitStatus.files.map {
            model.projectURL.appendingPathComponent($0.path).standardizedFileURL
        }
    }

    private var errors: Int {
        groups.reduce(0) { $0 + $1.rows.filter { $0.severity == 1 }.count }
    }

    private var warnings: Int {
        groups.reduce(0) { $0 + $1.rows.filter { $0.severity == 2 }.count }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("Diagnostics", systemImage: "stethoscope")
                    .font(.title3.weight(.semibold))
                Spacer()
                Button(busy ? "Checking…" : "Check Changed Files") { run() }
                    .disabled(busy || files.isEmpty)
                Button("Done") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }

            if files.isEmpty {
                Text("No changed files to check.")
                    .foregroundStyle(.secondary)
            } else {
                Text("\(files.count) changed file\(files.count == 1 ? "" : "s") · \(errors) error\(errors == 1 ? "" : "s") · \(warnings) warning\(warnings == 1 ? "" : "s")")
                    .font(.callout)
                    .foregroundStyle(errors > 0 ? .red : .secondary)
            }

            if busy {
                ProgressView(value: Double(checked), total: Double(max(files.count, 1)))
                    .progressViewStyle(.linear)
            }

            if groups.isEmpty && !busy {
                Text(ran
                     ? "No diagnostics reported."
                     : "Run a check to query the language servers for the changed files.")
                    .foregroundStyle(.secondary)
                    .padding(.top, 8)
            }

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    ForEach(groups, id: \.path) { group in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(group.path.replacingOccurrences(of: model.projectURL.path + "/", with: ""))
                                .font(.system(.caption, design: .monospaced).weight(.semibold))
                                .textSelection(.enabled)
                            ForEach(Array(group.rows.enumerated()), id: \.offset) { _, row in
                                HStack(alignment: .firstTextBaseline, spacing: 6) {
                                    Image(systemName: icon(for: row.severity))
                                        .foregroundStyle(color(for: row.severity))
                                    Text("\(row.line):\(row.column)")
                                        .font(.system(.caption, design: .monospaced))
                                        .foregroundStyle(.secondary)
                                    Text(row.message)
                                        .font(.callout)
                                        .textSelection(.enabled)
                                    if let source = row.source {
                                        Text(source)
                                            .font(.caption2)
                                            .foregroundStyle(.tertiary)
                                    }
                                }
                            }
                        }
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
                    }
                }
            }

            if !failures.isEmpty {
                Text(failures.joined(separator: "\n"))
                    .font(.caption)
                    .foregroundStyle(.orange)
                    .textSelection(.enabled)
            }

            Text("Servers: " + LSPManager.availability()
                .map { "\($0.0) \($0.1 ? "✓" : "—")" }
                .joined(separator: " · "))
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding(16)
        .frame(width: 660, height: 560)
        .task(id: files.count) {
            if !files.isEmpty { run() }
        }
    }

    private func run() {
        let targets = files
        guard !targets.isEmpty else { return }
        busy = true
        groups = []
        failures = []
        checked = 0
        ran = true
        Task {
            var collected: [String: [LSPDiagnostic]] = [:]
            var problems: [String] = []
            for url in targets {
                do {
                    let rows = try await LSPManager.shared.diagnostics(path: url.path, timeout: 10)
                    if !rows.isEmpty { collected[url.path] = rows }
                } catch {
                    problems.append("\(url.lastPathComponent): \(error.localizedDescription)")
                }
                await MainActor.run { checked += 1 }
            }
            let ordered = targets.compactMap { url -> (String, [LSPDiagnostic])? in
                guard let rows = collected[url.path] else { return nil }
                return (url.path, rows.sorted { ($0.line, $0.column) < ($1.line, $1.column) })
            }
            await MainActor.run {
                groups = ordered.map { (path: $0.0, rows: $0.1) }
                failures = problems
                busy = false
            }
        }
    }

    private func icon(for severity: Int) -> String {
        switch severity {
        case 1: return "xmark.octagon.fill"
        case 2: return "exclamationmark.triangle.fill"
        case 3: return "info.circle.fill"
        default: return "lightbulb.fill"
        }
    }

    private func color(for severity: Int) -> Color {
        switch severity {
        case 1: return .red
        case 2: return .orange
        case 3: return .blue
        default: return .secondary
        }
    }
}
