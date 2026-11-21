import AVFoundation
import EventKit
import Foundation

@main
struct VoiceEndpointChecks {
    @MainActor
    static func main() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Europe/Paris")!
        let now = ISO8601DateFormatter().date(from: "2026-10-24T12:00:00Z")!
        let tomorrow = MeetingWindow.tomorrow.dates(now: now, calendar: calendar)
        precondition(tomorrow.duration == 25 * 3600)
        precondition(MeetingWindow(request: "Prepare me for tomorrow’s meeting.") == .tomorrow)
        let prep = MeetingRequest("Prepare me for tomorrow’s meeting. Find what I promised last time, check what’s still open, and remind me before I leave.")
        precondition(prep?.window == .tomorrow && prep?.departureReminder == true)
        precondition(MeetingRequest("Help me get ready for my next call and include my open tasks and remind me before heading out.")?.departureReminder == true)
        precondition(MeetingRequest("Prepare me for my next meeting and email everyone.") == nil)
        let store = EKEventStore()
        func fixture(_ title: String, _ start: Date, allDay: Bool = false) -> EKEvent {
            let event = EKEvent(eventStore: store)
            event.title = title
            event.startDate = start
            event.endDate = start.addingTimeInterval(1800)
            event.isAllDay = allDay
            return event
        }
        let events = [fixture("All day", tomorrow.start, allDay: true),
                      fixture("Already started", now), fixture("Following day", tomorrow.end),
                      fixture("Later", tomorrow.start.addingTimeInterval(60_000)),
                      fixture("Atlas review", tomorrow.start.addingTimeInterval(36_000))]
        precondition(MeetingCalendar.firstMeeting(events, within: tomorrow)?.title == "Atlas review")
        precondition(MeetingCalendar.firstMeeting([], within: tomorrow) == nil)
        let partial = VoiceRecognitionUpdate(text: "Can you here", final: false, failed: false)
        precondition(partial == .caption("Can you here"))
        let final = VoiceRecognitionUpdate(text: "Can you hear me?", final: true, failed: false)
        precondition(final == .submit("Can you hear me?"))
        precondition(VoiceRecognitionUpdate(text: "", final: true, failed: false) == .retry)
        precondition(VoiceRecognitionUpdate(text: "Can you", final: false, failed: true) == .retry)
        precondition(roomRequest("Hey Talos, add buy paper to my to-do list.") == "add buy paper to my to-do list.")
        precondition(roomRequest("Talos, can you hear me?") == "can you hear me?")
        for chatter in ["Add buy paper to my to-do list.", "She said hey Talos.", "Talos is listening.",
                        "Jamie, remind me in ten minutes.", "Hey Talosaurus, are you there?", "Hey, tell us what happened."] {
            precondition(roomRequest(chatter) == nil)
        }
        var mode = VoiceCaptureMode.dictation
        var draft = ""
        precondition(mode.appendFinalToDraft("Please make a note", draft: &draft))
        precondition(mode == .dictation)
        mode = .finishingDraft
        precondition(mode.appendFinalToDraft("about Thursday's meeting", draft: &draft))
        precondition(mode == .room && draft == "Please make a note about Thursday's meeting")
        precondition(!mode.appendFinalToDraft("Overheard conversation", draft: &draft))
        let format = AVAudioFormat(standardFormatWithSampleRate: 24_000, channels: 1)!
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 480)!
        buffer.frameLength = 480
        let samples = buffer.floatChannelData![0]
        let endpoint = VoiceEndpoint()
        for index in 0..<480 { samples[index] = 0 }
        endpoint.observe(buffer, at: 1)
        precondition(endpoint.remaining(afterPartial: 1, now: 2.25) > 0)
        for index in 0..<480 { samples[index] = 0.1 * sin(Float(index) / 10) }
        endpoint.observe(buffer, at: 1.64)
        precondition(endpoint.remaining(afterPartial: 1, now: 1.65) > 0.4)
        precondition(endpoint.remaining(afterPartial: 1, now: 2.80) > 0)
        endpoint.observe(buffer, at: 2.99)
        precondition(endpoint.remaining(afterPartial: 1, now: 3) > 1)
        precondition(endpoint.remaining(afterPartial: 1, now: 4.35) == 0)
        endpoint.observe(buffer, at: 6.99)
        precondition(endpoint.remaining(afterPartial: 1, now: 7) == 0)
        print("Passed address, mid-sentence pause, stalled-caption and final silence checks.")

        if CommandLine.arguments.count > 1 {
            let file = try AVAudioFile(forReading: URL(fileURLWithPath: CommandLine.arguments[1]))
            let frames = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: 480)!
            let recorded = VoiceEndpoint()
            var time = 0.0
            var extended = false
            while file.framePosition < file.length {
                try file.read(into: frames, frameCount: 480)
                time += Double(frames.frameLength) / file.processingFormat.sampleRate
                recorded.observe(frames, at: time)
                if time >= 0.65, time < 1.0, recorded.remaining(afterPartial: 0, now: time) > 0 {
                    extended = true
                }
            }
            precondition(extended && recorded.remaining(afterPartial: time, now: time + 1.36) == 0)
            print("Passed delayed-caption check on a real speech waveform.")
        }
    }
}
