import AVFoundation
import EventKit
import ImageIO
import Vision
import Foundation

@main
struct VoiceEndpointChecks {
    static func checkSpeechInputFormats() {
        for count: AVAudioChannelCount in [1, 2, 4] {
            var layout = AudioChannelLayout()
            switch count {
            case 1: layout.mChannelLayoutTag = kAudioChannelLayoutTag_Mono
            case 2: layout.mChannelLayoutTag = kAudioChannelLayoutTag_Stereo
            default: layout.mChannelLayoutTag = kAudioChannelLayoutTag_Quadraphonic
            }
            let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16_000,
                                       interleaved: false, channelLayout: AVAudioChannelLayout(layout: &layout))
            let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 8)!
            buffer.frameLength = 2
            for channel in 0..<Int(count) {
                buffer.floatChannelData![channel][0] = Float(channel) / 4
                buffer.floatChannelData![channel][1] = -Float(channel) / 4
            }
            let output = SpeechAudioBufferNormalizer.speechCompatibleBuffer(from: buffer)
            if count <= 2 {
                precondition(output === buffer)
            } else {
                precondition(output.format.channelCount == 1 && output.format.sampleRate == 16_000)
                precondition(output.frameLength == 2)
                precondition(output.floatChannelData![0][0] == 0.375 && output.floatChannelData![0][1] == -0.375)
            }
        }
    }

    @MainActor
    static func main() throws {
        let partialRequest = SpokenContinuation("remind me in five minutes to", at: 100, conversation: 95, viaFacing: true)!
        precondition(partialRequest.response(to: "continue check the oven", at: 105, conversation: 95)
                     == .completed("remind me in five minutes to check the oven"))
        precondition(partialRequest.response(to: "Hey Talos, never mind.", at: 101, conversation: 95) == .cancelled)
        precondition(partialRequest.response(to: "continue check the oven", at: 131, conversation: 95) == nil)
        precondition(partialRequest.response(to: "continue check the oven", at: 105, conversation: 97) == nil)
        precondition(partialRequest.response(to: "unaddressed conversation", at: 105, conversation: 95) == nil)
        precondition(SpokenContinuation("tell me about", at: 100, conversation: nil, viaFacing: false) == nil)
        checkSpeechInputFormats()
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
        var focus = ConversationFocus()
        focus.open(at: 1, question: true)
        precondition(focus.request("In twenty minutes.", at: 4) != nil)
        precondition(focus.request("Actually, make that five minutes.", at: 5) != nil)
        precondition(focus.request("Jamie, remind me to stretch.", at: 5) == nil)
        precondition(focus.request("Actually, she was there.", at: 5) == nil)
        precondition(focus.request("And what about Friday?", at: 10) == nil)
        focus.open(at: 12, question: false, called: true)
        precondition(focus.request("Can you hear me?", at: 13) != nil)
        focus.clear()
        precondition(focus.request("Can you hear me?", at: 13) == nil)
        precondition(ConversationFocus.isInterruption("Hey Talos, stop.", playback: "I found the following."))
        precondition(!ConversationFocus.isInterruption("Hey Talos, stop.", playback: "Say Hey Talos, stop."))
        precondition(ConversationFocus.removePlaybackPrefix("the meeting starts Friday can we change that", playback: "The meeting starts Friday.") == "can we change that")
        precondition(ConversationFocus.removePlaybackPrefix("the meeting starts Friday", playback: "The meeting starts Friday.") == "the meeting starts Friday")
        precondition(needsContinuation("Hey Talos, could you remind me about"))
        precondition(!needsContinuation("Hey Talos, can you hear me?"))
        precondition(ConversationFocus.conversationalInterruption("Actually, I meant the lens.", playback: "We could clean the shutter first."))
        precondition(!ConversationFocus.conversationalInterruption("Hold on", playback: "Hold on to the railing."))
        precondition(!ConversationFocus.conversationalInterruption("It is raining", playback: "Try again tomorrow."))
        precondition(ConversationFocus.conversationalInterruption("What about Friday?", playback: "Try again tomorrow.", allowSpeech: true))
        precondition(!ConversationFocus.conversationalInterruption("try again", playback: "Try again tomorrow.", allowSpeech: true))
        precondition(ConversationFocus.conversationalInterruption("stop", playback: "Try again tomorrow.", allowSpeech: true))
        precondition(ConversationFocus.pauseOnly("Wait,"))
        precondition(!ConversationFocus.pauseOnly("Wait, I meant Thursday."))
        precondition(ConversationFocus.conversationalInterruption("wait", playback: "Awaiting your reply.", allowSpeech: true))
        precondition(!ConversationFocus.conversationalInterruption("wait", playback: "Please wait here.", allowSpeech: true))
        focus.open(at: 20, question: false)
        precondition(focus.request("What did I just say?", at: 23) != nil)
        precondition(focus.request("What did I just say?", at: 29) == nil)
        var facing = FacingAttention()
        for time in [10.0,10.7,11.4] { facing.observe(true, at: time) }
        precondition(facing.accepts("Can you hear me?", from: 10.9, until: 11.8, hasOffer: false))
        for words in ["Dana, can you hear me?", "She asked can you see me", "Can you hear me, Dana?", "confirm"] {
            precondition(!facing.accepts(words, from: 10.9, until: 11.8, hasOffer: false))
        }
        precondition(!facing.available(from: 9.0, until: 11.8))
        precondition(!facing.available(from: 10.9, until: 15))
        facing.observe(false, at: 12)
        precondition(!facing.available(from: 10.9, until: 12.1))
        precondition(!FacingAttention.singleFacing(persons: 2, faces: 1, width: 0.3, yaw: 0, pitch: 0))
        precondition(!FacingAttention.singleFacing(persons: 1, faces: 2, width: 0.3, yaw: 0, pitch: 0))
        precondition(!FacingAttention.singleFacing(persons: 1, faces: 1, width: 0.3, yaw: nil, pitch: 0))
        precondition(!FacingAttention.singleFacing(persons: 1, faces: 1, width: 0.3, yaw: 0.7, pitch: 0))
        var visit = VisitorConversation<Float>(compare: { abs($0-$1) })
        precondition(visit.begin(0, profiles: [], now: 1) != nil)
        precondition(visit.answer("Can you tell me the time?", at: 2) == nil)
        guard case .prompt? = visit.answer("My name is Robin. I teach ceramics.", at: 3) else { fatalError("intro") }
        precondition(visit.confirmed == nil)
        guard case let .enroll(name, introduction)? = visit.answer("Yes please.", at: 4) else { fatalError("consent") }
        precondition(name == "Robin" && introduction.contains("ceramics"))
        let familiar = VisitProfile(id:"a", name:name, sample:Float(0))
        visit.use(familiar)
        visit.leave()
        precondition(visit.confirmed == nil)
        precondition(visit.begin(0.05, profiles:[familiar], now: 5) == nil)
        precondition(visit.begin(0.05, profiles:[familiar], now: 70)?.contains("Robin") == true)
        guard case .returning? = visit.answer("Yes.", at: 71) else { fatalError("return") }
        visit.leave()
        let similar = VisitProfile(id:"b",name:"Avery",sample:Float(0.06))
        precondition(visit.begin(0.03, profiles:[familiar, similar], now: 140)?.contains("What name") == true)
        guard case .decline? = visit.answer("Leave me alone.", at: 141) else { fatalError("decline") }
        precondition(visit.confirmed == nil && !visit.waiting)
        visit.leave()
        _ = visit.begin(0.9, profiles:[familiar], now: 220)
        precondition(visit.answer("Robin", at: 280) == nil)
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

        if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "--portraits" {
            try replayPortraits(URL(fileURLWithPath: CommandLine.arguments[2]))
            return
        }
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
    // Four equal quadrants in order: frontal A, changed view of A, frontal B, side view of B.
    // Supply generated pictures. This test never opens a capture device.
    static func replayPortraits(_ url: URL) throws {
        guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
              let sheet = CGImageSourceCreateImageAtIndex(src, 0, nil) else { throw CocoaError(.fileReadCorruptFile) }
        let w = sheet.width / 2, h = sheet.height / 2
        var samples: [VNFeaturePrintObservation?] = []
        for i in 0..<4 {
            guard let image = sheet.cropping(to: CGRect(x:i % 2 * w, y:i / 2 * h, width:w, height:h)) else {
                throw CocoaError(.fileReadCorruptFile)
            }
            let handler = VNImageRequestHandler(cgImage: image, orientation: .up)
            let human = VNDetectHumanRectanglesRequest()
            human.upperBodyOnly = true
            try handler.perform([human])
            let people = (human.results ?? []).filter { $0.confidence >= 0.6 }
            let view = VisitorFrame.observe(handler, people: people.count, faces: VNDetectFaceRectanglesRequest(), sample: true)
            samples.append(view.sample)
            print("Portrait \(i + 1): people=\(people.count) facing=\(view.facing) template=\(view.sample != nil)")
        }
        guard let original = samples[0], let changed = samples[1], let other = samples[2],
              let sameDistance = VisitorConversation.distance(original, changed),
              let otherDistance = VisitorConversation.distance(original, other) else {
            throw NSError(domain:"Talos.Replay",code:1,userInfo:[NSLocalizedDescriptionKey:"A frontal portrait did not produce a comparable template."])
        }
        print("Same fictional subject distance \(sameDistance), different subject distance \(otherDistance)")
        guard sameDistance <= 0.32, otherDistance > 0.32, samples[3] == nil else {
            throw NSError(domain:"Talos.Replay",code:2,userInfo:[NSLocalizedDescriptionKey:"The current threshold failed this portrait fixture."])
        }
    }

}
