import SwiftUI

struct MCPSamplingView: View {
    @EnvironmentObject private var model: AppModel
    let request: MCPSamplingRequest

    private var imageCount: Int {
        request.messages.reduce(0) { $0 + $1.images.count }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 10) {
                Image(systemName: "brain.head.profile")
                    .foregroundStyle(.tint)
                Text("\(request.serverName) wants a model response")
                    .font(.title3.weight(.semibold))
            }

            Text("This MCP server asked Talos to run a completion. It sees only the response, not your conversation or workspace.")
                .font(.callout)
                .fixedSize(horizontal: false, vertical: true)

            ScrollView {
                Text(request.promptPreview.isEmpty ? "No prompt text." : request.promptPreview)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(10)
            }
            .frame(maxHeight: 220)
            .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))

            if let system = request.systemPrompt, !system.isEmpty {
                DisclosureGroup("System prompt") {
                    Text(system)
                        .font(.system(.caption2, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.top, 4)
                }
                .font(.caption)
            }

            HStack(spacing: 14) {
                Label(model.choice.id, systemImage: "cpu")
                if let maxTokens = request.maxTokens {
                    Label("\(maxTokens) max tokens", systemImage: "text.alignleft")
                }
                if let temperature = request.temperature {
                    Label(String(format: "temp %.1f", temperature), systemImage: "thermometer.medium")
                }
                if imageCount > 0 {
                    Label("\(imageCount) image\(imageCount == 1 ? "" : "s")", systemImage: "photo")
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)

            if let hints = request.modelHints, !hints.isEmpty {
                Text("Server model hints: \(hints)")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }

            HStack {
                Spacer()
                Button("Deny") { model.resolveSamplingApproval(.deny) }
                Button("Allow for This Session") { model.resolveSamplingApproval(.session) }
                Button("Allow Once") { model.resolveSamplingApproval(.once) }
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 600)
        .interactiveDismissDisabled()
    }
}
