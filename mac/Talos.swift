import AppKit
import AVFoundation
import Foundation
import Speech
import SwiftUI
import Vision
import CoreImage

struct RoomClient {
    private static let transport = URLSession(configuration: .ephemeral)
    let port: Int
    let token: String

    func request(_ path: String, body: [String: Any]? = nil) async throws -> [String: Any] {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/v1/\(path)")!)
        request.timeoutInterval = (path == "turn" || path == "voice/facing" || path == "work/preview") ? 150 : path == "voice" ? 20 : 5
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let body {
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await Self.transport.data(for: request)
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
    let onFacing: (Bool, TimeInterval) -> Void
    private let faceRequest = VNDetectFaceRectanglesRequest()
    let onSample: (VNFeaturePrintObservation, TimeInterval) -> Void
    private var checkVisitors = false
    private var lastSample = -Double.infinity
    func setVisitors(_ enabled: Bool) { queue.async { self.checkVisitors = enabled } }
    private var checkFacing: Bool
    func setFacing(_ enabled: Bool) { queue.async { self.checkFacing = enabled } }
    private let detection = VNDetectHumanRectanglesRequest()
    private var lastFrame = Date.distantPast
    private var lastReport = Date.distantPast
    private var candidate: Bool?
    private var confirmations = 0
    private var occupied: Bool?

    init(checkFacing: Bool, onSample: @escaping (VNFeaturePrintObservation, TimeInterval) -> Void, onFacing: @escaping (Bool, TimeInterval) -> Void,
         onObservation: @escaping (Bool?) -> Void, onFailure: @escaping (String) -> Void) {
        self.checkFacing = checkFacing
        self.onFacing = onFacing
        self.onSample = onSample
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

    private let imageContext = CIContext()
    private var latestBuffer: CVPixelBuffer?
    private var framePeople: Int?
    func snapshot() async -> (jpeg: String, people: Int?)? {
        await withCheckedContinuation { continuation in
            queue.async {
                guard self.session.isRunning, Date().timeIntervalSince(self.lastFrame) < 2,
                      let buffer = self.latestBuffer else { continuation.resume(returning: nil); return }
                let input = CIImage(cvPixelBuffer: buffer)
                let scale = min(1, 640 / max(input.extent.width, input.extent.height))
                let image = input.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
                let data = self.imageContext.jpegRepresentation(of: image,
                    colorSpace: CGColorSpaceCreateDeviceRGB(),
                    options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: 0.65])
                continuation.resume(returning: data.flatMap { $0.count <= 180000 ? ($0.base64EncodedString(), self.framePeople) : nil })
            }
        }
    }
    func stop() {
        queue.async {
            self.latestBuffer = nil
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
        framePeople = nil
        latestBuffer = CMSampleBufferGetImageBuffer(sample)
        lastFrame = now
        do {
            let timestamp = ProcessInfo.processInfo.systemUptime
            let handler = VNImageRequestHandler(cmSampleBuffer: sample, orientation: .up)
            try handler.perform([detection])
            let people = (detection.results ?? []).filter { $0.confidence >= 0.6 }
            framePeople = people.count
            let detected = !people.isEmpty
            if checkFacing {
                let sampleNeeded = checkVisitors && timestamp - lastSample >= 1.4
                let view = VisitorFrame.observe(handler, people: people.count, faces: faceRequest, sample: sampleNeeded)
                onFacing(view.facing, timestamp)
                if let sample = view.sample {
                    lastSample = timestamp
                    onSample(sample, timestamp)
                }
            }
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
            onFacing(false, ProcessInfo.processInfo.systemUptime)
            if occupied != nil || now.timeIntervalSince(lastReport) >= 20 {
                lastReport = now
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
    @Published var rememberVoice = false {
        didSet { if !isTrial { UserDefaults.standard.set(rememberVoice, forKey: "rememberVoiceTranscripts") } }
    }
    @Published var busy = false
    @Published private(set) var workPlanID: String?
    @Published private(set) var workPlanLines: [String] = []
    @Published private(set) var remainingWork = ""
    @Published private(set) var speaking = false
    @Published var draft = "" {
        didSet {
            if draft.isEmpty { draftContainsVoice = false }
            if oldValue != draft, workPlanID != nil { dismissPlan() }
        }
    }
    private(set) var draftContainsVoice = false
    @Published private var captureMode = VoiceCaptureMode.room
    var dictating: Bool { captureMode != .room }
    @Published var occupancy = "Camera off"
    @Published var previewSession: AVCaptureSession?
    @Published var modelState = ""
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
    let isTrial = ProcessInfo.processInfo.arguments.contains("--room-trial")
    let goals = GoalController()
    let privateSources = PrivateSourceController()
    @Published var presentGoals = false
    @Published var presentSources = false
    @Published var speakUnprompted = false {
        didSet { if !isTrial { UserDefaults.standard.set(speakUnprompted, forKey: "speakUnprompted") } }
    }
    private lazy var calendar = MeetingCalendar()
    let client: RoomClient?
    private var vaultURL: URL? {
        let args = ProcessInfo.processInfo.arguments
        guard let i = args.firstIndex(of: "--token-file"), args.indices.contains(i+1) else { return nil }
        return URL(fileURLWithPath: args[i+1]).deletingLastPathComponent().appendingPathComponent("Talos Memory")
    }
    func openVault() {
        guard let url = vaultURL else { return }
        do {
            try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true,
                                                     attributes: [.posixPermissions: 0o700])
            NSWorkspace.shared.open(url)
        } catch { status = error.localizedDescription }
    }
    private let recognizer = SFSpeechRecognizer()
    private let engine = AVAudioEngine()
    private let speaker = AVSpeechSynthesizer()
    private var systemUtterance: AVSpeechUtterance?
    private var recognition: SFSpeechRecognitionTask?
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var tapInstalled = false
    private var turnProbe: Task<Void, Never>?
    private var modelHold = false
    private var utteranceTimer: Task<Void, Never>?
    private var finalResultTimer: Task<Void, Never>?
    private var recognitionLimit: Task<Void, Never>?
    private var speechEpoch = UUID()
    private var activity: VoiceEndpoint?
    private var playbackMeasurement: (id: Int?, speechEnd: TimeInterval)?
    private var currentText = ""
    private var focus = ConversationFocus()
    private var facing = FacingAttention()
    private var visitor = VisitorConversation<VNFeaturePrintObservation>(compare: VisitorConversation.distance)
    @Published private(set) var profiles: [VisitorProfile] = []
    @Published var forgetCandidate: VisitorProfile?
    func forgetReference(_ profile: VisitorProfile) {
        guard let client else { return }
        Task {
            do {
                _ = try await client.request("visitors/forget-reference", body: ["id":profile.id])
                leaveVisitor()
                loadProfiles()
                addLine("Talos", "Removed the visual reference for " + profile.name + ". The conversation notes remain in Memory.")
            } catch { status = error.localizedDescription }
        }
    }
    private var profilesLoaded = false
    private var profilesEpoch = UUID()
    private var sampleTime = -Double.infinity
    @Published var rememberVisitors = false {
        didSet {
            leaveVisitor()
            camera?.setVisitors(rememberVisitors)
            if !isTrial { UserDefaults.standard.set(rememberVisitors, forKey: "rememberVisitors") }
            loadProfiles()
        }
    }
    @Published private(set) var visitorName: String?
    private func leaveVisitor() {
        visitor.leave()
        if visitorName != nil { visitorName = nil }
    }
    private func useVisitor(_ profile: VisitorProfile) {
        visitor.use(profile)
        visitorName = profile.name
    }
    private func loadProfiles() {
        profiles = []
        profilesLoaded = false
        profilesEpoch = UUID()
        let epoch = profilesEpoch
        guard rememberVisitors, let client else { return }
        Task {
            do {
                let result = try await client.request("visitors")
                guard rememberVisitors, epoch == profilesEpoch else { return }
                let stored = result["profiles"] as? [[String: String]] ?? []
                profiles = stored.compactMap { row in
                    guard let id = row["id"], let name = row["name"], let value = row["template"],
                          let bytes = Data(base64Encoded: value),
                          let sample = try? NSKeyedUnarchiver.unarchivedObject(ofClass: VNFeaturePrintObservation.self, from: bytes) else { return nil }
                    return VisitorProfile(id: id, name: name, sample: sample)
                }
                profilesLoaded = true
            } catch { status = error.localizedDescription }
        }
    }
    private func inspectVisitor(_ face: VNFeaturePrintObservation, at stamp: TimeInterval) {
        guard rememberVisitors, acceptFacing, cameraRunning, microphone, profilesLoaded else { return }
        let now = ProcessInfo.processInfo.systemUptime
        guard now - stamp < 1.8 else { return }
        if stamp - sampleTime > 3 { leaveVisitor() }
        sampleTime = stamp
        if let original = visitor.sample, (VisitorConversation.distance(face, original) ?? 2) > 0.48 {
            leaveVisitor()
            facingOffer = nil
            facingContext = nil
        }
        visitor.expire(at: now)
        guard !busy, !speaking, !dictating, currentText.isEmpty, facing.available(from: stamp, until: now),
              let prompt = visitor.begin(face, profiles: profiles, now: now) else { return }
        addLine("Talos", prompt)
        if speakUnprompted { say(prompt) }
    }
    private func profileForTurn(at now: TimeInterval) -> VisitorProfile? {
        guard rememberVisitors, now - sampleTime < 2.5, facing.available(from: firstCaptionAt ?? now, until: now) else { return nil }
        return visitor.confirmed
    }

    private func introduction(_ text: String, at now: TimeInterval) -> Bool {
        guard rememberVisitors, now - sampleTime < 2.5, facing.available(from: firstCaptionAt ?? now, until: now),
              let answer = visitor.answer(text, at: now) else { return false }
        func speak(_ line: String) { addLine("Talos", line); say(line) }
        switch answer {
        case .prompt(let line): speak(line)
        case .decline: speak("Okay.")
        case .returning(let person):
            useVisitor(person)
            speak("Welcome back, " + person.name + ".")
        case let .enroll(name, words):
            guard let face = visitor.sample, let client else { return false }
            let id = UUID().uuidString.lowercased()
            let interval = facing.since
            busy = true
            stopRecognition()
            Task {
                defer { busy = false; beginRecognition() }
                do {
                    let bytes = try NSKeyedArchiver.archivedData(withRootObject: face, requiringSecureCoding: true)
                    _ = try await client.request("visitors/enroll", body: ["id":id, "name":name, "introduction":words,
                        "template":bytes.base64EncodedString(), "agreed":true])
                    let person = VisitorProfile(id: id, name: name, sample: face)
                    guard rememberVisitors else { return }
                    profiles.append(person)
                    if interval == facing.since, visitor.sample === face {
                        useVisitor(person)
                        speak("I'll remember you, " + name + ".")
                    }
                } catch { status = error.localizedDescription }
            }
        }
        return true
    }
    private var firstCaptionAt: TimeInterval?
    private var facingOffer: String?
    private var facingContext: String?
    private var facingSince: TimeInterval?
    @Published var conversationStarted: TimeInterval?
    private func conversationActive(_ now: TimeInterval) -> Bool {
        conversationStarted.map { now - $0 < 90 } ?? false
    }
    func toggleConversation() {
        focus.clear()
        facingOffer = nil
        facingContext = nil
        if conversationStarted != nil { conversationStarted = nil; return }
        conversationStarted = ProcessInfo.processInfo.systemUptime
        if !microphone { toggleMicrophone() }
    }
    private var attentionInterval: TimeInterval? {
        conversationActive(ProcessInfo.processInfo.systemUptime) ? conversationStarted : facing.since
    }
    @Published var acceptFacing = false {
        didSet {
            facing = FacingAttention()
            leaveVisitor()
            facingOffer = nil
            camera?.setFacing(acceptFacing)
            if !isTrial { UserDefaults.standard.set(acceptFacing, forKey: "acceptFacingRequests") }
        }
    }
    private var playbackText = ""
    private var interruptedPlayback = ""
    private var openFocusAfterPlayback = false
    @Published var useLocalTurnModel = false
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
        if !isTrial, let saved = UserDefaults.standard.object(forKey: "acceptFacingRequests") as? Bool {
            acceptFacing = saved
        }
        if !isTrial { rememberVisitors = UserDefaults.standard.object(forKey: "rememberVisitors") as? Bool ?? false }
        if !isTrial { rememberVoice = UserDefaults.standard.bool(forKey: "rememberVoiceTranscripts") }
        if !isTrial { speakUnprompted = UserDefaults.standard.bool(forKey: "speakUnprompted") }
        privateSources.client = client
        goals.client = client
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
                        modelState = result?["model_state"] as? String ?? ""
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
                            if rememberVisitors { loadProfiles() }
                            if isTrial {
                                status = "Practice room · temporary inbox"
                                addLine("Trial", "Switch the microphone on when ready. Try a pause, a correction, a side conversation, and an interruption. Timing appears after a spoken reply.")
                            } else {
                                addLine("Talos", startupLine)
                                status = "Room ready"
                                startRoom()
                            }
                        }
                    } catch { status = error.localizedDescription }
                }
                if conversationStarted != nil && !conversationActive(ProcessInfo.processInfo.systemUptime) {
                    conversationStarted = nil
                    facingOffer = nil
                    facingContext = nil
                }
                await pollSpeech()
                await goals.reconcile(calendar)
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
            focus.clear()
            facingOffer = nil
            openFocusAfterPlayback = false
            microphone = false
            conversationStarted = nil
            leaveVisitor()
            captureMode = .room
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
        guard microphone, (!busy || speaking), recognition == nil, let recognizer else { return }
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            captureMode = .room
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
        let endpoint = VoiceEndpoint()
        activity = endpoint
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { audio, _ in
            endpoint.observe(audio, at: ProcessInfo.processInfo.systemUptime)
            buffer.append(audio)
        }
        tapInstalled = true
        do {
            engine.prepare()
            try engine.start()
        } catch {
            stopRecognition()
            captureMode = .room
            microphone = false
            status = error.localizedDescription
            return
        }
        status = speaking ? "Speaking" : dictating ? "Dictating into the message field" :
            conversationActive(ProcessInfo.processInfo.systemUptime) ? "Listening for this conversation" :
            acceptFacing && cameraRunning ? "Listening for a direct request" : "Listening for Hey Talos"
        let recognitionStart = ProcessInfo.processInfo.systemUptime
        recognition = recognizer.recognitionTask(with: buffer) { [weak self] result, error in
            let recognized = result?.bestTranscription.formattedString
            let startOffset = result?.bestTranscription.segments.first?.timestamp
            let final = result?.isFinal ?? false
            Task { @MainActor in
                guard let self, self.speechEpoch == epoch else { return }
                let echo = self.speaking ? self.playbackText : self.interruptedPlayback
                let text = recognized.map { echo.isEmpty ? $0 : ConversationFocus.removePlaybackPrefix($0, playback: echo) }
                if self.speaking {
                    let now = ProcessInfo.processInfo.systemUptime
                    let engaged = self.openFocusAfterPlayback || self.conversationActive(now) || (self.facingContext != nil &&
                        self.facing.available(from: now, until: now)) || self.profileForTurn(at: now) != nil
                    if let text, engaged, error == nil, !ConversationFocus.isInterruption(text, playback: self.playbackText), ConversationFocus.conversationalInterruption(text, playback: self.playbackText, allowSpeech: true) {
                        if final && ConversationFocus.pauseOnly(text) {
                            self.stopSpeaking()
                            return
                        }
                        let addressedReply = self.openFocusAfterPlayback
                        self.interruptedPlayback = self.playbackText
                        self.interruptForConversation()
                        if addressedReply { self.focus.open(at: now, question: false, called: true) }
                        // Process this very callback below. It may already be the user's final result.
                    } else {
                        if let text, ConversationFocus.isInterruption(text, playback: self.playbackText) {
                            self.openFocusAfterPlayback = true
                            self.stopSpeaking()
                        } else if final || error != nil {
                            self.stopRecognition()
                            try? await Task.sleep(for: .milliseconds(250))
                            self.beginRecognition()
                        }
                        return
                    }
                }
                if self.firstCaptionAt == nil, let startOffset {
                    Task { _ = try? await self.client?.request("room/activity", body: [:]) }
                    self.firstCaptionAt = min(ProcessInfo.processInfo.systemUptime, recognitionStart + startOffset)
                }
                switch VoiceRecognitionUpdate(text: text, final: final, failed: error != nil) {
                case .pending:
                    break
                case .submit(let words):
                    self.commitVoice(words)
                case .caption(let words):
                    guard words != self.currentText else { return }
                    self.recognitionFailures = 0
                    if self.firstCaptionAt == nil { self.firstCaptionAt = ProcessInfo.processInfo.systemUptime }
                    self.currentText = words
                    self.liveTranscript = words
                    guard !self.dictating, self.finalResultTimer == nil else { return }
                    self.utteranceTimer?.cancel()
                    let partialAt = ProcessInfo.processInfo.systemUptime
                    let addressed = self.focus.request(words, at: partialAt) != nil || self.facingRequest(words, at: partialAt)
                    let unfinished = addressed && needsContinuation(words)
                    self.turnProbe?.cancel()
                    self.modelHold = false
                    if addressed, !unfinished, self.useLocalTurnModel {
                        self.turnProbe = Task {
                            guard (try? await Task.sleep(for: .milliseconds(650))) != nil else { return }
                            let result = try? await self.client?.request("voice/endpoint", body: ["text": words])
                            guard self.speechEpoch == epoch, !Task.isCancelled, self.currentText == words else { return }
                            self.modelHold = result?["hold"] as? Bool == true
                        }
                    }
                    self.utteranceTimer = Task {
                        while self.speechEpoch == epoch && !Task.isCancelled {
                            let now = ProcessInfo.processInfo.systemUptime
                            let silence = endpoint.remaining(afterPartial: partialAt, now: now)
                            let extra = unfinished || self.modelHold ? partialAt + 3.5 - now : 0
                            let delay = max(silence, extra)
                            if delay <= 0 {
                                self.finishCapture()
                                return
                            }
                            try? await Task.sleep(for: .seconds(delay))
                        }
                    }
                case .retry:
                    if !self.currentText.isEmpty && (self.dictating || roomRequest(self.currentText) != nil) {
                        self.addLine("Service", "I couldn't confirm those words. Please try again.")
                    }
                    if self.captureMode == .finishingDraft { self.captureMode = .room }
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
                finishCapture()
            }
        }
    }

    private func finishCapture() {
        guard finalResultTimer == nil else { return }
        let epoch = speechEpoch
        endAudioInput()
        // Let Apple's recognizer finish processing the audio it has already accepted.
        recognition?.finish()
        finalResultTimer = Task {
            guard (try? await Task.sleep(for: .seconds(2))) != nil, speechEpoch == epoch else { return }
            if !currentText.isEmpty && (dictating || roomRequest(currentText) != nil) {
                addLine("Service", "I couldn't confirm those words. Please try again.")
            }
            if captureMode == .finishingDraft { captureMode = .room }
            stopRecognition()
            beginRecognition()
        }
    }

    private func endAudioInput() {
        engine.stop()
        if tapInstalled {
            engine.inputNode.removeTap(onBus: 0)
            tapInstalled = false
        }
        request?.endAudio()
        request = nil
    }

    private func stopRecognition() {
        interruptedPlayback = ""
        speechEpoch = UUID()
        utteranceTimer?.cancel()
        turnProbe?.cancel()
        modelHold = false
        recognitionLimit?.cancel()
        finalResultTimer?.cancel()
        finalResultTimer = nil
        endAudioInput()
        recognition?.cancel()
        recognition = nil
        currentText = ""
        firstCaptionAt = nil
        activity = nil
        liveTranscript = ""
    }

    func toggleDictation() {
        guard microphone, !busy, !speaking, captureMode != .finishingDraft else { return }
        if dictating && recognition != nil {
            captureMode = .finishingDraft
            finishCapture()
            return
        }
        captureMode = dictating ? .room : .dictation
        stopRecognition()
        beginRecognition()
    }

    private func facingRequest(_ words: String, at now: TimeInterval) -> Bool {
        if conversationActive(now) { return !words.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        return acceptFacing && cameraRunning && facing.accepts(words, from: firstCaptionAt ?? now, until: now, hasOffer: facingOffer != nil && facingSince == facing.since, hasContext: facingContext != nil && facingSince == facing.since)
    }

    private func commitVoice(_ finalText: String) {
        // A partial “wait” silences playback immediately. Keep listening in case
        // the final caption is a correction such as “wait, I meant Thursday”.
        if !interruptedPlayback.isEmpty && ConversationFocus.pauseOnly(finalText) {
            stopRecognition()
            beginRecognition()
            return
        }
        if captureMode.appendFinalToDraft(finalText, draft: &draft) {
            draftContainsVoice = true
            stopRecognition()
            beginRecognition()
            return
        }
        let now = ProcessInfo.processInfo.systemUptime
        if roomRequest(finalText) == nil && introduction(finalText, at: now) { return }
        let identified = profileForTurn(at: now)
        let personal = identified != nil && finalText.range(of: #"(?i)\b(?:i|my|we|our)\b|^what (?:did i|do you)"#, options: .regularExpression) != nil
        let focused = focus.request(finalText, at: now)
        let useFacing = focused == nil && (personal || facingRequest(finalText, at: now))
        let request = focused ?? (useFacing ? finalText : nil)
        if request == "" {
            focus.open(at: now, question: false, called: true)
        }
        guard let text = request, !text.isEmpty else {
            stopRecognition()
            beginRecognition()
            return
        }
        if !useFacing, roomRequest(finalText) != nil,
           text.range(of: #"(?i)^(?:can we talk|let['’]s (?:talk|chat)|start a conversation)[?.! ]*$"#, options: .regularExpression) != nil {
            conversationStarted = ProcessInfo.processInfo.systemUptime
            focus.clear()
            facingOffer = nil
            facingContext = nil
            addLine("Talos", "Sure. What's on your mind?")
            say("Sure. What's on your mind?")
            return
        }
        if !useFacing { facingOffer = nil; facingContext = nil }
        let followUp = focus.isFollowUp(finalText, at: now) || (!interruptedPlayback.isEmpty && roomRequest(finalText) == nil)
        focus.clear()
        if !useFacing && needsContinuation(text) && text.range(of: #"(?i)^(?:(?:please|could you|can you) )?(?:remind|add|create|save|make a note|record)\b"#, options: .regularExpression) != nil {
            if draft.isEmpty {
                draft = text
                draftContainsVoice = true
                addLine("Talos", "I heard part of a request. Finish it in the message field or use Dictate a message.")
            } else {
                addLine("Talos", "I heard only part of that request. Please repeat it.")
            }
            stopRecognition()
            beginRecognition()
        } else if text.count > 800 {
            stopRecognition()
            addLine("Talos", "That turn was too long. Could you split it into two?")
            beginRecognition()
        } else {
            send(text, source: "voice", speechEnd: activity?.lastActivity, followUp: followUp, viaFacing: useFacing || identified != nil, visitorID: identified?.id)
        }
    }

    func send(_ text: String, source: String = "text", speechEnd: TimeInterval? = nil, followUp: Bool = false, viaFacing: Bool = false, visitorID: String? = nil) {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !busy, !speaking, let client else { return }
        guard text.count <= 800 else { status = "Send up to 800 characters per turn."; return }
        if !viaFacing, !isTrial, let prefix = text.range(of: #"(?i)^(?:get me ready for|track (?:a |the )?goal for|keep me ready for)\s+"#, options: .regularExpression) {
            let topic = String(text[prefix.upperBound...]).trimmingCharacters(in: .punctuationCharacters)
            guard !topic.isEmpty else { return }
            busy = true; stopRecognition()
            Task {
                defer { busy = false; beginRecognition() }
                do {
                    if source == "voice" && !calendar.connected { throw RoomError("Connect Calendar with Next meeting brief first.") }
                    guard let meeting = try await calendar.matching(topic) else { throw RoomError("No upcoming timed event matched " + topic) }
                    try await goals.reload()
                    goals.prepare(meeting, outcome: text)
                    presentGoals = true
                    addLine("Talos", "I found " + meeting.title + ". Review the goal and choose any required document.")
                } catch { addLine("Service", error.localizedDescription) }
            }
            return
        }
        if !viaFacing, !isTrial, let meeting = MeetingRequest(text) {
            if source == "voice" && !calendar.connected {
                stopRecognition()
                let message = "Choose Next meeting brief in the app, or type the request to use your calendar."
                addLine("Talos", message)
                say(message)
            } else {
                showMeetingBrief(meeting.window, announce: source == "voice", departureReminder: meeting.departureReminder)
            }
            return
        }
        busy = true
        stopRecognition()
        addLine("You", text)
        status = "Thinking…"
        let submitted = Date()
        Task {
            do {
                let interval = attentionInterval
                let offer = interval == facingSince ? facingOffer : nil
                let context = interval == facingSince ? facingContext : nil
                facingOffer = nil
                facingContext = nil
                let route = viaFacing ? "voice/facing" : "turn"
                var body: [String: Any] = [
                    "camera_available": cameraRunning,
                    "offer": offer ?? "", "context": context ?? "",
                    "text": text, "source": source, "remember": rememberVoice || visitorID != nil, "visitor": visitorID ?? "", "follow_up": followUp, "request_id": UUID().uuidString,
                    "occupied": (cameraRunning && Date().timeIntervalSince(presenceAt) < 75
                                 ? latestPresence : nil).map { $0 as Any } ?? NSNull(),
                ]
                var result = try await client.request(route, body: body)
                if result["request_frame"] as? Bool == true {
                    let epoch = cameraEpoch
                    let snapshot = await camera?.snapshot()
                    if let snapshot, cameraRunning, epoch == cameraEpoch {
                        body["camera_jpeg"] = snapshot.jpeg
                        body["frame_people"] = snapshot.people.map { $0 as Any } ?? NSNull()
                    } else { body["camera_jpeg"] = "" }
                    result = try await client.request(route, body: body)
                }
                if viaFacing && interval != nil && interval == attentionInterval {
                    facingOffer = result["offer"] as? String
                    facingContext = result["context"] as? String
                    facingSince = interval
                }
                let reply = result["reply"] as? String ?? ""
                let replyMs = Int(Date().timeIntervalSince(submitted) * 1000)
                let planningMs = result["planning_ms"] as? Int ?? 0
                let queueMs = result["queue_ms"] as? Int ?? 0
                lastTiming = "Reply \(replyMs) ms · decision \(planningMs) ms · queue \(queueMs) ms"
                if !reply.isEmpty {
                    addLine("Talos", reply)
                    for step in result["steps"] as? [[String: Any]] ?? [] {
                        if let id = step["id"] as? Int, let detail = step["detail"] as? String,
                           let tool = step["tool"] as? String {
                            let label = ["create_task": "Task", "save_note": "Note", "set_reminder": "Reminder"][tool] ?? "Step"
                            addLine("\(label) #\(id)", detail)
                        }
                    }
                    if let sources = result["sources"] as? [[String: String]] {
                        for source in sources {
                            if let title = source["title"], let url = source["url"] {
                                addLine("Source", "\(title) — \(url)")
                            }
                        }
                    }
                    if source == "voice" {
                        if let speechEnd {
                            playbackMeasurement = (result["id"] as? Int, speechEnd)
                        }
                        openFocusAfterPlayback = source == "voice" && !viaFacing
                        say(reply, turnStart: submitted)
                    }
                }
                status = "Connected"
            } catch {
                status = error.localizedDescription
                addLine("Service", status)
            }
            busy = false
            beginRecognition()
        }
    }

    func previewDraft() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let client, !busy, !speaking, !dictating, !draftContainsVoice, (1...800).contains(text.count) else { return }
        busy = true
        stopRecognition()
        Task {
            defer { busy = false; beginRecognition() }
            do {
                let plan = try await client.request("work/preview", body: ["text": text])
                guard let id = plan["id"] as? String, let steps = plan["steps"] as? [[String: Any]] else {
                    throw RoomError("Talos returned an incomplete plan.")
                }
                guard draft.trimmingCharacters(in: .whitespacesAndNewlines) == text else { return }
                workPlanLines = steps.compactMap { step in
                    guard let tool = step["tool"] as? String, let detail = step["detail"] as? String else { return nil }
                    switch tool {
                    case "set_reminder": return "Remind in \(step["remind_in_minutes"] as? Int ?? 0) min · \(detail)"
                    case "create_task": return "Task · \(detail)"
                    case "save_note": return "Note · \(detail)"
                    default: return nil
                    }
                }
                guard steps.count == workPlanLines.count else { throw RoomError("Unknown step in work plan.") }
                remainingWork = plan["remaining"] as? String ?? ""
                workPlanID = id
            } catch { addLine("Service", error.localizedDescription) }
        }
    }

    func dismissPlan() {
        workPlanID = nil
        workPlanLines = []
        remainingWork = ""
    }

    func applyPlan() {
        guard let client, let id = workPlanID, !workPlanLines.isEmpty, !busy, !speaking, !dictating else { return }
        busy = true
        stopRecognition()
        Task {
            defer { busy = false; beginRecognition() }
            do {
                let result = try await client.request("work/apply", body: ["id": id])
                if let reply = result["reply"] as? String { addLine("Talos", reply) }
                if !remainingWork.isEmpty { addLine("Still open", remainingWork) }
                if workPlanID == id { draft = ""; dismissPlan() }
            } catch { addLine("Service", error.localizedDescription) }
        }
    }

    func showMeetingBrief(_ window: MeetingWindow, announce: Bool = false, departureReminder: Bool = false) {
        guard !busy, !speaking, !dictating, let client else { return }
        busy = true
        stopRecognition()
        Task {
            defer {
                busy = false
                beginRecognition()
            }
            do {
                guard let meeting = try await calendar.next(in: window) else {
                    addLine("Calendar", window == .tomorrow ? "No upcoming timed event tomorrow." :
                            "No upcoming timed event in the next seven days.")
                    return
                }
                addLine("Calendar", "\(meeting.title) · \(meeting.start.formatted(date: .abbreviated, time: .shortened))")
                let brief = try await client.request("meeting/brief", body: [
                    "topic": String(meeting.title.prefix(160)), "departure_reminder": departureReminder,
                    "source": announce ? "voice" : "text",
                ])
                if let reply = brief["reply"] as? String {
                    addLine("Talos", reply)
                    if let question = brief["question"] as? String {
                        addLine("Talos", question)
                        if announce { openFocusAfterPlayback = true; say("Your meeting brief is on screen. " + question) }
                    } else if announce {
                        say("Your meeting brief is on screen.")
                    }
                }
            } catch {
                addLine("Service", error.localizedDescription)
            }
        }
    }

    func toggleCamera() {
        if cameraRunning {
            cameraEpoch = UUID()
            camera?.stop()
            camera = nil
            previewSession = nil
            cameraRunning = false
            facing = FacingAttention()
            leaveVisitor()
            facingOffer = nil
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
            camera = CameraDetector(checkFacing: acceptFacing,
                onSample: { [weak self] face, stamp in
                    Task { @MainActor in
                        guard let self, self.cameraEpoch == epoch else { return }
                        self.inspectVisitor(face, at: stamp)
                    }
                },
                onFacing: { [weak self] available, stamp in
                    Task { @MainActor in
                        guard let self, self.cameraEpoch == epoch, self.acceptFacing else { return }
                        self.facing.observe(available, at: stamp)
                        if !available {
                            if !self.conversationActive(stamp) { self.facingOffer = nil; self.facingContext = nil }
                            self.leaveVisitor()
                        }
                    }
                },
                onObservation: { [weak self] occupied in
                    Task { @MainActor in
                        guard let self, self.cameraEpoch == epoch else { return }
                        self.occupancy = occupied.map { $0 ? "Person detected" : "No person detected" } ?? "Camera analysis unavailable · use Talk for 90 seconds"
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
            camera?.setVisitors(rememberVisitors)
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
        guard microphone || cameraRunning, !busy, !speaking, !dictating, currentText.isEmpty else { return }
        do {
            if let pendingAcknowledgment {
                _ = try await client?.request("speech/ack", body: ["id": pendingAcknowledgment])
                self.pendingAcknowledgment = nil
            }
            let result = try await client?.request("speech")
            modelState = result?["model_state"] as? String ?? ""
            endpointFailures = 0
            guard microphone || cameraRunning, !busy, !speaking, !dictating, currentText.isEmpty,
                  let item = result?["item"] as? [String: Any],
                  let id = item["id"] as? Int, let text = item["text"] as? String else { return }
            addLine("Talos", text)
            if item["kind"] as? String != "reminder" && !speakUnprompted {
                pendingAcknowledgment = id
                return
            }
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

    private func interruptForConversation() {
        playbackEpoch = UUID()
        speechRequest?.cancel()
        player?.stop()
        systemUtterance = nil
        if speaker.isSpeaking { speaker.stopSpeaking(at: .immediate) }
        if let speechID { pendingAcknowledgment = speechID }
        finishSpeech(delivered: false, keepListening: true)
    }

    private func systemSpeech(_ text: String) {
        let utterance = AVSpeechUtterance(string: text)
        systemUtterance = utterance
        speaker.speak(utterance)
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
        playbackText = text
        speechID = id
        stopRecognition()
        beginRecognition()
        guard let kokoroVoice, let client else {
            systemSpeech(text)
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
                    if index == 0 { recordPlaybackStart() }
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
                systemSpeech(clips.dropFirst(played).joined(separator: " "))
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

    private func recordPlaybackStart() {
        guard let measurement = playbackMeasurement else { return }
        playbackMeasurement = nil
        let elapsed = Int((ProcessInfo.processInfo.systemUptime - measurement.speechEnd) * 1000)
        lastTiming += " · speech end to playback \(elapsed) ms"
        guard let id = measurement.id else { return }
        Task {
            _ = try? await client?.request("voice/latency", body: ["id": id, "elapsed_ms": elapsed])
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didStart utterance: AVSpeechUtterance) {
        Task { @MainActor in if systemUtterance === utterance { recordPlaybackStart() } }
    }

    private func finishSpeech(delivered: Bool, keepListening: Bool = false) {
        if delivered, let speechID { pendingAcknowledgment = speechID }
        playbackMeasurement = nil
        speechID = nil
        systemUtterance = nil
        if !keepListening { stopRecognition() }
        if openFocusAfterPlayback {
            focus.open(at: ProcessInfo.processInfo.systemUptime, question: playbackText.contains("?"))
        }
        openFocusAfterPlayback = false
        playbackText = ""
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
        Task { @MainActor in if systemUtterance === utterance { finishSpeech(delivered: true) } }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor in if systemUtterance === utterance { finishSpeech(delivered: false) } }
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
            Text(room.isTrial ? "Talos practice room" : "Talos").font(.largeTitle.bold())
            Text(room.status).foregroundStyle(.secondary)
            HStack {
                Text(room.voiceLabel)
                if !room.modelState.isEmpty { Text("Model: \(room.modelState)") }
                if !room.lastTiming.isEmpty { Text(room.lastTiming) }
            }.font(.caption).foregroundStyle(.secondary)
            HStack {
                Button(room.conversationStarted == nil ? "Talk for 90 seconds" : "End conversation", action: room.toggleConversation)
                    .disabled(room.busy || room.speaking)
                Button(room.microphone ? "Microphone on" : "Microphone off", action: room.toggleMicrophone)
                if room.microphone {
                    Button(room.dictating ? "Finish dictation" : "Dictate a message", action: room.toggleDictation)
                        .disabled(room.busy || room.speaking)
                }
                Button(room.cameraRunning ? "Camera on" : "Camera off", action: room.toggleCamera)
                Button("Goals") { room.goals.open(); room.presentGoals = true }
                Menu("Memory") {
                    Button("Private sources and commitments") { room.presentSources = true }
                    Button("Open memory folder", action: room.openVault)
                    ForEach(room.profiles, id: \.id) { profile in
                        Button("Forget visual reference for " + profile.name) { room.forgetCandidate = profile }
                    }
                }
                Button("Next meeting brief") { room.showMeetingBrief(.upcoming) }
                    .disabled(room.isTrial || room.busy || room.speaking || room.dictating)
                if room.speaking { Button("Stop speaking", action: room.stopSpeaking) }
            }
            if room.microphone {
                Text(room.dictating ? "Pause as needed. Finish dictation, review the message, then send." :
                     (room.acceptFacing && room.cameraRunning ? "Face the Mac to ask a question, or say “Hey Talos”." : "Start a hands-free request with “Hey Talos”.")).font(.caption)
            }
            if let session = room.previewSession {
                HStack(alignment: .center, spacing: 16) {
                    CameraPreview(session: session)
                        .frame(width: 224, height: 126)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Live camera").font(.headline)
                        Text(room.occupancy).foregroundStyle(.secondary)
                        Text(room.acceptFacing ? "Facing detection · visual questions use one local frame" : "Visual questions use one local frame").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            if !room.liveTranscript.isEmpty {
                Text("Hearing: \(room.liveTranscript)").foregroundStyle(.secondary)
            }
            if room.draft.count > 800 {
                Text("Shorten the message to 800 characters before sending.").font(.caption)
            }
            Toggle("Accept direct requests while one person faces the Mac", isOn: $room.acceptFacing)
            Toggle("Offer introductions and remember consenting visitors", isOn: $room.rememberVisitors)
                .disabled(!room.acceptFacing)
            Toggle("Use local model for mid-sentence pauses", isOn: $room.useLocalTurnModel)
            Toggle("Speak proactive updates and introductions", isOn: $room.speakUnprompted)
            Toggle("Remember voice transcripts", isOn: $room.rememberVoice)
            if let name = room.visitorName { Text("Remembering this conversation with " + name).font(.caption) }
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
            if room.workPlanID != nil {
                VStack(alignment: .leading) {
                    Text("Proposed local changes").font(.headline)
                    ForEach(Array(room.workPlanLines.enumerated()), id: \.offset) { _, line in Text(line) }
                    if !room.remainingWork.isEmpty { Text("Still open: \(room.remainingWork)") }
                    HStack {
                        Button("Apply listed steps", action: room.applyPlan).disabled(room.workPlanLines.isEmpty || room.busy || room.speaking || room.dictating)
                        Button("Dismiss", action: room.dismissPlan).disabled(room.busy)
                    }
                }
            }
            HStack {
                TextField("Message Talos", text: $room.draft).onSubmit(submit)
                Button("Preview plan", action: room.previewDraft)
                    .disabled(room.busy || room.speaking || room.dictating || room.draftContainsVoice || room.draft.isEmpty || room.draft.count > 800)
                Button("Send", action: submit).disabled(room.busy || room.speaking || room.dictating || room.draft.isEmpty || room.draft.count > 800)
            }
        }
        .padding(24)
        .frame(minWidth: 660, minHeight: 440)
        .alert("Forget this visual reference?", isPresented: Binding(get: { room.forgetCandidate != nil },
                set: { if !$0 { room.forgetCandidate = nil } }), presenting: room.forgetCandidate) { profile in
            Button("Forget reference", role: .destructive) { room.forgetReference(profile) }
            Button("Cancel", role: .cancel) { }
        } message: { profile in Text("Talos will stop suggesting " + profile.name + " from the camera. Saved conversation notes will remain.") }
        .sheet(isPresented: $room.presentGoals, onDismiss: { room.goals.showing = false }) { GoalPanel(goals: room.goals) }
        .sheet(isPresented: $room.presentSources) { PrivateSources(model: room.privateSources) }
        .task { room.connect() }
    }

    private func submit() {
        guard !room.busy, !room.speaking, !room.dictating, room.draft.count <= 800 else { return }
        room.send(room.draft, source: room.draftContainsVoice ? "voice" : "text")
        room.draft = ""
    }
}

final class TalosAppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
}

#if !TALOS_CONTROLLER_REPLAY
@main
#endif
struct TalosApp: App {
    @NSApplicationDelegateAdaptor(TalosAppDelegate.self) var delegate
    @StateObject private var room = RoomController()

    var body: some Scene {
        Window("Talos", id: "room") { RoomView(room: room) }
    }
}
