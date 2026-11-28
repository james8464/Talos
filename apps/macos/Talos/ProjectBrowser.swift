import SwiftUI
import AppKit

struct ProjectBrowserView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    @State private var tree: [FileNode] = []
    @State private var selected: URL?
    @State private var loading = true
    @State private var showNewFile = false

    private var root: URL {
        model.projectURL
    }

    var body: some View {
        HSplitView {
            VStack(spacing: 0) {
                if loading {
                    ProgressView()
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if tree.isEmpty {
                    ContentUnavailableView(
                        "No Files",
                        systemImage: "folder",
                        description: Text("The project folder is empty.")
                    )
                } else {
                    List(selection: $selected) {
                        OutlineGroup(tree, children: \.children) { node in
                            Label(
                                node.url.lastPathComponent,
                                systemImage: node.isDirectory ? "folder" : fileSymbol(node.url)
                            )
                            .tag(node.url)
                        }
                    }
                    .listStyle(.inset)
                }
            }
            .frame(minWidth: 280, idealWidth: 330)

            FilePreview(url: selected, root: root)
                .frame(minWidth: 420, maxWidth: .infinity, maxHeight: .infinity)
        }
        .navigationTitle("\(model.currentProject?.name ?? "Project") Files")
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button {
                    showNewFile = true
                } label: {
                    Label("New File", systemImage: "doc.badge.plus")
                }
                Button {
                    refresh()
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                Button {
                    NSWorkspace.shared.activateFileViewerSelecting([root])
                } label: {
                    Label("Reveal", systemImage: "folder")
                }
            }
            ToolbarItem(placement: .cancellationAction) {
                Button("Done") { dismiss() }
            }
        }
        .sheet(isPresented: $showNewFile) {
            NewFileSheet(root: root) { url in
                refresh()
                selected = url
            }
        }
        .onAppear(perform: refresh)
        .onChange(of: selected) { _, url in
            if let url, !url.hasDirectoryPath {
                model.recentDocuments.record(url)
            }
        }
        .frame(minWidth: 820, minHeight: 540)
    }

    private func refresh() {
        loading = true
        let root = self.root
        Task {
            let result = await Task.detached(priority: .userInitiated) {
                ProjectFileTree.build(root: root)
            }.value
            tree = result
            loading = false
        }
    }

    private func fileSymbol(_ url: URL) -> String {
        switch url.pathExtension.lowercased() {
        case "png", "jpg", "jpeg", "gif", "webp", "heic", "tiff":
            return "photo"
        case "swift": return "swift"
        case "md", "txt", "rtf": return "doc.text"
        case "json", "yaml", "yml", "toml": return "curlybraces"
        case "html", "css", "js", "ts", "tsx", "jsx": return "chevron.left.forwardslash.chevron.right"
        default: return "doc"
        }
    }
}

private struct FilePreview: View {
    let url: URL?
    let root: URL

    var body: some View {
        Group {
            if let url {
                if url.hasDirectoryPath {
                    folderPreview(url)
                } else {
                    filePreview(url)
                }
            } else {
                ContentUnavailableView(
                    "Select a File",
                    systemImage: "doc.text.magnifyingglass",
                    description: Text("Choose a file to preview it.")
                )
            }
        }
    }

    private func folderPreview(_ url: URL) -> some View {
        VStack(spacing: 12) {
            Image(systemName: "folder")
                .font(.system(size: 42))
                .foregroundStyle(.secondary)
            Text(url.lastPathComponent)
                .font(.title3.weight(.semibold))
            Text(ProjectFileTree.relativePath(of: url, in: root))
                .font(.caption)
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
            HStack {
                Button("Open in Finder") {
                    NSWorkspace.shared.activateFileViewerSelecting([url])
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding()
    }

    @ViewBuilder
    private func filePreview(_ url: URL) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(url.lastPathComponent)
                        .font(.headline)
                    Text(ProjectFileTree.relativePath(of: url, in: root))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
                Spacer()
                Button("Open") { NSWorkspace.shared.open(url) }
                    .buttonStyle(.borderless)
                Button("Reveal") {
                    NSWorkspace.shared.activateFileViewerSelecting([url])
                }
                .buttonStyle(.borderless)
            }
            .padding(12)

            Divider()

            if let image = NSImage(contentsOf: url) {
                ScrollView([.horizontal, .vertical]) {
                    Image(nsImage: image)
                        .resizable()
                        .scaledToFit()
                        .frame(maxWidth: 900)
                        .padding()
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let text = readText(url) {
                ScrollView {
                    Text(text)
                        .font(.system(size: 12, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ContentUnavailableView(
                    "Preview Unavailable",
                    systemImage: "doc",
                    description: Text("Open the file in its default app.")
                )
            }
        }
    }

    private func readText(_ url: URL) -> String? {
        guard let data = try? Data(contentsOf: url), data.count <= 500_000 else { return nil }
        return String(data: data, encoding: .utf8)
    }
}

private struct NewFileSheet: View {
    let root: URL
    var created: (URL) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var name = "Untitled"
    @State private var format = "md"
    @State private var content = "# Untitled\n\n"
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("New File")
                .font(.title3.weight(.semibold))

            HStack(spacing: 8) {
                TextField("File name", text: $name)
                    .textFieldStyle(.roundedBorder)
                Picker("Format", selection: $format) {
                    ForEach(DocumentWriter.formats, id: \.self) { format in
                        Text(format.uppercased()).tag(format)
                    }
                }
                .labelsHidden()
                .frame(width: 100)
            }

            TextEditor(text: $content)
                .font(.system(size: 12, design: .monospaced))
                .frame(minHeight: 260)
                .overlay(
                    RoundedRectangle(cornerRadius: 6)
                        .strokeBorder(Color(nsColor: .separatorColor), lineWidth: 1)
                )

            if let error {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.red)
            }

            HStack {
                Text("Saved in \(root.lastPathComponent)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                Button("Create") { create() }
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 620, height: 460)
        .onChange(of: format) {
            switch format {
            case "html": content = "<!doctype html>\n<html>\n<body>\n\n</body>\n</html>\n"
            case "csv": content = "Column 1,Column 2\n"
            case "json": content = "{}\n"
            case "txt", "rtf", "docx": content = ""
            default: content = "# Untitled\n\n"
            }
        }
    }

    private func create() {
        do {
            let url = try DocumentWriter.create(name: name, format: format, content: content, in: root)
            created(url)
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }
}
