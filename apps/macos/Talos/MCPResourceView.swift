import SwiftUI
import AppKit

struct MCPResourceView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let resource: MCPResource

    @State private var content: MCPResourceContent?
    @State private var loading = true
    @State private var error: String?
    @State private var reloadToken = 0

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Label(resource.name, systemImage: "doc.text.magnifyingglass")
                    .font(.headline)
                Text(resource.serverName)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Copy") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(content?.text ?? "", forType: .string)
                }
                .disabled(content?.text == nil)
                Button("Reload") { reloadToken += 1 }
                Button("Close") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }
            .padding(12)

            HStack(spacing: 12) {
                Text(resource.uri)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                if let mime = resource.mimeType {
                    Text(mime)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if let content {
                    Text("\(content.byteCount.formatted()) bytes")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }
            .padding(.horizontal, 12)
            .padding(.bottom, 8)

            Divider()

            Group {
                if loading {
                    ProgressView("Reading \(resource.name)…")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let error {
                    ContentUnavailableView(
                        "Could not read this resource",
                        systemImage: "exclamationmark.triangle",
                        description: Text(error)
                    )
                } else if let content {
                    if resource.isImage, let data = content.blob, let image = NSImage(data: data) {
                        ScrollView([.horizontal, .vertical]) {
                            Image(nsImage: image)
                                .resizable()
                                .scaledToFit()
                                .frame(maxWidth: 760, maxHeight: 520)
                                .padding()
                        }
                    } else if let text = content.text, !text.isEmpty {
                        ScrollView {
                            Text(text)
                                .font(.system(.body, design: .monospaced))
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(14)
                        }
                    } else {
                        ContentUnavailableView(
                            "Binary resource",
                            systemImage: "doc",
                            description: Text("\(content.byteCount.formatted()) bytes · \(content.mimeType ?? "unknown type")")
                        )
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(width: 820, height: 640)
        .task(id: reloadToken) {
            await load()
        }
    }

    private func load() async {
        loading = true
        error = nil
        switch await model.readMCPResource(uri: resource.uri) {
        case .success(let value):
            content = value
        case .failure(let failure):
            error = failure.localizedDescription
        }
        loading = false
    }
}
