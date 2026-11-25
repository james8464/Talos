import AVFoundation
import Foundation

// Read-only readiness probe. Creating a discovery session or asking for
// authorization status never opens a capture session or records media.

func statusName(_ value: AVAuthorizationStatus) -> String {
    switch value {
    case .authorized: return "authorized"
    case .denied: return "denied"
    case .restricted: return "restricted"
    case .notDetermined: return "not_determined"
    @unknown default: return "unknown"
    }
}

let cameraPermission = statusName(AVCaptureDevice.authorizationStatus(for: .video))
let microphonePermission = statusName(AVCaptureDevice.authorizationStatus(for: .audio))
let cameraDevice = AVCaptureDevice.default(for: .video) != nil
let microphoneDevice = AVCaptureDevice.default(for: .audio) != nil
let cameras = AVCaptureDevice.DiscoverySession(
    deviceTypes: [.builtInWideAngleCamera],
    mediaType: .video,
    position: .unspecified
).devices.count
let microphones = AVCaptureDevice.DiscoverySession(
    deviceTypes: [.builtInMicrophone],
    mediaType: .audio,
    position: .unspecified
).devices.count

let payload: [String: Any] = [
    "camera_permission": cameraPermission,
    "microphone_permission": microphonePermission,
    "camera_device": cameraDevice,
    "microphone_device": microphoneDevice,
    "cameras": cameras,
    "microphones": microphones,
    "capture_started": false,
    "recording_started": false,
]
let data = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
print(String(data: data, encoding: .utf8) ?? "{}")
