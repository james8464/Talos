// AXAudit checks app-owned interactive controls for accessible labels.
//
// Usage:
//   swiftc apps/macos/Tools/AXAudit.swift -o /tmp/talos-ax-audit
//   /tmp/talos-ax-audit <pid of the running Talos app>
//
// It skips the menu bar and system window/scrollbar chrome, then reports any
// interactive control without AXTitle, AXDescription, AXHelp, or a placeholder.
// Requires Accessibility permission for the calling terminal.

import Foundation
import ApplicationServices

guard CommandLine.arguments.count > 1, let pid = Int32(CommandLine.arguments[1]) else {
    print("usage: ax-audit <pid>")
    exit(2)
}
guard AXIsProcessTrusted() else {
    print("Accessibility permission is required: System Settings → Privacy & Security → Accessibility.")
    exit(3)
}

let application = AXUIElementCreateApplication(pid)

func attribute(_ element: AXUIElement, _ key: String) -> AnyObject? {
    var value: AnyObject?
    guard AXUIElementCopyAttributeValue(element, key as CFString, &value) == .success else { return nil }
    return value
}

func text(_ element: AXUIElement, _ key: String) -> String? {
    if let value = attribute(element, key) as? String, !value.isEmpty, value != " " {
        return value
    }
    return nil
}

func children(_ element: AXUIElement) -> [AXUIElement] {
    (attribute(element, kAXChildrenAttribute) as? [AXUIElement]) ?? []
}

let interactiveRoles: Set<String> = [
    "AXButton", "AXTextField", "AXTextArea", "AXCheckBox", "AXPopUpButton",
    "AXSlider", "AXMenuButton", "AXRadioButton", "AXComboBox", "AXLink",
    "AXSegmentedControl", "AXDisclosureTriangle", "AXTabGroup",
]

let systemSubroles: Set<String> = [
    "AXCloseButton", "AXMinimizeButton", "AXZoomButton", "AXFullScreenButton",
    "AXIncrementArrow", "AXDecrementArrow", "AXIncrementPage", "AXDecrementPage",
]

func parentChain(_ element: AXUIElement) -> String {
    var parts: [String] = []
    var current: AXUIElement? = element
    var depth = 0
    while let node = current, depth < 4 {
        let role = text(node, kAXRoleAttribute) ?? "?"
        let title = text(node, kAXTitleAttribute) ?? text(node, kAXDescriptionAttribute) ?? ""
        let shortTitle = title.count > 40 ? String(title.prefix(40)) + "…" : title
        parts.append(shortTitle.isEmpty ? role : "\(role)[\(shortTitle)]")
        if let parent = attribute(node, kAXParentAttribute) {
            current = unsafeBitCast(parent, to: AXUIElement.self)
        } else {
            current = nil
        }
        depth += 1
    }
    return parts.joined(separator: " < ")
}

var visited = Set<CFHashCode>()
var controls = 0
var unlabeled: [String] = []

func walk(_ element: AXUIElement, depth: Int) {
    guard depth < 60 else { return }
    let hash = CFHash(element)
    if visited.contains(hash) { return }
    visited.insert(hash)

    let role = text(element, kAXRoleAttribute) ?? ""
    if role == "AXMenuBar" || role == "AXMenu" || role == "AXMenuItem" || role == "AXMenuBarItem" {
        return
    }

    if interactiveRoles.contains(role) {
        let subrole = text(element, "AXSubrole") ?? ""
        if !systemSubroles.contains(subrole) {
            controls += 1
            let label = text(element, kAXTitleAttribute)
                ?? text(element, kAXDescriptionAttribute)
                ?? text(element, kAXHelpAttribute)
                ?? text(element, "AXPlaceholderValue")
            if label == nil {
                let identifier = text(element, "AXIdentifier") ?? "-"
                unlabeled.append("\(role) id=\(identifier) in \(parentChain(element))")
            }
        }
    }

    for child in children(element) {
        walk(child, depth: depth + 1)
    }
}

walk(application, depth: 0)
print("APP_CONTROLS: \(controls)")
print("UNLABELED: \(unlabeled.count)")
for row in unlabeled.prefix(50) {
    print("  \(row)")
}
exit(unlabeled.isEmpty ? 0 : 1)
