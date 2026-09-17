import XCTest
import ServiceManagement
@testable import HarborCore

final class LoginItemTests: XCTestCase {
    /// Deterministic fake for SMAppService used by every test. Never touches
    /// the real login item registration.
    final class FakeService: LoginItemService {
        var status: SMAppService.Status = .notRegistered
        var registerCalls = 0
        var unregisterCalls = 0
        var registerError: Error?
        var unregisterError: Error?

        func register() throws {
            registerCalls += 1
            if let registerError { throw registerError }
            status = .enabled
        }

        func unregister() async throws {
            unregisterCalls += 1
            if let unregisterError { throw unregisterError }
            status = .notRegistered
        }
    }

    /// Lets the controller's fire-and-forget unregister task run to completion.
    @MainActor
    func waitForIdle(_ controller: LoginItemController, timeout: TimeInterval = 1.0) async {
        let deadline = Date().addingTimeInterval(timeout)
        while controller.isChanging && Date() < deadline {
            try? await Task.sleep(nanoseconds: 1_000_000)
        }
    }

    @MainActor
    func testInitReflectsOSStatusWithoutRegistering() {
        let fake = FakeService()
        fake.status = .enabled
        let controller = LoginItemController(service: fake)
        XCTAssertTrue(controller.isEnabled)
        XCTAssertFalse(controller.isChanging)
        XCTAssertEqual(controller.statusMessage, "Harbor opens automatically when you log in.")
        XCTAssertEqual(controller.errorMessage, "")
        XCTAssertEqual(fake.registerCalls, 0)
        XCTAssertEqual(fake.unregisterCalls, 0)
    }

    @MainActor
    func testRefreshReadsStatusFromOS() {
        let fake = FakeService()
        let controller = LoginItemController(service: fake)
        XCTAssertFalse(controller.isEnabled)

        fake.status = .enabled
        controller.refresh()
        XCTAssertTrue(controller.isEnabled)
        XCTAssertEqual(controller.statusMessage, "Harbor opens automatically when you log in.")
        XCTAssertEqual(controller.errorMessage, "")

        fake.status = .notRegistered
        controller.refresh()
        XCTAssertFalse(controller.isEnabled)
        XCTAssertEqual(controller.statusMessage, "Harbor does not open at login.")
    }

    @MainActor
    func testEnableRegistersAndUpdatesStatus() {
        let fake = FakeService()
        let controller = LoginItemController(service: fake)
        controller.setEnabled(true)
        XCTAssertEqual(fake.registerCalls, 1)
        XCTAssertEqual(fake.unregisterCalls, 0)
        XCTAssertTrue(controller.isEnabled)
        XCTAssertFalse(controller.isChanging)
        XCTAssertEqual(controller.statusMessage, "Harbor opens automatically when you log in.")
        XCTAssertEqual(controller.errorMessage, "")
    }

    @MainActor
    func testDisableUnregistersAndUpdatesStatus() async {
        let fake = FakeService()
        fake.status = .enabled
        let controller = LoginItemController(service: fake)
        controller.setEnabled(false)
        XCTAssertTrue(controller.isChanging)
        XCTAssertEqual(controller.statusMessage, "Removing Harbor from login items…")
        await waitForIdle(controller)
        XCTAssertEqual(fake.unregisterCalls, 1)
        XCTAssertEqual(fake.registerCalls, 0)
        XCTAssertFalse(controller.isEnabled)
        XCTAssertFalse(controller.isChanging)
        XCTAssertEqual(controller.statusMessage, "Harbor does not open at login.")
        XCTAssertEqual(controller.errorMessage, "")
    }

    @MainActor
    func testRegisterFailureSurfacesSanitizedErrorAndKeepsOSStatus() {
        let fake = FakeService()
        fake.registerError = NSError(domain: "SyntheticServiceError", code: Int(kSMErrorLaunchDeniedByUser))
        let controller = LoginItemController(service: fake)
        controller.setEnabled(true)
        XCTAssertEqual(fake.registerCalls, 1)
        XCTAssertFalse(controller.isEnabled)
        XCTAssertFalse(controller.isChanging)
        XCTAssertEqual(controller.errorMessage, "Harbor could not be added to login items. Try again or enable it in System Settings.")
        XCTAssertEqual(controller.statusMessage, "Harbor could not be added to login items.")
    }

    @MainActor
    func testUnregisterFailureSurfacesSanitizedErrorAndKeepsOSStatus() async {
        let fake = FakeService()
        fake.status = .enabled
        fake.unregisterError = NSError(domain: "SyntheticServiceError", code: Int(kSMErrorJobNotFound))
        let controller = LoginItemController(service: fake)
        controller.setEnabled(false)
        await waitForIdle(controller)
        XCTAssertEqual(fake.unregisterCalls, 1)
        XCTAssertTrue(controller.isEnabled)
        XCTAssertFalse(controller.isChanging)
        XCTAssertEqual(controller.errorMessage, "Harbor could not be removed from login items. Try again or remove it in System Settings.")
        XCTAssertEqual(controller.statusMessage, "Harbor could not be removed from login items.")
    }

    @MainActor
    func testOverlappingOperationsAreIgnored() async {
        let fake = FakeService()
        let controller = LoginItemController(service: fake)
        controller.setEnabled(true)
        controller.setEnabled(false)
        controller.setEnabled(true)
        XCTAssertEqual(fake.registerCalls, 1)
        XCTAssertEqual(fake.unregisterCalls, 0)
        XCTAssertTrue(controller.isEnabled)
    }

    @MainActor
    func testNoOpWhenRequestedStateMatchesOS() {
        let fake = FakeService()
        fake.status = .enabled
        let controller = LoginItemController(service: fake)
        controller.setEnabled(true)
        XCTAssertEqual(fake.registerCalls, 0)

        fake.status = .notRegistered
        controller.setEnabled(false)
        XCTAssertEqual(fake.unregisterCalls, 0)
    }

    @MainActor
    func testDisableWhileRequiresApprovalUnregisters() async {
        let fake = FakeService()
        fake.status = .requiresApproval
        let controller = LoginItemController(service: fake)
        XCTAssertFalse(controller.isEnabled)
        controller.setEnabled(false)
        await waitForIdle(controller)
        XCTAssertEqual(fake.unregisterCalls, 1)
        XCTAssertFalse(controller.isEnabled)
        XCTAssertEqual(controller.statusMessage, "Harbor does not open at login.")
    }

    @MainActor
    func testRequiresApprovalStatusMessage() {
        let fake = FakeService()
        fake.status = .requiresApproval
        let controller = LoginItemController(service: fake)
        controller.refresh()
        XCTAssertFalse(controller.isEnabled)
        XCTAssertEqual(controller.statusMessage, "Harbor is waiting for approval in System Settings.")
    }

    @MainActor
    func testNotFoundStatusMessage() {
        let fake = FakeService()
        fake.status = .notFound
        let controller = LoginItemController(service: fake)
        controller.refresh()
        XCTAssertFalse(controller.isEnabled)
        XCTAssertEqual(controller.statusMessage, "The Harbor login item could not be found.")
    }

    @MainActor
    func testUnknownErrorFallsBackToActionCopy() {
        let fake = FakeService()
        fake.registerError = NSError(domain: "SomeDomain", code: 999, userInfo: [NSLocalizedDescriptionKey: "Private system path /secret/should-not-leak"])
        let controller = LoginItemController(service: fake)
        controller.setEnabled(true)
        XCTAssertEqual(controller.errorMessage,
                       "Harbor could not be added to login items. Try again or enable it in System Settings.")
    }
}
