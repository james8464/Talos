import SwiftUI

struct RoomView: View {
    @ObservedObject var room: RoomController
    @SceneStorage("talos.room.showComposer") private var composing = false
    @FocusState private var editorFocused: Bool

    private var showsComposer: Bool { composing || room.dictating || !room.draft.isEmpty }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(room.isTrial ? "Talos practice room" : "Talos")
                    .font(.title2.weight(.semibold))
                Spacer()
                if room.cameraRunning { Label("Camera on", systemImage: "video.fill").font(.caption) }
                roomMenu
            }
            .padding(.horizontal, 22).padding(.vertical, 12)
            Divider()
            if room.lines.isEmpty && !room.dictating {
                VStack(spacing: 12) {
                    Image(systemName: "waveform").font(.largeTitle).foregroundStyle(.secondary).accessibilityHidden(true)
                    Text("Try “Remind me in ten minutes to check the oven.”")
                        .font(.body).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }.padding(20).frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                transcript
            }
            if room.workPlanID != nil { plan }
            Divider()
            VStack(alignment: .leading, spacing: 10) {
                if !room.liveTranscript.isEmpty && !room.dictating {
                    Text(room.liveTranscript).italic().textSelection(.enabled)
                        .accessibilityLabel("Live transcript, " + room.liveTranscript)
                }
                if showsComposer { composer }
                HStack(spacing: 12) {
                    Button(action: performVoiceAction) {
                        Label(voiceActionTitle, systemImage: voiceSymbol)
                    }
                    .buttonStyle(.borderedProminent).controlSize(.large)
                    .keyboardShortcut("d", modifiers: [.command, .shift])
                    .disabled(room.busy && !room.speaking && room.conversationStarted == nil)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(room.status).font(.callout).lineLimit(2)
                        if let name = room.visitorName {
                            Text("Remembering this conversation with " + name).font(.caption)
                        } else {
                            Text(inputStatus).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Spacer(minLength: 4)
                    if room.dictating {
                        Button("Cancel dictation", action: room.cancelDictation)
                    } else if !showsComposer {
                        Button { composing = true; editorFocused = true } label: {
                            Label("Type a message", systemImage: "keyboard")
                        }.labelStyle(.iconOnly).help("Type a message")
                    }
                }
            }.padding(.horizontal, 22).padding(.vertical, 16)
        }
        .frame(minWidth: 460, minHeight: 360)
        .alert("Forget this visual reference?", isPresented: Binding(get: { room.forgetCandidate != nil },
                set: { if !$0 { room.forgetCandidate = nil } }), presenting: room.forgetCandidate) { profile in
            Button("Forget reference", role: .destructive) { room.forgetReference(profile) }
            Button("Cancel", role: .cancel) { }
        } message: { profile in Text("Talos will stop suggesting " + profile.name + " from the camera. Saved conversation notes will remain.") }
        .sheet(isPresented: $room.reviewingPlan) { reviewPlan }
        .onChange(of: room.workPlanID) { _, id in room.reviewingPlan = id != nil }
        .sheet(isPresented: $room.presentGoals, onDismiss: { room.goals.showing = false }) { GoalPanel(goals: room.goals) }
        .sheet(isPresented: $room.presentSources) { PrivateSources(model: room.privateSources) }
        .task { room.connect() }
    }

    private var inputStatus: String {
        if room.dictating { return "Dictating into your draft" }
        if room.microphone && room.conversationStarted != nil { return "Listening for your next turn" }
        if room.microphone { return "Microphone on · say “Hey Talos”" }
        return "Microphone off"
    }

    private var voiceActionTitle: String {
        if room.speaking { return "Stop reply" }
        if room.dictating { return "Finish dictation" }
        return room.conversationStarted == nil ? "Talk to Talos" : "End conversation"
    }

    private var voiceSymbol: String {
        if room.speaking || room.conversationStarted != nil && !room.dictating { return "stop.fill" }
        return room.dictating ? "checkmark" : "mic.fill"
    }

    private func performVoiceAction() {
        if room.speaking { room.stopSpeaking() }
        else if room.dictating { room.toggleDictation() }
        else { room.toggleConversation() }
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    ForEach(Array(room.lines.enumerated()), id: \.offset) { _, line in
                        Text(line).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                    }
                    Color.clear.frame(height: 1).id("end")
                }.padding(22)
            }
            .onChange(of: room.lines) { _, _ in proxy.scrollTo("end", anchor: .bottom) }
        }
    }

    @ViewBuilder private var composer: some View {
        HStack(alignment: .bottom) {
            TextField("Message Talos", text: $room.draft, selection: $room.draftSelection, axis: .vertical)
                .lineLimit(1...6).disabled(room.dictating).focused($editorFocused)
                .onSubmit(submit)
            if room.draft.isEmpty && !room.dictating {
                Button("Close", systemImage: "xmark") { composing = false }
                    .labelStyle(.iconOnly).help("Close message field")
            } else if !room.dictating {
                Button("Send", systemImage: "arrow.up") { submit() }
                    .disabled(room.busy || room.speaking || room.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || room.draft.unicodeScalars.count > room.draftLimit)
            }
        }
        if room.draft.unicodeScalars.count > room.draftLimit {
            Text("Shorten your draft to \(room.draftLimit) characters to send it.").font(.caption)
        }
    }

    private func submit() {
        room.submitDraft()
        if room.draft.isEmpty { composing = false }
    }

    private var roomMenu: some View {
        Menu {
            Button("Dictate a draft", systemImage: "text.cursor") { room.toggleDictation() }
                .disabled(room.busy || room.speaking || room.dictating)
            Button(room.microphone ? "Mute microphone" : "Enable microphone", action: room.toggleMicrophone)
            Button(room.cameraRunning ? "Turn camera off" : "Turn camera on", action: room.toggleCamera)
            Divider()
            Button("Live activity in Obsidian", action: room.openLiveActivity)
            Button("Goals") { room.goals.open(); room.presentGoals = true }
            Button("Next meeting brief") { room.showMeetingBrief(.upcoming) }
                .disabled(room.isTrial || room.busy || room.speaking || room.dictating)
            Menu("Memory") {
                Button("Private sources and commitments") { room.presentSources = true }
                Button("Open memory folder", action: room.openVault)
                Button("Import Calendar and Reminders", action: room.importScheduleMemory)
                Button("Index text in images and scans", action: room.importImageMemory)
                ForEach(room.profiles, id: \.id) { profile in
                    Button("Forget visual reference for " + profile.name) { room.forgetCandidate = profile }
                }
            }
            if !room.draft.isEmpty {
                Button("Preview plan", action: room.previewDraft)
                    .disabled(room.busy || room.speaking || room.dictating || room.draftContainsVoice || room.draft.count > 800)
            }
            Divider()
            SettingsLink { Text("Room settings…") }
        } label: { Label("Room options", systemImage: "ellipsis.circle").labelStyle(.iconOnly) }
        .menuStyle(.borderlessButton).fixedSize().help("Room options")
    }

    private var plan: some View {
        HStack {
            Text("Proposed local changes").font(.headline)
            Spacer()
            Button("Review plan") { room.reviewingPlan = true }
        }.padding(.horizontal, 22).padding(.bottom, 12)
    }

    private var reviewPlan: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Review local changes").font(.title2.bold())
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(Array(room.workPlanLines.enumerated()), id: \.offset) { index, line in
                        Text("\(index + 1). \(line)").fixedSize(horizontal: false, vertical: true)
                    }
                    if !room.remainingWork.isEmpty {
                        Text("Still open: " + room.remainingWork).fixedSize(horizontal: false, vertical: true)
                    }
                }.frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
            }
            Divider()
            HStack {
                Button("Dismiss plan", action: room.dismissPlan).disabled(room.busy)
                Spacer()
                Button("Close") { room.reviewingPlan = false }.keyboardShortcut(.cancelAction)
                Button("Apply listed steps", action: room.applyPlan)
                    .disabled(room.workPlanLines.isEmpty || room.busy || room.speaking || room.dictating)
            }
        }.padding(20).frame(minWidth: 430, idealWidth: 480, minHeight: 285, idealHeight: 340)
    }
}

struct RoomSettings: View {
    @ObservedObject var room: RoomController

    var body: some View {
        Form {
            Section("Voice") {
                Toggle("Use the local model for mid-sentence pauses", isOn: $room.useLocalTurnModel)
                Toggle("Speak proactive updates and introductions", isOn: $room.speakUnprompted)
                Toggle("Remember voice transcripts", isOn: $room.rememberVoice)
            }
            Section("Camera") {
                Toggle("Accept requests while one person faces the Mac", isOn: $room.acceptFacing)
                Toggle("Offer introductions and remember consenting visitors", isOn: $room.rememberVisitors)
                    .disabled(!room.acceptFacing)
                if let session = room.previewSession {
                    CameraPreview(session: session).frame(width: 224, height: 126)
                    Text(room.occupancy)
                }
            }
            Section("Service") {
                Text(room.voiceLabel)
                if !room.modelState.isEmpty { LabeledContent("Model", value: room.modelState) }
                if !room.lastTiming.isEmpty { Text(room.lastTiming) }
            }
        }.formStyle(.grouped).frame(width: 470, height: 440)
    }
}
