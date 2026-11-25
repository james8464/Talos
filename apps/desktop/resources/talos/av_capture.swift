import AVFoundation
import CoreImage
import Foundation

// This helper runs only after the operator passes an explicit consent flag.
// It requests access when needed, captures one frame and one short clip, then
// exits. The caller deletes both files after verifying the endpoints.

enum CaptureFailure: Error {
    case message(String)
}

func permissionName(_ value: AVAuthorizationStatus) -> String {
    switch value {
    case .authorized: return "authorized"
    case .denied: return "denied"
    case .restricted: return "restricted"
    case .notDetermined: return "not_determined"
    @unknown default: return "unknown"
    }
}

func requireAccess(_ media: AVMediaType, _ label: String) throws {
    switch AVCaptureDevice.authorizationStatus(for: media) {
    case .authorized:
        return
    case .notDetermined:
        let ready = DispatchSemaphore(value: 0)
        var granted = false
        AVCaptureDevice.requestAccess(for: media) { allowed in
            granted = allowed
            ready.signal()
        }
        if ready.wait(timeout: .now() + 30) == .timedOut {
            throw CaptureFailure.message("\(label) permission timed out")
        }
        if !granted {
            throw CaptureFailure.message("\(label) permission denied")
        }
    default:
        throw CaptureFailure.message("\(label) permission is not authorized")
    }
}

final class FrameGrabber: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    let ready = DispatchSemaphore(value: 0)
    let context = CIContext()
    var jpeg: Data?

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        guard jpeg == nil, let buffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let image = CIImage(cvPixelBuffer: buffer)
        if let data = context.jpegRepresentation(of: image, colorSpace: CGColorSpaceCreateDeviceRGB(),
                                                 options: [:]) {
            jpeg = data
            ready.signal()
        }
    }
}

func captureFrame(to path: String) throws {
    guard let device = AVCaptureDevice.default(for: .video) else {
        throw CaptureFailure.message("no camera device is available")
    }
    let input = try AVCaptureDeviceInput(device: device)
    let output = AVCaptureVideoDataOutput()
    let session = AVCaptureSession()
    session.sessionPreset = .photo
    guard session.canAddInput(input), session.canAddOutput(output) else {
        throw CaptureFailure.message("the camera cannot be configured")
    }
    session.addInput(input)
    session.addOutput(output)
    let grabber = FrameGrabber()
    output.setSampleBufferDelegate(grabber, queue: DispatchQueue(label: "talos.capture.camera"))
    session.startRunning()
    defer { session.stopRunning() }
    if grabber.ready.wait(timeout: .now() + 10) == .timedOut {
        throw CaptureFailure.message("camera frame timed out")
    }
    guard let data = grabber.jpeg else {
        throw CaptureFailure.message("camera returned no frame")
    }
    try data.write(to: URL(fileURLWithPath: path), options: .atomic)
}

func captureAudio(to path: String, seconds: Double) throws {
    let settings: [String: Any] = [
        AVFormatIDKey: Int(kAudioFormatLinearPCM),
        AVSampleRateKey: 24_000.0,
        AVNumberOfChannelsKey: 1,
        AVLinearPCMBitDepthKey: 16,
        AVLinearPCMIsFloatKey: false,
        AVLinearPCMIsBigEndianKey: false,
    ]
    let recorder = try AVAudioRecorder(url: URL(fileURLWithPath: path), settings: settings)
    guard recorder.record(forDuration: seconds) else {
        throw CaptureFailure.message("the microphone did not start")
    }
    while recorder.isRecording {
        RunLoop.current.run(until: Date().addingTimeInterval(0.05))
    }
    recorder.stop()
}

let arguments = CommandLine.arguments
guard arguments.count == 4, let seconds = Double(arguments[3]), seconds > 0, seconds <= 10 else {
    FileHandle.standardError.write(Data("usage: av_capture FRAME.jpg CLIP.wav SECONDS\n".utf8))
    exit(2)
}

do {
    try requireAccess(.video, "camera")
    try requireAccess(.audio, "microphone")
    try captureFrame(to: arguments[1])
    try captureAudio(to: arguments[2], seconds: seconds)
    let payload: [String: Any] = [
        "camera_permission": permissionName(AVCaptureDevice.authorizationStatus(for: .video)),
        "microphone_permission": permissionName(AVCaptureDevice.authorizationStatus(for: .audio)),
        "camera": true,
        "audio": true,
        "seconds": seconds,
    ]
    let data = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
    print(String(data: data, encoding: .utf8) ?? "{}")
} catch let CaptureFailure.message(detail) {
    FileHandle.standardError.write(Data((detail + "\n").utf8))
    exit(1)
} catch {
    FileHandle.standardError.write(Data(("capture failed: \(error)\n").utf8))
    exit(1)
}
