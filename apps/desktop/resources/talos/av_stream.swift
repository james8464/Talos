import AVFoundation
import Foundation

// Streams 16 kHz mono signed 16-bit PCM to stdout from one long-lived audio
// session, so the microphone never has to restart between chunks. The caller
// gates utterances and deletes nothing on disk; no audio is retained here.

enum StreamFailure: Error {
    case message(String)
}

func requireAccess() throws {
    switch AVCaptureDevice.authorizationStatus(for: .audio) {
    case .authorized:
        return
    case .notDetermined:
        let ready = DispatchSemaphore(value: 0)
        var granted = false
        AVCaptureDevice.requestAccess(for: .audio) { allowed in
            granted = allowed
            ready.signal()
        }
        if ready.wait(timeout: .now() + 30) == .timedOut {
            throw StreamFailure.message("microphone permission timed out")
        }
        if !granted {
            throw StreamFailure.message("microphone permission denied")
        }
    default:
        throw StreamFailure.message("microphone permission is not authorized")
    }
}

func run() throws {
    try requireAccess()
    guard
        let target = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: 16_000,
            channels: 1,
            interleaved: true
        )
    else {
        throw StreamFailure.message("the 16 kHz format is unavailable")
    }
    let engine = AVAudioEngine()
    let input = engine.inputNode
    let source = input.inputFormat(forBus: 0)
    guard let converter = AVAudioConverter(from: source, to: target) else {
        throw StreamFailure.message("the audio converter is unavailable")
    }
    let out = FileHandle.standardOutput
    let ratio = target.sampleRate / source.sampleRate
    input.installTap(onBus: 0, bufferSize: 4_096, format: source) { buffer, _ in
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 1_024
        guard
            let converted = AVAudioPCMBuffer(
                pcmFormat: target,
                frameCapacity: capacity
            )
        else { return }
        var error: NSError?
        var supplied = false
        let status = converter.convert(to: converted, error: &error) { _, outStatus in
            if supplied {
                outStatus.pointee = .noDataNow
                return nil
            }
            supplied = true
            outStatus.pointee = .haveData
            return buffer
        }
        guard status == .haveData || status == .inputRanDry else { return }
        guard let samples = converted.int16ChannelData?[0] else { return }
        let data = Data(bytes: samples, count: Int(converted.frameLength) * 2)
        try? out.write(contentsOf: data)
    }
    engine.prepare()
    try engine.start()
    while true {
        RunLoop.current.run(mode: .default, before: .distantFuture)
    }
}

do {
    try run()
} catch {
    FileHandle.standardError.write(Data("av_stream: \(error)\n".utf8))
    exit(1)
}
