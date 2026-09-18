import Foundation
import Darwin

@MainActor
final class GrokAdapter {
    private var servicePrepared = false
    private var candidateRuntimeID: String?
    private let serviceRequest: () async throws -> Data
    private let statusRequest: () async throws -> Data
    private let controlRequest: (String, String, Data, GatewayIdentity?) async throws -> Data
    private(set) var preservesExistingGateway = false
    private(set) var requiresMaintenanceForRuntimeUpdate = false

    init(serviceRequest: (() async throws -> Data)? = nil,
         statusRequest: (() async throws -> Data)? = nil,
         controlRequest: ((String, String, Data, GatewayIdentity?) async throws -> Data)? = nil) {
        self.serviceRequest = serviceRequest ?? Self.prepareService
        let control = controlRequest ?? { method, path, body, expected in
            try await Self.ownerControl(method, path: path, body: body, expectedRuntime: expected)
        }
        self.controlRequest = control
        self.statusRequest = statusRequest ?? { try await control("GET", "/harbor/status", Data(), nil) }
    }
    private static var bridgePort: Int {
        let port = Int(ProcessInfo.processInfo.environment["MODEL_HARBOR_PORT"] ?? "48118") ?? 48118
        return (1...65535).contains(port) ? port : 48118
    }

    private static var bridgeEnvironment: [String: String] {
        var environment = ProcessInfo.processInfo.environment
        environment["MODEL_HARBOR_TOKEN_PATH"] = AppPaths.codexDirectory.appendingPathComponent("model-harbor-bridge-token").path
        environment["MODEL_HARBOR_CONFIG_DIR"] = AppPaths.codexDirectory.path
        environment["MODEL_HARBOR_PORT"] = String(bridgePort)
        return environment
    }

    func start() async throws {
        if servicePrepared { return }
        let bytes = try await serviceRequest()
        let status = try JSONSerialization.jsonObject(with: bytes) as? [String: Any]
        guard let state = status?["state"] as? String, ["attached", "registered"].contains(state) else {
            throw ProviderError.message(status?["error"] as? String ?? "The independent gateway could not start. Existing services were preserved.")
        }
        preservesExistingGateway = state == "attached" || status?["existing_service"] as? Bool == true
        candidateRuntimeID = status?["candidate_runtime_id"] as? String
        requiresMaintenanceForRuntimeUpdate = status?["maintenance_required"] as? Bool ?? true
        servicePrepared = true
    }

    private static func prepareService() async throws -> Data {
        guard let script = Bundle.main.url(forResource: "gateway_service", withExtension: "py") else {
            throw ProviderError.message("The independent gateway helper is missing from the app bundle.")
        }
        let executable = try PythonRuntime.executable()
        let environment = bridgeEnvironment
        return try await Task.detached {
            let helper = Process()
            helper.executableURL = executable
            helper.arguments = ["-B", "-u", script.path, "--source", script.deletingLastPathComponent().path]
            helper.environment = environment
            let output = Pipe()
            helper.standardInput = FileHandle.nullDevice
            helper.standardOutput = output
            helper.standardError = FileHandle.nullDevice
            try helper.run()
            let bytes = output.fileHandleForReading.readDataToEndOfFile()
            helper.waitUntilExit()
            guard helper.terminationStatus == 0 else {
                let status = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any]
                throw ProviderError.message(status?["error"] as? String ?? "The independent gateway could not start. Existing services were preserved.")
            }
            return bytes
        }.value
    }

    func isHealthy() async -> Bool {
        guard let bytes = try? await connectionStatus(),
              let value = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any],
              value["routing"] as? String == "per-task",
              value["providers"] is [String: Any] else { return false }
        if let runtime = value["runtime"] as? [String: Any] {
            guard runtime["protocol_version"] as? Int == 1 else { return false }
            if runtime["mode"] as? String == "legacy" {
                requiresMaintenanceForRuntimeUpdate = true
                return true
            }
            guard runtime["mode"] as? String == "independent",
                  let identity = runtime["runtime_id"] as? String,
                  identity.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil,
                  let bootID = runtime["boot_id"] as? String,
                  UUID(uuidString: bootID) != nil else { return false }
            if let candidateRuntimeID {
                requiresMaintenanceForRuntimeUpdate = identity != candidateRuntimeID
            }
        } else {
            requiresMaintenanceForRuntimeUpdate = true
        }
        return true
    }

    func detach() {
        servicePrepared = false
    }

    static func ownerControl(_ method: String, path: String, body: Data = Data(),
                             expectedRuntime: GatewayIdentity? = nil) async throws -> Data {
        guard let script = Bundle.main.url(forResource: "gateway_control", withExtension: "py") else {
            throw ProviderError.message("The authenticated gateway control helper is missing from the app bundle.")
        }
        var command: [String: Any] = ["method": method, "path": path, "body_base64": body.base64EncodedString()]
        if let expectedRuntime { command["expected_runtime"] = expectedRuntime.object }
        let input = try JSONSerialization.data(withJSONObject: command)
        let timeout: TimeInterval
        switch path {
        case "/harbor/usage": timeout = 125
        case "/oauth/status": timeout = 65
        case "/harbor/baseten/reconnect": timeout = 75
        case "/harbor/providers/restore": timeout = 45
        default: timeout = 20
        }
        let result = try await GatewayControlProcess.run(executable: PythonRuntime.executable(),
            arguments: ["-B", "-u", script.path], environment: bridgeEnvironment, input: input, timeout: timeout)
        guard result.status == 0,
              let value = try? JSONSerialization.jsonObject(with: result.output) as? [String: Any],
              value["ok"] as? Bool == true,
              let encoded = value["body_base64"] as? String,
              let response = Data(base64Encoded: encoded) else {
            let value = try? JSONSerialization.jsonObject(with: result.output) as? [String: Any]
            throw ProviderError.message(value?["error"] as? String ?? "The gateway did not confirm the control request. No request was replayed.")
        }
        if let expectedRuntime {
            guard let runtime = value["runtime"],
                  let bytes = try? JSONSerialization.data(withJSONObject: runtime),
                  let identity = try? JSONDecoder().decode(GatewayIdentity.self, from: bytes),
                  identity == expectedRuntime else {
                throw ProviderError.message("The gateway identity changed. The operation was not confirmed.")
            }
        }
        return response
    }

    func restoreSavedConnection(service: CodexService, modelID: String,
                                readCredential: (String) async throws -> String?) async throws -> RestoredGatewayRoute {
        guard ["azure", "openrouter"].contains(service.id),
              service.models.contains(where: { $0.id == modelID }), !modelID.isEmpty else {
            throw ProviderError.message("Choose an existing saved Azure deployment or OpenRouter model.")
        }
        let model = LiveRouting.modelID(for: SelectedModel(serviceID: service.id, modelID: modelID))
        let bytes = try await connectionStatus()
        let context: GatewayRestoreContext
        do { context = try JSONDecoder().decode(GatewayRestoreContext.self, from: bytes) }
        catch { throw ProviderError.message("The gateway did not provide a valid restoration context.") }
        guard context.routing == "per-task", context.runtime.isIndependent,
              context.runtime.runtimeID == candidateRuntimeID,
              GatewayIdentity.isDigest(context.configurationRevision) else {
            throw ProviderError.message("Gateway update pending or runtime identity unavailable. Finish coordinated maintenance before restoring saved connections.")
        }
        try Task.checkCancellation()
        let endpoint = service.id == "azure" ? try AzureAPI.endpoint(service.baseURL).absoluteString : nil
        guard let key = try await readCredential("provider:" + service.id), !key.isEmpty else {
            throw ProviderError.message("No saved key is available for this provider. Use its connection setup to save one.")
        }
        try Task.checkCancellation()
        var connection = ["key": key]
        if let endpoint { connection["endpoint"] = endpoint }
        let body = try JSONSerialization.data(withJSONObject: [
            "expected_runtime": context.runtime.object,
            "expected_configuration_revision": context.configurationRevision,
            "connections": [service.id: connection], "required_models": [model]
        ])
        do {
            let response = try await controlRequest("POST", "/harbor/providers/restore", body, context.runtime)
            try Task.checkCancellation()
            let receipt = try JSONDecoder().decode(GatewayRestoreReceipt.self, from: response)
            guard receipt.runtime == context.runtime, GatewayIdentity.isDigest(receipt.configurationRevision),
                  receipt.restored + receipt.alreadyPresent == [service.id],
                  receipt.routes.count == 1, receipt.routes[0].model == model,
                  receipt.routes[0].verified else {
                throw ProviderError.message("The gateway returned an incomplete restoration receipt.")
            }
            return RestoredGatewayRoute(model: model, runtime: receipt.runtime,
                configurationRevision: receipt.configurationRevision, restored: !receipt.restored.isEmpty)
        } catch {
            throw ProviderError.message("The gateway did not confirm restoration and verification. The restoration outcome is unknown; no request was replayed. " + error.localizedDescription)
        }
    }

    func connectionStatus() async throws -> Data {
        try await statusRequest()
    }

    func verifyRoute(model: String) async throws {
        let body = try JSONSerialization.data(withJSONObject: ["model": model])
        let bytes = try await Self.ownerControl("POST", path: "/harbor/verify", body: body)
        let result = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any]
        guard result?["verified"] as? Bool == true else {
            throw ProviderError.message("Harbor could not verify this route with its current settings. The existing gateway was preserved.")
        }
    }

    func setTaskRepairsEnabled(_ enabled: Bool) async throws {
        let action = enabled ? "enable" : "disable"
        _ = try await Self.ownerControl("POST", path: "/harbor/repairs/\(action)")
    }

    func configureOpenRouter(key: String) async throws {
        let body = try JSONSerialization.data(withJSONObject: ["key": key])
        _ = try await Self.ownerControl("POST", path: "/harbor/providers/openrouter", body: body)
    }

    func configureAzure(endpoint: String, key: String) async throws {
        let body = try JSONSerialization.data(withJSONObject: ["endpoint": endpoint, "key": key])
        _ = try await Self.ownerControl("POST", path: "/harbor/providers/azure", body: body)
    }

    func accountStatus() async throws -> Data {
        try await Self.ownerControl("GET", path: "/oauth/status")
    }

    func reconnectBaseten() async throws {
        _ = try await Self.ownerControl("POST", path: "/harbor/baseten/reconnect")
    }

    static func login() async throws {
        try await Task.detached {
            let home = FileManager.default.homeDirectoryForCurrentUser
            let paths = [home.appendingPathComponent(".local/bin/grok").path, "/opt/homebrew/bin/grok", "/usr/local/bin/grok"]
            guard let path = paths.first(where: { FileManager.default.isExecutableFile(atPath: $0) }) else {
                throw NSError(domain: "ModelHarbor", code: 1, userInfo: [NSLocalizedDescriptionKey: "Install the official Grok client to sign in."])
            }
            let child = Process()
            child.executableURL = URL(fileURLWithPath: path)
            child.arguments = ["login", "--oauth"]
            var environment = ProcessInfo.processInfo.environment
            for key in ["XAI_API_KEY", "GROK_DEPLOYMENT_KEY", "GROK_AUTH_PATH", "GROK_HOME"] { environment.removeValue(forKey: key) }
            child.environment = environment
            child.standardInput = FileHandle.nullDevice
            child.standardOutput = FileHandle.nullDevice
            child.standardError = FileHandle.nullDevice
            try child.run()
            child.waitUntilExit()
            guard child.terminationStatus == 0 else {
                throw NSError(domain: "ModelHarbor", code: 2, userInfo: [NSLocalizedDescriptionKey: "Grok sign-in did not finish. Try signing in again."])
            }
        }.value
    }
}

struct GatewayIdentity: Codable, Equatable, Sendable {
    let protocolVersion: Int
    let runtimeID: String
    let bootID: String
    let mode: String

    enum CodingKeys: String, CodingKey {
        case protocolVersion = "protocol_version", runtimeID = "runtime_id", bootID = "boot_id", mode
    }
    var isIndependent: Bool {
        protocolVersion == 1 && mode == "independent" && Self.isDigest(runtimeID) && UUID(uuidString: bootID) != nil
    }
    static func isDigest(_ value: String) -> Bool {
        value.count == 64 && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }
    var object: [String: Any] {
        ["protocol_version": protocolVersion, "runtime_id": runtimeID, "boot_id": bootID, "mode": mode]
    }
}

private struct GatewayRestoreContext: Decodable {
    let runtime: GatewayIdentity
    let configurationRevision: String
    let routing: String
    enum CodingKeys: String, CodingKey { case runtime, configurationRevision = "configuration_revision", routing }
}

private struct GatewayRestoreReceipt: Decodable {
    struct Route: Decodable { let model: String; let verified: Bool }
    let runtime: GatewayIdentity
    let configurationRevision: String
    let restored: [String]
    let alreadyPresent: [String]
    let routes: [Route]
    enum CodingKeys: String, CodingKey {
        case runtime, configurationRevision = "configuration_revision", restored, alreadyPresent = "already_present", routes
    }
}

struct RestoredGatewayRoute {
    let model: String
    let runtime: GatewayIdentity
    let configurationRevision: String
    let restored: Bool
}

final class GatewayControlProcess: @unchecked Sendable {
    struct Result: Sendable { let output: Data; let status: Int32 }
    private let process = Process()
    private let inputPipe = Pipe(), outputPipe = Pipe()
    private let lock = NSLock()
    private var stopped: String?
    private var finished = false

    private init(executable: URL, arguments: [String], environment: [String: String]) {
        process.executableURL = executable
        process.arguments = arguments
        process.environment = environment
        process.standardInput = inputPipe
        process.standardOutput = outputPipe
        process.standardError = FileHandle.nullDevice
    }

    static func run(executable: URL, arguments: [String], environment: [String: String],
                    input: Data, timeout: TimeInterval) async throws -> Result {
        let command = GatewayControlProcess(executable: executable, arguments: arguments, environment: environment)
        return try await withTaskCancellationHandler(operation: {
            try Task.checkCancellation()
            return try await Task.detached { try command.execute(input: input, timeout: timeout) }.value
        }, onCancel: { command.stop("The gateway control operation was cancelled.") })
    }

    private func stop(_ reason: String) {
        lock.lock()
        guard !finished, stopped == nil else { lock.unlock(); return }
        stopped = reason
        let running = process.isRunning
        if running { process.terminate() }
        lock.unlock()
        if running {
            DispatchQueue.global().asyncAfter(deadline: .now() + 0.25) { [self] in
                lock.lock()
                if !finished && process.isRunning { Darwin.kill(process.processIdentifier, SIGKILL) }
                lock.unlock()
            }
        }
    }

    private func execute(input: Data, timeout: TimeInterval) throws -> Result {
        let deadline = DispatchWorkItem { [weak self] in self?.stop("The gateway control operation timed out.") }
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: deadline)
        defer {
            deadline.cancel()
            lock.lock(); finished = true; lock.unlock()
            try? inputPipe.fileHandleForWriting.close()
            try? outputPipe.fileHandleForReading.close()
        }
        lock.lock()
        if let stopped { lock.unlock(); throw ProviderError.message(stopped) }
        do { try process.run() } catch { lock.unlock(); throw error }
        lock.unlock()
        do {
            try inputPipe.fileHandleForWriting.write(contentsOf: input)
            try inputPipe.fileHandleForWriting.close()
            var output = Data()
            while true {
                let chunk = outputPipe.fileHandleForReading.availableData
                if chunk.isEmpty { break }
                guard output.count + chunk.count <= 4 * 1024 * 1024 else {
                    stop("The gateway control response exceeded its limit.")
                    break
                }
                output.append(chunk)
            }
            process.waitUntilExit()
            lock.lock(); let reason = stopped; lock.unlock()
            if let reason { throw ProviderError.message(reason) }
            return Result(output: output, status: process.terminationStatus)
        } catch {
            stop("The gateway control connection failed.")
            process.waitUntilExit()
            throw error
        }
    }
}

enum GatewayStartupMode: Equatable {
    case attached
    case bootstrapped
}

@MainActor
enum GatewayStartupCoordinator {
    static func start(adapter: GrokAdapter, loadDisplay: () -> Void,
                      bootstrap: () async -> Void) async throws -> GatewayStartupMode {
        loadDisplay()
        try await adapter.start()
        for _ in 0..<50 {
            if await adapter.isHealthy() {
                if adapter.preservesExistingGateway { return .attached }
                await bootstrap()
                return .bootstrapped
            }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        throw ProviderError.message("The gateway did not become available. Existing services were preserved.")
    }
}

struct PythonRuntime {
    static func executable() throws -> URL {
        for path in ["/opt/homebrew/bin/python3", "/usr/local/bin/python3"] where FileManager.default.isExecutableFile(atPath: path) {
            return URL(fileURLWithPath: path)
        }
        throw NSError(domain: "Switcher", code: 2, userInfo: [NSLocalizedDescriptionKey: "Python 3.11 or newer is required. Install Python with Homebrew, then reopen this app."])
    }

    static func run(_ code: String, input: [String: Any]) throws -> Data {
        let child = Process()
        child.executableURL = try executable()
        child.arguments = ["-c", code]
        let stdin = Pipe(), stdout = Pipe(), stderr = Pipe()
        child.standardInput = stdin
        child.standardOutput = stdout
        child.standardError = stderr
        try child.run()
        stdin.fileHandleForWriting.write(try JSONSerialization.data(withJSONObject: input))
        try stdin.fileHandleForWriting.close()
        let output = stdout.fileHandleForReading.readDataToEndOfFile()
        let errors = stderr.fileHandleForReading.readDataToEndOfFile()
        child.waitUntilExit()
        guard child.terminationStatus == 0 else {
            throw NSError(domain: "Switcher", code: 3, userInfo: [NSLocalizedDescriptionKey:
                String(data: errors, encoding: .utf8) ?? "Configuration validation failed."])
        }
        return output
    }
}
