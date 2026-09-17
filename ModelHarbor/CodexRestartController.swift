import AppKit
import Combine
import Foundation

@MainActor
protocol CodexApplication: AnyObject {
    var bundleURL: URL? { get }
    var isTerminated: Bool { get }
    @discardableResult
    func requestTermination() -> Bool
    @discardableResult
    func forceTerminate() -> Bool
}

@MainActor
private final class RunningCodexApplication: CodexApplication {
    let application: NSRunningApplication

    init(application: NSRunningApplication) {
        self.application = application
    }

    var bundleURL: URL? { application.bundleURL }
    var isTerminated: Bool { application.isTerminated }
    @discardableResult func requestTermination() -> Bool { application.terminate() }
    @discardableResult func forceTerminate() -> Bool { application.forceTerminate() }
}

@MainActor
protocol CodexApplicationOperations {
    func runningApplications(bundleIdentifier: String) -> [any CodexApplication]
    func applicationURL(bundleIdentifier: String) -> URL?
    func openApplication(at url: URL, completionHandler: @escaping @Sendable (Error?) -> Void)
}

@MainActor
final class NSWorkspaceCodexApplicationOperations: CodexApplicationOperations {
    init() {
    }

    func runningApplications(bundleIdentifier: String) -> [any CodexApplication] {
        NSRunningApplication.runningApplications(withBundleIdentifier: bundleIdentifier)
            .map(RunningCodexApplication.init)
    }

    func applicationURL(bundleIdentifier: String) -> URL? {
        NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleIdentifier)
    }

    func openApplication(at url: URL, completionHandler: @escaping @Sendable (Error?) -> Void) {
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = true
        NSWorkspace.shared.openApplication(at: url, configuration: configuration) { _, error in
            completionHandler(error)
        }
    }
}

@MainActor
protocol CodexRestartAlertPresenting: AnyObject {
    @discardableResult
    func runModal(_ alert: NSAlert) -> NSApplication.ModalResponse
}

@MainActor
final class CodexRestartNSAlertPresenter: CodexRestartAlertPresenting {
    @discardableResult
    func runModal(_ alert: NSAlert) -> NSApplication.ModalResponse {
        alert.runModal()
    }
}

@MainActor
final class CodexRestartController: ObservableObject {
    static let shared = CodexRestartController()

    @Published private(set) var isRestarting = false
    @Published private(set) var statusMessage = ""

    let codexBundleIdentifier = "com.openai.codex"
    private let terminationTimeout: TimeInterval

    private let alertPresenter: CodexRestartAlertPresenting
    private let operations: CodexApplicationOperations
    private var preferences: LifecyclePreferences
    private let terminationDelay: UInt64

    init(
        alertPresenter: CodexRestartAlertPresenting? = nil,
        operations: CodexApplicationOperations? = nil,
        preferences: LifecyclePreferences? = nil,
        terminationDelay: UInt64 = 100_000_000,
        terminationTimeout: TimeInterval = 10
    ) {
        self.alertPresenter = alertPresenter ?? CodexRestartNSAlertPresenter()
        self.operations = operations ?? NSWorkspaceCodexApplicationOperations()
        self.preferences = preferences ?? .shared
        self.terminationDelay = terminationDelay
        self.terminationTimeout = terminationTimeout
    }

    func requestRestart() {
        guard !isRestarting else { return }

        let method = preferences.codexCloseMethod
        let shouldReopen = preferences.reopenCodex
        let runningApps = operations.runningApplications(bundleIdentifier: codexBundleIdentifier)
        guard let runningApp = runningApps.first else {
            statusMessage = "Codex is not running."
            return
        }
        guard let bundleURL = runningApp.bundleURL
                ?? operations.applicationURL(bundleIdentifier: codexBundleIdentifier) else {
            statusMessage = "Codex was not found on this Mac."
            return
        }

        guard runningApps.count == 1 else {
            statusMessage = "More than one Codex instance is running. Close the extra instance first."
            return
        }
        isRestarting = true
        let alert = NSAlert()
        alert.messageText = shouldReopen ? "Restart Codex?" : "Close Codex?"
        alert.informativeText = informativeText(for: method, reopening: shouldReopen)
        alert.alertStyle = method == .force ? .warning : .informational
        alert.addButton(withTitle: shouldReopen ? "Restart" : "Close")
        alert.addButton(withTitle: "Cancel")
        let response = alertPresenter.runModal(alert)
        guard response == .alertFirstButtonReturn else {
            isRestarting = false
            statusMessage = ""
            return
        }

        isRestarting = true
        statusMessage = shouldReopen ? "Restarting Codex…" : "Closing Codex…"
        Task { [weak self] in
            await self?.performRestart(
                runningApplication: runningApp,
                bundleURL: bundleURL,
                method: method,
                reopen: shouldReopen
            )
        }
    }

    private func performRestart(
        runningApplication: any CodexApplication,
        bundleURL: URL,
        method: CodexCloseMethod,
        reopen: Bool
    ) async {
        defer {
            isRestarting = false
        }

        let accepted = method == .force
            ? runningApplication.forceTerminate()
            : runningApplication.requestTermination()
        guard accepted || runningApplication.isTerminated else {
            statusMessage = "Codex declined to quit. It was left running."
            return
        }
        let terminated = await Self.waitForTermination(
            runningApplication: runningApplication,
            timeout: terminationTimeout, delay: terminationDelay)

        guard terminated else {
            statusMessage = reopen
                ? "Codex did not quit in time. It was left running and was not force quit."
                : "Codex did not quit in time. It was left running."
            return
        }

        guard reopen else {
            statusMessage = "Codex closed."
            return
        }

        guard operations.runningApplications(bundleIdentifier: codexBundleIdentifier).allSatisfy({ $0.isTerminated }) else {
            statusMessage = "Codex is already open. Harbor did not launch another instance."
            return
        }
        do {
            try await openCodex(at: bundleURL)
            statusMessage = "Codex restarted."
        } catch {
            statusMessage = "Codex could not be reopened. Open it from Applications."
        }
    }

    private func openCodex(at bundleURL: URL) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            operations.openApplication(at: bundleURL) { error in
                if let error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume()
                }
            }
        }
    }

    private func informativeText(for method: CodexCloseMethod, reopening: Bool) -> String {
        let action = reopening ? "restart" : "close"
        switch method {
        case .graceful:
            return "Harbor will ask Codex to quit and wait up to 10 seconds. It will not force quit Codex if the request takes longer."
        case .force:
            return "Force closing Codex can lose unsaved work. Harbor will \(action) it after you confirm."
        }
    }

    private static func waitForTermination(
        runningApplication: any CodexApplication,
        timeout: TimeInterval,
        delay: UInt64
    ) async -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if runningApplication.isTerminated { return true }
            do { try await Task.sleep(nanoseconds: delay) } catch { return false }
        }
        return runningApplication.isTerminated
    }
}
