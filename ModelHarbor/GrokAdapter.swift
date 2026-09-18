import Foundation

@MainActor
final class GrokAdapter {
    private var servicePrepared = false
    private var candidateRuntimeID: String?
    private let serviceRequest: () async throws -> Data
    private let statusRequest: () async throws -> Data
    private(set) var preservesExistingGateway = false
    private(set) var requiresMaintenanceForRuntimeUpdate = false

    init(serviceRequest: (() async throws -> Data)? = nil,
         statusRequest: (() async throws -> Data)? = nil) {
        self.serviceRequest = serviceRequest ?? Self.prepareService
        self.statusRequest = statusRequest ?? { try await Self.ownerControl("GET", path: "/harbor/status") }
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

    static func ownerControl(_ method: String, path: String, body: Data = Data()) async throws -> Data {
        guard let script = Bundle.main.url(forResource: "gateway_control", withExtension: "py") else {
            throw ProviderError.message("The authenticated gateway control helper is missing from the app bundle.")
        }
        let executable = try PythonRuntime.executable()
        let environment = bridgeEnvironment
        let input = try JSONSerialization.data(withJSONObject: [
            "method": method, "path": path, "body_base64": body.base64EncodedString()
        ])
        return try await Task.detached {
            let helper = Process()
            helper.executableURL = executable
            helper.arguments = ["-B", "-u", script.path]
            helper.environment = environment
            let stdin = Pipe(), stdout = Pipe()
            helper.standardInput = stdin
            helper.standardOutput = stdout
            helper.standardError = FileHandle.nullDevice
            try helper.run()
            stdin.fileHandleForWriting.write(input)
            try stdin.fileHandleForWriting.close()
            let bytes = stdout.fileHandleForReading.readDataToEndOfFile()
            helper.waitUntilExit()
            let result = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any]
            guard helper.terminationStatus == 0,
                  result?["ok"] as? Bool == true,
                  let encoded = result?["body_base64"] as? String,
                  let response = Data(base64Encoded: encoded) else {
                throw ProviderError.message(result?["error"] as? String ?? "Harbor server authentication failed. The listener was left untouched.")
            }
            return response
        }.value
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
