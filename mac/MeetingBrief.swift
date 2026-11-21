import EventKit
import Foundation

enum MeetingWindow {
    case upcoming, tomorrow

    init?(request: String) {
        let words = request.lowercased().replacingOccurrences(of: "’", with: "'")
            .trimmingCharacters(in: CharacterSet.whitespacesAndNewlines.union(.punctuationCharacters))
        let preparation = #"(?:please )?(?:prepare me for|(?:help me )?get ready for|brief me (?:on|for)|(?:show|give) me (?:a |the )?brief(?:ing)? (?:on|for)) "#
        let target = #"(?:(?:my|the) next|tomorrow's) (?:meeting|appointment|call)"#
        guard words.range(of: "^" + preparation + target + "$", options: .regularExpression) != nil else { return nil }
        self = words.contains("tomorrow's") ? .tomorrow : .upcoming
    }

    func dates(now: Date, calendar: Calendar) -> DateInterval {
        let start = self == .tomorrow ? calendar.startOfDay(for: calendar.date(byAdding: .day, value: 1, to: now)!) : now
        let end = calendar.date(byAdding: .day, value: self == .tomorrow ? 1 : 7, to: start)!
        return DateInterval(start: start, end: end)
    }
}

struct MeetingRequest {
    let window: MeetingWindow
    let departureReminder: Bool

    init?(_ text: String) {
        let normalized = text.lowercased().replacingOccurrences(of: "’", with: "'")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let clauses = normalized.components(separatedBy: CharacterSet(charactersIn: ".,;!?"))
            .flatMap { $0.components(separatedBy: " and ") }
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        guard let first = clauses.first, let window = MeetingWindow(request: first) else { return nil }
        let trailing = clauses.dropFirst().map { $0.hasPrefix("and ") ? String($0.dropFirst(4)) : $0 }
        let reminder = #"^remind me (?:before i (?:leave|head out)|before (?:leaving|heading out))$"#
        let records = #"^(?:find|show|check|review|include)(?: me)? (?:what i (?:promised|agreed to|committed to)(?: last time)?|(?:what's|what is) (?:still )?open|(?:my |the )?(?:open tasks|notes|commitments|reminders))$"#
        var departure = false
        for clause in trailing {
            if clause.range(of: reminder, options: .regularExpression) != nil {
                departure = true
            } else if clause.range(of: records, options: .regularExpression) == nil {
                return nil
            }
        }
        self.window = window
        departureReminder = departure
    }
}

struct ScheduledMeeting {
    let title: String
    let start: Date
    var binding = ""
}

@MainActor
final class MeetingCalendar {
    private let store = EKEventStore()

    var connected: Bool { EKEventStore.authorizationStatus(for: .event) == .fullAccess }

    func next(in window: MeetingWindow) async throws -> ScheduledMeeting? {
        // EventKit requires full calendar authorization for reading timed events.
        if !connected, try await !store.requestFullAccessToEvents() {
            throw NSError(domain: EKErrorDomain, code: EKError.Code.eventStoreNotAuthorized.rawValue,
                          userInfo: [NSLocalizedDescriptionKey: "Calendar access is off. Type “Brief me on” and a project name."])
        }
        let interval = window.dates(now: Date(), calendar: .current)
        let predicate = store.predicateForEvents(withStart: interval.start, end: interval.end, calendars: nil)
        return Self.firstMeeting(store.events(matching: predicate), within: interval)
    }

    func matching(_ words: String) async throws -> ScheduledMeeting? {
        if !connected, try await !store.requestFullAccessToEvents() { throw NSError(domain: EKErrorDomain, code: 1) }
        let start = Date()
        let end = Calendar.current.date(byAdding: .day, value: 14, to: start)!
        let predicate = store.predicateForEvents(withStart: start, end: end, calendars: nil)
        let tokens = Set(words.lowercased().components(separatedBy: CharacterSet.alphanumerics.inverted))
        let days = Calendar(identifier: .gregorian).weekdaySymbols.map { $0.lowercased() }
        let wantedDay = days.firstIndex(where: tokens.contains)
        let generic: Set<String> = ["", "s","my","the","next","a","meeting","review","call","tomorrow","today"]
        let project = tokens.subtracting(generic.union(days))
        let matches = store.events(matching: predicate).filter { event in
            let title = Set((event.title ?? "").lowercased().components(separatedBy: CharacterSet.alphanumerics.inverted))
            return project.isSubset(of: title) &&
                (wantedDay == nil || Calendar.current.component(.weekday, from: event.startDate) == wantedDay! + 1) &&
                (!tokens.contains("tomorrow") || Calendar.current.isDateInTomorrow(event.startDate)) &&
                (!tokens.contains("today") || Calendar.current.isDateInToday(event.startDate))
        }
        let interval = DateInterval(start: start, end: end)
        let candidates = matches.compactMap { Self.firstMeeting([$0], within: interval) }
        if Set(candidates.map { $0.title.lowercased() }).count > 1 {
            throw NSError(domain: "Talos.Calendar", code: 2, userInfo: [NSLocalizedDescriptionKey: "More than one event matches. Include the exact meeting title or weekday."])
        }
        return candidates.min { $0.start < $1.start }
    }

    func observation(binding: String, deadline: Date?) -> (state: String, start: Date?) {
        guard connected else { return ("unavailable", nil) }
        guard let data = binding.data(using: .utf8),
              let keys = try? JSONDecoder().decode([String].self, from: data), keys.count == 2 else { return ("missing", nil) }
        var event: EKEvent?
        if keys[1].isEmpty { event = store.event(withIdentifier: keys[0]) }
        else if let deadline, let original = Double(keys[1]) {
            let anchor = Date(timeIntervalSince1970: original)
            let pred = store.predicateForEvents(withStart: min(deadline, anchor).addingTimeInterval(-30 * 86400),
                                                end: max(deadline, anchor).addingTimeInterval(30 * 86400), calendars: nil)
            event = store.events(matching: pred).first { $0.calendarItemIdentifier == keys[0] && $0.occurrenceDate == anchor }
        }
        guard let event, event.status != .canceled,
              !(event.attendees ?? []).contains(where: { $0.isCurrentUser && $0.participantStatus == .declined }) else { return ("missing", nil) }
        return ("confirmed", event.startDate)
    }

    static func firstMeeting(_ events: [EKEvent], within interval: DateInterval) -> ScheduledMeeting? {
        let first = events.filter { event in
            !event.isAllDay && event.status != .canceled && event.startDate >= interval.start
                && event.startDate < interval.end
                && !(event.attendees ?? []).contains { $0.isCurrentUser && $0.participantStatus == .declined }
        }.min { $0.startDate < $1.startDate }
        guard let first else { return nil }
        let recurring = first.hasRecurrenceRules || first.isDetached
        let keys = recurring ? [first.calendarItemIdentifier, String((first.occurrenceDate ?? first.startDate).timeIntervalSince1970)] :
            [first.eventIdentifier ?? "", ""]
        let binding = (try? JSONEncoder().encode(keys)).flatMap { String(data: $0, encoding: .utf8) } ?? ""
        return ScheduledMeeting(title: first.title ?? "Untitled event", start: first.startDate, binding: binding)
    }
}
