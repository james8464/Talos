import AppKit
import ApplicationServices
import Foundation

// Live Arabic localization regression for Talos.
//
// Usage (from the repository root):
//   make localization-eval
//
// Requires Accessibility permission for the terminal. Launches the built app
// with AppleLanguages=ar, creates a new task, opens Settings, visits every
// Settings page, and fails on app-owned English UI strings, missing Arabic
// labels, or unlabeled interactive controls. Restores English on exit.

struct Violation {
    let page: String
    let message: String
}

let bundleID = "app.talos.macos"
let pageTitles = [
    "عام", "المشاريع", "النماذج", "المزوّدون", "الأدوات", "التكوينات",
    "الخطافات", "الذاكرة", "السجل", "السياسة", "مهام السحابة", "خادم التطبيق",
    "المهارات", "الإضافات", "MCP", "التحكم عن بعد", "المظهر", "الاستخدام",
    "متقدم", "حول",
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

func containsArabic(_ text: String) -> Bool {
    text.unicodeScalars.contains { (0x0600...0x06FF).contains($0.value) }
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

func setArabicDefaults() {
    run("/usr/bin/defaults", ["write", bundleID, "AppleLanguages", "-array", "ar"])
    run("/usr/bin/defaults", ["write", bundleID, "AppleLocale", "-string", "ar_SA"])
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

var englishKeys = Set<String>()
for (key, raw) in strings {
    guard let entry = raw as? [String: Any],
          let localizations = entry["localizations"] as? [String: Any],
          let arabic = localizations["ar"] as? [String: Any],
          let unit = arabic["stringUnit"] as? [String: Any],
          let value = unit["value"] as? String,
          containsArabic(value),
          key.rangeOfCharacter(from: CharacterSet.letters) != nil,
          !brandAllowlist.contains(key) else { continue }
    englishKeys.insert(key)
}

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
setArabicDefaults()


run("/usr/bin/open", ["-a", "\(FileManager.default.currentDirectoryPath)/apps/macos/.build/Build/Products/Debug/Talos.app"])

var running: NSRunningApplication?
for _ in 0..<80 {
    usleep(250_000)
    if let app = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).first {
        running = app
        break
    }
}
guard let running else {
    print("APP_LAUNCH_FAILED")
    finish(1)
}
activate(running.processIdentifier)
let appElement = AXUIElementCreateApplication(running.processIdentifier)
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

func collect(_ root: AXUIElement, page: String) -> (arabic: Int, english: Int, unlabeled: Int, labels: [String]) {
    visited.removeAll()
    var arabic = 0
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
            if containsArabic(text) { arabic += 1 }
            if englishKeys.contains(text) {
                english += 1
                violations.append(Violation(page: page, message: "English UI string: \(text)"))
            }
        }

        if interactiveRoles.contains(role) {
            let subrole = string(element, "AXSubrole") ?? ""
            if !systemSubroles.contains(subrole) {
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
    return (arabic, english, unlabeled, labels)
}

// New task so the welcome screen is visible.
key(45, flags: .maskCommand) // ⌘N
usleep(1_500_000)
let mainWindows = windows(appElement)
if let main = mainWindows.first {
    let result = collect(main, page: "Main")
    let required = ["ماذا تريد أن تفعل؟", "إرسال الرسالة", "إرفاق ملفات", "بحث في المهام"]
    let missing = required.filter { value in !result.labels.contains { $0.contains(value) } }
    if !missing.isEmpty {
        violations.append(Violation(page: "Main", message: "Missing Arabic labels: \(missing.joined(separator: ", "))"))
    }
    print("MAIN arabic=\(result.arabic) english=\(result.english) unlabeled=\(result.unlabeled)")
} else {
    violations.append(Violation(page: "Main", message: "No main window"))
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
    if result.arabic < 2 {
        violations.append(Violation(page: page, message: "Too few Arabic labels: \(result.arabic)"))
    }
    print("PAGE \(page) arabic=\(result.arabic) english=\(result.english) unlabeled=\(result.unlabeled)")
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
