import Foundation
import UniformTypeIdentifiers

struct ImagePayload {
    let path: String
    let mediaType: String
    let base64: String
}

enum ImageLoader {
    static func load(
        paths: [String],
        maxBytes: Int = 10_000_000,
        maxImages: Int = 16
    ) -> [ImagePayload] {
        var seen = Set<String>()
        var images: [ImagePayload] = []
        for path in paths where !seen.contains(path) {
            seen.insert(path)
            if images.count >= maxImages { break }
            if PDFLoader.isPDF(path: path) {
                let pages = PDFLoader.pageImages(
                    path: path,
                    maxPages: min(maxImages - images.count, 8),
                    maxBytes: maxBytes
                )
                images.append(contentsOf: pages.prefix(maxImages - images.count))
                continue
            }
            let url = URL(fileURLWithPath: path)
            let ext = url.pathExtension.lowercased()
            guard let type = UTType(filenameExtension: ext),
                  type.conforms(to: .image),
                  let data = try? Data(contentsOf: url),
                  data.count <= maxBytes else {
                continue
            }
            images.append(ImagePayload(
                path: path,
                mediaType: type.preferredMIMEType ?? "image/\(ext)",
                base64: data.base64EncodedString()
            ))
        }
        return images
    }

    static func ollamaBase64(
        paths: [String],
        maxBytes: Int = 10_000_000,
        maxImages: Int = 16
    ) -> [String] {
        load(paths: paths, maxBytes: maxBytes, maxImages: maxImages).map(\.base64)
    }
}
