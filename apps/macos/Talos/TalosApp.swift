import SwiftUI

@main
struct TalosApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
                .environmentObject(model.settings)
                .environmentObject(model.usage)
                .tint(Brand.bronze)
                .preferredColorScheme(colorScheme)
                .frame(minWidth: 900, minHeight: 600)
        }
        .defaultSize(width: 1200, height: 780)
        .windowToolbarStyle(.unified)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("New Task") { model.newSession() }
                    .keyboardShortcut("n", modifiers: .command)
            }
            CommandMenu("Task") {
                Button("Stop") { model.stop() }
                    .keyboardShortcut(".", modifiers: .command)
                    .disabled(!model.isStreaming)
                Button("Compact Conversation") { model.compactCurrentSession() }
                    .keyboardShortcut("k", modifiers: [.command, .shift])
                    .disabled(model.isStreaming)
            }
            CommandMenu("Model") {
                Button("Choose Model…") { model.showModelPicker = true }
                    .keyboardShortcut("k", modifiers: .command)
                SettingsLink { Text("Model Settings…") }
            }
            CommandGroup(after: .sidebar) {
                Button(model.showInspector ? "Hide Inspector" : "Show Inspector") {
                    model.showInspector.toggle()
                }
                .keyboardShortcut("i", modifiers: [.command, .option])
                Divider()
                Button(model.columnVisibility == .detailOnly ? "Show Sidebar" : "Hide Sidebar") {
                    model.columnVisibility = model.columnVisibility == .detailOnly ? .all : .detailOnly
                }
                .keyboardShortcut("s", modifiers: [.command, .control])
            }
        }
        Settings {
            SettingsView()
                .environmentObject(model)
                .environmentObject(model.settings)
                .environmentObject(model.usage)
                .tint(Brand.bronze)
                .preferredColorScheme(colorScheme)
        }
    }

    private var colorScheme: ColorScheme? {
        switch model.settings.appearance {
        case .system: return nil
        case .light: return .light
        case .dark: return .dark
        }
    }
}
