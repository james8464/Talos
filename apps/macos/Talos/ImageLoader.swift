import Foundation
import UniformTypeIdentifiers

struct ImagePayload {
    let path: String
    let mediaType: String
    let base64: String
}

enum ImageLoader {
    static func load(paths: [String], maxBytes: Int = 10_000_000) -> [ImagePayload] {
        var seen = Set<String>()
        var images: [ImagePayload] = []
        for path in paths where !seen.contains(path) {
            seen.insert(path)
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

    static func ollamaBase64(paths: [String], maxBytes: Int = 10_000_000) -> [String] {
        load(paths: paths, maxBytes: maxBytes).map(\.base64)
    }
}
