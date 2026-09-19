import Darwin
import XCTest
@testable import HarborCore

@MainActor
final class GatewayRestorationTests: XCTestCase {
    private let candidate = String(repeating: "a", count: 64)
    private let revision = String(repeating: "c", count: 64)
    private let updatedRevision = String(repeating: "d", count: 64)
    private let boot = "550e8400-e29b-41d4-a716-446655440000"
    private var identity: GatewayIdentity {
        GatewayIdentity(protocolVersion: 1, runtimeID: candidate, bootID: boot, mode: "independent")
    }
    private func encode(_ object: [String: Any]) throws -> Data {
        try JSONSerialization.data(withJSONObject: object)
    }
    private func context(_ runtime: [String: Any]? = nil, revision: String? = nil) throws -> Data {
        try encode(["routing": "per-task", "runtime": runtime ?? identity.object,
                    "configuration_revision": revision ?? self.revision, "providers": [:]])
    }
    private func receipt(provider: String = "azure", model: String = "harbor/azure/deployment-two",
                         alreadyPresent: Bool = false) -> [String: Any] {
        ["runtime": identity.object, "configuration_revision": updatedRevision,
         "restored": alreadyPresent ? [] : [provider], "already_present": alreadyPresent ? [provider] : [],
         "routes": [["model": model, "verified": true]]]
    }
    private func service(_ provider: String = "azure", catalog: String? = nil) -> CodexService {
        CodexService(id: provider, name: provider, baseURL: "https://fixture.openai.azure.com",
            envKey: "SYNTHETIC_KEY", apiKey: "metadata-key-must-not-be-used",
            models: [CodexModel(id: "deployment-one", name: "One"), CodexModel(id: "deployment-two", name: "Two")],
            catalogPath: catalog)
    }
    private func adapter(status: Data? = nil,
                         control: @escaping (String, String, Data, GatewayIdentity?) async throws -> Data) async throws -> GrokAdapter {
        let prepared = try encode(["state": "attached", "mode": "independent", "candidate_runtime_id": candidate])
        let current = try status ?? context()
        let result = GrokAdapter(serviceRequest: { prepared }, statusRequest: { current }, controlRequest: control)
        try await result.start()
        return result
    }

    func testPendingRuntimeRefusesBeforeAnyCredentialReadOrMutation() async throws {
        var runtime = identity.object
        runtime["runtime_id"] = String(repeating: "b", count: 64)
        var reads = 0, posts = 0
        let adapter = try await adapter(status: context(runtime)) { _, _, _, _ in posts += 1; return Data() }
        do {
            _ = try await adapter.restoreSavedConnection(service: service(), modelID: "deployment-two") { _ in
                reads += 1; return "synthetic-key"
            }
            XCTFail("A pending gateway must refuse restoration")
        } catch { XCTAssertTrue(error.localizedDescription.contains("Gateway update pending")) }
        XCTAssertEqual(reads, 0)
        XCTAssertEqual(posts, 0)
    }

    func testInvalidContextNeverReadsKeychain() async throws {
        var contexts: [Data] = [try encode(["routing": "per-task", "providers": [:]])]
        for (field, value) in [("protocol_version", 2 as Any), ("boot_id", "invalid" as Any), ("mode", "legacy" as Any)] {
            var runtime = identity.object; runtime[field] = value
            contexts.append(try context(runtime))
        }
        contexts.append(try context(revision: "invalid"))
        for status in contexts {
            let adapter = try await adapter(status: status) { _, _, _, _ in XCTFail("Unexpected write"); return Data() }
            do {
                _ = try await adapter.restoreSavedConnection(service: service(), modelID: "deployment-two") { _ in
                    XCTFail("Invalid status must not read a credential"); return nil
                }
                XCTFail("Invalid restoration context accepted")
            } catch {}
        }
    }

    func testRestoresOnlyChosenAzureCredentialAndExactSavedRouteWithoutLocalChanges() async throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let catalog = folder.appendingPathComponent("azure.json")
        let bytes = Data("{\"models\":[\"keep-this-catalog-exact\"]}".utf8)
        try bytes.write(to: catalog)
        let saved = service(catalog: catalog.path)
        let selection = SelectedModel(serviceID: "openrouter", modelID: "existing-choice")
        let metadata = AppData(services: [saved], selectedModel: selection)
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        let before = try encoder.encode(metadata)
        var accounts: [String] = [], commands = 0
        let response = try encode(receipt())
        let expected = identity
        let adapter = try await adapter { method, path, body, runtime in
            commands += 1
            XCTAssertEqual(method, "POST")
            XCTAssertEqual(path, "/harbor/providers/restore")
            XCTAssertEqual(runtime, expected)
            let input = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
            XCTAssertEqual(Set(input.keys), ["expected_runtime", "expected_configuration_revision", "connections", "required_models"])
            XCTAssertEqual(input["expected_configuration_revision"] as? String, self.revision)
            XCTAssertEqual(input["required_models"] as? [String], ["harbor/azure/deployment-two"])
            let connections = try XCTUnwrap(input["connections"] as? [String: [String: String]])
            XCTAssertEqual(connections, ["azure": ["endpoint": "https://fixture.openai.azure.com/openai/v1", "key": "synthetic-keychain-value"]])
            return response
        }
        let result = try await adapter.restoreSavedConnection(service: saved, modelID: "deployment-two") { account in
            accounts.append(account); return "synthetic-keychain-value"
        }
        XCTAssertEqual(accounts, ["provider:azure"])
        XCTAssertEqual(commands, 1)
        XCTAssertEqual(result.model, "harbor/azure/deployment-two")
        XCTAssertEqual(result.runtime, identity)
        XCTAssertEqual(result.configurationRevision, updatedRevision)
        XCTAssertTrue(result.restored)
        XCTAssertEqual(try Data(contentsOf: catalog), bytes)
        XCTAssertEqual(try encoder.encode(metadata), before)
        XCTAssertEqual(metadata.selectedModel, selection)
        XCTAssertEqual(metadata.services, [saved])
    }

    func testOpenRouterUsesOnlyItsKeyAndAcceptsAlreadyPresentVerifiedReceipt() async throws {
        var saved = service("openrouter")
        saved.models = [CodexModel(id: "fixture/coder", name: "Coder")]
        let response = try encode(receipt(provider: "openrouter", model: "harbor/openrouter/fixture/coder", alreadyPresent: true))
        var reads: [String] = []
        let adapter = try await adapter { _, _, body, _ in
            let input = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
            XCTAssertEqual(input["connections"] as? [String: [String: String]], ["openrouter": ["key": "synthetic-router"]])
            XCTAssertEqual(input["required_models"] as? [String], ["harbor/openrouter/fixture/coder"])
            return response
        }
        let result = try await adapter.restoreSavedConnection(service: saved, modelID: "fixture/coder") { account in
            reads.append(account); return "synthetic-router"
        }
        XCTAssertEqual(reads, ["provider:openrouter"])
        XCTAssertFalse(result.restored)
    }

    func testUnknownRouteAndUnsupportedProviderDoNotReadCredentials() async throws {
        for (saved, model) in [(service(), "not-saved"), (service("baseten"), "deployment-two")] {
            let adapter = try await adapter { _, _, _, _ in XCTFail("Unexpected write"); return Data() }
            do {
                _ = try await adapter.restoreSavedConnection(service: saved, modelID: model) { _ in
                    XCTFail("Unexpected credential read"); return nil
                }
                XCTFail("Invalid route accepted")
            } catch {}
        }
    }

    func testMissingSavedKeyDoesNotUseMetadataKeyOrSendRequest() async throws {
        let adapter = try await adapter { _, _, _, _ in XCTFail("Missing saved key must not dispatch"); return Data() }
        do {
            _ = try await adapter.restoreSavedConnection(service: service(), modelID: "deployment-two") { _ in nil }
            XCTFail("Metadata key must not replace a missing Keychain item")
        } catch { XCTAssertTrue(error.localizedDescription.contains("No saved key")) }
    }

    func testInvalidReceiptCannotClaimVerificationAndIsNotReplayed() async throws {
        var variants: [[String: Any]] = []
        var bad = receipt(); var runtime = identity.object; runtime["boot_id"] = UUID().uuidString; bad["runtime"] = runtime; variants.append(bad)
        bad = receipt(); bad["routes"] = [["model": "harbor/azure/deployment-one", "verified": true]]; variants.append(bad)
        bad = receipt(); bad["routes"] = [["model": "harbor/azure/deployment-two", "verified": false]]; variants.append(bad)
        bad = receipt(); bad["routes"] = [["model": "harbor/azure/deployment-two", "verified": 1]]; variants.append(bad)
        bad = receipt(); bad["already_present"] = ["azure"]; variants.append(bad)
        bad = receipt(); bad["configuration_revision"] = "missing"; variants.append(bad)
        bad = receipt(); bad.removeValue(forKey: "routes"); variants.append(bad)
        for variant in variants {
            let response = try encode(variant)
            var posts = 0
            let adapter = try await adapter { _, _, _, _ in posts += 1; return response }
            do {
                _ = try await adapter.restoreSavedConnection(service: service(), modelID: "deployment-two") { _ in "synthetic-key" }
                XCTFail("Bad receipt accepted")
            } catch { XCTAssertTrue(error.localizedDescription.contains("outcome is unknown")) }
            XCTAssertEqual(posts, 1)
        }
    }

    func testLostReplyHasUnknownOutcomeAndNoRetry() async throws {
        var posts = 0
        let adapter = try await adapter { _, _, _, _ in posts += 1; throw URLError(.networkConnectionLost) }
        do {
            _ = try await adapter.restoreSavedConnection(service: service(), modelID: "deployment-two") { _ in "synthetic-key" }
            XCTFail("Lost reply must not claim success")
        } catch { XCTAssertTrue(error.localizedDescription.contains("outcome is unknown")) }
        XCTAssertEqual(posts, 1)
    }

    func testCancelledCredentialReadCompletesBeforeSystemPromptReturns() async throws {
        let reading = expectation(description: "scoped credential read began")
        let cancelled = expectation(description: "restore completed cancellation")
        let invalidated = expectation(description: "authentication invalidated")
        let returned = expectation(description: "late system read returned")
        let releaseRead = DispatchSemaphore(value: 0)
        defer { releaseRead.signal() }
        var posts = 0
        let adapter = try await adapter { _, _, _, _ in posts += 1; return Data() }
        let saved = service()
        let task = Task {
            do {
                _ = try await adapter.restoreSavedConnection(service: saved, modelID: "deployment-two") { _ in
                    try await CancellableCredentialRead.run(operation: {
                        reading.fulfill()
                        releaseRead.wait()
                        returned.fulfill()
                        return "synthetic-key"
                    }, cancelAuthentication: { invalidated.fulfill() })
                }
                XCTFail("Cancelled restore must not succeed")
            } catch { XCTAssertTrue(error is CancellationError) }
            cancelled.fulfill()
        }
        await fulfillment(of: [reading], timeout: 3)
        task.cancel()
        await fulfillment(of: [cancelled, invalidated], timeout: 1)
        XCTAssertEqual(posts, 0)
        releaseRead.signal()
        await fulfillment(of: [returned], timeout: 3)
        await task.value
        XCTAssertEqual(posts, 0)
    }

    func testCredentialDeadlineReturnsBeforeBlockedSystemReadAndInvalidatesAuthentication() async throws {
        let reading = expectation(description: "credential read started")
        let timedOut = expectation(description: "credential timeout returned")
        let invalidated = expectation(description: "authentication invalidated")
        let returned = expectation(description: "late credential read returned")
        let releaseRead = DispatchSemaphore(value: 0)
        defer { releaseRead.signal() }
        let task = Task {
            do {
                _ = try await CancellableCredentialRead.run(operation: {
                    reading.fulfill()
                    releaseRead.wait()
                    returned.fulfill()
                    return "synthetic-key"
                }, cancelAuthentication: { invalidated.fulfill() }, timeout: 0.15)
                XCTFail("Credential timeout must not succeed")
            } catch { XCTAssertTrue(error.localizedDescription.contains("timed out")) }
            timedOut.fulfill()
        }
        await fulfillment(of: [reading], timeout: 3)
        await fulfillment(of: [timedOut, invalidated], timeout: 1)
        releaseRead.signal()
        await fulfillment(of: [returned], timeout: 3)
        await task.value
    }

    func testCancellationAfterCredentialReadPreventsDispatch() async throws {
        var posts = 0
        let adapter = try await adapter { _, _, _, _ in posts += 1; return Data() }
        let saved = service()
        let task = Task {
            try await adapter.restoreSavedConnection(service: saved, modelID: "deployment-two") { _ in
                withUnsafeCurrentTask { $0?.cancel() }
                return "synthetic-key"
            }
        }
        do { _ = try await task.value; XCTFail("Cancelled restore dispatched") }
        catch { XCTAssertTrue(error is CancellationError) }
        XCTAssertEqual(posts, 0)
    }

    func testCancellationWhileWaitingForRestoreDoesNotReplay() async throws {
        let started = expectation(description: "restore dispatched")
        var posts = 0
        let adapter = try await adapter { _, _, _, _ in
            posts += 1; started.fulfill()
            try await Task.sleep(nanoseconds: 30_000_000_000)
            return Data()
        }
        let saved = service()
        let task = Task { try await adapter.restoreSavedConnection(service: saved, modelID: "deployment-two") { _ in "synthetic-key" } }
        await fulfillment(of: [started], timeout: 3)
        task.cancel()
        do { _ = try await task.value; XCTFail("Cancelled restore claimed success") }
        catch { XCTAssertTrue(error.localizedDescription.contains("outcome is unknown")) }
        XCTAssertEqual(posts, 1)
    }
}

@MainActor
final class GatewayControlProcessTests: XCTestCase {
    private func fixture() throws -> (URL, URL) {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let marker = directory.appendingPathComponent("started")
        let script = directory.appendingPathComponent("fixture.py")
        try """
        import os, pathlib, signal, sys, time
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))
        sys.stdin.buffer.read()
        time.sleep(30)
        """.write(to: script, atomically: true, encoding: .utf8)
        return (script, marker)
    }

    func testHelperEchoUsesStdin() async throws {
        let input = Data("synthetic-only-private-payload".utf8)
        let result = try await GatewayControlProcess.run(executable: PythonRuntime.executable(),
            arguments: ["-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
            environment: ProcessInfo.processInfo.environment, input: input, timeout: 3)
        XCTAssertEqual(result.status, 0)
        XCTAssertEqual(result.output, input)
    }

    func testHelperDeadlineTerminatesOnlyItsOwnUnresponsiveProcess() async throws {
        let (script, marker) = try fixture()
        defer { try? FileManager.default.removeItem(at: script.deletingLastPathComponent()) }
        let unrelated = Process()
        unrelated.executableURL = URL(fileURLWithPath: "/bin/sleep")
        unrelated.arguments = ["30"]
        try unrelated.run()
        defer { unrelated.terminate(); unrelated.waitUntilExit() }
        do {
            _ = try await GatewayControlProcess.run(executable: PythonRuntime.executable(),
                arguments: [script.path, marker.path], environment: ProcessInfo.processInfo.environment,
                input: Data(), timeout: 0.5)
            XCTFail("Deadline did not stop helper")
        } catch { XCTAssertTrue(error.localizedDescription.contains("timed out")) }
        XCTAssertTrue(unrelated.isRunning)
        if FileManager.default.fileExists(atPath: marker.path) {
            let pid = try XCTUnwrap(Int32(String(contentsOf: marker, encoding: .utf8)))
            XCTAssertEqual(Darwin.kill(pid, 0), -1)
            XCTAssertEqual(errno, ESRCH)
        }
    }

    func testCancellationStopsRunningHelper() async throws {
        let (script, marker) = try fixture()
        defer { try? FileManager.default.removeItem(at: script.deletingLastPathComponent()) }
        let python = try PythonRuntime.executable()
        let task = Task {
            try await GatewayControlProcess.run(executable: python, arguments: [script.path, marker.path],
                environment: ProcessInfo.processInfo.environment, input: Data(), timeout: 10)
        }
        let deadline = ContinuousClock.now + .seconds(5)
        while !FileManager.default.fileExists(atPath: marker.path) && ContinuousClock.now < deadline {
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTAssertTrue(FileManager.default.fileExists(atPath: marker.path))
        task.cancel()
        do { _ = try await task.value; XCTFail("Cancelled helper completed") }
        catch { XCTAssertTrue(error.localizedDescription.contains("cancelled")) }
        let pid = try XCTUnwrap(Int32(String(contentsOf: marker, encoding: .utf8)))
        XCTAssertEqual(Darwin.kill(pid, 0), -1)
        XCTAssertEqual(errno, ESRCH)
    }
}
