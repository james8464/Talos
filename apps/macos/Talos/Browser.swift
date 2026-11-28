import Foundation
import AppKit
import ApplicationServices
import CoreGraphics
import Vision

struct BrowserElement {
    let index: Int
    let role: String
    let label: String
    let value: String
    let frame: CGRect
    let element: AXUIElement
}

@MainActor
final class BrowserController {
    static let shared = BrowserController()

    private var elements: [BrowserElement] = []
    private var ownerPID: pid_t = 0
    private var ownerName = "Browser"

    func read() -> ToolResult {
        guard let (app, window) = browserWindow() else {
            return .fail("No Safari or Chrome window is available.")
        }
        ownerPID = app.processIdentifier
        ownerName = app.localizedName ?? "Browser"
        elements = []

        let title = attribute(window, kAXTitleAttribute) as? String ?? ""
        let url = documentURL(window) ?? ""
        var pageText: [String] = []
        var nodeCount = 0

        walk(window, depth: 0, nodeCount: &nodeCount) { element, role, label, value, frame in
            if ["AXStaticText", "AXHeading", "AXParagraph"].contains(role),
               !label.isEmpty,
               pageText.count < 400 {
                pageText.append(label)
            }
            if isInteractive(role), !label.isEmpty, elements.count < 300 {
                let index = elements.count
                elements.append(BrowserElement(
                    index: index,
                    role: role.replacingOccurrences(of: "AX", with: ""),
                    label: label,
                    value: value,
                    frame: frame,
                    element: element
                ))
            }
        }

        let text = pageText
            .joined(separator: "\n")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let clipped = text.count > 18_000 ? String(text.prefix(18_000)) + "\n…" : text
        let elementLines = elements.map {
            "[\($0.index)] \($0.role) \"\($0.label)\" value=\"\($0.value)\" @ \(Int($0.frame.origin.x)),\(Int($0.frame.origin.y))"
        }.joined(separator: "\n")

        return .ok("""
        Browser: \(ownerName)
        URL: \(url.isEmpty ? "unknown" : url)
        Title: \(title)

        Interactive elements (call browser read again if the page changes):
        \(elementLines.isEmpty ? "No interactive elements found." : elementLines)

        Page text:
        \(clipped.isEmpty ? "No page text extracted. Use action screenshot for OCR." : clipped)
        """)
    }

    func click(index: Int) -> ToolResult {
        guard index >= 0, index < elements.count else {
            return .fail("Element \(index) is not in the last browser snapshot. Run browser read first.")
        }
        let element = elements[index].element
        let result = AXUIElementPerformAction(element, kAXPressAction as CFString)
        if result == .success {
            return .ok("Clicked [\(index)] \(elements[index].label).")
        }
        AXUIElementSetAttributeValue(element, kAXFocusedAttribute as CFString, kCFBooleanTrue)
        return .fail("The browser did not expose a press action for [\(index)]. Use browser screenshot and computer click coordinates.")
    }

    func type(index: Int, text: String) -> ToolResult {
        guard index >= 0, index < elements.count else {
            return .fail("Element \(index) is not in the last browser snapshot. Run browser read first.")
        }
        guard ToolPermission.accessibility else {
            ToolPermission.requestAccessibility()
            return .fail("Accessibility is not enabled for Talos.")
        }
        let element = elements[index].element
        let direct = AXUIElementSetAttributeValue(element, kAXValueAttribute as CFString, text as CFString)
        if direct == .success {
            return .ok("Filled [\(index)] \(elements[index].label).")
        }

        AXUIElementSetAttributeValue(element, kAXFocusedAttribute as CFString, kCFBooleanTrue)
        let pasteboard = NSPasteboard.general
        let previous = pasteboard.string(forType: .string)
        pasteboard.clearContents()
        pasteboard.setString(text, forType: .string)
        postKey(9, flags: .maskCommand)
        if let previous {
            pasteboard.clearContents()
            pasteboard.setString(previous, forType: .string)
        }
        return .ok("Focused [\(index)] and typed \(text.count) characters.")
    }

    func navigate(to url: URL) async -> ToolResult {
        guard let (app, _) = browserWindow() else {
            return .fail("No Safari or Chrome window is available.")
        }
        guard let appURL = NSWorkspace.shared.urlForApplication(withBundleIdentifier: app.bundleIdentifier ?? "") else {
            NSWorkspace.shared.open(url)
            return .ok("Opened \(url.absoluteString) in the default browser.")
        }
        let configuration = NSWorkspace.OpenConfiguration()
        await withCheckedContinuation { continuation in
            NSWorkspace.shared.open([url], withApplicationAt: appURL, configuration: configuration) { _, _ in
                continuation.resume()
            }
        }
        try? await Task.sleep(nanoseconds: 800_000_000)
        return .ok("Opened \(url.absoluteString) in \(ownerName).")
    }

    func screenshot() async -> ToolResult {
        guard ToolPermission.screenRecording else {
            ToolPermission.requestScreenRecording()
            return .fail("Screen Recording is not enabled for Talos.")
        }
        guard let (app, window) = browserWindow() else {
            return .fail("No Safari or Chrome window is available.")
        }
        ownerName = app.localizedName ?? "Browser"
        guard let frame = frame(of: window) else {
            return .fail("Could not read the browser window bounds.")
        }
        let path = FileManager.default.temporaryDirectory
            .appendingPathComponent("talos-browser-\(UUID().uuidString).png")
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
        process.arguments = [
            "-x",
            "-R", "\(Int(frame.origin.x)),\(Int(frame.origin.y)),\(Int(frame.width)),\(Int(frame.height))",
            path.path,
        ]
        do {
            try process.run()
        } catch {
            return .fail(error.localizedDescription)
        }
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            return .fail("Browser screenshot failed.")
        }
        let ocr = await recognizeText(at: path)
        return .ok("Browser screenshot saved to \(path.path).\n\nPage text from screenshot:\n\(ocr)", attachment: path.path)
    }

    private static let browserBundleIDs = [
        "com.apple.Safari",
        "com.apple.SafariTechnologyPreview",
        "com.google.Chrome",
        "com.google.Chrome.canary",
        "org.chromium.Chromium",
        "company.thebrowser.Browser",
        "com.microsoft.edgemac",
        "org.mozilla.firefox",
    ]

    func isRunning() -> Bool {
        !runningBrowsers().isEmpty
    }

    private func runningBrowsers() -> [NSRunningApplication] {
        let known = Self.browserBundleIDs.flatMap {
            NSRunningApplication.runningApplications(withBundleIdentifier: $0)
        }
        let others = NSWorkspace.shared.runningApplications.filter(isBrowser)
        var seen = Set<pid_t>()
        return (known + others).filter { seen.insert($0.processIdentifier).inserted }
    }

    private func browserWindow() -> (NSRunningApplication, AXUIElement)? {
        let front = NSWorkspace.shared.frontmostApplication
        let browsers = runningBrowsers()
        guard !browsers.isEmpty else { return nil }
        let ordered = browsers.sorted { lhs, rhs in
            if lhs.processIdentifier == front?.processIdentifier { return true }
            if rhs.processIdentifier == front?.processIdentifier { return false }
            return lhs.isActive && !rhs.isActive
        }
        for app in ordered {
            let axApp = AXUIElementCreateApplication(app.processIdentifier)
            var focused: CFTypeRef?
            if AXUIElementCopyAttributeValue(axApp, kAXFocusedWindowAttribute as CFString, &focused) == .success,
               let focused {
                return (app, unsafeBitCast(focused, to: AXUIElement.self))
            }
            if let window = (attribute(axApp, kAXWindowsAttribute) as? [AXUIElement])?.first {
                return (app, window)
            }
        }
        return nil
    }

    private func isBrowser(_ app: NSRunningApplication) -> Bool {
        let id = app.bundleIdentifier?.lowercased() ?? ""
        return id.contains("safari")
            || id.contains("google.chrome")
            || id.contains("chromium")
            || id.contains("thebrowser")
            || id.contains("edgemac")
            || id.contains("firefox")
    }

    private func documentURL(_ element: AXUIElement) -> String? {
        if let direct = urlAttribute(element) { return direct }
        var found: String?
        var nodeCount = 0
        walk(element, depth: 0, nodeCount: &nodeCount) { node, role, _, _, _ in
            if found == nil, role == "AXWebArea" {
                found = urlAttribute(node)
            }
        }
        return found
    }

    private func urlAttribute(_ element: AXUIElement) -> String? {
        for key in [kAXDocumentAttribute, "AXURL"] {
            let raw = attribute(element, key)
            if let value = raw as? String, !value.isEmpty { return value }
            if let value = raw as? URL, !value.absoluteString.isEmpty { return value.absoluteString }
            if let value = raw as? NSURL, !(value.absoluteString ?? "").isEmpty { return value.absoluteString }
        }
        return nil
    }

    private func walk(
        _ element: AXUIElement,
        depth: Int,
        nodeCount: inout Int,
        visit: (AXUIElement, String, String, String, CGRect) -> Void
    ) {
        guard depth < 32, nodeCount < 7_000 else { return }
        nodeCount += 1
        let role = attribute(element, kAXRoleAttribute) as? String ?? ""
        let title = attribute(element, kAXTitleAttribute) as? String ?? ""
        let description = attribute(element, kAXDescriptionAttribute) as? String ?? ""
        let value = attribute(element, kAXValueAttribute) as? String ?? ""
        let placeholder = attribute(element, "AXPlaceholderValue") as? String ?? ""
        let label = [title, description, value, placeholder]
            .first { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        visit(element, role, label, value, frame(of: element) ?? .zero)
        for child in children(of: element) {
            walk(child, depth: depth + 1, nodeCount: &nodeCount, visit: visit)
        }
    }

    private func isInteractive(_ role: String) -> Bool {
        [
            "AXButton", "AXLink", "AXTextField", "AXTextArea", "AXSearchField",
            "AXComboBox", "AXPopUpButton", "AXCheckBox", "AXRadioButton", "AXMenuItem",
        ].contains(role)
    }

    private func children(of element: AXUIElement) -> [AXUIElement] {
        (attribute(element, kAXChildrenAttribute) as? [AXUIElement]) ?? []
    }

    private func attribute(_ element: AXUIElement, _ name: String) -> AnyObject? {
        var value: CFTypeRef?
        return AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success ? value : nil
    }

    private func frame(of element: AXUIElement) -> CGRect? {
        guard let positionRaw = attribute(element, kAXPositionAttribute),
              let sizeRaw = attribute(element, kAXSizeAttribute),
              CFGetTypeID(positionRaw) == AXValueGetTypeID(),
              CFGetTypeID(sizeRaw) == AXValueGetTypeID() else {
            return nil
        }
        var point = CGPoint.zero
        var size = CGSize.zero
        let position = unsafeBitCast(positionRaw, to: AXValue.self)
        let sizeValue = unsafeBitCast(sizeRaw, to: AXValue.self)
        guard AXValueGetValue(position, .cgPoint, &point),
              AXValueGetValue(sizeValue, .cgSize, &size) else {
            return nil
        }
        return CGRect(origin: point, size: size)
    }

    private func postKey(_ code: CGKeyCode, flags: CGEventFlags) {
        let source = CGEventSource(stateID: .hidSystemState)
        let down = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: true)
        down?.flags = flags
        down?.post(tap: .cghidEventTap)
        let up = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: false)
        up?.flags = flags
        up?.post(tap: .cghidEventTap)
    }

    private func recognizeText(at url: URL) async -> String {
        await Task.detached(priority: .userInitiated) {
            let request = VNRecognizeTextRequest()
            request.recognitionLevel = .accurate
            request.usesLanguageCorrection = true
            let handler = VNImageRequestHandler(url: url, options: [:])
            do {
                try handler.perform([request])
            } catch {
                return "OCR failed: \(error.localizedDescription)"
            }
            let lines = request.results?.compactMap { $0.topCandidates(1).first?.string } ?? []
            let text = lines.joined(separator: "\n")
            return text.count > 12_000 ? String(text.prefix(12_000)) + "\n…" : text
        }.value
    }
}
