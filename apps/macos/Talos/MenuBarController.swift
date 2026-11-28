import AppKit
import SwiftUI
import Combine

@MainActor
final class MenuBarController: NSObject, ObservableObject {
    private var statusItem: NSStatusItem?
    private let popover = NSPopover()
    private weak var model: AppModel?
    private weak var settings: SettingsStore?
    private var cancellable: AnyCancellable?
    private var configured = false

    var isVisible: Bool { statusItem != nil }

    func configure(model: AppModel, settings: SettingsStore) {
        guard !configured else { return }
        configured = true
        self.model = model
        self.settings = settings

        popover.behavior = .transient
        popover.animates = true
        popover.contentSize = NSSize(width: 340, height: 300)
        popover.contentViewController = NSHostingController(
            rootView: MenuBarPanel(
                onShow: { [weak self] in self?.showMainWindow() },
                onQuit: { NSApp.terminate(nil) }
            )
            .environmentObject(model)
            .environmentObject(settings)
            .tint(Brand.bronze)
        )

        cancellable = settings.$menuBarEnabled.sink { [weak self] enabled in
            self?.setVisible(enabled)
        }
        setVisible(settings.menuBarEnabled)
    }

    private func setVisible(_ visible: Bool) {
        if visible {
            guard statusItem == nil else { return }
            let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
            item.button?.image = NSImage(
                systemSymbolName: "sparkles",
                accessibilityDescription: "Talos"
            )
            item.button?.image?.isTemplate = true
            item.button?.toolTip = "Talos"
            item.button?.target = self
            item.button?.action = #selector(togglePopover)
            statusItem = item
        } else {
            if let statusItem {
                NSStatusBar.system.removeStatusItem(statusItem)
            }
            statusItem = nil
            popover.performClose(nil)
        }
    }

    @objc private func togglePopover() {
        guard let button = statusItem?.button else { return }
        if popover.isShown {
            popover.performClose(nil)
            return
        }
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        popover.contentViewController?.view.window?.makeKey()
        NSApp.activate(ignoringOtherApps: true)
    }

    func showMainWindow() {
        popover.performClose(nil)
        NSApp.activate(ignoringOtherApps: true)
        if let window = NSApp.windows.first(where: { $0.canBecomeMain }) {
            window.makeKeyAndOrderFront(nil)
        }
    }

    private func openSettings() {
        popover.performClose(nil)
        NSApp.activate(ignoringOtherApps: true)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) {
            if !NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil) {
                if !NSApp.sendAction(Selector(("showPreferencesWindow:")), to: nil, from: nil) {
                    NSApp.windows.first(where: { $0.canBecomeMain })?.makeKeyAndOrderFront(nil)
                }
            }
        }
    }
}

private struct MenuBarPanel: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    @Environment(\.openSettings) private var openSettings
    let onShow: () -> Void
    let onQuit: () -> Void

    private var statusColor: Color {
        if model.isStreaming { return .orange }
        if let error = model.error, !error.isEmpty { return .red }
        return .green
    }

    private var statusText: String {
        if model.isStreaming {
            return "Working on \(model.projectURL.lastPathComponent)…"
        }
        if let error = model.error, !error.isEmpty {
            return error
        }
        return "Ready in \(model.projectURL.lastPathComponent)"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 11) {
            HStack(spacing: 8) {
                Image(systemName: "sparkles")
                    .foregroundStyle(Brand.bronze)
                Text("Talos")
                    .font(.headline)
                Spacer()
                Circle()
                    .fill(statusColor)
                    .frame(width: 8, height: 8)
                    .help(statusText)
            }

            Text(statusText)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(2)

            Divider()

            HStack {
                Text("Model")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Menu {
                    ForEach(model.choices) { choice in
                        Button {
                            model.choose(choice)
                        } label: {
                            if choice == model.choice {
                                Label(choice.display, systemImage: "checkmark")
                            } else {
                                Text(choice.display)
                            }
                        }
                    }
                } label: {
                    Text(model.choice.id)
                        .font(.caption)
                        .lineLimit(1)
                }
                .menuStyle(.borderlessButton)
                .fixedSize()
                .disabled(
                    model.choices.isEmpty
                        || model.managedPolicy?.isModelLocked == true
                        || model.isStreaming
                )
            }

            HStack {
                Text(model.choice.provider.title)
                Text("·")
                Text(model.contextUsage.label)
                if let queue = model.selected?.queue.count, queue > 0 {
                    Text("·")
                    Text("\(queue) queued")
                }
            }
            .font(.caption2)
            .foregroundStyle(.secondary)
            .lineLimit(1)

            Divider()

            HStack(spacing: 8) {
                Button("New Task") {
                    model.newSession()
                }
                Spacer()
                if model.isStreaming {
                    Button("Stop") { model.stop() }
                } else {
                    Button("Show Talos") { onShow() }
                }
            }

            HStack(spacing: 8) {
                Button("Open Talos") { onShow() }
                Spacer()
                Button("Settings…") {
                    NSApp.activate(ignoringOtherApps: true)
                    openSettings()
                }
                Button("Quit") { onQuit() }
            }
            .font(.caption)
        }
        .padding(14)
        .frame(width: 340)
    }
}
