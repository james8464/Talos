// Talos control overlay. Draws an agent cursor, click ripple, and a
// "Talos is controlling this Mac" indicator above all windows. It is
// click-through, never takes focus, and reads newline-delimited JSON on stdin:
//   {"x":100,"y":200,"label":"click","visible":true,"control":true}
//   {"click":true,"x":100,"y":200}
//   {"visible":false,"control":false}
// Coordinates use the top-left origin used by CGEvent/screencapture.

import Cocoa
import QuartzCore

final class OverlayWindow: NSWindow {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

final class CursorView: NSView {
    private let pill = NSView()
    private let label = NSTextField(labelWithString: "TALOS")

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        pill.wantsLayer = true
        pill.layer?.backgroundColor = NSColor.black.withAlphaComponent(0.74).cgColor
        pill.layer?.cornerRadius = 12
        pill.frame = NSRect(x: 22, y: 4, width: 204, height: 24)
        addSubview(pill)
        label.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .semibold)
        label.textColor = .white
        label.drawsBackground = false
        label.isBezeled = false
        label.lineBreakMode = .byTruncatingTail
        label.frame = NSRect(x: 30, y: 7, width: 188, height: 16)
        pill.addSubview(label)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func update(label text: String) {
        label.stringValue = text
        needsDisplay = true
    }

    override func draw(_ dirtyRect: NSRect) {
        let arrow = NSBezierPath()
        arrow.move(to: NSPoint(x: 3, y: 27))
        arrow.line(to: NSPoint(x: 3, y: 7))
        arrow.line(to: NSPoint(x: 17, y: 18))
        arrow.close()
        NSColor.systemCyan.setFill()
        arrow.fill()
        NSColor.white.setStroke()
        arrow.lineWidth = 1.1
        arrow.stroke()
        let ring = NSBezierPath(ovalIn: NSRect(x: 8, y: 11, width: 11, height: 11))
        NSColor.systemCyan.withAlphaComponent(0.9).setStroke()
        ring.lineWidth = 2
        ring.stroke()
    }
}

final class ControlView: NSView {
    private let pill = NSView()
    private let label = NSTextField(labelWithString: "●  TALOS IS CONTROLLING THIS MAC")
    private var highlightLayer: CAShapeLayer?

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.borderWidth = 2
        layer?.borderColor = NSColor.systemCyan.withAlphaComponent(0.62).cgColor
        layer?.cornerRadius = 14
        pill.wantsLayer = true
        pill.layer?.backgroundColor = NSColor.black.withAlphaComponent(0.72).cgColor
        pill.layer?.cornerRadius = 14
        label.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .semibold)
        label.textColor = .systemCyan
        label.alignment = .center
        label.drawsBackground = false
        label.isBezeled = false
        pill.addSubview(label)
        addSubview(pill)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func layout() {
        super.layout()
        let width = min(380, max(220, bounds.width - 40))
        pill.frame = NSRect(x: (bounds.width - width) / 2, y: bounds.height - 54,
                            width: width, height: 30)
        label.frame = NSRect(x: 12, y: 7, width: width - 24, height: 16)
    }

    func setActive(_ active: Bool) {
        isHidden = !active
        needsLayout = true
    }

    func highlight(_ rect: NSRect, label text: String) {
        highlightLayer?.removeFromSuperlayer()
        let shape = CAShapeLayer()
        shape.frame = bounds
        let path = NSBezierPath(roundedRect: rect.insetBy(dx: -4, dy: -4),
                                xRadius: 12, yRadius: 12)
        shape.path = path.cgPath
        shape.fillColor = NSColor.systemCyan.withAlphaComponent(0.08).cgColor
        shape.strokeColor = NSColor.systemCyan.cgColor
        shape.lineWidth = 3
        layer?.addSublayer(shape)
        highlightLayer = shape
        label.stringValue = "●  " + text.uppercased()
    }

    func clearHighlight() {
        highlightLayer?.removeFromSuperlayer()
        highlightLayer = nil
        label.stringValue = "●  TALOS IS CONTROLLING THIS MAC"
    }

    func ripple(at point: NSPoint) {
        let size: CGFloat = 48
        let layer = CAShapeLayer()
        layer.path = CGPath(ellipseIn: CGRect(x: -size / 2, y: -size / 2,
                                              width: size, height: size), transform: nil)
        layer.fillColor = NSColor.clear.cgColor
        layer.strokeColor = NSColor.systemCyan.cgColor
        layer.lineWidth = 3
        layer.position = point
        self.layer?.addSublayer(layer)
        let scale = CABasicAnimation(keyPath: "transform.scale")
        scale.fromValue = 0.25
        scale.toValue = 1.8
        let fade = CABasicAnimation(keyPath: "opacity")
        fade.fromValue = 1.0
        fade.toValue = 0.0
        let group = CAAnimationGroup()
        group.animations = [scale, fade]
        group.duration = 0.55
        group.timingFunction = CAMediaTimingFunction(name: .easeOut)
        layer.add(group, forKey: "ripple")
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.62) {
            layer.removeFromSuperlayer()
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

let screen = NSScreen.main ?? NSScreen.screens.first!
let screenFrame = screen.frame

let cursorWidth: CGFloat = 240
let cursorHeight: CGFloat = 32
let cursorWindow = OverlayWindow(
    contentRect: NSRect(x: 0, y: 0, width: cursorWidth, height: cursorHeight),
    styleMask: .borderless,
    backing: .buffered,
    defer: false
)
cursorWindow.level = .screenSaver
cursorWindow.backgroundColor = .clear
cursorWindow.isOpaque = false
cursorWindow.hasShadow = false
cursorWindow.ignoresMouseEvents = true
cursorWindow.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
let cursorView = CursorView(frame: NSRect(x: 0, y: 0, width: cursorWidth, height: cursorHeight))
cursorWindow.contentView = cursorView

let controlWindow = OverlayWindow(
    contentRect: screenFrame,
    styleMask: .borderless,
    backing: .buffered,
    defer: false
)
controlWindow.level = .screenSaver
controlWindow.backgroundColor = .clear
controlWindow.isOpaque = false
controlWindow.hasShadow = false
controlWindow.ignoresMouseEvents = true
controlWindow.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
let controlView = ControlView(frame: NSRect(origin: .zero, size: screenFrame.size))
controlView.autoresizingMask = [.width, .height]
controlView.setActive(false)
controlWindow.contentView = controlView

func moveCursor(x: CGFloat, y: CGFloat, label: String) {
    let originY = screenFrame.maxY - y - 30
    cursorWindow.setFrameOrigin(NSPoint(x: x - 3, y: originY))
    cursorView.update(label: label)
    cursorWindow.orderFrontRegardless()
}

func ripple(x: CGFloat, y: CGFloat) {
    let point = NSPoint(x: x - screenFrame.minX, y: screenFrame.maxY - y)
    controlView.setActive(true)
    controlWindow.orderFrontRegardless()
    controlView.ripple(at: point)
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
            if object["session"] as? Bool == true {
                controlView.setActive(true)
                controlWindow.orderFrontRegardless()
                return
            }
            if object["session"] as? Bool == false {
                controlView.setActive(false)
                controlView.clearHighlight()
                controlWindow.orderOut(nil)
                return
            }
            if object["highlight"] as? Bool == true {
                let x = (object["x"] as? NSNumber)?.doubleValue ?? 0
                let y = (object["y"] as? NSNumber)?.doubleValue ?? 0
                let width = (object["width"] as? NSNumber)?.doubleValue ?? 56
                let height = (object["height"] as? NSNumber)?.doubleValue ?? 56
                let rect = NSRect(x: x - screenFrame.minX,
                                  y: screenFrame.maxY - (y + height),
                                  width: width, height: height)
                controlView.highlight(rect, label: object["label"] as? String ?? "target")
                controlView.setActive(true)
                controlWindow.orderFrontRegardless()
                return
            }
            if object["highlight"] as? Bool == false {
                controlView.clearHighlight()
            }
            if object["control"] as? Bool == false || object["visible"] as? Bool == false {
                cursorWindow.orderOut(nil)
                controlView.setActive(false)
                controlView.clearHighlight()
                controlWindow.orderOut(nil)
                if object["visible"] as? Bool == false { return }
            }
            let x = (object["x"] as? NSNumber)?.doubleValue ?? 100
            let y = (object["y"] as? NSNumber)?.doubleValue ?? 100
            moveCursor(x: CGFloat(x), y: CGFloat(y),
                       label: object["label"] as? String ?? "Talos")
            if object["click"] as? Bool == true {
                ripple(x: CGFloat(x), y: CGFloat(y))
            } else if object["control"] as? Bool == true {
                controlView.setActive(true)
                controlWindow.orderFrontRegardless()
            }
        }
    }
}

app.run()
