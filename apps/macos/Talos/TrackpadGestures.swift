import Foundation
import AppKit

enum GestureAction: String, CaseIterable, Codable, Identifiable {
    case none
    case nextTask
    case previousTask
    case toggleInspector
    case quickSwitcher
    case newTask
    case stop
    case compact

    var id: String { rawValue }

    var title: String {
        switch self {
        case .none: return String(localized: "None")
        case .nextTask: return String(localized: "Next Task")
        case .previousTask: return String(localized: "Previous Task")
        case .toggleInspector: return String(localized: "Toggle Inspector")
        case .quickSwitcher: return String(localized: "Quick Switcher")
        case .newTask: return String(localized: "New Task")
        case .stop: return String(localized: "Stop Response")
        case .compact: return String(localized: "Compact Conversation")
        }
    }
}

/// Turns two-finger trackpad swipes into actions. The recognizer is a small
/// state machine so it can be tested with synthetic deltas and also used by the
/// live `NSEvent` monitor.
struct TrackpadGestureRecognizer {
    private(set) var accumulatedX: Double = 0
    private(set) var accumulatedY: Double = 0
    private var tracking = false

    let threshold: Double
    let dominance: Double

    init(threshold: Double = 42, dominance: Double = 1.6) {
        self.threshold = threshold
        self.dominance = dominance
    }

    mutating func reset() {
        accumulatedX = 0
        accumulatedY = 0
        tracking = false
    }

    mutating func handle(
        deltaX: Double,
        deltaY: Double,
        phase: NSEvent.Phase,
        enabled: Bool,
        requireOption: Bool,
        modifiers: NSEvent.ModifierFlags
    ) -> Swipe? {
        guard enabled else {
            reset()
            return nil
        }
        if requireOption, !modifiers.contains(.option) {
            return nil
        }

        if phase.contains(.began) {
            reset()
            tracking = true
        } else if !tracking {
            // Discrete wheel events and synthetic tests may not send a began
            // phase; start tracking on the first delta.
            tracking = true
        }

        accumulatedX += deltaX
        accumulatedY += deltaY

        if phase.contains(.ended) || phase.contains(.cancelled) {
            let action = resolve()
            reset()
            return action
        }
        if phase.isEmpty {
            // Mouse wheels and synthetic events arrive without phases. Resolve
            // as soon as a direction crosses the threshold.
            let action = resolve()
            if action != nil { reset() }
            return action
        }
        return nil
    }

    func resolve() -> Swipe? {
        let horizontal = abs(accumulatedX)
        let vertical = abs(accumulatedY)
        guard max(horizontal, vertical) >= threshold else { return nil }

        if horizontal >= vertical * dominance {
            return accumulatedX < 0 ? .swipeLeft : .swipeRight
        }
        if vertical >= horizontal * dominance {
            return accumulatedY < 0 ? .swipeUp : .swipeDown
        }
        return nil
    }

    enum Swipe {
        case swipeLeft
        case swipeRight
        case swipeUp
        case swipeDown
    }
}

/// Installs a local scroll-wheel monitor. The event is always passed through so
/// normal scrolling continues to work.
@MainActor
final class TrackpadGestureMonitor {
    private let settings: SettingsStore
    private let perform: (GestureAction) -> Void
    private var monitor: Any?
    private var recognizer = TrackpadGestureRecognizer()

    init(settings: SettingsStore, perform: @escaping (GestureAction) -> Void) {
        self.settings = settings
        self.perform = perform
        install()
    }

    deinit {
        if let monitor {
            NSEvent.removeMonitor(monitor)
        }
    }

    private func install() {
        monitor = NSEvent.addLocalMonitorForEvents(matching: [.scrollWheel]) { [weak self] event in
            guard let self else { return event }
            Task { @MainActor in
                self.process(event)
            }
            return event
        }
    }

    private func process(_ event: NSEvent) {
        guard settings.customGesturesEnabled else { return }
        if event.momentumPhase != [] { return }

        let swipe = recognizer.handle(
            deltaX: event.scrollingDeltaX,
            deltaY: event.scrollingDeltaY,
            phase: event.phase,
            enabled: settings.customGesturesEnabled,
            requireOption: settings.gesturesRequireOption,
            modifiers: event.modifierFlags
        )
        guard let swipe else { return }

        let action: GestureAction
        switch swipe {
        case .swipeLeft: action = settings.gestureSwipeLeft
        case .swipeRight: action = settings.gestureSwipeRight
        case .swipeUp: action = settings.gestureSwipeUp
        case .swipeDown: action = settings.gestureSwipeDown
        }
        guard action != .none else { return }
        perform(action)
    }
}
