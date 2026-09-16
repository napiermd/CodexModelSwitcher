import Foundation

@MainActor
final class GrokAdapter {
    private var process: Process?

    func start() throws {
        if process?.isRunning == true { return }
        guard let script = Bundle.main.url(forResource: "grok_adapter", withExtension: "py") else {
            throw NSError(domain: "Switcher", code: 1, userInfo: [NSLocalizedDescriptionKey: "The Grok adapter is missing from the app bundle."])
        }
        let child = Process()
        child.executableURL = try PythonRuntime.executable()
        child.arguments = ["-u", script.path]
        var environment = ProcessInfo.processInfo.environment
        environment["MODEL_HARBOR_TOKEN_PATH"] = AppPaths.codexDirectory.appendingPathComponent("model-harbor-bridge-token").path
        environment["MODEL_HARBOR_CONFIG_DIR"] = AppPaths.codexDirectory.path
        child.environment = environment
        child.standardOutput = FileHandle.nullDevice
        child.standardError = FileHandle.nullDevice
        try child.run()
        process = child
    }

    func isHealthy() async -> Bool {
        guard process?.isRunning == true else { return false }
        do {
            let request = URLRequest(url: URL(string: "http://127.0.0.1:48118/health")!, timeoutInterval: 1)
            let (bytes, _) = try await URLSession.shared.data(for: request)
            let json = try JSONSerialization.jsonObject(with: bytes) as? [String: Any]
            return json?["adapter"] as? String == "codex-model-switcher-grok"
        } catch { return false }
    }

    func stop() {
        process?.terminate()
        process = nil
    }

    func connectionStatus() async throws -> Data {
        let token = try String(contentsOf: AppPaths.codexDirectory.appendingPathComponent("model-harbor-bridge-token"), encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
        var request = URLRequest(url: URL(string: "http://127.0.0.1:48118/harbor/status")!, timeoutInterval: 2)
        request.setValue(token, forHTTPHeaderField: "X-Model-Harbor-Token")
        let (bytes, _) = try await URLSession.shared.data(for: request)
        return bytes
    }

    func setTaskRepairsEnabled(_ enabled: Bool) async throws {
        let token = try String(contentsOf: AppPaths.codexDirectory.appendingPathComponent("model-harbor-bridge-token"), encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
        let action = enabled ? "enable" : "disable"
        var request = URLRequest(url: URL(string: "http://127.0.0.1:48118/harbor/repairs/\(action)")!, timeoutInterval: 5)
        request.httpMethod = "POST"
        request.httpBody = Data()
        request.setValue(token, forHTTPHeaderField: "X-Model-Harbor-Token")
        let (_, response) = try await URLSession.shared.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else {
            throw NSError(domain: "ModelHarbor", code: 1, userInfo: [NSLocalizedDescriptionKey: "Task repair settings could not be saved. Try again."])
        }
    }

    func accountStatus() async throws -> Data {
        let token = try String(contentsOf: AppPaths.codexDirectory.appendingPathComponent("model-harbor-bridge-token"), encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
        var request = URLRequest(url: URL(string: "http://127.0.0.1:48118/oauth/status")!, timeoutInterval: 60)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let (bytes, response) = try await URLSession.shared.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else {
            throw NSError(domain: "ModelHarbor", code: 401, userInfo: [NSLocalizedDescriptionKey: "Sign in to Grok to load your account’s models."])
        }
        return bytes
    }

    func reconnectBaseten() async throws {
        let token = try String(contentsOf: AppPaths.codexDirectory.appendingPathComponent("model-harbor-bridge-token"), encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
        var request = URLRequest(url: URL(string: "http://127.0.0.1:48118/harbor/baseten/reconnect")!, timeoutInterval: 70)
        request.httpMethod = "POST"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let (bytes, response) = try await URLSession.shared.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else {
            let result = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any]
            throw NSError(domain: "ModelHarbor", code: 401, userInfo: [NSLocalizedDescriptionKey: result?["error"] as? String ?? "Baseten could not reconnect. Try again when 1Password is ready."])
        }
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
