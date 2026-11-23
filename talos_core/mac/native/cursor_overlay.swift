// Talos's own cursor overlay. Draws a cyan target and label above all windows
// without taking focus or mouse events. Reads newline-delimited JSON on stdin:
//   {"x":100,"y":200,"label":"Talos","visible":true}
// Coordinates use the top-left origin used by CGEvent/screencapture.

import Cocoa

final class CursorView: NSView {
    let dot = NSView()
    let label = NSTextField(labelWithString: "TALOS")

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        dot.wantsLayer = true
        dot.layer?.backgroundColor = NSColor.systemCyan.cgColor
        dot.layer?.cornerRadius = 8
        dot.frame = NSRect(x: 0, y: 4, width: 16, height: 16)
        addSubview(dot)
        label.font = NSFont.systemFont(ofSize: 11, weight: .semibold)
        label.textColor = .systemCyan
        label.drawsBackground = false
        label.isBezeled = false
        label.frame = NSRect(x: 22, y: 3, width: 132, height: 18)
        addSubview(label)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func update(label text: String) {
        label.stringValue = text
        needsDisplay = true
    }
}

final class OverlayWindow: NSWindow {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

let width: CGFloat = 160
let height: CGFloat = 26
let window = OverlayWindow(
    contentRect: NSRect(x: 0, y: 0, width: width, height: height),
    styleMask: .borderless,
    backing: .buffered,
    defer: false
)
window.level = .screenSaver
window.backgroundColor = .clear
window.isOpaque = false
window.hasShadow = false
window.ignoresMouseEvents = true
window.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
let view = CursorView(frame: NSRect(x: 0, y: 0, width: width, height: height))
window.contentView = view
window.orderFrontRegardless()

func move(x: CGFloat, y: CGFloat, label: String) {
    let screen = NSScreen.screens.first
    let top = screen?.frame.origin.y ?? 0
    let total = screen?.frame.height ?? 0
    // Input is top-left origin; NSWindow is bottom-left origin.
    let originY = top + total - y - height
    window.setFrameOrigin(NSPoint(x: x - 11, y: originY))
    view.update(label: label)
    view.needsDisplay = true
    window.orderFrontRegardless()
}

let handle = FileHandle.standardInput
handle.readabilityHandler = { stream in
    let data = stream.availableData
    if data.isEmpty {
        DispatchQueue.main.async { app.terminate(nil) }
        return
    }
    for line in String(decoding: data, as: UTF8.self).split(separator: "\n") {
        guard let object = try? JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any]
        else { continue }
        DispatchQueue.main.async {
            if object["visible"] as? Bool == false {
                window.orderOut(nil)
                return
            }
            let x = (object["x"] as? NSNumber)?.doubleValue ?? 100
            let y = (object["y"] as? NSNumber)?.doubleValue ?? 100
            move(x: CGFloat(x), y: CGFloat(y), label: object["label"] as? String ?? "Talos")
        }
    }
}

app.run()
