import XCTest
@testable import HarborCore

final class ConnectionPresentationTests: XCTestCase {
    private func connection(result: String = "verified", fresh: Bool = false,
                            credentials: Bool = true, revision: String = "current",
                            boot: String = "current") -> APIConnectionPresentation {
        APIConnectionPresentation(credentialsAvailable: credentials, proofs: [[
            "provider": "azure", "result": result, "verified": fresh,
            "configuration_revision": revision, "boot_id": boot
        ]], provider: "azure", revision: "current", bootID: "current")
    }

    func testExpiredSuccessfulCheckRemainsConnected() {
        XCTAssertTrue(connection().connected)
        XCTAssertEqual(connection().label, "Connected")
        XCTAssertTrue(connection(fresh: true).connected)
    }

    func testCredentialsAndCurrentAccountEvidenceAreRequired() {
        XCTAssertFalse(connection(credentials: false).connected)
        XCTAssertFalse(connection(revision: "old").connected)
        XCTAssertFalse(connection(boot: "old").connected)
        XCTAssertEqual(connection(result: "unavailable").label, "Configured")
        XCTAssertEqual(connection(result: "auth_failed").label, "Reconnect required")
    }
}
