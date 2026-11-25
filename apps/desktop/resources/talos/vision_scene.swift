// Detect people and faces in an image with Apple's Vision framework.
// Prints JSON: {"people":1,"faces":1,"empty":false}. It reads an image only;
// it never captures a camera frame or produces audio.

import Foundation
import Vision
import ImageIO

let arguments = CommandLine.arguments
guard arguments.count >= 2 else {
    FileHandle.standardError.write(Data("usage: vision_scene IMAGE\n".utf8))
    exit(2)
}
let url = URL(fileURLWithPath: arguments[1])
guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
    FileHandle.standardError.write(Data("Could not read the image.\n".utf8))
    exit(1)
}
let handler = VNImageRequestHandler(cgImage: image, options: [:])
let faces = VNDetectFaceRectanglesRequest()
let humans = VNDetectHumanRectanglesRequest()
do {
    try handler.perform([faces, humans])
} catch {
    FileHandle.standardError.write(Data("Vision error: \(error.localizedDescription)\n".utf8))
    exit(1)
}
let faceCount = faces.results?.count ?? 0
let humanCount = humans.results?.count ?? 0
let people = max(faceCount, humanCount)
let payload: [String: Any] = [
    "people": people,
    "faces": faceCount,
    "empty": people == 0,
    "window": humanCount,
]
let data = try JSONSerialization.data(withJSONObject: payload, options: [])
FileHandle.standardOutput.write(data)
FileHandle.standardOutput.write(Data("\n".utf8))
