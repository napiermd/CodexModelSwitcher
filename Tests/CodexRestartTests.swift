import AppKit
import XCTest
@testable import HarborCore

@MainActor
private final class FakeCodexApplication: CodexApplication {
    let bundleURL: URL? = URL(fileURLWithPath: "/private/tmp/test-codex.app")
    var isTerminated = false
    var acceptsTermination = true
    var finishesTermination = true
    var gracefulCalls = 0
    var forceCalls = 0
    @discardableResult func requestTermination() -> Bool {
        gracefulCalls += 1
        isTerminated = acceptsTermination && finishesTermination
        return acceptsTermination
    }
    @discardableResult func forceTerminate() -> Bool {
        forceCalls += 1
        isTerminated = acceptsTermination && finishesTermination
        return acceptsTermination
    }
}

@MainActor
private final class FakeCodexApplicationOperations: CodexApplicationOperations {
    var applications: [any CodexApplication] = []
    var openCalls = 0
    var openError: Error?
    var identifiers: [String] = []

    func runningApplications(bundleIdentifier: String) -> [any CodexApplication] {
        identifiers.append(bundleIdentifier)
        return applications
    }
    func applicationURL(bundleIdentifier: String) -> URL? { applications.first?.bundleURL }
    func openApplication(at url: URL, completionHandler: @escaping @Sendable (Error?) -> Void) {
        openCalls += 1
        completionHandler(openError)
    }
}

@MainActor
private final class FakeAlertPresenter: CodexRestartAlertPresenting {
    var response: NSApplication.ModalResponse = .cancel
    private(set) var presentedAlerts: [NSAlert] = []

    @discardableResult
    func runModal(_ alert: NSAlert) -> NSApplication.ModalResponse {
        presentedAlerts.append(alert)
        return response
    }
}

@MainActor
final class CodexRestartTests: XCTestCase {
    func testAlertCopyReflectsPreferencesAndExplicitCancelDoesNothing() throws {
        let defaults = try temporaryDefaults()
        let preferences = LifecyclePreferences(defaults: defaults)
        preferences.codexCloseMethod = .force
        preferences.reopenCodex = false
        let alerts = FakeAlertPresenter()
        let operations = FakeCodexApplicationOperations()
        operations.applications = [FakeCodexApplication()]
        let controller = CodexRestartController(
            alertPresenter: alerts,
            operations: operations,
            preferences: preferences,
            terminationDelay: 1
        )

        controller.requestRestart()

        XCTAssertTrue(controller.statusMessage.isEmpty)
        XCTAssertFalse(controller.isRestarting)
        let alert = try XCTUnwrap(alerts.presentedAlerts.first)
        XCTAssertEqual(alert.messageText, "Close Codex?")
        XCTAssertTrue(alert.informativeText.contains("unsaved work"))
        XCTAssertEqual(alert.buttons.first?.title, "Close")
    }

    func testNoRunningCodexProducesHonestErrorWithoutTerminating() throws {
        let preferences = LifecyclePreferences(defaults: try temporaryDefaults())
        let controller = CodexRestartController(
            alertPresenter: FakeAlertPresenter(),
            operations: FakeCodexApplicationOperations(),
            preferences: preferences,
            terminationDelay: 1
        )

        controller.requestRestart()

        XCTAssertEqual(controller.statusMessage, "Codex is not running.")
        XCTAssertFalse(controller.isRestarting)
    }

    func testControllerUsesExactCodexBundleIdentifier() {
        let controller = CodexRestartController(
            alertPresenter: FakeAlertPresenter(),
            operations: FakeCodexApplicationOperations(),
            preferences: LifecyclePreferences(defaults: try! temporaryDefaults()),
            terminationDelay: 1
        )

        XCTAssertEqual(controller.codexBundleIdentifier, "com.openai.codex")
    }

    func testGracefulRestartWaitsAndReopensOnce() async throws {
        let app = FakeCodexApplication()
        let ops = FakeCodexApplicationOperations(); ops.applications = [app]
        let alerts = FakeAlertPresenter(); alerts.response = .alertFirstButtonReturn
        let controller = CodexRestartController(alertPresenter: alerts, operations: ops,
            preferences: LifecyclePreferences(defaults: try temporaryDefaults()), terminationTimeout: 0.01)
        controller.requestRestart()
        controller.requestRestart()
        await settle(controller)
        XCTAssertEqual(app.gracefulCalls, 1)
        XCTAssertEqual(app.forceCalls, 0)
        XCTAssertEqual(ops.openCalls, 1)
        XCTAssertEqual(controller.statusMessage, "Codex restarted.")
        XCTAssertTrue(ops.identifiers.allSatisfy { $0 == "com.openai.codex" })
    }

    func testGracefulTimeoutDoesNotEscalateOrReopen() async throws {
        let app = FakeCodexApplication(); app.finishesTermination = false
        let ops = FakeCodexApplicationOperations(); ops.applications = [app]
        let alerts = FakeAlertPresenter(); alerts.response = .alertFirstButtonReturn
        let controller = CodexRestartController(alertPresenter: alerts, operations: ops,
            preferences: LifecyclePreferences(defaults: try temporaryDefaults()),
            terminationDelay: 1_000_000, terminationTimeout: 0.003)
        controller.requestRestart()
        await settle(controller)
        XCTAssertEqual(app.gracefulCalls, 1)
        XCTAssertEqual(app.forceCalls, 0)
        XCTAssertEqual(ops.openCalls, 0)
        XCTAssertTrue(controller.statusMessage.contains("did not quit in time"))
    }

    func testForceAcceptedIsNotAssumedToMeanExited() async throws {
        let app = FakeCodexApplication(); app.finishesTermination = false
        let ops = FakeCodexApplicationOperations(); ops.applications = [app]
        let prefs = LifecyclePreferences(defaults: try temporaryDefaults()); prefs.codexCloseMethod = .force
        let alerts = FakeAlertPresenter(); alerts.response = .alertFirstButtonReturn
        let controller = CodexRestartController(alertPresenter: alerts, operations: ops, preferences: prefs,
            terminationDelay: 1_000_000, terminationTimeout: 0.003)
        controller.requestRestart()
        await settle(controller)
        XCTAssertEqual(app.forceCalls, 1)
        XCTAssertEqual(app.gracefulCalls, 0)
        XCTAssertEqual(ops.openCalls, 0)
    }

    func testCloseOnlyAndLaunchFailurePreserveOutcome() async throws {
        let app = FakeCodexApplication()
        let ops = FakeCodexApplicationOperations(); ops.applications = [app]
        let prefs = LifecyclePreferences(defaults: try temporaryDefaults()); prefs.reopenCodex = false
        let alerts = FakeAlertPresenter(); alerts.response = .alertFirstButtonReturn
        let controller = CodexRestartController(alertPresenter: alerts, operations: ops, preferences: prefs)
        controller.requestRestart()
        await settle(controller)
        XCTAssertEqual(ops.openCalls, 0)
        XCTAssertEqual(controller.statusMessage, "Codex closed.")
        app.isTerminated = false; prefs.reopenCodex = true
        ops.openError = NSError(domain: "Synthetic", code: 1)
        controller.requestRestart()
        await settle(controller)
        XCTAssertEqual(ops.openCalls, 1)
        XCTAssertEqual(controller.statusMessage, "Codex could not be reopened. Open it from Applications.")
    }

    private func settle(_ controller: CodexRestartController) async {
        for _ in 0..<200 where controller.isRestarting { try? await Task.sleep(nanoseconds: 1_000_000) }
        XCTAssertFalse(controller.isRestarting)
    }

    private func temporaryDefaults() throws -> UserDefaults {
        let suiteName = "com.modelharbor.tests.codex-restart.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suiteName))
        addTeardownBlock { [suiteName] in
            defaults.removePersistentDomain(forName: suiteName)
        }
        return defaults
    }
}
