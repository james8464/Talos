import SwiftUI

struct ManagedPolicySettingsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Form {
            Section("Status") {
                LabeledContent("Policy", value: model.managedPolicy == nil ? "Not configured" : "Applied")
                if let source = model.policySource {
                    LabeledContent("Source") {
                        Text(source)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                            .lineLimit(2)
                    }
                }
                LabeledContent("Signature", value: model.policySignature)
                if let error = model.policyError {
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(.red)
                        .textSelection(.enabled)
                }
                if let policy = model.managedPolicy {
                    LabeledContent("Name", value: policy.title)
                    if let message = policy.message, !message.isEmpty {
                        Text(message)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                }
                Button("Reload Policy") {
                    model.reloadPolicy()
                }
            }

            if let policy = model.managedPolicy {
                Section("Locked values") {
                    if policy.lockedSummary.isEmpty {
                        Text("This policy does not lock any values.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(policy.lockedSummary, id: \.self) { line in
                            Label(line, systemImage: "lock.fill")
                                .font(.callout)
                        }
                    }
                }
            }

            Section("Search paths") {
                ForEach(ManagedPolicyLoader.candidatePaths, id: \.self) { path in
                    Text(path)
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
            }

            Section {
                Text("Managed policies are read-only. A signed policy is verified with an Ed25519 key from policy.pub or TALOS_POLICY_PUBLIC_KEY. Local unsigned files are accepted only from the managed search paths or an explicit TALOS_POLICY_FILE.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .padding()
    }
}
