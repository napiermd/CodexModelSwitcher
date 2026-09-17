import AppKit
import XCTest
@testable import HarborCore

@MainActor
final class WindowLifecycleTests: XCTestCase {
    private final class Presenter: WindowLifecycleAlertPresenting {
        var response: NSApplication.ModalResponse = .alertThirdButtonReturn
        var isSuppressionButtonChecked = false
        var calls = 0
        func runModal(_ alert: NSAlert) -> NSApplication.ModalResponse { calls += 1; return response }
    }

    func testCloseChoicesAndRememberingDoNotQuitOrDestroyWindow() throws {
        _ = NSApplication.shared
        let suite = "harbor-window-test-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let prefs = LifecyclePreferences(defaults: defaults)
        let alerts = Presenter()
        var applied: [HarborPresence] = []
        let controller = WindowLifecycleController(preferences: prefs, alertPresenter: alerts,
            setPresence: { applied.append($0) })
        let window = NSWindow(contentRect: .zero, styleMask: [.titled, .closable], backing: .buffered, defer: false)
        controller.attach(to: window)
        XCTAssertFalse(controller.windowShouldClose(window)) // Cancel
        XCTAssertTrue(applied.isEmpty)
        XCTAssertEqual(prefs.closeBehavior, .ask)
        alerts.response = .alertSecondButtonReturn
        XCTAssertFalse(controller.windowShouldClose(window))
        XCTAssertEqual(applied.last, .menuBarOnly)
        XCTAssertEqual(prefs.closeBehavior, .ask)
        alerts.response = .alertFirstButtonReturn
        alerts.isSuppressionButtonChecked = true
        XCTAssertFalse(controller.windowShouldClose(window))
        XCTAssertEqual(applied.last, .dockAndMenuBar)
        XCTAssertEqual(prefs.closeBehavior, .keepInDock)
        XCTAssertFalse(controller.windowShouldClose(window))
        XCTAssertEqual(alerts.calls, 3)
        XCTAssertEqual(LifecyclePreferences(defaults: defaults).closeBehavior, .keepInDock)
    }

    func testPresenceChangesWorkBeforeAWindowIsOpened() throws {
        let suite = "harbor-presence-test-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let prefs = LifecyclePreferences(defaults: defaults)
        var applied: [HarborPresence] = []
        let controller = WindowLifecycleController(preferences: prefs, setPresence: { applied.append($0) })
        prefs.presence = .menuBarOnly
        prefs.presence = .dockAndMenuBar
        withExtendedLifetime(controller) { XCTAssertEqual(applied, [.menuBarOnly, .dockAndMenuBar]) }
    }
}
