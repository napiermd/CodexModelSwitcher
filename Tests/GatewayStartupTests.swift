import XCTest
@testable import HarborCore

@MainActor
final class GatewayStartupTests: XCTestCase {
    private let candidate = String(repeating: "a", count: 64)
    private let previous = String(repeating: "b", count: 64)

    private func encoded(_ value: [String: Any]) throws -> Data {
        try JSONSerialization.data(withJSONObject: value)
    }

    private func service(state: String = "attached", existing: Bool = true,
                         maintenance: Bool = false) throws -> Data {
        try encoded(["state": state, "mode": "independent", "existing_service": existing,
                     "candidate_runtime_id": candidate, "maintenance_required": maintenance])
    }

    private func status(runtime: String) throws -> Data {
        try encoded(["routing": "per-task", "providers": [:],
                     "runtime": ["mode": "independent", "protocol_version": 1,
                                 "runtime_id": runtime, "boot_id": "550e8400-e29b-41d4-a716-446655440000"]])
    }

    func testExistingGatewayLoadsDisplayAndNeverBootstraps() async throws {
        var events: [String] = []
        let preparation = try service()
        let health = try status(runtime: candidate)
        let adapter = GrokAdapter(serviceRequest: { events.append("attach"); return preparation },
                                  statusRequest: { events.append("status"); return health })
        let mode = try await GatewayStartupCoordinator.start(adapter: adapter,
            loadDisplay: { events.append("display") }, bootstrap: { events.append("bootstrap writes") })
        XCTAssertEqual(mode, .attached)
        XCTAssertEqual(events, ["display", "attach", "status"])
        XCTAssertFalse(adapter.requiresMaintenanceForRuntimeUpdate)
    }

    func testExistingGatewayMismatchSurvivesRepeatedHealthChecks() async throws {
        let preparation = try service(maintenance: true)
        let health = try status(runtime: previous)
        let adapter = GrokAdapter(serviceRequest: { preparation }, statusRequest: { health })
        var bootstraps = 0
        let mode = try await GatewayStartupCoordinator.start(adapter: adapter,
            loadDisplay: {}, bootstrap: { bootstraps += 1 })
        XCTAssertEqual(mode, .attached)
        XCTAssertEqual(bootstraps, 0)
        for _ in 0..<3 {
            let healthy = await adapter.isHealthy()
            XCTAssertTrue(healthy)
            XCTAssertTrue(adapter.requiresMaintenanceForRuntimeUpdate)
        }
    }

    func testExistingRegisteredServiceSkipsBootstrapAfterItBecomesHealthy() async throws {
        let preparation = try service(state: "registered", maintenance: true)
        let health = try status(runtime: previous)
        let adapter = GrokAdapter(serviceRequest: { preparation }, statusRequest: { health })
        var bootstraps = 0
        let mode = try await GatewayStartupCoordinator.start(adapter: adapter,
            loadDisplay: {}, bootstrap: { bootstraps += 1 })
        XCTAssertEqual(mode, .attached)
        XCTAssertEqual(bootstraps, 0)
        XCTAssertTrue(adapter.requiresMaintenanceForRuntimeUpdate)
    }

    func testFreshGatewayBootstrapsOnceAfterAuthenticatedHealth() async throws {
        var events: [String] = []
        let preparation = try service(state: "registered", existing: false)
        let health = try status(runtime: candidate)
        let adapter = GrokAdapter(serviceRequest: { events.append("register"); return preparation },
                                  statusRequest: { events.append("status"); return health })
        let mode = try await GatewayStartupCoordinator.start(adapter: adapter,
            loadDisplay: { events.append("display") }, bootstrap: { events.append("bootstrap writes") })
        XCTAssertEqual(mode, .bootstrapped)
        XCTAssertEqual(events, ["display", "register", "status", "bootstrap writes"])
        XCTAssertFalse(adapter.requiresMaintenanceForRuntimeUpdate)
    }

    func testStartupFailureLeavesDisplayAvailableWithoutBootstrap() async throws {
        var events: [String] = []
        let preparation = try encoded(["state": "blocked", "error": "Synthetic foreign listener"])
        let adapter = GrokAdapter(serviceRequest: { events.append("prepare"); return preparation },
                                  statusRequest: { events.append("status"); return Data() })
        do {
            _ = try await GatewayStartupCoordinator.start(adapter: adapter,
                loadDisplay: { events.append("display") }, bootstrap: { events.append("bootstrap writes") })
            XCTFail("A foreign listener must not trigger bootstrap")
        } catch {
            XCTAssertTrue(error.localizedDescription.contains("Synthetic foreign listener"))
        }
        XCTAssertEqual(events, ["display", "prepare"])
    }

    func testBootstrapWaitsForValidRuntimeIdentity() async throws {
        let preparation = try service(state: "registered", existing: false)
        let invalid = try status(runtime: "not-a-runtime-digest")
        let valid = try status(runtime: candidate)
        var statusCalls = 0
        var statusCallsAtBootstrap = 0
        let adapter = GrokAdapter(serviceRequest: { preparation }, statusRequest: {
            statusCalls += 1
            return statusCalls == 1 ? invalid : valid
        })
        _ = try await GatewayStartupCoordinator.start(adapter: adapter,
            loadDisplay: {}, bootstrap: { statusCallsAtBootstrap = statusCalls })
        XCTAssertEqual(statusCalls, 2)
        XCTAssertEqual(statusCallsAtBootstrap, 2)
    }

    func testMatchingAuthenticatedRuntimeResolvesRegisteredUnknownWarning() async throws {
        let preparation = try service(state: "registered", maintenance: true)
        let health = try status(runtime: candidate)
        let adapter = GrokAdapter(serviceRequest: { preparation }, statusRequest: { health })
        try await adapter.start()
        XCTAssertTrue(adapter.requiresMaintenanceForRuntimeUpdate)
        let healthy = await adapter.isHealthy()
        XCTAssertTrue(healthy)
        XCTAssertFalse(adapter.requiresMaintenanceForRuntimeUpdate)
        XCTAssertTrue(adapter.preservesExistingGateway)
    }

    func testLegacyGatewayKeepsMaintenanceWarningAndSkipsBootstrap() async throws {
        let preparation = try encoded(["state": "attached", "mode": "legacy", "maintenance_required": true])
        let health = try encoded(["routing": "per-task", "providers": [:],
                                  "runtime": ["mode": "legacy", "protocol_version": 1]])
        let adapter = GrokAdapter(serviceRequest: { preparation }, statusRequest: { health })
        var bootstraps = 0
        let mode = try await GatewayStartupCoordinator.start(adapter: adapter,
            loadDisplay: {}, bootstrap: { bootstraps += 1 })
        XCTAssertEqual(mode, .attached)
        XCTAssertEqual(bootstraps, 0)
        let healthy = await adapter.isHealthy()
        XCTAssertTrue(healthy)
        XCTAssertTrue(adapter.requiresMaintenanceForRuntimeUpdate)
    }
}
