import AppKit
import AVFoundation
import Foundation
import Speech
import SwiftUI
import Vision
import CoreImage
import PDFKit
import UniformTypeIdentifiers

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
    @MainActor
    func spokenTurn(body: [String: Any], receive: ([String: Any]) async throws -> Void) async throws -> [String: Any] {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/v1/voice/facing/stream")!)
        request.timeoutInterval = 150
        request.httpMethod = "POST"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (bytes, response) = try await Self.transport.bytes(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else {
            throw RoomError("The spoken request failed.")
        }
        for try await line in bytes.lines {
            try Task.checkCancellation()
            guard line.utf8.count <= 248192,
                  let event = try JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any] else {
                throw RoomError("Invalid spoken response.")
            }
            if let result = event["result"] as? [String: Any] { return result }
            if let error = event["error"] as? String { throw RoomError(error) }
            try await receive(event)
        }
        throw RoomError("The spoken response ended early.")
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
    func setVisitors(_ enabled: Bool) {
        queue.async {
            if enabled && !self.checkVisitors { self.cadence.requestAnalysis() }
            self.checkVisitors = enabled
        }
    }
    private var checkFacing: Bool
    func setFacing(_ enabled: Bool) {
        queue.async {
            if enabled && !self.checkFacing { self.cadence.requestAnalysis() }
            self.checkFacing = enabled
        }
    }
    private let detection = VNDetectHumanRectanglesRequest()
    private var cadence = CameraCadence()
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
                self.cadence.requestAnalysis()
                guard self.session.isRunning, self.cadence.hasFreshFrame(at: ProcessInfo.processInfo.systemUptime),
                      let buffer = self.latestBuffer else { continuation.resume(returning: nil); return }
                if self.framePeople == nil {
                    let handler = VNImageRequestHandler(cvPixelBuffer: buffer, orientation: .up)
                    self.framePeople = try? self.countPeople(handler)
                }
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
            self.framePeople = nil
            self.cadence = CameraCadence()
            self.session.stopRunning()
            for output in self.session.outputs { self.session.removeOutput(output) }
            for input in self.session.inputs { self.session.removeInput(input) }
        }
    }

    private func countPeople(_ handler: VNImageRequestHandler) throws -> Int {
        try handler.perform([detection])
        return (detection.results ?? []).filter { $0.confidence >= 0.6 }.count
    }

    func captureOutput(
        _ output: AVCaptureOutput, didOutput sample: CMSampleBuffer, from connection: AVCaptureConnection
    ) {
        let timestamp = ProcessInfo.processInfo.systemUptime
        let work = cadence.next(at: timestamp, settledEmpty: occupied == false && candidate == false)
        guard work != .skip else { return }
        framePeople = nil
        latestBuffer = CMSampleBufferGetImageBuffer(sample)
        guard work == .analyze else { return }
        let now = Date()
        do {
            let handler = VNImageRequestHandler(cmSampleBuffer: sample, orientation: .up)
            let people = try countPeople(handler)
            framePeople = people
            let detected = people > 0
            if checkFacing {
                let sampleNeeded = checkVisitors && timestamp - lastSample >= 1.4
                let view = VisitorFrame.observe(handler, people: people, faces: faceRequest, sample: sampleNeeded)
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
    var draftLimit: Int { draftContainsVoice ? 4000 : 800 }
    @Published var draftSelection: TextSelection?
    private var draftSegment: DictationTextSession?
    private var dictationStartedMic = false
    private var draftBeforeDictation = ""
    private var wasVoiceDraft = false
    @Published private var captureMode = VoiceCaptureMode.room
    var dictating: Bool { captureMode != .room }
    @Published var occupancy = "Camera off"
    @Published var previewSession: AVCaptureSession?
    @Published var modelState = ""
    @Published var voiceLabel = "Voice: connecting"
    @Published var liveTranscript = ""
    @Published var lastTiming = ""
    private var kokoroVoice: String?
    private var kokoroRetryAfter: TimeInterval = 0
    private var checkingOutput = false
    private var reportingSystemOutput = false
    private var outputUpload: Task<Void, Never>?
    private var outputWatchdog: Task<Void, Never>?
    private var fallbackClips: ArraySlice<String> = []
    private var streamClipIndex: Int?
    private var failedStreamClip: Int?
    private var voiceClock: TimeInterval {
        #if TALOS_CONTROLLER_REPLAY
        if let replayVoiceClock { return replayVoiceClock() }
        #endif
        return ProcessInfo.processInfo.systemUptime
    }
    private var prefersKokoro: Bool {
        kokoroVoice != nil && voiceClock >= kokoroRetryAfter
    }
    #if TALOS_CONTROLLER_REPLAY
    // Test binaries can render into a silent sink on hosts without an audio device.
    var replayVoiceFetch: ((String) async throws -> [String: Any])?
    var replaySystemOutput: ((AVSpeechUtterance) -> Void)?
    var replayVoiceClock: (() -> TimeInterval)?
    var replayVoiceOutcome: ((String) -> Void)?
    var replayOutputTimeout: Duration?
    func replayConfigureVoice(_ name: String?) { kokoroVoice = name }
    func replayCheckVoiceOutput() { checkVoiceOutput() }
    func replaySystemStarted(_ utterance: AVSpeechUtterance) { speechSynthesizer(speaker, didStart: utterance) }
    func replaySystemFinished(_ utterance: AVSpeechUtterance) { speechSynthesizer(speaker, didFinish: utterance) }
    var replayClip: ((Data) throws -> Void)?
    var replayStop: (() -> Void)?
    var replayUtterance: ((String) -> Void)?
    func replayCaption(_ text: String) { commitVoice(text) }
    #endif
    private var streamID: String?
    private var streamTask: Task<Void, Never>?
    private var player: AVAudioPlayer?
    private var finalClip = false
    private var latestPresence: Bool?
    private var presenceAt = Date.distantPast
    private var resumeMicrophone = false
    private var resumeCamera = false
    private var reconnecting = false
    private var speechRequest: Task<Void, Never>?
    private var playbackEpoch = UUID()
    #if TALOS_CONTROLLER_REPLAY
    let silentMode = false
    #else
    let silentMode: Bool = {
        let arguments = ProcessInfo.processInfo.arguments
        let value = (ProcessInfo.processInfo.environment["TALOS_MUTE"] ?? "").lowercased()
        return arguments.contains("--mute")
            || ["1", "true", "yes", "on"].contains(value)
            || !arguments.contains("--unmute")
    }()
    #endif
    let isTrial = ProcessInfo.processInfo.arguments.contains("--room-trial")
    let goals = GoalController()
    let privateSources = PrivateSourceController()
    @Published var reviewingPlan = false
    @Published var presentGoals = false
    @Published var presentSources = false
    @Published var speakUnprompted = false {
        didSet { if !isTrial { UserDefaults.standard.set(speakUnprompted, forKey: "speakUnprompted") } }
    }
    private lazy var calendar = MeetingCalendar()
    let client: RoomClient?
    @Published var reviewingPlan = false
    private var vaultURL: URL? {
        let args = ProcessInfo.processInfo.arguments
        guard let i = args.firstIndex(of: "--token-file"), args.indices.contains(i+1) else { return nil }
        return URL(fileURLWithPath: args[i+1]).deletingLastPathComponent().appendingPathComponent("Talos Memory")
    }
    func openLiveActivity() {
        guard let state = vaultURL?.deletingLastPathComponent() else { return }
        let note = state.appendingPathComponent("Talos live.md")
        guard FileManager.default.fileExists(atPath: note.path) else {
            status = "Start the Talos memory workers to publish the live activity note."
            return
        }
        var url = URLComponents()
        url.scheme = "obsidian"
        url.host = "open"
        url.queryItems = [URLQueryItem(name: "path", value: note.path)]
        if let link = url.url, NSWorkspace.shared.open(link) { return }
        NSWorkspace.shared.open(note)
    }

    func openVault() {
        guard let url = vaultURL else { return }
        do {
            try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true,
                                                     attributes: [.posixPermissions: 0o700])
            NSWorkspace.shared.open(url)
        } catch { status = error.localizedDescription }
    }
    func importScheduleMemory() {
        guard !isTrial, let vault = vaultURL else { return }
        let folder = vault.deletingLastPathComponent().appendingPathComponent("knowledge-imports")
        Task {
            do {
                let sources = try await calendar.exportMemory(to: folder)
                status = sources.isEmpty ? "No Calendar or Reminders access granted."
                    : "Saved \(sources.joined(separator: " and ")) for the next memory refresh."
            } catch { status = error.localizedDescription }
        }
    }

    func importImageMemory() {
        guard !isTrial, let vault = vaultURL else { return }
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.allowedContentTypes = [.image, .pdf]
        guard panel.runModal() == .OK else { return }
        let files = panel.urls
        let folder = vault.deletingLastPathComponent().appendingPathComponent("knowledge-imports")
        Task {
            let rows = await Task.detached(priority: .utility) {
                files.compactMap { file -> [String: String]? in
                    func recognize(_ handler: VNImageRequestHandler) -> String {
                        let request = VNRecognizeTextRequest()
                        guard (try? handler.perform([request])) != nil else { return "" }
                        return (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
                    }
                    let text: String
                    if file.pathExtension.lowercased() == "pdf", let pdf = PDFDocument(url: file) {
                        text = (0..<min(pdf.pageCount, 15)).compactMap { index -> String? in
                            guard let page = pdf.page(at: index) else { return nil }
                            let image = page.thumbnail(of: NSSize(width: 1600, height: 2000), for: .mediaBox)
                            guard let data = image.tiffRepresentation else { return nil }
                            return recognize(VNImageRequestHandler(data: data))
                        }.joined(separator: "\n")
                    } else {
                        text = recognize(VNImageRequestHandler(url: file))
                    }
                    guard !text.isEmpty else { return nil }
                    return ["id": file.path, "path": file.path, "text": text]
                }
            }.value
            do {
                try MeetingCalendar.writeMemory(rows, kind: "OCR", folder: folder)
                status = "Saved text from \(rows.count) files for the next memory refresh."
            } catch { status = error.localizedDescription }
        }
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
    private var lastActivityPulse = -Double.infinity
    private var firstCaptionAt: TimeInterval?
    private var facingOffer: String?
    private var facingContext: String?
    private var facingSince: TimeInterval?
    private var playingCheckIn: String?
    private var checkInReply: (token: String, until: TimeInterval)?
    private var continuation: SpokenContinuation?
    private var conversationLastTurn: TimeInterval?
    @Published var conversationStarted: TimeInterval?
    private func conversationActive(_ now: TimeInterval) -> Bool {
        guard let start = conversationStarted else { return false }
        return now - max(start, conversationLastTurn ?? start) < 90
    }
    func toggleConversation() {
        guard client != nil else { status = "Connect to the Talos service first."; return }
        continuation = nil
        conversationLastTurn = nil
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
    @Published var useLocalTurnModel = false {
        didSet { if !isTrial { UserDefaults.standard.set(useLocalTurnModel, forKey: "roomPauseModel") } }
    }
    private var recognitionFailures = 0
    private var microphoneStarting = false
    private var cameraStarting = false
    private var endpointFailures = 0
    private var speechID: Int?
    private var pendingAcknowledgment: (id: Int, playback: String)?
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
        if !isTrial { useLocalTurnModel = UserDefaults.standard.bool(forKey: "roomPauseModel") }
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
                        voiceLabel = kokoroVoice.map { "Voice: Kokoro · \($0)" } ?? "Voice: text only"
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
                                status = "Room ready"
                                startRoom()
                            }
                        }
                    } catch { status = error.localizedDescription }
                }
                if conversationStarted != nil && !speaking && !busy && !conversationActive(ProcessInfo.processInfo.systemUptime) {
                    conversationStarted = nil
                    continuation = nil
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
        if acceptFacing && !cameraRunning { toggleCamera() }
    }

    func toggleMicrophone() {
        if microphone {
            focus.clear()
            facingOffer = nil
            openFocusAfterPlayback = false
            microphone = false
            checkInReply = nil
            continuation = nil
            conversationStarted = nil
            leaveVisitor()
            captureMode = .room
            dictationStartedMic = false
            stopRecognition()
            status = "Microphone off."
            return
        }
        startMicrophone(forDictation: false)
    }

    private func startMicrophone(forDictation: Bool) {
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
                conversationStarted = nil
                status = "Allow microphone and speech recognition in System Settings."
                return
            }
            guard let recognizer, recognizer.supportsOnDeviceRecognition else {
                conversationStarted = nil
                status = "On-device speech recognition is unavailable for this Mac's language."
                return
            }
            if forDictation {
                dictationStartedMic = true
                captureMode = .dictation
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
        if dictating {
            var selection = NSRange(location: draft.utf16.count, length: 0)
            if let draftSelection, case .selection(let range) = draftSelection.indices {
                selection = NSRange(range, in: draft)
            }
            draftSegment = DictationTextSession(baseText: draft, replacementRange: selection)
            buffer.taskHint = .dictation
            buffer.addsPunctuation = true
        }
        buffer.requiresOnDeviceRecognition = true
        buffer.shouldReportPartialResults = true
        var hints = ["Talos", "Hey Talos", "Hello Talos"]
        if rememberVisitors {
            for profile in profiles.reversed() where hints.count < 64 {
                if !hints.contains(profile.name) { hints.append(profile.name) }
            }
        }
        buffer.contextualStrings = hints
        request = buffer
        let epoch = UUID()
        speechEpoch = epoch
        let endpoint = VoiceEndpoint()
        activity = endpoint
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { audio, _ in
            let compatible = SpeechAudioBufferNormalizer.speechCompatibleBuffer(from: audio)
            endpoint.observe(compatible, at: ProcessInfo.processInfo.systemUptime)
            buffer.append(compatible)
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
                    let engaged = self.streamID != nil || self.openFocusAfterPlayback || self.conversationActive(now) || (self.facingContext != nil &&
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
                if let startOffset {
                    let now = ProcessInfo.processInfo.systemUptime
                    if self.firstCaptionAt == nil || now - self.lastActivityPulse >= 5 {
                        self.lastActivityPulse = now
                        Task { _ = try? await self.client?.request("room/activity", body: [:]) }
                    }
                    if self.firstCaptionAt == nil {
                        self.firstCaptionAt = min(now, recognitionStart + startOffset)
                    }
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
                    if self.dictating {
                        self.updateDictation(words)
                        return
                    }
                    guard self.finalResultTimer == nil else { return }
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
                    if self.dictating {
                        self.recoverDictation(text)
                    } else if !self.currentText.isEmpty && roomRequest(self.currentText) != nil {
                        self.addLine("Service", "I couldn't confirm those words. Please try again.")
                    }
                    if self.captureMode == .finishingDraft { self.endDictationMode() }
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
            if dictating {
                recoverDictation(nil)
            } else if !currentText.isEmpty && roomRequest(currentText) != nil {
                addLine("Service", "I couldn't confirm those words. Please try again.")
            }
            if captureMode == .finishingDraft { endDictationMode() }
            stopRecognition()
            beginRecognition()
        }
    }

    private func recoverDictation(_ lastCaption: String?) {
        let caption = lastCaption?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let words = caption.isEmpty ? currentText : caption
        if !words.isEmpty {
            updateDictation(words)
            addLine("Service", "I kept the last caption in your draft. Review it before sending.")
        }
    }

    private func updateDictation(_ words: String) {
        guard let segment = draftSegment else { return }
        let update = segment.update(transcript: words)
        draft = update.text
        draftContainsVoice = true
        if let range = Range(update.selection, in: draft) {
            draftSelection = TextSelection(range: range)
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
        draftSegment = nil
        firstCaptionAt = nil
        activity = nil
        liveTranscript = ""
    }

    func toggleDictation() {
        continuation = nil
        guard !busy, !speaking, captureMode != .finishingDraft else { return }
        if !dictating {
            checkInReply = nil
            draftBeforeDictation = draft
            wasVoiceDraft = draftContainsVoice
        }
        if !microphone {
            startMicrophone(forDictation: true)
            return
        }
        if dictating && recognition != nil {
            captureMode = .finishingDraft
            finishCapture()
            return
        }
        if dictating { endDictationMode() } else { captureMode = .dictation }
        stopRecognition()
        beginRecognition()
    }

    private func endDictationMode() {
        captureMode = .room
        if dictationStartedMic {
            microphone = false
            status = "Microphone off."
        }
        dictationStartedMic = false
    }

    func cancelDictation() {
        guard dictating else { return }
        stopRecognition()
        draft = draftBeforeDictation
        draftContainsVoice = wasVoiceDraft
        draftSelection = nil
        endDictationMode()
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
        if dictating {
            updateDictation(finalText)
            if captureMode == .finishingDraft { endDictationMode() }
            stopRecognition()
            beginRecognition()
            return
        }
        let now = ProcessInfo.processInfo.systemUptime
        if roomRequest(finalText) == nil && introduction(finalText, at: now) { return }
        let identified = profileForTurn(at: now)
        let personal = identified != nil && finalText.range(of: #"(?i)\b(?:i|my|we|our)\b|^what (?:did i|do you)"#, options: .regularExpression) != nil
        let focused = focus.request(finalText, at: now)
        var useFacing = focused == nil && (personal || facingRequest(finalText, at: now))
        var request = focused ?? (useFacing ? finalText : nil)
        if request == nil, let checkInReply, now <= checkInReply.until,
           Self.isShortCancel(finalText) { request = finalText }
        if let saved = continuation {
            if identified != nil || now > saved.expires || saved.conversation != conversationStarted {
                continuation = nil
            } else if let response = saved.response(to: finalText, at: now, conversation: conversationStarted) {
                continuation = nil
                switch response {
                case .cancelled:
                    addLine("Talos", "Request cancelled.")
                    say("Request cancelled.")
                    return
                case .completed(let completed):
                    request = completed
                    useFacing = saved.viaFacing
                }
            } else if request != nil {
                continuation = nil
            }
        }
        if request == "" {
            focus.open(at: now, question: false, called: true)
        }
        guard let text = request, !text.isEmpty else {
            stopRecognition()
            beginRecognition()
            return
        }
        if conversationStarted != nil {
            if text.range(of: #"(?i)^(?:end (?:this |the |our )?conversation|stop listening to this conversation)[.!? ]*$"#,
                          options: .regularExpression) != nil {
                toggleConversation()
                addLine("Talos", "Conversation ended.")
                say("Conversation ended.")
                return
            }
            conversationLastTurn = now
        }
        if !useFacing, roomRequest(finalText) != nil,
           text.range(of: #"(?i)^(?:can we talk|let['’]s (?:talk|chat)|start a conversation)[?.! ]*$"#, options: .regularExpression) != nil {
            conversationLastTurn = nil
            conversationStarted = now
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
        if identified == nil, (!useFacing || conversationStarted != nil),
           let partial = SpokenContinuation(text, at: now, conversation: conversationStarted, viaFacing: useFacing) {
            continuation = partial
            let question = "Say continue, followed by the rest of your request, or say cancel that request."
            addLine("Talos", question)
            say(question)
        } else if text.count > 800 {
            stopRecognition()
            addLine("Talos", "That turn was too long. Could you split it into two?")
            beginRecognition()
        } else {
            send(text, source: "voice", speechEnd: activity?.lastActivity, followUp: followUp, viaFacing: useFacing || identified != nil, visitorID: identified?.id)
        }
    }

    private static func isShortCancel(_ text: String) -> Bool {
        let words = text.trimmingCharacters(in: .whitespacesAndNewlines.union(.punctuationCharacters)).lowercased()
        return ["cancel", "cancel it", "never mind"].contains(words)
    }

    func send(_ text: String, source: String = "text", reviewedDictation: Bool = false, speechEnd: TimeInterval? = nil, followUp: Bool = false, viaFacing: Bool = false, visitorID: String? = nil) {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !busy, !speaking, let client else { return }
        let prompt = checkInReply
        checkInReply = nil
        let replyToken = source == "voice" && !reviewedDictation && Self.isShortCancel(text)
            && ProcessInfo.processInfo.systemUptime <= (prompt?.until ?? 0) ? prompt?.token : nil
        let limit = reviewedDictation ? 4000 : 800
        guard text.unicodeScalars.count <= limit else { status = "This turn exceeds the message limit."; return }
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
        let requestID = UUID().uuidString
        let stream = viaFacing && source == "voice" && prefersKokoro
        if stream { streamID = requestID; failedStreamClip = nil }
        let task = Task {
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
                    "text": text, "source": source, "reviewed_dictation": reviewedDictation, "remember": rememberVoice || visitorID != nil, "visitor": visitorID ?? "", "follow_up": followUp, "request_id": requestID,
                    "occupied": (cameraRunning && Date().timeIntervalSince(presenceAt) < 75
                                 ? latestPresence : nil).map { $0 as Any } ?? NSNull(),
                ]
                var played: [String] = []
                var streamStarted = false
                var audioFailed = false
                var result: [String: Any]
                if let replyToken {
                    try await acknowledgePlayback(using: client)
                    body["reply_to_checkin"] = replyToken
                }
                if stream {
                    result = try await client.spokenTurn(body: body) { event in
                        guard self.streamID == requestID else { throw CancellationError() }
                        if event["started"] as? Bool == true {
                            streamStarted = true
                            self.speaking = true
                            self.playbackText = ""
                            if let speechEnd { self.playbackMeasurement = (nil, speechEnd) }
                            self.beginRecognition()
                        } else if let sentence = event["sentence"] as? String, !audioFailed {
                            do {
                                try await self.playStreamedSentence(sentence, client: client, index: played.count)
                                played.append(sentence)
                            } catch {
                                try Task.checkCancellation()
                                self.kokoroFailed()
                                audioFailed = true
                            }
                        }
                    }
                } else {
                    result = try await client.request(route, body: body)
                }
                try Task.checkCancellation()
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
                    if result["voice_recovery"] as? String == "test_output" {
                        checkVoiceOutput()
                    } else if source == "voice" {
                        if let failedStreamClip { played = Array(played.prefix(failedStreamClip)); audioFailed = true }
                        if streamStarted && !played.isEmpty {
                            if audioFailed {
                                while player?.isPlaying == true { try await Task.sleep(for: .milliseconds(10)) }
                                let remainder = String(reply.dropFirst(played.joined(separator: " ").count))
                                    .trimmingCharacters(in: .whitespacesAndNewlines)
                                if remainder.isEmpty { finishSpeech(delivered: true) }
                                else { systemSpeech(remainder) }
                            } else if player?.isPlaying == true {
                                finalClip = true
                            } else {
                                finishSpeech(delivered: true)
                            }
                        } else {
                            if let speechEnd { playbackMeasurement = (result["id"] as? Int, speechEnd) }
                            openFocusAfterPlayback = !viaFacing
                            say(reply, turnStart: submitted)
                        }
                    }
                }
                if !checkingOutput { status = "Connected" }
            } catch {
                if Task.isCancelled { return }
                if stream, speaking { stopSpeaking() }
                status = error.localizedDescription
                if reviewedDictation && draft.isEmpty {
                    draft = text
                    draftContainsVoice = true
                }
                addLine("Service", status)
            }
            if streamID == requestID {
                streamID = nil
                streamTask = nil
            }
            busy = false
            beginRecognition()
        }
        if stream { streamTask = task }
    }

    func submitDraft() {
        guard client != nil, !busy, !speaking, !dictating,
              draft.unicodeScalars.count <= draftLimit,
              !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        let text = draft
        let dictated = draftContainsVoice
        draft = ""
        send(text, source: dictated ? "voice" : "text", reviewedDictation: dictated)
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
                        self.occupancy = occupied.map { $0 ? "Person detected" : "No person detected" } ?? "Camera analysis unavailable · choose Talk to Talos"
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

    private func acknowledgePlayback(using client: RoomClient) async throws {
        guard let ack = pendingAcknowledgment else { return }
        _ = try await client.request("speech/ack", body: ["id": ack.id, "playback": ack.playback])
        if pendingAcknowledgment?.id == ack.id { pendingAcknowledgment = nil }
    }

    private func pollSpeech() async {
        guard microphone || cameraRunning, !busy, !speaking, !dictating, currentText.isEmpty else { return }
        do {
            if let client { try await acknowledgePlayback(using: client) }
            let result = try await client?.request("speech")
            modelState = result?["model_state"] as? String ?? ""
            endpointFailures = 0
            guard microphone || cameraRunning, !busy, !speaking, !dictating, currentText.isEmpty,
                  let item = result?["item"] as? [String: Any],
                  let id = item["id"] as? Int, let text = item["text"] as? String else { return }
            addLine("Talos", text)
            if item["kind"] as? String != "reminder" && !speakUnprompted {
                pendingAcknowledgment = (id, "suppressed")
                return
            }
            let checkIn = item["check_in"] as? Bool == true
            playingCheckIn = checkIn ? item["check_in_token"] as? String : nil
            openFocusAfterPlayback = checkIn
            let spoken = checkIn || item["kind"] as? String == "speech" ? text : "You have a local Talos notification."
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

    private func playStreamedSentence(_ sentence: String, client: RoomClient, index: Int) async throws {
        if silentMode { throw RoomError("Audio is muted.") }
        guard prefersKokoro else { throw RoomError("Kokoro is recovering.") }
        let text = spokenProjection(sentence)
        let result = try await client.request("voice", body: ["text": text])
        try Task.checkCancellation()
        guard let encoded = result["wav"] as? String, let data = Data(base64Encoded: encoded) else {
            throw RoomError("Kokoro returned invalid audio.")
        }
        #if TALOS_CONTROLLER_REPLAY
        if let replayClip {
            try replayClip(data)
            if index == 0 { recordPlaybackStart() }
            return
        }
        #endif
        let audio = try AVAudioPlayer(data: data)
        while player?.isPlaying == true { try await Task.sleep(for: .milliseconds(10)) }
        try Task.checkCancellation()
        guard prefersKokoro else { throw RoomError("Kokoro is recovering.") }
        streamClipIndex = index
        fallbackClips = [text]
        finalClip = false
        audio.delegate = self
        playbackText = text
        player = audio
        guard audio.play() else { throw RoomError("Kokoro audio could not play.") }
        if index == 0 { recordPlaybackStart() }
    }

    private func cancelStream() {
        guard let id = streamID else { return }
        streamID = nil
        streamTask?.cancel()
        streamTask = nil
        busy = false
        Task { _ = try? await client?.request("voice/stream/cancel", body: ["request_id": id]) }
    }

    private func stopPlayer() {
        #if TALOS_CONTROLLER_REPLAY
        replayStop?()
        #endif
        player?.stop()
    }

    private func interruptForConversation() {
        cancelStream()
        playbackEpoch = UUID()
        speechRequest?.cancel()
        stopPlayer()
        systemUtterance = nil
        if speaker.isSpeaking { speaker.stopSpeaking(at: .immediate) }
        finishSpeech(delivered: false, keepListening: true)
    }

    private func voiceOutcome(_ code: String) {
        #if TALOS_CONTROLLER_REPLAY
        if let replayVoiceOutcome { replayVoiceOutcome(code); return }
        #endif
        let previous = outputUpload
        let client = self.client
        outputUpload = Task {
            await previous?.value
            _ = try? await client?.request("voice/outcome", body: ["outcome": code])
        }
    }

    private func checkVoiceOutput() {
        if silentMode {
            voiceLabel = "Voice: muted"
            status = "Audio is muted."
            addLine("Voice", "Audio is muted.")
            return
        }
        guard kokoroVoice != nil, prefersKokoro else {
            voiceLabel = "Voice: text only"
            status = "High-quality voice unavailable; replies stay on screen."
            addLine("Voice", "No fallback voice will play.")
            return
        }
        checkingOutput = true
        status = "Testing voice output…"
        say("This is Talos checking voice output.")
    }

    private func kokoroFailed() {
        if voiceClock >= kokoroRetryAfter {
            voiceOutcome("kokoro_failed")
            addLine("Voice", "Kokoro audio failed. No fallback voice will play.")
        }
        kokoroRetryAfter = voiceClock + 60
        voiceLabel = "Voice: text only · Kokoro retry in one minute"
    }

    private func armOutputWatchdog(_ utterance: AVSpeechUtterance, seconds: Double) {
        outputWatchdog?.cancel()
        var delay = Duration.seconds(seconds)
        #if TALOS_CONTROLLER_REPLAY
        if let replayOutputTimeout { delay = replayOutputTimeout }
        #endif
        outputWatchdog = Task { [weak self] in
            do { try await Task.sleep(for: delay) } catch { return }
            guard let self, self.systemUtterance === utterance else { return }
            self.systemOutputFailed()
        }
    }

    private func systemOutputFailed() {
        systemUtterance = nil
        if speaker.isSpeaking { speaker.stopSpeaking(at: .immediate) }
        voiceOutcome("macos_unavailable")
        finishSpeech(delivered: false, outcome: "unknown")
        let message = "Voice playback is unavailable. Your reply is on screen. Check the Mac sound output."
        voiceLabel = "Voice: output unavailable"
        status = message
        addLine("Voice", message)
    }

    private func systemSpeech(_ text: String) {
        guard !text.isEmpty else { finishSpeech(delivered: false); return }
        voiceLabel = "Voice: text only"
        status = "High-quality voice unavailable; the reply is on screen."
        addLine("Voice", "No fallback voice will play.")
        finishSpeech(delivered: false, keepListening: true)
    }

    func stopSpeaking() {
        cancelStream()
        playbackEpoch = UUID()
        speechRequest?.cancel()
        systemUtterance = nil
        if speaker.isSpeaking { speaker.stopSpeaking(at: .immediate) }
        stopPlayer()
        finishSpeech(delivered: false)
    }

    private func fetchVoice(_ clip: String) async throws -> [String: Any] {
        #if TALOS_CONTROLLER_REPLAY
        if let replayVoiceFetch { return try await replayVoiceFetch(clip) }
        #endif
        guard let client else { throw RoomError("The voice service is unavailable.") }
        return try await client.request("voice", body: ["text": clip])
    }

    private func say(_ reply: String, id: Int? = nil, turnStart: Date? = nil) {
        checkInReply = nil
        if id == nil { playingCheckIn = nil }
        let text = spokenProjection(reply)
        #if TALOS_CONTROLLER_REPLAY
        if let replayUtterance { replayUtterance(text); return }
        #endif
        if silentMode {
            voiceLabel = "Voice: muted"
            status = "Audio is muted."
            return
        }
        guard let kokoroVoice, prefersKokoro else {
            voiceLabel = "Voice: text only"
            status = "High-quality voice unavailable; the reply is on screen."
            addLine("Voice", "No fallback voice will play.")
            return
        }
        speaking = true
        playbackText = text
        speechID = id
        stopRecognition()
        beginRecognition()
        let epoch = UUID()
        playbackEpoch = epoch
        let requested = Date()
        speechRequest = Task {
            var played = 0
            // Prepare the following sentence while the preceding one plays.
            let clips = turnStart != nil ? Self.speechClips(text) : [text]
            do {
                for (index, clip) in clips.enumerated() {
                    let result = try await fetchVoice(clip)
                    guard !Task.isCancelled, playbackEpoch == epoch else { return }
                    guard let encoded = result["wav"] as? String, let data = Data(base64Encoded: encoded) else {
                        throw RoomError("Kokoro returned invalid audio.")
                    }
                    let audio = try AVAudioPlayer(data: data)
                    while player?.isPlaying == true { try await Task.sleep(for: .milliseconds(15)) }
                    guard !Task.isCancelled, playbackEpoch == epoch else { return }
                    audio.delegate = self
                    streamClipIndex = nil
                    fallbackClips = clips[index...]
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
                kokoroFailed()
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
        Task { @MainActor in
            guard systemUtterance === utterance else { return }
            if reportingSystemOutput { voiceOutcome("macos_started") }
            armOutputWatchdog(utterance, seconds: max(20, Double(utterance.speechString.count) / 4))
            recordPlaybackStart()
        }
    }

    private func finishSpeech(delivered: Bool, keepListening: Bool = false, outcome: String? = nil) {
        outputWatchdog?.cancel()
        outputWatchdog = nil
        let completedTest = delivered && checkingOutput
        if completedTest {
            if systemUtterance == nil { voiceOutcome("kokoro_finished") }
            addLine("Voice", "The audio engine reported that the test finished. If it was silent, check the Mac sound output.")
        }
        checkingOutput = false
        reportingSystemOutput = false
        fallbackClips = []
        streamClipIndex = nil
        if let speechID { pendingAcknowledgment = (speechID, outcome ?? (delivered ? "played" : "interrupted")) }
        if delivered, let token = playingCheckIn {
            checkInReply = (token, ProcessInfo.processInfo.systemUptime + 8)
        }
        playingCheckIn = nil
        playbackMeasurement = nil
        speechID = nil
        systemUtterance = nil
        if !keepListening { stopRecognition() }
        if delivered && conversationStarted != nil { conversationLastTurn = ProcessInfo.processInfo.systemUptime }
        if openFocusAfterPlayback {
            focus.open(at: ProcessInfo.processInfo.systemUptime, question: playbackText.contains("?"))
        }
        openFocusAfterPlayback = false
        playbackText = ""
        speaking = false
        player = nil
        speechRequest = nil
        beginRecognition()
        if completedTest { status = "Voice test finished" }
    }

    private func playerFailed(_ failed: AVAudioPlayer) {
        guard player === failed else { return }
        kokoroFailed()
        player = nil
        if streamID != nil, let streamClipIndex {
            failedStreamClip = streamClipIndex
            return  // The pending turn will speak the remainder through the fallback.
        }
        playbackEpoch = UUID()
        speechRequest?.cancel()
        systemSpeech(fallbackClips.joined(separator: " "))
    }

    nonisolated func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        Task { @MainActor in playerFailed(player) }
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in
            guard self.player === player else { return }
            if !flag { playerFailed(player) }
            else if finalClip { finishSpeech(delivered: true) }
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor in
            guard systemUtterance === utterance else { return }
            if reportingSystemOutput { voiceOutcome("macos_finished") }
            finishSpeech(delivered: true)
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor in if systemUtterance === utterance { systemOutputFailed() } }
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
            .defaultSize(width: 580, height: 540)
        Settings { RoomSettings(room: room) }
    }
}
