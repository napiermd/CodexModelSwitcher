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
