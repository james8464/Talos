// Classify non-word sounds in an audio file with Apple's built-in
// SoundAnalysis classifier. Prints JSON: [{"label":"Speech","confidence":0.98}].
// This reads a file only; it never records or plays audio.

import Foundation
import SoundAnalysis

final class Collector: NSObject, SNResultsObserving {
    var best: [String: Double] = [:]
    let done = DispatchSemaphore(value: 0)
    var failure: String?

    func request(_ request: SNRequest, didProduce result: SNResult) {
        guard let result = result as? SNClassificationResult else { return }
        for item in result.classifications {
            best[item.identifier] = max(best[item.identifier] ?? 0, item.confidence)
        }
    }

    func request(_ request: SNRequest, didFailWithError error: Error) {
        failure = error.localizedDescription
        done.signal()
    }

    func requestDidComplete(_ request: SNRequest) {
        done.signal()
    }
}

let arguments = CommandLine.arguments
guard arguments.count >= 2 else {
    FileHandle.standardError.write(Data("usage: sound_classify FILE\n".utf8))
    exit(2)
}
let url = URL(fileURLWithPath: arguments[1])
do {
    let request = try SNClassifySoundRequest(classifierIdentifier: .version1)
    let analyzer = try SNAudioFileAnalyzer(url: url)
    let collector = Collector()
    try analyzer.add(request, withObserver: collector)
    analyzer.analyze()
    _ = collector.done.wait(timeout: .now() + 120)
    if let failure = collector.failure {
        FileHandle.standardError.write(Data("analysis failed: \(failure)\n".utf8))
        exit(1)
    }
    let rows = collector.best
        .sorted { $0.value > $1.value }
        .prefix(12)
        .map { ["label": $0.key, "confidence": $0.value] as [String: Any] }
    let data = try JSONSerialization.data(withJSONObject: rows, options: [])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
} catch {
    FileHandle.standardError.write(Data("SoundAnalysis error: \(error.localizedDescription)\n".utf8))
    exit(1)
}
