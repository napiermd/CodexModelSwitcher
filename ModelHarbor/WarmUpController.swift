import Combine
import Foundation

enum WarmUpMode: String, CaseIterable, Identifiable {
    case off, afterLaunch, daily
    var id: String { rawValue }
    var title: String {
        switch self {
        case .off: return "Off"
        case .afterLaunch: return "After startup"
        case .daily: return "Daily"
        }
    }
}

enum WarmUpError: Error, LocalizedError {
    case signIn, invalidPrompt, http(Int), incomplete, responseTooLarge, timedOut
    var errorDescription: String? {
        switch self {
        case .signIn: return "Sign in to Codex again, then retry warm-up."
        case .invalidPrompt: return "Use a warm-up prompt between 1 and 240 characters."
        case .http(let status): return "Codex returned HTTP \(status). Warm-up did not complete."
        case .incomplete: return "Codex did not complete the warm-up response."
        case .responseTooLarge: return "Warm-up stopped because the response exceeded its size limit."
        case .timedOut: return "Warm-up timed out."
        }
    }
}

struct WarmUpEventParser {
    private var line = Data()
    private var eventData: [String] = []
    private var count = 0
    private(set) var completed = false

    mutating func accept(_ byte: UInt8) throws {
        count += 1
        guard count <= 131_072 else { throw WarmUpError.responseTooLarge }
        if byte != 10 { line.append(byte); return }
        let value = String(decoding: line, as: UTF8.self).trimmingCharacters(in: .newlines)
        line.removeAll(keepingCapacity: true)
        if value.isEmpty {
            try finishEvent()
        } else if value.hasPrefix("data:") {
            eventData.append(String(value.dropFirst(5)).trimmingCharacters(in: .whitespaces))
        }
    }

    mutating func finishEvent() throws {
        defer { eventData.removeAll(keepingCapacity: true) }
        guard !eventData.isEmpty else { return }
        let raw = eventData.joined(separator: "\n")
        guard raw != "[DONE]", let data = raw.data(using: .utf8),
              let event = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        switch event["type"] as? String {
        case "response.completed":
            guard let response = event["response"] as? [String: Any],
                  response["status"] as? String == "completed" else { throw WarmUpError.incomplete }
            completed = true
        case "error", "response.failed", "response.incomplete": throw WarmUpError.incomplete
        default: break
        }
    }
}

private final class WarmUpRedirectGuard: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

struct CodexWarmUpTransport {
    static let model = "gpt-5.6-luna"

    static func request(auth: Data, prompt: String, now: Date = Date()) throws -> URLRequest {
        let text = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard (1...240).contains(text.count) else { throw WarmUpError.invalidPrompt }
        guard let account = CodexUsageAccount.parse(auth: String(decoding: auth, as: UTF8.self)),
              let accountID = account.accountID, !accountID.isEmpty,
              !account.token.hasPrefix("sk-") else { throw WarmUpError.signIn }
        let parts = account.token.split(separator: ".")
        var encoded = String(parts[1]).replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        encoded += String(repeating: "=", count: (4 - encoded.count % 4) % 4)
        guard let bytes = Data(base64Encoded: encoded),
              let claims = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any],
              let expiry = claims["exp"] as? Double, expiry > now.timeIntervalSince1970 + 5 else { throw WarmUpError.signIn }
        var request = URLRequest(url: URL(string: "https://chatgpt.com/backend-api/codex/responses")!, timeoutInterval: 60)
        request.httpMethod = "POST"
        request.setValue("Bearer \(account.token)", forHTTPHeaderField: "Authorization")
        request.setValue(accountID, forHTTPHeaderField: "ChatGPT-Account-ID")
        request.setValue("ModelHarbor/1.0", forHTTPHeaderField: "User-Agent")
        request.setValue("codex_cli_rs", forHTTPHeaderField: "originator")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "model": model, "instructions": "You are Codex.",
            "input": [["type": "message", "role": "user", "content": [["type": "input_text", "text": text]]]],
            "tools": [], "parallel_tool_calls": false,
            "reasoning": ["effort": "low"], "store": false, "stream": true
        ])
        return request
    }

    static func send(prompt: String) async throws {
        let path = AppPaths.codexDirectory.appendingPathComponent("auth.json")
        guard let auth = try? Data(contentsOf: path) else { throw WarmUpError.signIn }
        let request = try request(auth: auth, prompt: prompt)
        try await withThrowingTaskGroup(of: Void.self) { group in
            group.addTask {
                let configuration = URLSessionConfiguration.ephemeral
                configuration.timeoutIntervalForRequest = 60
                configuration.timeoutIntervalForResource = 60
                let session = URLSession(configuration: configuration, delegate: WarmUpRedirectGuard(), delegateQueue: nil)
                defer { session.invalidateAndCancel() }
                let (bytes, response) = try await session.bytes(for: request)
                guard let response = response as? HTTPURLResponse else { throw WarmUpError.incomplete }
                guard response.statusCode == 200 else { throw WarmUpError.http(response.statusCode) }
                var parser = WarmUpEventParser()
                for try await byte in bytes {
                    try Task.checkCancellation()
                    try parser.accept(byte)
                    if parser.completed { return }
                }
                throw WarmUpError.incomplete
            }
            group.addTask {
                try await Task.sleep(nanoseconds: 60_000_000_000)
                throw WarmUpError.timedOut
            }
            defer { group.cancelAll() }
            _ = try await group.next()
        }
    }
}

@MainActor
final class WarmUpController: ObservableObject {
    static let shared = WarmUpController()
    @Published var mode: WarmUpMode { didSet { defaults.set(mode.rawValue, forKey: "harbor.warmUpMode") } }
    @Published var dailyMinute: Int { didSet { defaults.set(min(1439, max(0, dailyMinute)), forKey: "harbor.warmUpDailyMinute") } }
    @Published var prompt: String { didSet { defaults.set(prompt, forKey: "harbor.warmUpPrompt") } }
    @Published private(set) var isRunning = false
    @Published private(set) var statusMessage = "Warm-up is optional and uses Codex subscription quota."
    private let defaults: UserDefaults
    private let now: () -> Date
    private let calendar: Calendar
    private let send: (String) async throws -> Void
    private var isBusy: () -> Bool = { true }
    private var isReady: () -> Bool = { false }
    private var scheduler: Task<Void, Never>?
    private var startedAt: Date?
    private var lastAttempt: Date?
    private static let automaticAttemptKey = "harbor.warmUpLastAutomaticAttempt"

    init(defaults: UserDefaults = .standard, now: @escaping () -> Date = Date.init,
         calendar: Calendar = .autoupdatingCurrent,
         send: @escaping (String) async throws -> Void = CodexWarmUpTransport.send) {
        self.defaults = defaults
        self.now = now
        self.calendar = calendar
        self.send = send
        mode = WarmUpMode(rawValue: defaults.string(forKey: "harbor.warmUpMode") ?? "") ?? .off
        dailyMinute = min(1439, max(0, defaults.object(forKey: "harbor.warmUpDailyMinute") as? Int ?? 540))
        prompt = defaults.string(forKey: "harbor.warmUpPrompt") ?? "Reply with OK."
    }

    func start(isBusy: @escaping () -> Bool, isReady: @escaping () -> Bool) {
        self.isBusy = isBusy
        self.isReady = isReady
        guard scheduler == nil else { return }
        startedAt = now()
        scheduler = Task { [weak self] in
            while !Task.isCancelled {
                await self?.checkSchedule()
                do { try await Task.sleep(nanoseconds: 60_000_000_000) }
                catch { return }
            }
        }
    }

    func stop() {
        scheduler?.cancel()
        scheduler = nil
        isReady = { false }
    }

    func checkSchedule() async {
        let date = now()
        guard mode != .off, let startedAt, !isRunning, isReady(), !isBusy() else { return }
        if let last = defaults.object(forKey: Self.automaticAttemptKey) as? Date,
           calendar.isDate(last, inSameDayAs: date) || last > date { return }
        let due: Bool
        if mode == .afterLaunch {
            due = (0..<600).contains(date.timeIntervalSince(startedAt))
        } else {
            let minute = calendar.component(.hour, from: date) * 60 + calendar.component(.minute, from: date)
            due = (0..<5).contains(minute - min(1439, max(0, dailyMinute)))
        }
        guard due else { return }
        // Persist before dispatch so a crash or failed request cannot create a retry loop.
        defaults.set(date, forKey: Self.automaticAttemptKey)
        await run()
    }

    func warmNow() async { await run() }

    private func run() async {
        guard !isRunning else { return }
        guard isReady() else { statusMessage = "Connect Harbor and sign in to Codex before warming up."; return }
        guard !isBusy() else { statusMessage = "A task is running. Try warm-up when Harbor is idle."; return }
        let date = now()
        guard lastAttempt.map({ date.timeIntervalSince($0) >= 60 }) ?? true else {
            statusMessage = "Wait one minute between warm-up attempts."
            return
        }
        let text = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard (1...240).contains(text.count) else { statusMessage = WarmUpError.invalidPrompt.localizedDescription; return }
        lastAttempt = date
        isRunning = true
        statusMessage = "Warming up the current Codex account…"
        defer { isRunning = false }
        do {
            try await send(text)
            statusMessage = "Codex warm-up completed at \(now().formatted(date: .omitted, time: .shortened))."
        } catch is CancellationError {
            statusMessage = "Warm-up was cancelled."
        } catch let error as WarmUpError {
            statusMessage = error.localizedDescription
        } catch {
            statusMessage = "Warm-up could not reach Codex. Check your connection and sign-in."
        }
    }
}
