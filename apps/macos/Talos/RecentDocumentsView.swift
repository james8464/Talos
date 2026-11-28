import SwiftUI
import AppKit

struct RecentDocumentsView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var selected: String?

    private var documents: [RecentDocument] { model.recentDocuments.items }

    private var selectedDocument: RecentDocument? {
        guard let selected else { return nil }
        return documents.first { $0.id == selected }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("Recent Documents", systemImage: "clock.arrow.circlepath")
                    .font(.title3.weight(.semibold))
                Spacer()
                Button("Clear Menu") {
                    model.recentDocuments.clear()
                    selected = nil
                }
                .disabled(documents.isEmpty)
                Button("Done") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }

            if documents.isEmpty {
                ContentUnavailableView(
                    "No Recent Documents",
                    systemImage: "doc.on.doc",
                    description: Text("Files you preview, open, or attach appear here.")
                )
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(selection: $selected) {
                    ForEach(documents) { document in
                        row(document)
                            .tag(document.id)
                            .contextMenu {
                                Button("Open") { open(document) }
                                    .disabled(!document.exists)
                                Button("Reveal in Finder") { reveal(document) }
                                Divider()
                                Button("Remove from Recent", role: .destructive) {
                                    model.recentDocuments.remove(document)
                                }
                            }
                    }
                }
                .listStyle(.inset)

                HStack(spacing: 8) {
                    Button("Open") {
                        if let selectedDocument { open(selectedDocument) }
                    }
                    .disabled(selectedDocument?.exists != true)
                    Button("Reveal in Finder") {
                        if let selectedDocument { reveal(selectedDocument) }
                    }
                    .disabled(selectedDocument == nil)
                    Button("Remove") {
                        if let selectedDocument {
                            model.recentDocuments.remove(selectedDocument)
                            selected = nil
                        }
                    }
                    .disabled(selectedDocument == nil)
                    Spacer()
                    Text("\(documents.count) document\(documents.count == 1 ? "" : "s")")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(16)
        .frame(width: 640, height: 520)
    }

    private func row(_ document: RecentDocument) -> some View {
        HStack(spacing: 10) {
            Image(nsImage: NSWorkspace.shared.icon(forFile: document.path))
                .resizable()
                .frame(width: 26, height: 26)
                .opacity(document.exists ? 1 : 0.4)

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(document.name)
                        .font(.body)
                        .lineLimit(1)
                    if !document.exists {
                        Text("Missing")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(.red)
                    }
                }
                Text(document.path)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }

            Spacer()

            Text(document.lastOpened, style: .relative)
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
        .padding(.vertical, 3)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(document.name), \(document.path)")
    }

    private func open(_ document: RecentDocument) {
        guard document.exists else { return }
        NSWorkspace.shared.open(document.url)
        model.recentDocuments.record(document.url)
    }

    private func reveal(_ document: RecentDocument) {
        NSWorkspace.shared.activateFileViewerSelecting([document.url])
    }
}
