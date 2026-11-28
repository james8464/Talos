import Foundation
import AppKit
import PDFKit
import UniformTypeIdentifiers

struct PDFTextExtract: Sendable {
    let text: String
    let pageCount: Int
    let extractedPages: Int
    let truncated: Bool
}

enum PDFLoader {
    static func isPDF(path: String) -> Bool {
        let ext = URL(fileURLWithPath: path).pathExtension.lowercased()
        guard let type = UTType(filenameExtension: ext) else {
            return ext == "pdf"
        }
        return type.conforms(to: .pdf)
    }

    static func text(
        path: String,
        maxPages: Int = 40,
        maxCharacters: Int = 20_000
    ) -> PDFTextExtract? {
        guard isPDF(path: path),
              let document = PDFDocument(url: URL(fileURLWithPath: path)) else {
            return nil
        }
        let pagesToRead = min(document.pageCount, max(1, maxPages))
        var pieces: [String] = []
        for index in 0..<pagesToRead {
            guard let page = document.page(at: index) else { continue }
            let text = page.string?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            if !text.isEmpty {
                pieces.append(text)
            }
        }
        var combined = pieces.joined(separator: "\n\n")
        var truncated = document.pageCount > pagesToRead
        if combined.count > maxCharacters {
            combined = String(combined.prefix(maxCharacters))
            truncated = true
        }
        return PDFTextExtract(
            text: combined,
            pageCount: document.pageCount,
            extractedPages: pagesToRead,
            truncated: truncated
        )
    }

    static func extractedText(paths: [String], maxCharacters: Int = 12_000) -> String {
        var sections: [String] = []
        var remaining = maxCharacters
        for path in paths where isPDF(path: path) {
            guard remaining > 0,
                  let extract = text(path: path, maxCharacters: remaining) else { continue }
            guard !extract.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                sections.append("[\(URL(fileURLWithPath: path).lastPathComponent): no selectable text on \(extract.pageCount) page(s)]")
                continue
            }
            var section = "[\(URL(fileURLWithPath: path).lastPathComponent), \(extract.pageCount) page(s)]\n\(extract.text)"
            if extract.truncated {
                section += "\n… PDF text truncated …"
            }
            sections.append(section)
            remaining -= min(section.count, remaining)
        }
        return sections.joined(separator: "\n\n")
    }

    static func pageImages(
        path: String,
        maxPages: Int = 8,
        maxDimension: CGFloat = 1_600,
        maxBytes: Int = 4_000_000
    ) -> [ImagePayload] {
        guard isPDF(path: path),
              let document = PDFDocument(url: URL(fileURLWithPath: path)) else {
            return []
        }
        var images: [ImagePayload] = []
        let pages = min(document.pageCount, max(1, maxPages))
        for index in 0..<pages {
            guard let page = document.page(at: index) else { continue }
            let bounds = page.bounds(for: .mediaBox)
            guard bounds.width > 1, bounds.height > 1 else { continue }
            let scale = min(maxDimension / max(bounds.width, bounds.height), 2.0)
            let width = max(Int((bounds.width * scale).rounded()), 1)
            let height = max(Int((bounds.height * scale).rounded()), 1)
            guard let representation = NSBitmapImageRep(
                bitmapDataPlanes: nil,
                pixelsWide: width,
                pixelsHigh: height,
                bitsPerSample: 8,
                samplesPerPixel: 4,
                hasAlpha: true,
                isPlanar: false,
                colorSpaceName: .deviceRGB,
                bytesPerRow: 0,
                bitsPerPixel: 0
            ) else {
                continue
            }
            representation.size = NSSize(width: width, height: height)
            NSGraphicsContext.saveGraphicsState()
            if let graphics = NSGraphicsContext(bitmapImageRep: representation) {
                NSGraphicsContext.current = graphics
                let context = graphics.cgContext
                NSColor.white.setFill()
                NSRect(x: 0, y: 0, width: width, height: height).fill()
                context.scaleBy(x: scale, y: scale)
                page.draw(with: .mediaBox, to: context)
            }
            NSGraphicsContext.restoreGraphicsState()
            guard let png = representation.representation(using: .png, properties: [:]) else {
                continue
            }
            guard png.count <= maxBytes else {
                continue
            }
            images.append(ImagePayload(
                path: "\(path)#page=\(index + 1)",
                mediaType: "image/png",
                base64: png.base64EncodedString()
            ))
        }
        return images
    }
}
