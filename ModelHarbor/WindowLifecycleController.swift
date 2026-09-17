import AppKit
import Combine
import Foundation

@MainActor
final class WindowLifecycleController: NSObject, NSWindowDelegate {
    private var preferences: LifecyclePreferences
    private let alertPresenter: WindowLifecycleAlertPresenting
    private var cancellable: AnyCancellable?
    private let setPresence: @MainActor (HarborPresence) -> Void

    init(
        preferences: LifecyclePreferences? = nil,
        alertPresenter: WindowLifecycleAlertPresenting? = nil,
        setPresence: @escaping @MainActor (HarborPresence) -> Void = { NSApplication.shared.setActivationPolicy($0 == .dockAndMenuBar ? .regular : .accessory) }
    ) {
        self.preferences = preferences ?? .shared
        self.alertPresenter = alertPresenter ?? WindowLifecycleNSAlertPresenter()
        self.setPresence = setPresence
        super.init()
        cancellable = self.preferences.$presence
            .dropFirst()
            .sink { [weak self] presence in self?.applyPresence(presence) }
    }

    func attach(to window: NSWindow) {
        window.delegate = self
    }

    func applyPresence(_ presence: HarborPresence) {
        setPresence(presence)
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        switch preferences.closeBehavior {
        case .keepInDock:
            applyPresence(.dockAndMenuBar)
            sender.orderOut(nil)
            return false
        case .menuBarOnly:
            applyPresence(.menuBarOnly)
            sender.orderOut(nil)
            return false
        case .ask:
            return askForCloseBehavior(sender)
        }
    }

    private func askForCloseBehavior(_ window: NSWindow) -> Bool {
        let alert = NSAlert()
        alert.messageText = "Keep Model Harbor in the Dock?"
        alert.informativeText = "Harbor's local bridge stays running in both modes."
        alert.alertStyle = .informational
        alert.addButton(withTitle: "Keep in Dock")
        alert.addButton(withTitle: "Menu Bar Only")
        alert.addButton(withTitle: "Cancel")
        alert.showsSuppressionButton = true
        alert.suppressionButton?.title = "Remember this choice"
        let response = alertPresenter.runModal(alert)

        switch response {
        case .alertFirstButtonReturn:
            rememberIfNeeded(.keepInDock)
            applyPresence(.dockAndMenuBar)
            window.orderOut(nil)
            return false
        case .alertSecondButtonReturn:
            rememberIfNeeded(.menuBarOnly)
            applyOneOffPresence(.menuBarOnly)
            window.orderOut(nil)
            return false
        default:
            return false
        }
    }

    private func rememberIfNeeded(_ behavior: HarborCloseBehavior) {
        if alertPresenter.isSuppressionButtonChecked { preferences.closeBehavior = behavior }
    }

    private func applyOneOffPresence(_ presence: HarborPresence) {
        // A one-off answer is deliberately not written back to LifecyclePreferences.
        applyPresence(presence)
    }
}

@MainActor
protocol WindowLifecycleAlertPresenting: AnyObject {
    var isSuppressionButtonChecked: Bool { get }
    @discardableResult
    func runModal(_ alert: NSAlert) -> NSApplication.ModalResponse
}

@MainActor
final class WindowLifecycleNSAlertPresenter: WindowLifecycleAlertPresenting {
    private(set) var isSuppressionButtonChecked = false

    @discardableResult
    func runModal(_ alert: NSAlert) -> NSApplication.ModalResponse {
        let response = alert.runModal()
        isSuppressionButtonChecked = alert.suppressionButton?.state == .on
        return response
    }
}
