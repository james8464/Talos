import AppKit
import AVFoundation
import Foundation
import Speech
import SwiftUI
import Vision

struct RoomClient {
    let port: Int
    let token: String

    func request(_ path: String, body: [String: Any]? = nil) async throws -> [String: Any] {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/v1/\(path)")!)
        request.timeoutInterval = path == "turn" ? 150 : path == "voice" ? 20 : 5
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let body {
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse,
              let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw RoomError("Talos returned an invalid response.")
        }
        guard (200..<300).contains(http.statusCode) else {
            throw RoomError(result["error"] as? String ?? "Talos returned HTTP \(http.statusCode).")
        }
        return result
    }
}

struct RoomError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

final class CameraDetector: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    let queue = DispatchQueue(label: "local.talos.camera")
    let session = AVCaptureSession()
    let onObservation: (Bool?) -> Void
    let onFailure: (String) -> Void
    private let detection = VNDetectHumanRectanglesRequest()
    private var lastFrame = Date.distantPast
    private var lastReport = Date.distantPast
    private var candidate: Bool?
    private var confirmations = 0
    private var occupied: Bool?

    init(onObservation: @escaping (Bool?) -> Void, onFailure: @escaping (String) -> Void) {
        self.onObservation = onObservation
        self.onFailure = onFailure
        super.init()
        detection.upperBodyOnly = true
    }

    func start() {
        queue.async {
            do {
                guard let device = AVCaptureDevice.default(for: .video) else {
                    throw RoomError("No camera is available.")
                }
                let input = try AVCaptureDeviceInput(device: device)
                let output = AVCaptureVideoDataOutput()
                output.alwaysDiscardsLateVideoFrames = true
                output.setSampleBufferDelegate(self, queue: self.queue)
                guard self.session.canAddInput(input), self.session.canAddOutput(output) else {
                    throw RoomError("Could not configure the camera.")
                }
                self.session.beginConfiguration()
                if self.session.canSetSessionPreset(.vga640x480) {
                    self.session.sessionPreset = .vga640x480
                }
                self.session.addInput(input)
                self.session.addOutput(output)
                self.session.commitConfiguration()
                self.session.startRunning()
            } catch {
                self.onFailure(error.localizedDescription)
            }
        }
    }

    func stop() {
        queue.async {
            self.session.stopRunning()
            for output in self.session.outputs { self.session.removeOutput(output) }
            for input in self.session.inputs { self.session.removeInput(input) }
        }
    }

    func captureOutput(
        _ output: AVCaptureOutput, didOutput sample: CMSampleBuffer, from connection: AVCaptureConnection
    ) {
        let now = Date()
        guard now.timeIntervalSince(lastFrame) >= 0.7 else { return }
        lastFrame = now
        do {
            try VNImageRequestHandler(cmSampleBuffer: sample, orientation: .up).perform([detection])
            let detected = detection.results?.contains { $0.confidence >= 0.6 } ?? false
            if candidate != detected {
                candidate = detected
                confirmations = 0
            }
            confirmations += 1
            let confirmed = confirmations >= (detected ? 2 : 4)
            if confirmed && (occupied != detected || now.timeIntervalSince(lastReport) >= 20) {
                occupied = detected
                lastReport = now
                onObservation(detected)
            }
        } catch {
            if occupied != nil {
                onObservation(nil)
                occupied = nil
            }
            candidate = nil
            confirmations = 0
        }
    }
}

@MainActor
final class RoomController: NSObject, ObservableObject, AVSpeechSynthesizerDelegate, AVAudioPlayerDelegate {
    @Published var lines: [String] = []
    @Published var status = "Connecting to the local Talos service…"
    @Published var microphone = false
    @Published var cameraRunning = false
    @Published var rememberVoice = false
    @Published var busy = false
    @Published private(set) var speaking = false
    @Published var draft = ""
    @Published var occupancy = "Camera off"
    @Published var previewSession: AVCaptureSession?
    @Published var voiceLabel = "Voice: connecting"
    @Published var liveTranscript = ""
    @Published var lastTiming = ""
    private let startupLine = "Talos online. I was beginning to think you had forgotten me."
    private var kokoroVoice: String?
    private var player: AVAudioPlayer?
    private var finalClip = false
    private var latestPresence: Bool?
    private var presenceAt = Date.distantPast
    private var resumeMicrophone = false
    private var resumeCamera = false
    private var reconnecting = false
    private var speechRequest: Task<Void, Never>?
    private var playbackEpoch = UUID()
    private let client: RoomClient?
    private let recognizer = SFSpeechRecognizer()
    private let engine = AVAudioEngine()
    private let speaker = AVSpeechSynthesizer()
    private var recognition: SFSpeechRecognitionTask?
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var tapInstalled = false
    private var utteranceTimer: Task<Void, Never>?
    private var recognitionLimit: Task<Void, Never>?
    private var speechEpoch = UUID()
    private var currentText = ""
    private var recognitionFailures = 0
    private var microphoneStarting = false
    private var cameraStarting = false
    private var endpointFailures = 0
    private var speechID: Int?
    private var pendingAcknowledgment: Int?
    private var camera: CameraDetector?
    private var cameraEpoch = UUID()
    private var presenceSend: Task<Void, Never>?
    private var polling: Task<Void, Never>?
    private var announcedStartup = false

    override init() {
        let args = ProcessInfo.processInfo.arguments
        if let position = args.firstIndex(of: "--token-file"), args.count > position + 1,
           let key = try? String(contentsOfFile: args[position + 1], encoding: .utf8) {
            let portIndex = args.firstIndex(of: "--port")
            let port = portIndex.flatMap { args.indices.contains($0 + 1) ? Int(args[$0 + 1]) : nil } ?? 8765
            client = RoomClient(port: port, token: key.trimmingCharacters(in: .whitespacesAndNewlines))
        } else {
            client = nil
        }
        super.init()
        speaker.delegate = self
    }

    func connect() {
        guard polling == nil else { return }
        guard client != nil else {
            status = "Start Talos with python3 run_talos.py."
            return
        }
        polling = Task {
            while !Task.isCancelled {
                if !announcedStartup || reconnecting {
                    do {
                        let result = try await client?.request("status")
                        kokoroVoice = result?["voice"] as? String
                        voiceLabel = kokoroVoice.map { "Voice: Kokoro · \($0)" } ?? "Voice: macOS fallback"
                        status = "Connected"
                        if reconnecting {
                            reconnecting = false
                            if resumeMicrophone { toggleMicrophone() }
                            if resumeCamera { toggleCamera() }
                        }
                        if !announcedStartup && !busy && !speaking {
                            announcedStartup = true
                            addLine("Talos", startupLine)
                            say(startupLine)
                            startRoom()
                        }
                    } catch { status = error.localizedDescription }
                }
                await pollSpeech()
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }

    func startRoom() {
        if !microphone { toggleMicrophone() }
        if !cameraRunning { toggleCamera() }
    }

    func toggleMicrophone() {
        if microphone {
            microphone = false
            stopRecognition()
            status = "Microphone off."
            return
        }
        guard client != nil else { status = "Connect to the Talos service first."; return }
        guard !microphoneStarting else { return }
        microphoneStarting = true
        Task {
            defer { microphoneStarting = false }
            let permitted = await AVCaptureDevice.requestAccess(for: .audio)
            let speechStatus = await withCheckedContinuation { continuation in
                SFSpeechRecognizer.requestAuthorization { continuation.resume(returning: $0) }
            }
            guard permitted && speechStatus == .authorized else {
                status = "Allow microphone and speech recognition in System Settings."
                return
            }
            guard let recognizer, recognizer.supportsOnDeviceRecognition else {
                status = "On-device speech recognition is unavailable for this Mac's language."
                return
            }
            microphone = true
            recognitionFailures = 0
            beginRecognition()
        }
    }

    private func beginRecognition() {
        guard microphone, !speaking, !busy, recognition == nil, let recognizer else { return }
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            microphone = false
            status = "No usable microphone is available."
            return
        }
        let buffer = SFSpeechAudioBufferRecognitionRequest()
        buffer.requiresOnDeviceRecognition = true
        buffer.shouldReportPartialResults = true
        buffer.contextualStrings = ["Talos", "Hey Talos", "Hello Talos"]
        request = buffer
        let epoch = UUID()
        speechEpoch = epoch
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { audio, _ in buffer.append(audio) }
        tapInstalled = true
        do {
            engine.prepare()
            try engine.start()
        } catch {
            stopRecognition()
            microphone = false
            status = error.localizedDescription
            return
        }
        status = "Listening"
        recognition = recognizer.recognitionTask(with: buffer) { [weak self] result, error in
            let text = result?.bestTranscription.formattedString
            let final = result?.isFinal ?? false
            Task { @MainActor in
                guard let self, self.speechEpoch == epoch else { return }
                if let text, !text.isEmpty, text != self.currentText {
                    self.recognitionFailures = 0
                    self.currentText = text
                    self.liveTranscript = Self.normalizeAddress(text)
                    self.utteranceTimer?.cancel()
                    self.utteranceTimer = Task {
                        guard (try? await Task.sleep(for: .milliseconds(650))) != nil,
                              self.speechEpoch == epoch else { return }
                        self.commitVoice()
                    }
                }
                if final && !self.currentText.isEmpty {
                    self.commitVoice()
                } else if error != nil || final {
                    let pending = !self.currentText.isEmpty
                    if pending { self.commitVoice(); return }
                    self.stopRecognition()
                    self.recognitionFailures += 1
                    if !recognizer.isAvailable {
                        self.status = "Waiting for on-device speech recognition…"
                    }
                    try? await Task.sleep(for: .seconds(min(8, self.recognitionFailures)))
                    self.beginRecognition()
                }
            }
        }
        recognitionLimit = Task {
            guard (try? await Task.sleep(for: .seconds(45))) != nil, speechEpoch == epoch else { return }
            if currentText.isEmpty {
                stopRecognition()
                beginRecognition()
            } else {
                commitVoice()
            }
        }
    }

    private func stopRecognition() {
        speechEpoch = UUID()
        utteranceTimer?.cancel()
        recognitionLimit?.cancel()
        engine.stop()
        if tapInstalled {
            engine.inputNode.removeTap(onBus: 0)
            tapInstalled = false
        }
        request?.endAudio()
        recognition?.cancel()
        request = nil
        recognition = nil
        currentText = ""
        liveTranscript = ""
    }

    static func normalizeAddress(_ text: String) -> String {
        // Correct an apparent name only where the text looks like a direct address.
        text.replacingOccurrences(
            of: #"(?i)^((?:hey|hello)[,\s]+)tell us\b|^tell us(?=\s*[,!.?]|\s*$)"#,
            with: "$1Talos", options: .regularExpression
        ).replacingOccurrences(
            of: #"(?i)\b((?:can|do|did) you (?:hear|see) me)[, ]+tell us(?=[.!?]*$)"#,
            with: "$1 Talos", options: .regularExpression
        )
    }

    private func commitVoice() {
        let text = Self.normalizeAddress(currentText)
        if !text.isEmpty { send(text, source: "voice") }
    }

    func send(_ text: String, source: String = "text") {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !busy, !speaking, let client else { return }
        guard text.count <= 800 else { status = "Send up to 800 characters per turn."; return }
        busy = true
        stopRecognition()
        addLine("You", text)
        status = "Thinking…"
        let submitted = Date()
        Task {
            do {
                let result = try await client.request("turn", body: [
                    "text": text, "source": source, "remember": rememberVoice,
                    "occupied": (cameraRunning && Date().timeIntervalSince(presenceAt) < 75
                                 ? latestPresence : nil).map { $0 as Any } ?? NSNull(),
                ])
                let reply = result["reply"] as? String ?? ""
                let replyMs = Int(Date().timeIntervalSince(submitted) * 1000)
                let planningMs = result["planning_ms"] as? Int ?? 0
                let queueMs = result["queue_ms"] as? Int ?? 0
                lastTiming = "Reply \(replyMs) ms · decision \(planningMs) ms · queue \(queueMs) ms"
                if !reply.isEmpty {
                    addLine("Talos", reply)
                    if let sources = result["sources"] as? [[String: String]] {
                        for source in sources {
                            if let title = source["title"], let url = source["url"] {
                                addLine("Source", "\(title) — \(url)")
                            }
                        }
                    }
                    if source == "voice" || microphone { say(reply, turnStart: submitted) }
                }
                status = "Connected"
            } catch {
                status = error.localizedDescription
            }
            busy = false
            beginRecognition()
        }
    }

    func toggleCamera() {
        if cameraRunning {
            cameraEpoch = UUID()
            camera?.stop()
            camera = nil
            previewSession = nil
            cameraRunning = false
            occupancy = "Camera off"
            sendPresence(nil)
            return
        }
        guard client != nil else { status = "Connect to the Talos service first."; return }
        guard !cameraStarting else { return }
        cameraStarting = true
        Task { [self] in
            defer { cameraStarting = false }
            guard await AVCaptureDevice.requestAccess(for: .video) else {
                status = "Allow camera access in System Settings."
                return
            }
            let epoch = UUID()
            cameraEpoch = epoch
            cameraRunning = true
            occupancy = "Waiting for the first camera result"
            camera = CameraDetector(
                onObservation: { [weak self] occupied in
                    Task { @MainActor in
                        guard let self, self.cameraEpoch == epoch else { return }
                        self.occupancy = occupied.map { $0 ? "Person detected" : "No person detected" } ?? "Detection unavailable"
                        self.sendPresence(occupied)
                    }
                },
                onFailure: { [weak self] message in
                    Task { @MainActor in
                        guard let self, self.cameraEpoch == epoch else { return }
                        self.toggleCamera()
                        self.status = message
                    }
                }
            )
            previewSession = camera?.session
            camera?.start()
        }
    }

    private func sendPresence(_ occupied: Bool?) {
        latestPresence = occupied
        presenceAt = Date()
        let previous = presenceSend
        presenceSend = Task {
            await previous?.value
            do {
                _ = try await client?.request("presence", body: ["occupied": occupied.map { $0 as Any } ?? NSNull()])
            } catch { status = error.localizedDescription }
        }
    }

    private func pollSpeech() async {
        guard microphone || cameraRunning, !busy, !speaking, currentText.isEmpty else { return }
        do {
            if let pendingAcknowledgment {
                _ = try await client?.request("speech/ack", body: ["id": pendingAcknowledgment])
                self.pendingAcknowledgment = nil
            }
            let result = try await client?.request("speech")
            endpointFailures = 0
            guard microphone || cameraRunning, !busy, !speaking, currentText.isEmpty,
                  let item = result?["item"] as? [String: Any],
                  let id = item["id"] as? Int, let text = item["text"] as? String else { return }
            addLine("Talos", text)
            let spoken = item["kind"] as? String == "speech" ? text : "You have a local Talos notification."
            say(spoken, id: id)
        } catch {
            endpointFailures += 1
            status = error.localizedDescription
            if endpointFailures >= 3 {
                resumeMicrophone = microphone
                resumeCamera = cameraRunning
                reconnecting = true
                if microphone { toggleMicrophone() }
                if cameraRunning { toggleCamera() }
                status = "Room sensors paused because the local Talos endpoint stopped responding."
                endpointFailures = 0
            }
        }
    }

    func stopSpeaking() {
        if let speechID { pendingAcknowledgment = speechID }
        playbackEpoch = UUID()
        speechRequest?.cancel()
        speechRequest = nil
        if speaker.isSpeaking {
            speaker.stopSpeaking(at: .immediate)
        } else {
            player?.stop()
            finishSpeech(delivered: false)
        }
    }

    private func say(_ text: String, id: Int? = nil, turnStart: Date? = nil) {
        speaking = true
        speechID = id
        stopRecognition()
        guard let kokoroVoice, let client else {
            speaker.speak(AVSpeechUtterance(string: text))
            return
        }
        let epoch = UUID()
        playbackEpoch = epoch
        let requested = Date()
        speechRequest = Task {
            var played = 0
            // Prepare the following sentence while the preceding one plays.
            let clips = turnStart != nil ? Self.speechClips(text) : [text]
            do {
                for (index, clip) in clips.enumerated() {
                    let result = try await client.request("voice", body: ["text": clip])
                    guard !Task.isCancelled, playbackEpoch == epoch else { return }
                    guard let encoded = result["wav"] as? String, let data = Data(base64Encoded: encoded) else {
                        throw RoomError("Kokoro returned invalid audio.")
                    }
                    let audio = try AVAudioPlayer(data: data)
                    while player?.isPlaying == true { try await Task.sleep(for: .milliseconds(15)) }
                    guard !Task.isCancelled, playbackEpoch == epoch else { return }
                    audio.delegate = self
                    finalClip = index == clips.count - 1
                    player = audio
                    guard audio.play() else { throw RoomError("Kokoro audio could not play.") }
                    played += 1
                    voiceLabel = "Voice: Kokoro · \(kokoroVoice)"
                    if let turnStart, index == 0 {
                        let elapsed = Int(Date().timeIntervalSince(requested) * 1000)
                        let total = Int(Date().timeIntervalSince(turnStart) * 1000)
                        lastTiming += " · voice \(elapsed) ms · first audio at \(total) ms"
                    }
                }
            } catch {
                guard !Task.isCancelled, playbackEpoch == epoch else { return }
                voiceLabel = "Voice: macOS fallback · Kokoro unavailable"
                while player?.isPlaying == true {
                    guard (try? await Task.sleep(for: .milliseconds(20))) != nil else { return }
                }
                guard !Task.isCancelled, playbackEpoch == epoch else { return }
                player = nil
                speaker.speak(AVSpeechUtterance(string: clips.dropFirst(played).joined(separator: " ")))
            }
        }
    }

    static func speechClips(_ text: String) -> [String] {
        var clips: [String] = []
        text.enumerateSubstrings(in: text.startIndex..., options: .bySentences) { sentence, _, _, _ in
            if let sentence { clips.append(sentence.trimmingCharacters(in: .whitespacesAndNewlines)) }
        }
        return clips.isEmpty ? [text] : clips
    }

    private func finishSpeech(delivered: Bool) {
        if delivered, let speechID { pendingAcknowledgment = speechID }
        speechID = nil
        speaking = false
        player = nil
        speechRequest = nil
        beginRecognition()
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in
            guard self.player === player, self.finalClip else { return }
            finishSpeech(delivered: flag)
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor in finishSpeech(delivered: true) }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor in finishSpeech(delivered: false) }
    }

    private func addLine(_ role: String, _ text: String) {
        lines.append("\(role): \(text)")
        if lines.count > 100 { lines.removeFirst() }
    }
}

final class CameraSurface: NSView {
    let videoLayer = AVCaptureVideoPreviewLayer()

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        layer = videoLayer
        videoLayer.videoGravity = .resizeAspect
    }

    required init?(coder: NSCoder) { nil }
}

struct CameraPreview: NSViewRepresentable {
    let session: AVCaptureSession

    func makeNSView(context: Context) -> CameraSurface {
        let surface = CameraSurface(frame: .zero)
        surface.videoLayer.session = session
        return surface
    }

    func updateNSView(_ surface: CameraSurface, context: Context) {
        if surface.videoLayer.session !== session { surface.videoLayer.session = session }
    }
}

struct RoomView: View {
    @ObservedObject var room: RoomController

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Talos").font(.largeTitle.bold())
            Text(room.status).foregroundStyle(.secondary)
            HStack {
                Text(room.voiceLabel)
                if !room.lastTiming.isEmpty { Text(room.lastTiming) }
            }.font(.caption).foregroundStyle(.secondary)
            HStack {
                Button(room.microphone ? "Microphone on" : "Microphone off", action: room.toggleMicrophone)
                Button(room.cameraRunning ? "Camera on" : "Camera off", action: room.toggleCamera)
                if room.speaking { Button("Stop speaking", action: room.stopSpeaking) }
            }
            if let session = room.previewSession {
                HStack(alignment: .center, spacing: 16) {
                    CameraPreview(session: session)
                        .frame(width: 224, height: 126)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Live camera").font(.headline)
                        Text(room.occupancy).foregroundStyle(.secondary)
                        Text("Presence only").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            if !room.liveTranscript.isEmpty {
                Text("Hearing: \(room.liveTranscript)").foregroundStyle(.secondary)
            }
            Toggle("Remember voice transcripts", isOn: $room.rememberVoice)
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(Array(room.lines.enumerated()), id: \.offset) { _, line in
                            Text(line).textSelection(.enabled)
                        }
                        Color.clear.frame(height: 1).id("end")
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
                .onChange(of: room.lines) { _, _ in proxy.scrollTo("end", anchor: .bottom) }
            }
            HStack {
                TextField("Message Talos", text: $room.draft).onSubmit(submit)
                Button("Send", action: submit).disabled(room.busy || room.speaking || room.draft.isEmpty)
            }
        }
        .padding(24)
        .frame(minWidth: 660, minHeight: 440)
        .task { room.connect() }
    }

    private func submit() {
        guard !room.busy, !room.speaking else { return }
        room.send(room.draft)
        room.draft = ""
    }
}

final class TalosAppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
}

@main
struct TalosApp: App {
    @NSApplicationDelegateAdaptor(TalosAppDelegate.self) var delegate
    @StateObject private var room = RoomController()

    var body: some Scene {
        Window("Talos", id: "room") { RoomView(room: room) }
    }
}
