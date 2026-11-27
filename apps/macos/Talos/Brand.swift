import SwiftUI
import AppKit

enum Brand {
    static let bronze = Color(red: 0.80, green: 0.53, blue: 0.27)
    static let bronzeLight = Color(red: 0.95, green: 0.72, blue: 0.42)

    static var icon: NSImage? {
        if let url = Bundle.main.url(forResource: "Talos", withExtension: "icns"),
           let image = NSImage(contentsOf: url) {
            return image
        }
        return NSApp.applicationIconImage
    }
}
