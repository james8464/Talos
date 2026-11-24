import Foundation

@main
struct NativeSenseReplay {
    static func main() async {
        let args = CommandLine.arguments
        guard args.count >= 5, let port = Int(args[1]) else {
            FileHandle.standardError.write(Data("usage: sense-replay PORT TOKEN JPEG_BASE64 WAV_BASE64\n".utf8))
            exit(2)
        }
        let client = RoomClient(port: port, token: args[2])
        do {
            let scene = try await client.scene(args[3])
            print("scene=" + (scene["description"] as? String ?? "missing"))
            let sound = try await client.sound(args[4])
            print("sound=" + (sound["description"] as? String ?? "missing"))
        } catch {
            FileHandle.standardError.write(Data("error=\(error)\n".utf8))
            exit(1)
        }
    }
}
