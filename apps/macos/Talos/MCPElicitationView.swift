import SwiftUI

struct MCPElicitationView: View {
    let request: MCPElicitationRequest
    @EnvironmentObject private var model: AppModel
    @State private var values: [String: String] = [:]

    init(request: MCPElicitationRequest) {
        self.request = request
        var initial: [String: String] = [:]
        for field in request.fields {
            switch field.kind {
            case .boolean:
                initial[field.name] = field.defaultValue == "true" ? "true" : "false"
            default:
                initial[field.name] = field.defaultValue ?? ""
            }
        }
        _values = State(initialValue: initial)
    }

    private var complete: Bool {
        request.fields
            .filter(\.required)
            .allSatisfy { !(values[$0.name] ?? "").trimmingCharacters(in: .whitespaces).isEmpty }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 10) {
                Image(systemName: "person.badge.key")
                    .foregroundStyle(.tint)
                Text("\(request.serverName) needs input")
                    .font(.title3.weight(.semibold))
            }

            Text(request.message)
                .font(.callout)
                .fixedSize(horizontal: false, vertical: true)

            if request.fields.isEmpty {
                Text("This server asked for confirmation without a form.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }

            ForEach(request.fields) { field in
                VStack(alignment: .leading, spacing: 5) {
                    HStack(spacing: 6) {
                        Text(field.title)
                            .font(.callout.weight(.medium))
                        if field.required {
                            Text("required")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                    fieldEditor(field)
                    if let detail = field.detail, !detail.isEmpty {
                        Text(detail)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            HStack {
                Text("The server receives only these values.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Cancel") {
                    model.answerElicitation(.cancel)
                }
                Button("Decline") {
                    model.answerElicitation(.decline)
                }
                Button("Continue") {
                    model.answerElicitation(
                        MCPElicitationResponse(action: .accept, content: values)
                    )
                }
                .keyboardShortcut(.defaultAction)
                .disabled(!complete)
            }
        }
        .padding(20)
        .frame(width: 540)
    }

    @ViewBuilder
    private func fieldEditor(_ field: MCPElicitationField) -> some View {
        switch field.kind {
        case .boolean:
            Toggle("Enabled", isOn: Binding(
                get: { values[field.name] == "true" },
                set: { values[field.name] = $0 ? "true" : "false" }
            ))
            .toggleStyle(.switch)
            .accessibilityLabel(field.title)
        case .choice(let options):
            Picker(field.title, selection: Binding(
                get: { values[field.name] ?? options.first ?? "" },
                set: { values[field.name] = $0 }
            )) {
                ForEach(options, id: \.self) { option in
                    Text(option).tag(option)
                }
            }
            .labelsHidden()
            .accessibilityLabel(field.title)
        case .number, .integer:
            TextField("0", text: Binding(
                get: { values[field.name] ?? "" },
                set: { values[field.name] = $0 }
            ))
            .textFieldStyle(.roundedBorder)
            .accessibilityLabel(field.title)
        case .string:
            TextField("", text: Binding(
                get: { values[field.name] ?? "" },
                set: { values[field.name] = $0 }
            ))
            .textFieldStyle(.roundedBorder)
            .accessibilityLabel(field.title)
        }
    }
}
