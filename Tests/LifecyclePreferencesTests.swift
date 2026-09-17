import AppKit
import Combine
import CoreServices
import XCTest
@testable import HarborCore

@MainActor
final class LifecyclePreferencesTests: XCTestCase {
    private var defaults: UserDefaults!
    private var suiteName: String!
    private var cancellables: Set<AnyCancellable> = []

    override func setUp() {
        super.setUp()
        suiteName = "com.modelharbor.tests.lifecycle.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)!
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        defaults = nil
        suiteName = nil
        cancellables.removeAll()
        super.tearDown()
    }

    func testDefaultsUseNativeContractsAndDurableKeys() throws {
        let preferences = LifecyclePreferences(defaults: defaults)

        XCTAssertEqual(preferences.presence, .dockAndMenuBar)
        XCTAssertEqual(preferences.presence.title, "Dock + menu bar")
        XCTAssertEqual(preferences.closeBehavior, .ask)
        XCTAssertEqual(preferences.closeBehavior.title, "Ask every time")
        XCTAssertTrue(preferences.openWindowAtLaunch)
        XCTAssertEqual(preferences.codexCloseMethod, .graceful)
        XCTAssertEqual(preferences.codexCloseMethod.title, "Graceful")
        XCTAssertTrue(preferences.reopenCodex)
        XCTAssertEqual(Notification.Name.harborOpenWindow.rawValue, "harborOpenWindow")
        XCTAssertEqual(HarborPresence.allCases.map(\.title), ["Dock + menu bar", "Menu bar only"])
        XCTAssertEqual(HarborCloseBehavior.allCases.map(\.title), ["Ask every time", "Keep in Dock", "Menu bar only"])
        XCTAssertEqual(CodexCloseMethod.allCases.map(\.title), ["Graceful", "Force"])
    }

    func testPublishedValuesPersistUnderExactKeys() throws {
        let preferences = LifecyclePreferences(defaults: defaults)

        preferences.presence = .menuBarOnly
        preferences.closeBehavior = .keepInDock
        preferences.openWindowAtLaunch = false
        preferences.codexCloseMethod = .force
        preferences.reopenCodex = false

        XCTAssertEqual(defaults.string(forKey: "harbor.presence"), "menuBarOnly")
        XCTAssertEqual(defaults.string(forKey: "harbor.closeBehavior"), "keepInDock")
        XCTAssertEqual(defaults.bool(forKey: "harbor.openWindowAtLaunch"), false)
        XCTAssertEqual(defaults.string(forKey: "harbor.codexCloseMethod"), "force")
        XCTAssertEqual(defaults.bool(forKey: "harbor.reopenCodex"), false)

        let restored = LifecyclePreferences(defaults: defaults)
        XCTAssertEqual(restored.presence, .menuBarOnly)
        XCTAssertEqual(restored.closeBehavior, .keepInDock)
        XCTAssertFalse(restored.openWindowAtLaunch)
        XCTAssertEqual(restored.codexCloseMethod, .force)
        XCTAssertFalse(restored.reopenCodex)
    }

    func testUnknownStoredEnumFallsBackSafely() throws {
        defaults.set("unknown", forKey: "harbor.presence")
        defaults.set("force", forKey: "harbor.closeBehavior")
        let preferences = LifecyclePreferences(defaults: defaults)

        XCTAssertEqual(preferences.presence, .dockAndMenuBar)
        XCTAssertEqual(preferences.closeBehavior, .ask)
    }

    func testRecognizesLoginLaunchWithoutSuppressingManualLaunch() {
        let event = NSAppleEventDescriptor(
            eventClass: AEEventClass(kCoreEventClass),
            eventID: AEEventID(kAEOpenApplication), targetDescriptor: nil,
            returnID: -1, transactionID: 0)
        XCTAssertFalse(HarborLaunchEvent.isLoginItem(nil))
        XCTAssertFalse(HarborLaunchEvent.isLoginItem(event))
        event.setParam(NSAppleEventDescriptor(enumCode: OSType(keyAELaunchedAsLogInItem)),
                       forKeyword: AEKeyword(keyAEPropData))
        XCTAssertTrue(HarborLaunchEvent.isLoginItem(event))
        event.setParam(NSAppleEventDescriptor(enumCode: OSType(keyAELaunchedAsServiceItem)),
                       forKeyword: AEKeyword(keyAEPropData))
        XCTAssertFalse(HarborLaunchEvent.isLoginItem(event))
    }

    func testPublishedChangesEmitObjectWillChange() throws {
        let preferences = LifecyclePreferences(defaults: defaults)
        let expectation = expectation(description: "objectWillChange")
        expectation.assertForOverFulfill = false
        preferences.objectWillChange.sink { _ in expectation.fulfill() }.store(in: &cancellables)

        preferences.presence = .menuBarOnly
        preferences.closeBehavior = .keepInDock
        preferences.openWindowAtLaunch = false
        preferences.codexCloseMethod = .force
        preferences.reopenCodex = false

        wait(for: [expectation], timeout: 0.1)
    }
}
