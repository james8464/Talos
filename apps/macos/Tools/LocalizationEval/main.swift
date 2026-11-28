import AppKit
import ApplicationServices
import Foundation

// Live French localization regression for Talos.
//
// Usage (from the repository root):
//   make localization-eval
//
// Requires Accessibility permission for the terminal. Launches the built app
// with AppleLanguages=fr, then visits the main window, menu bar, dialogs,
// sheets, and every Settings page. It fails on app-owned English UI strings,
// missing French labels, or unlabeled interactive controls. Restores English
// on exit.

struct Violation {
    let page: String
    let message: String
}

let bundleID = "app.talos.macos"
let pageTitleKeys = [
    "General", "Projects", "Models", "Providers", "Tools", "Profiles",
    "Hooks", "Memory", "Chronicle", "Policy", "Cloud Tasks", "App Server",
    "Skills", "Plugins", "MCP", "Remote", "Appearance", "Usage", "Advanced", "About",
]
let brandAllowlist: Set<String> = [
    "Talos", "MCP", "Git", "GitHub", "Ollama", "OpenAI", "Anthropic", "Gemini",
    "OpenCode Go", "Keychain", "Finder", "macOS", "Ed25519", "SHA-256", "JSON-RPC",
]
let systemSubroles: Set<String> = [
    "AXCloseButton", "AXMinimizeButton", "AXZoomButton", "AXFullScreenButton",
    "AXIncrementArrow", "AXDecrementArrow", "AXIncrementPage", "AXDecrementPage",
]
let interactiveRoles: Set<String> = [
    "AXButton", "AXTextField", "AXTextArea", "AXCheckBox", "AXPopUpButton",
    "AXMenuButton", "AXSlider", "AXRadioButton", "AXComboBox", "AXLink",
    "AXSegmentedControl", "AXDisclosureTriangle",
]

func attr(_ element: AXUIElement, _ key: String) -> AnyObject? {
    var value: AnyObject?
    guard AXUIElementCopyAttributeValue(element, key as CFString, &value) == .success else { return nil }
    return value
}

func string(_ element: AXUIElement, _ key: String) -> String? {
    if let value = attr(element, key) as? String, !value.isEmpty, value != " " { return value }
    return nil
}

func children(_ element: AXUIElement) -> [AXUIElement] {
    (attr(element, kAXChildrenAttribute) as? [AXUIElement]) ?? []
}

func windows(_ app: AXUIElement) -> [AXUIElement] {
    children(app).filter { string($0, kAXRoleAttribute) == "AXWindow" }
}

func normalized(_ text: String) -> String {
    String(String.UnicodeScalarView(text.unicodeScalars.filter {
        !(0x064B...0x065F).contains($0.value) && $0.value != 0x0670
    }))
}

var frenchValues = Set<String>()

func isLocalized(_ text: String) -> Bool {
    if frenchValues.contains(text) { return true }
    return text.unicodeScalars.contains { (0x00C0...0x017F).contains($0.value) }
}

func run(_ executable: String, _ arguments: [String]) {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: executable)
    process.arguments = arguments
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    try? process.run()
    process.waitUntilExit()
}

func key(_ code: CGKeyCode, flags: CGEventFlags) {
    let down = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true)!
    down.flags = flags
    let up = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false)!
    up.flags = flags
    down.post(tap: .cghidEventTap)
    up.post(tap: .cghidEventTap)
}

func activate(_ pid: pid_t) {
    NSRunningApplication(processIdentifier: pid)?.activate(options: [.activateAllWindows, .activateIgnoringOtherApps])
}

func setFrenchDefaults() {
    run("/usr/bin/defaults", ["write", bundleID, "AppleLanguages", "-array", "fr"])
    run("/usr/bin/defaults", ["write", bundleID, "AppleLocale", "-string", "fr_FR"])
    run("/usr/bin/killall", ["cfprefsd"])
    usleep(600_000)
}

guard AXIsProcessTrusted() else {
    print("ACCESSIBILITY_PERMISSION_REQUIRED")
    print("Grant Accessibility to your terminal in System Settings → Privacy & Security → Accessibility.")
    exit(3)
}

let catalogPath = "apps/macos/Talos/Localizable.xcstrings"
guard let catalogData = try? Data(contentsOf: URL(fileURLWithPath: catalogPath)),
      let catalog = try? JSONSerialization.jsonObject(with: catalogData) as? [String: Any],
      let strings = catalog["strings"] as? [String: Any] else {
    print("Could not read \(catalogPath). Run from the repository root.")
    exit(2)
}

func localizedValue(_ key: String) -> String? {
    guard let entry = strings[key] as? NSDictionary,
          let localizations = entry["localizations"] as? NSDictionary,
          let french = localizations["fr"] as? NSDictionary,
          let unit = french["stringUnit"] as? NSDictionary,
          let value = unit["value"] as? String else { return nil }
    return value
}

var englishKeys = Set<String>()
for key in strings.keys {
    guard let value = localizedValue(key) else { continue }
    frenchValues.insert(value)
    if value != key,
       key.rangeOfCharacter(from: CharacterSet.letters) != nil,
       !brandAllowlist.contains(key) {
        englishKeys.insert(key)
    }
}

func french(_ key: String) -> String {
    localizedValue(key) ?? key
}

let pageTitles = pageTitleKeys.map(french)

func restoreEnglish() {
    NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).forEach { $0.terminate() }
    for _ in 0..<30 where !NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).isEmpty {
        usleep(100_000)
    }
    NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).forEach { $0.forceTerminate() }
    usleep(500_000)
    for _ in 0..<3 {
        run("/usr/bin/defaults", ["delete", bundleID, "AppleLanguages"])
        run("/usr/bin/defaults", ["delete", bundleID, "AppleLocale"])
        usleep(200_000)
    }
    run("/usr/bin/killall", ["cfprefsd"])
    usleep(500_000)
    run("/usr/bin/open", ["-a", "\(FileManager.default.currentDirectoryPath)/apps/macos/.build/Build/Products/Debug/Talos.app"])
}

func finish(_ code: Int32) -> Never {
    restoreEnglish()
    exit(code)
}

NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).forEach { $0.terminate() }
usleep(700_000)
setFrenchDefaults()


let appPath = "\(FileManager.default.currentDirectoryPath)/apps/macos/.build/Build/Products/Debug/Talos.app"

func terminateApp() {
    NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).forEach { $0.terminate() }
    for _ in 0..<30 where !NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).isEmpty {
        usleep(100_000)
    }
    NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).forEach { $0.forceTerminate() }
    usleep(400_000)
}

func launchApp(surface: String? = nil) -> NSRunningApplication? {
    terminateApp()
    var arguments = ["-a", appPath]
    if let surface {
        arguments = ["--env", "TALOS_TEST_SURFACE=\(surface)", "-a", appPath]
    }
    run("/usr/bin/open", arguments)
    for _ in 0..<80 {
        usleep(250_000)
        if let app = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).first {
            return app
        }
    }
    return nil
}

guard let running = launchApp() else {
    print("APP_LAUNCH_FAILED")
    finish(1)
}
activate(running.processIdentifier)
var appElement: AXUIElement! = AXUIElementCreateApplication(running.processIdentifier)
usleep(1_000_000)

var violations: [Violation] = []
var visited = Set<CFHashCode>()

func label(_ element: AXUIElement) -> String? {
    string(element, kAXTitleAttribute)
        ?? string(element, kAXDescriptionAttribute)
        ?? string(element, kAXHelpAttribute)
        ?? string(element, "AXPlaceholderValue")
        ?? string(element, kAXValueAttribute)
}

func collect(_ root: AXUIElement, page: String) -> (localized: Int, english: Int, unlabeled: Int, labels: [String]) {
    visited.removeAll()
    var localized = 0
    var english = 0
    var unlabeled = 0
    var labels: [String] = []

    func walk(_ element: AXUIElement, _ depth: Int) {
        guard depth < 60 else { return }
        let hash = CFHash(element)
        if visited.contains(hash) { return }
        visited.insert(hash)
        let role = string(element, kAXRoleAttribute) ?? ""
        if ["AXMenuBar", "AXMenu", "AXMenuItem", "AXMenuBarItem"].contains(role) { return }

        if let text = label(element) {
            labels.append(text)
            if isLocalized(text) { localized += 1 }
            if englishKeys.contains(text) {
                english += 1
                violations.append(Violation(page: page, message: "English UI string: \(text)"))
            }
        }

        if interactiveRoles.contains(role) {
            let subrole = string(element, "AXSubrole") ?? ""
            if !systemSubroles.contains(subrole) && role != "AXDisclosureTriangle" {
                let name = string(element, kAXTitleAttribute)
                    ?? string(element, kAXDescriptionAttribute)
                    ?? string(element, kAXHelpAttribute)
                    ?? string(element, "AXPlaceholderValue")
                if name == nil {
                    unlabeled += 1
                    violations.append(Violation(page: page, message: "Unlabeled \(role)"))
                }
            }
        }
        for child in children(element) { walk(child, depth + 1) }
    }
    walk(root, 0)
    return (localized, english, unlabeled, labels)
}

// New task so the welcome screen is visible.
key(45, flags: .maskCommand) // ⌘N
usleep(1_500_000)
let mainWindows = windows(appElement)
if let main = mainWindows.first {
    let result = collect(main, page: "Main")
    let required = [
        french("What would you like to do?"),
        french("Send message"),
        french("Attach files"),
    ]
    let missing = required.filter { value in !result.labels.contains { $0.contains(value) } }
    if !missing.isEmpty {
        violations.append(Violation(page: "Main", message: "Missing French labels: \(missing.joined(separator: ", "))"))
    }
    print("MAIN french=\(result.localized) english=\(result.english) unlabeled=\(result.unlabeled)")
} else {
    violations.append(Violation(page: "Main", message: "No main window"))
}

// Menu bar titles and menu items.
func checkMenuBar() {
    guard let menuBar = children(appElement).first(where: { string($0, kAXRoleAttribute) == "AXMenuBar" }) else {
        violations.append(Violation(page: "Menu Bar", message: "No menu bar"))
        return
    }
    var localized = 0
    var english = 0
    var labels: [String] = []
    for item in children(menuBar).dropFirst() {
        let title = string(item, kAXTitleAttribute) ?? ""
        if isLocalized(title) { localized += 1 }
        if englishKeys.contains(title) {
            english += 1
            violations.append(Violation(page: "Menu Bar", message: "English menu title: \(title)"))
        }
        if !title.isEmpty { labels.append(title) }
        press(item)
        usleep(250_000)
        if let menu = findElement(item, matching: { string($0, kAXRoleAttribute) == "AXMenu" }) {
            for entry in findAllElements(menu, matching: { string($0, kAXRoleAttribute) == "AXMenuItem" }) {
                let name = string(entry, kAXTitleAttribute) ?? string(entry, kAXDescriptionAttribute) ?? ""
                if name.isEmpty { continue }
                labels.append(name)
                if isLocalized(name) { localized += 1 }
                if englishKeys.contains(name) {
                    english += 1
                    violations.append(Violation(page: "Menu Bar", message: "English menu item: \(name)"))
                }
            }
        }
        key(53, flags: [])
        usleep(150_000)
    }
    if localized < 3 {
        violations.append(Violation(page: "Menu Bar", message: "Too few French menu labels: \(localized)"))
    }
    print("MENUBAR french=\(localized) english=\(english) items=\(labels.count)")
}

checkMenuBar()

// Rename task from the Task menu.
if pressMenuItem(french("Rename Task…")) {
    usleep(800_000)
    checkSurface("Rename Task", required: [french("Rename Task"), french("Task name")])
    dismissSheet()
} else {
    violations.append(Violation(page: "Rename Task", message: "Could not open from the Task menu"))
}

// Delete task confirmation from the Task menu.
if pressMenuItem(french("Delete Task…")) {
    usleep(800_000)
    checkSurface("Delete Task", required: [
        french("Delete"),
        french("This removes the task and its messages. This cannot be undone."),
    ])
    dismissSheet()
} else {
    violations.append(Violation(page: "Delete Task", message: "Could not open from the Task menu"))
}

// Sheets and panels outside Settings. These are held-out surfaces: the
// Settings traversal below does not reach them, and the source scan cannot
// prove that a sheet actually renders French at runtime.
func mainWindow() -> AXUIElement? {
    windows(appElement).first { window in
        let title = string(window, kAXTitleAttribute) ?? ""
        return !pageTitles.contains { normalized($0) == normalized(title) }
    }
}

func findElement(_ root: AXUIElement, _ depth: Int = 0, matching: (AXUIElement) -> Bool) -> AXUIElement? {
    guard depth < 60 else { return nil }
    if matching(root) { return root }
    for child in children(root) {
        if let found = findElement(child, depth + 1, matching: matching) { return found }
    }
    return nil
}

func press(_ element: AXUIElement) {
    AXUIElementPerformAction(element, kAXPressAction as CFString)
}

func button(_ title: String) -> AXUIElement? {
    guard let window = mainWindow() else { return nil }
    return findElement(window) { element in
        guard string(element, kAXRoleAttribute) == "AXButton" else { return false }
        let name = string(element, kAXTitleAttribute)
            ?? string(element, kAXDescriptionAttribute)
            ?? string(element, kAXHelpAttribute)
        return name.map { normalized($0) == normalized(title) } ?? false
    }
}

func dismissSheet() {
    for title in [french("Done"), french("Close")] {
        if let target = button(title) {
            press(target)
            usleep(700_000)
            return
        }
    }
    key(53, flags: []) // esc
    usleep(700_000)
}

func checkSurface(_ name: String, required: [String]) {
    guard let window = mainWindow() else {
        violations.append(Violation(page: name, message: "No main window for surface"))
        return
    }
    let result = collect(window, page: name)
    let missing = required.filter { value in !result.labels.contains { $0.contains(value) } }
    if !missing.isEmpty {
        violations.append(Violation(page: name, message: "Missing French labels: \(missing.joined(separator: ", "))"))
    }
    if result.localized < 3 {
        violations.append(Violation(page: name, message: "Too few French labels: \(result.localized)"))
    }
    print("SURFACE \(name) french=\(result.localized) english=\(result.english) unlabeled=\(result.unlabeled)")
}

func menuItem(matching title: String) -> (AXUIElement) -> Bool {
    return { element in
        guard string(element, kAXRoleAttribute) == "AXMenuItem" else { return false }
        let name = string(element, kAXTitleAttribute) ?? string(element, kAXDescriptionAttribute) ?? ""
        return normalized(name).contains(normalized(title))
    }
}

func pressMenuItem(_ title: String) -> Bool {
    // Toolbar menus (the project menu owns New Worktree).
    if let window = mainWindow() {
        for menu in findAllElements(window, matching: { string($0, kAXRoleAttribute) == "AXMenuButton" }) {
            press(menu)
            usleep(450_000)
            if let item = findElement(appElement, matching: menuItem(matching: title)) {
                press(item)
                return true
            }
            key(53, flags: [])
            usleep(200_000)
        }
    }
    // Menu bar commands (Help lives here).
    guard let menuBar = children(appElement).first(where: { string($0, kAXRoleAttribute) == "AXMenuBar" }) else {
        return false
    }
    for barItem in children(menuBar) {
        press(barItem)
        usleep(350_000)
        if let item = findElement(appElement, matching: menuItem(matching: title)) {
            press(item)
            return true
        }
        key(53, flags: [])
        usleep(200_000)
    }
    return false
}

func findAllElements(_ root: AXUIElement, _ depth: Int = 0, matching: (AXUIElement) -> Bool) -> [AXUIElement] {
    guard depth < 60 else { return [] }
    var found: [AXUIElement] = matching(root) ? [root] : []
    for child in children(root) {
        found.append(contentsOf: findAllElements(child, depth + 1, matching: matching))
    }
    return found
}

// Quick Switcher (⌘K)
key(40, flags: .maskCommand)
usleep(900_000)
checkSurface("Quick Switcher", required: [
        french("New Task"), french("Review Changes"), french("Settings"),
    ])
dismissSheet()

// Help sheet through the Help menu.
if pressMenuItem(french("Talos Help")) {
    usleep(900_000)
    checkSurface("Help", required: [french("Getting started")])
    dismissSheet()
} else {
    violations.append(Violation(page: "Help", message: "Could not open from the Help menu"))
}

// Project browser (⌘⇧O)
key(31, flags: [.maskCommand, .maskShift])
usleep(1_200_000)
checkSurface("Project Browser", required: [french("New File")])
dismissSheet()

// Git panel (⌘⇧G)
key(5, flags: [.maskCommand, .maskShift])
usleep(1_000_000)
checkSurface("Git Panel", required: [french("Commit"), french("Refresh")])
dismissSheet()

// Dialogs that have no normal UI trigger in a fresh launch. The app exposes a
// DEBUG-only TALOS_TEST_SURFACE seam for these deterministic checks.
func dismissElement(_ element: AXUIElement) {
    for title in [french("Skip"), french("Deny"), french("Cancel"), french("Close"), french("Done")] {
        if let target = findElement(element, matching: { candidate in
            guard string(candidate, kAXRoleAttribute) == "AXButton" else { return false }
            let name = string(candidate, kAXTitleAttribute)
                ?? string(candidate, kAXDescriptionAttribute)
                ?? string(candidate, kAXHelpAttribute)
            return name.map { normalized($0) == normalized(title) } ?? false
        }) {
            press(target)
            usleep(600_000)
            return
        }
    }
    key(53, flags: [])
    usleep(500_000)
}

func checkHookedSurface(_ key: String, name: String, required: [String]) {
    guard let app = launchApp(surface: key) else {
        violations.append(Violation(page: name, message: "App launch failed"))
        return
    }
    activateIfNeeded(app)
    let element = AXUIElementCreateApplication(app.processIdentifier)
    usleep(1_200_000)
    guard let window = windows(element).first else {
        violations.append(Violation(page: name, message: "No window"))
        terminateApp()
        return
    }
    let result = collect(window, page: name)
    let missing = required.filter { value in !result.labels.contains { $0.contains(value) } }
    if !missing.isEmpty {
        violations.append(Violation(page: name, message: "Missing French labels: \(missing.joined(separator: ", "))"))
    }
    if result.localized < 2 {
        violations.append(Violation(page: name, message: "Too few French labels: \(result.localized)"))
    }
    print("SURFACE \(name) french=\(result.localized) english=\(result.english) unlabeled=\(result.unlabeled)")
    dismissElement(element)
    terminateApp()
}

func activateIfNeeded(_ app: NSRunningApplication) {
    NSRunningApplication(processIdentifier: app.processIdentifier)?
        .activate(options: [.activateAllWindows, .activateIgnoringOtherApps])
}

checkHookedSurface("approval", name: "Approval Sheet", required: [
        french("Allow Once"), french("Deny"),
    ])
checkHookedSurface("elicitation", name: "MCP Elicitation", required: [
        french("Decline"), french("Continue"),
    ])
checkHookedSurface("sampling", name: "MCP Sampling", required: [
        french("Decline"), french("Allow for This Session"),
    ])
checkHookedSurface("recent", name: "Recent Documents", required: [french("Recent Documents")])
checkHookedSurface("provider", name: "Provider Editor", required: [french("Credential")])
checkHookedSurface("hooktrust", name: "Hook Trust", required: [
        french("Trust this hook?"), french("Skip"),
    ])
checkHookedSurface("mcpapp", name: "MCP App", required: [
        french("Reload"), french("Close"),
    ])

// Restore the normal app for the Settings traversal.
guard let relaunched = launchApp() else {
    violations.append(Violation(page: "Settings", message: "Relaunch failed"))
    finish(1)
}
activateIfNeeded(relaunched)
appElement = AXUIElementCreateApplication(relaunched.processIdentifier)
usleep(1_200_000)

// Worktree sheet through the project menu.
if pressMenuItem(french("New Worktree…")) {
    usleep(1_000_000)
    checkSurface("Worktree Sheet", required: [
        french("Worktree name"), french("Create Worktree"),
    ])
    dismissSheet()
} else {
    violations.append(Violation(page: "Worktree Sheet", message: "Could not open from the project menu"))
}

// Open Settings.
key(43, flags: .maskCommand) // ⌘,
usleep(1_500_000)

func settingsWindow() -> AXUIElement? {
    windows(appElement).first { window in
        guard let title = string(window, kAXTitleAttribute) else { return false }
        let normalizedTitle = normalized(title)
        return pageTitles.contains { normalized($0) == normalizedTitle }
    }
}

guard let settings = settingsWindow(),
      let settingsTitle = string(settings, kAXTitleAttribute) else {
    print("SETTINGS_WINDOW_NOT_FOUND")
    violations.append(Violation(page: "Settings", message: "Settings window not found"))
    for violation in violations { print("  [\(violation.page)] \(violation.message)") }
    finish(1)
}

for page in pageTitles {
    visited.removeAll()
    var foundRow: AXUIElement?
    func findRow(_ element: AXUIElement, _ depth: Int) {
        guard depth < 50, foundRow == nil else { return }
        let hash = CFHash(element)
        if visited.contains(hash) { return }
        visited.insert(hash)
        let role = string(element, kAXRoleAttribute) ?? ""
        let text = string(element, kAXValueAttribute) ?? string(element, kAXTitleAttribute) ?? ""
        if normalized(text) == normalized(page) {
            var current: AXUIElement? = element
            for _ in 0..<8 {
                guard let node = current else { break }
                if string(node, kAXRoleAttribute) == "AXRow" {
                    foundRow = node
                    break
                }
                if let parent = attr(node, kAXParentAttribute) {
                    current = unsafeBitCast(parent, to: AXUIElement.self)
                } else { break }
            }
        }
        for child in children(element) { findRow(child, depth + 1) }
    }
    findRow(appElement, 0)
    guard let row = foundRow else {
        violations.append(Violation(page: page, message: "Settings row not found"))
        continue
    }
    AXUIElementSetAttributeValue(row, kAXSelectedAttribute as CFString, kCFBooleanTrue)
    AXUIElementPerformAction(row, kAXPressAction as CFString)
    usleep(900_000)

    guard let current = settingsWindow() else {
        violations.append(Violation(page: page, message: "Settings window disappeared"))
        continue
    }
    let result = collect(current, page: page)
    let title = string(current, kAXTitleAttribute) ?? ""
    if normalized(title) != normalized(page) {
        violations.append(Violation(page: page, message: "Page did not switch (title: \(title))"))
    }
    if result.localized < 2 {
        violations.append(Violation(page: page, message: "Too few French labels: \(result.localized)"))
    }
    print("PAGE \(page) french=\(result.localized) english=\(result.english) unlabeled=\(result.unlabeled)")
}

print("SETTINGS_START_PAGE: \(settingsTitle)")
if violations.isEmpty {
    print("FAILURES: 0")
    finish(0)
}
print("FAILURES: \(violations.count)")
for violation in violations {
    print("  [\(violation.page)] \(violation.message)")
}
finish(1)
