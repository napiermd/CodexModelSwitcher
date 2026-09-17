import AppKit
import Combine
import CoreServices
import Foundation

enum HarborPresence: String, CaseIterable, Identifiable {
    case dockAndMenuBar
    case menuBarOnly

    var id: String { rawValue }
    var title: String {
        switch self {
        case .dockAndMenuBar: return "Dock + menu bar"
        case .menuBarOnly: return "Menu bar only"
        }
    }
}

enum HarborCloseBehavior: String, CaseIterable, Identifiable {
    case ask
    case keepInDock
    case menuBarOnly

    var id: String { rawValue }
    var title: String {
        switch self {
        case .ask: return "Ask every time"
        case .keepInDock: return "Keep in Dock"
        case .menuBarOnly: return "Menu bar only"
        }
    }
}

enum CodexCloseMethod: String, CaseIterable, Identifiable {
    case graceful
    case force

    var id: String { rawValue }
    var title: String {
        switch self {
        case .graceful: return "Graceful"
        case .force: return "Force"
        }
    }
}

@MainActor
final class LifecyclePreferences: ObservableObject {
    static let shared = LifecyclePreferences()

    @Published var presence: HarborPresence
    @Published var closeBehavior: HarborCloseBehavior
    @Published var openWindowAtLaunch: Bool
    @Published var codexCloseMethod: CodexCloseMethod
    @Published var reopenCodex: Bool

    let defaults: UserDefaults
    private var cancellables: Set<AnyCancellable> = []

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        presence = Self.read(defaults, key: Keys.presence, default: .dockAndMenuBar)
        closeBehavior = Self.read(defaults, key: Keys.closeBehavior, default: .ask)
        openWindowAtLaunch = defaults.object(forKey: Keys.openWindowAtLaunch) as? Bool ?? true
        codexCloseMethod = Self.read(defaults, key: Keys.codexCloseMethod, default: .graceful)
        reopenCodex = defaults.object(forKey: Keys.reopenCodex) as? Bool ?? true

        $presence
            .dropFirst()
            .sink { [defaults, key = Keys.presence] value in
                defaults.set(value.rawValue, forKey: key)
            }
            .store(in: &cancellables)
        $closeBehavior
            .dropFirst()
            .sink { [defaults, key = Keys.closeBehavior] value in
                defaults.set(value.rawValue, forKey: key)
            }
            .store(in: &cancellables)
        $openWindowAtLaunch
            .dropFirst()
            .sink { [defaults, key = Keys.openWindowAtLaunch] value in
                defaults.set(value, forKey: key)
            }
            .store(in: &cancellables)
        $codexCloseMethod
            .dropFirst()
            .sink { [defaults, key = Keys.codexCloseMethod] value in
                defaults.set(value.rawValue, forKey: key)
            }
            .store(in: &cancellables)
        $reopenCodex
            .dropFirst()
            .sink { [defaults, key = Keys.reopenCodex] value in
                defaults.set(value, forKey: key)
            }
            .store(in: &cancellables)
    }

    private enum Keys {
        static let presence = "harbor.presence"
        static let closeBehavior = "harbor.closeBehavior"
        static let openWindowAtLaunch = "harbor.openWindowAtLaunch"
        static let codexCloseMethod = "harbor.codexCloseMethod"
        static let reopenCodex = "harbor.reopenCodex"
    }

    private static func read<T: RawRepresentable>(
        _ defaults: UserDefaults,
        key: String,
        default fallback: T
    ) -> T where T.RawValue == String {
        guard let rawValue = defaults.string(forKey: key),
              let value = T(rawValue: rawValue) else { return fallback }
        return value
    }
}

extension Notification.Name {
    static let harborShowSettings = Notification.Name("harborShowSettings")
    static let harborOpenWindow = Notification.Name("harborOpenWindow")
}

/// Login launches carry their launch reason in the open-application event's property data.
enum HarborLaunchEvent {
    static func isLoginItem(_ event: NSAppleEventDescriptor?) -> Bool {
        event?.eventID == AEEventID(kAEOpenApplication)
            && event?.paramDescriptor(forKeyword: AEKeyword(keyAEPropData))?.enumCodeValue == OSType(keyAELaunchedAsLogInItem)
    }
}
