import Foundation
import AppKit
import AVFoundation
import Speech

enum DictationText {
    static func merged(base: String, transcript: String) -> String {
        let base = base.trimmingCharacters(in: .whitespacesAndNewlines)
        let transcript = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        if transcript.isEmpty { return base }
        if base.isEmpty { return transcript }
        return base + " " + transcript
    }
}

@MainActor
final class DictationController: ObservableObject {
    @Published private(set) var isRecording = false
    @Published private(set) var transcript = ""
    @Published private(set) var speechStatus = SFSpeechRecognizer.authorizationStatus()
    @Published private(set) var microphoneStatus = AVCaptureDevice.authorizationStatus(for: .audio)
    @Published var error: String?

    var onTranscript: ((String) -> Void)?

    private var audioEngine: AVAudioEngine?
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    func start() {
        Task { await startRecording() }
    }

    func stop() {
        finishRecording(keepTranscript: true)
    }

    func cancel() {
        task?.cancel()
        task = nil
        request?.endAudio()
        request = nil
        audioEngine?.stop()
        audioEngine?.inputNode.removeTap(onBus: 0)
        audioEngine = nil
        isRecording = false
        transcript = ""
    }

    private func startRecording() async {
        guard !isRecording else { return }
        error = nil
        guard await ensurePermissions() else { return }
        guard let recognizer = SFSpeechRecognizer(locale: Locale.current),
              recognizer.isAvailable else {
            error = "Speech recognition is not available right now."
            return
        }

        let engine = AVAudioEngine()
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        if recognizer.supportsOnDeviceRecognition {
            request.requiresOnDeviceRecognition = true
        }

        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            error = "No microphone input is available."
            return
        }
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            request.append(buffer)
        }
        engine.prepare()
        do {
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            self.error = "Could not start the microphone: \(error.localizedDescription)"
            return
        }

        audioEngine = engine
        self.request = request
        transcript = ""
        isRecording = true
        task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            Task { @MainActor in
                self?.handle(result: result, error: error)
            }
        }
    }

    private func handle(result: SFSpeechRecognitionResult?, error: Error?) {
        if let result {
            transcript = result.bestTranscription.formattedString
            onTranscript?(transcript)
            if result.isFinal {
                finishRecording(keepTranscript: true)
                return
            }
        }
        if let error, isRecording {
            let nsError = error as NSError
            if nsError.code != 216 && nsError.code != 203 {
                self.error = "Dictation stopped: \(error.localizedDescription)"
            }
            finishRecording(keepTranscript: !transcript.isEmpty)
        }
    }

    private func finishRecording(keepTranscript: Bool) {
        guard isRecording else { return }
        isRecording = false
        audioEngine?.stop()
        audioEngine?.inputNode.removeTap(onBus: 0)
        audioEngine = nil
        request?.endAudio()
        request = nil
        task?.finish()
        task = nil
        if !keepTranscript {
            transcript = ""
        }
    }

    private func ensurePermissions() async -> Bool {
        let speech = await Self.requestSpeechAuthorizationIfNeeded()
        speechStatus = speech
        guard speech == .authorized else {
            error = "Speech recognition permission is required. Enable Talos in System Settings → Privacy & Security → Speech Recognition."
            return false
        }

        var microphone = AVCaptureDevice.authorizationStatus(for: .audio)
        if microphone == .notDetermined {
            let granted = await AVCaptureDevice.requestAccess(for: .audio)
            microphone = granted ? .authorized : .denied
        }
        microphoneStatus = microphone
        guard microphone == .authorized else {
            error = "Microphone permission is required. Enable Talos in System Settings → Privacy & Security → Microphone."
            return false
        }
        return true
    }

    static func requestPermissions() async -> (speech: SFSpeechRecognizerAuthorizationStatus, microphone: AVAuthorizationStatus) {
        let speech = await requestSpeechAuthorizationIfNeeded()
        var microphone = AVCaptureDevice.authorizationStatus(for: .audio)
        if microphone == .notDetermined {
            let granted = await AVCaptureDevice.requestAccess(for: .audio)
            microphone = granted ? .authorized : .denied
        }
        return (speech, microphone)
    }

    static func currentPermissions() -> (speech: SFSpeechRecognizerAuthorizationStatus, microphone: AVAuthorizationStatus) {
        (SFSpeechRecognizer.authorizationStatus(), AVCaptureDevice.authorizationStatus(for: .audio))
    }

    private static func requestSpeechAuthorizationIfNeeded() async -> SFSpeechRecognizerAuthorizationStatus {
        let current = SFSpeechRecognizer.authorizationStatus()
        guard current == .notDetermined else { return current }
        return await withCheckedContinuation { continuation in
            let lock = NSLock()
            var finished = false
            let resume: (SFSpeechRecognizerAuthorizationStatus) -> Void = { status in
                lock.lock()
                if finished {
                    lock.unlock()
                    return
                }
                finished = true
                lock.unlock()
                continuation.resume(returning: status)
            }
            SFSpeechRecognizer.requestAuthorization { status in
                resume(status)
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 15) {
                resume(.denied)
            }
        }
    }

    static func openMicrophoneSettings() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone") {
            NSWorkspace.shared.open(url)
        }
    }

    static func openSpeechSettings() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_SpeechRecognition") {
            NSWorkspace.shared.open(url)
        }
    }

    /// Transcribes an audio file with the same on-device settings used for live dictation.
    static func transcribeFile(_ url: URL) async throws -> String {
        let speech = await requestSpeechAuthorizationIfNeeded()
        guard speech == .authorized else {
            throw NSError(
                domain: "TalosDictation",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Speech recognition permission is not authorized."]
            )
        }
        guard let recognizer = SFSpeechRecognizer(locale: Locale.current),
              recognizer.isAvailable else {
            throw NSError(
                domain: "TalosDictation",
                code: 2,
                userInfo: [NSLocalizedDescriptionKey: "Speech recognition is unavailable."]
            )
        }
        let request = SFSpeechURLRecognitionRequest(url: url)
        request.shouldReportPartialResults = false
        if recognizer.supportsOnDeviceRecognition {
            request.requiresOnDeviceRecognition = true
        }
        return try await withCheckedThrowingContinuation { continuation in
            var resumed = false
            let finish: (Result<String, Error>) -> Void = { result in
                guard !resumed else { return }
                resumed = true
                continuation.resume(with: result)
            }
            let task = recognizer.recognitionTask(with: request) { result, error in
                if let result, result.isFinal {
                    finish(.success(result.bestTranscription.formattedString))
                } else if let error {
                    finish(.failure(error))
                }
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 60) {
                task.cancel()
                finish(.failure(NSError(
                    domain: "TalosDictation",
                    code: 3,
                    userInfo: [NSLocalizedDescriptionKey: "Transcription timed out."]
                )))
            }
        }
    }
}
