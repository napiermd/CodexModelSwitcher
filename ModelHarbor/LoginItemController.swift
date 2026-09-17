import Foundation
import Combine
import ServiceManagement

/// Abstraction over `SMAppService.mainApp` so unit tests can inject a fake.
protocol LoginItemService {
    var status: SMAppService.Status { get }
    func register() throws
    func unregister() async throws
}

extension SMAppService: LoginItemService {}

/// Controls whether Harbor launches at login using the system login item.
///
/// The real OS status always wins: `isEnabled` reflects the actual
/// `SMAppService.mainApp.status`, never a cached preference. Registration and
/// unregistration happen only when `setEnabled` is called explicitly — there is
/// no registration at init and no automatic startup registration.
@MainActor
final class LoginItemController: ObservableObject {
    static let shared = LoginItemController()

    @Published private(set) var isEnabled: Bool
    @Published private(set) var isChanging: Bool = false
    @Published private(set) var statusMessage: String = ""
    @Published private(set) var errorMessage: String = ""

    private let service: LoginItemService

    init(service: LoginItemService = SMAppService.mainApp) {
        self.service = service
        self.isEnabled = service.status == .enabled
        self.statusMessage = Self.message(for: service.status)
    }

    /// Reads the current status from the OS and updates published state.
    /// Overlapping refreshes are ignored.
    func refresh() {
        guard !isChanging else { return }
        let status = service.status
        isEnabled = status == .enabled
        statusMessage = Self.message(for: status)
        errorMessage = ""
    }

    /// Registers or unregisters the login item. No-ops while an operation is
    /// already running or when the requested state already matches the OS.
    /// Turning the toggle off while the OS reports `.requiresApproval`
    /// unregisters so the pending approval is cleared.
    func setEnabled(_ enabled: Bool) {
        guard !isChanging else { return }
        let status = service.status
        if enabled && status == .enabled { return }
        if !enabled && status != .enabled && status != .requiresApproval { return }

        isChanging = true
        errorMessage = ""
        statusMessage = enabled ? "Adding Harbor to login items…" : "Removing Harbor from login items…"

        if enabled {
            do {
                try service.register()
                apply(status: service.status)
            } catch {
                fail(error, whileEnabling: true)
            }
            isChanging = false
        } else {
            Task { [service] in
                do {
                    try await service.unregister()
                    apply(status: service.status)
                } catch {
                    fail(error, whileEnabling: false)
                }
                isChanging = false
            }
        }
    }

    /// Opens System Settings to the Login Items panel so the user can approve
    /// or remove Harbor manually.
    func openSystemSettings() {
        SMAppService.openSystemSettingsLoginItems()
    }

    private func apply(status: SMAppService.Status) {
        isEnabled = status == .enabled
        statusMessage = Self.message(for: status)
    }

    private func fail(_ error: Error, whileEnabling: Bool) {
        isEnabled = service.status == .enabled
        errorMessage = Self.sanitizedMessage(for: error, whileEnabling: whileEnabling)
        statusMessage = whileEnabling
            ? "Harbor could not be added to login items."
            : "Harbor could not be removed from login items."
    }

    private static func message(for status: SMAppService.Status) -> String {
        switch status {
        case .enabled:
            return "Harbor opens automatically when you log in."
        case .requiresApproval:
            return "Harbor is waiting for approval in System Settings."
        case .notFound:
            return "The Harbor login item could not be found."
        case .notRegistered:
            return "Harbor does not open at login."
        @unknown default:
            return "Harbor's login item status is unknown."
        }
    }

    /// Maps system errors to short, user-facing copy without exposing raw
    /// system internals.
    private static func sanitizedMessage(for error: Error, whileEnabling: Bool) -> String {
        whileEnabling
            ? "Harbor could not be added to login items. Try again or enable it in System Settings."
            : "Harbor could not be removed from login items. Try again or remove it in System Settings."
    }
}
