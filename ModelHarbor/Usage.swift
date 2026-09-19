import Foundation
import CryptoKit
import Darwin

struct UsageWindow: Codable, Identifiable {
    var id: String
    var title: String
    var remainingPercent: Double?
    var resetsAt: Date?
}

struct UsageDay: Codable, Identifiable {
    var date: String
    var costUSD: Double?
    var tokens: Int?
    var id: String { date }
    var timestamp: Date? { UsageParser.date(date + "T00:00:00Z") }
}

struct UsageSnapshot: Codable, Identifiable {
    var id: String
    var providerID: String
    var accountLabel: String
    var source: String
    var scope: String
    var windows: [UsageWindow] = []
    var daily: [UsageDay] = []
    var updatedAt: Date?
    var plan: String?
    var note: String?
    var error: String?
    var costKind: String?
    var costToday: Double?
    var cost30Days: Double?
    var costMonth: Double?
    var costAllTime: Double?
    var tokensToday: Int?
    var tokens30Days: Int?
    var balance: Double?
    var isStale: Bool { error != nil || Date().timeIntervalSince(updatedAt ?? .distantPast) > 900 }
    var isEstimate: Bool { costKind == "estimate" }

    func historyRange(days: Int) -> ClosedRange<Date> {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        let end = calendar.startOfDay(for: updatedAt ?? Date()).addingTimeInterval(86400)
        return end.addingTimeInterval(-Double(days) * 86400)...end
    }
    func history(days: Int) -> [UsageDay] {
        let range = historyRange(days: days)
        return daily.filter { row in row.timestamp.map { $0 >= range.lowerBound && $0 < range.upperBound } ?? false }
            .sorted { $0.date < $1.date }
    }

    static var decoder: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let value = try decoder.singleValueContainer()
            if let seconds = try? value.decode(Double.self), seconds.isFinite { return Date(timeIntervalSince1970: seconds) }
            let string = try value.decode(String.self)
            guard let date = UsageParser.date(string) else { throw DecodingError.dataCorruptedError(in: value, debugDescription: "Invalid usage date") }
            return date
        }
        return decoder
    }
}

enum UsageParser {
    static func date(_ value: String?) -> Date? {
        guard let value else { return nil }
        let formatter = ISO8601DateFormatter()
        if let result = formatter.date(from: value) { return result }
        formatter.formatOptions.insert(.withFractionalSeconds)
        return formatter.date(from: value)
    }
    static func number(_ value: Any?) -> Double? {
        guard let value, !(value is NSNull) else { return nil }
        if let n = value as? NSNumber, CFGetTypeID(n) == CFBooleanGetTypeID() { return nil }
        let result = (value as? NSNumber)?.doubleValue ?? (value as? String).flatMap(Double.init)
        guard let result, result.isFinite, result >= 0 else { return nil }
        return result
    }
    static func codex(_ raw: [String: Any], id: String, label: String, now: Date = Date()) throws -> UsageSnapshot {
        var result = UsageSnapshot(id: id, providerID: "codex-subscription", accountLabel: label,
                                   source: "Codex account usage", scope: "This ChatGPT account", updatedAt: now,
                                   plan: raw["plan_type"] as? String,
                                   note: "Subscription limits are reported by Codex. Dollar estimates from local logs are shown separately.")
        func append(_ group: [String: Any], prefix: String, name: String?) {
            for key in ["primary_window", "secondary_window"] {
                guard let row = group[key] as? [String: Any] else { continue }
                let seconds = number(row["limit_window_seconds"])
                let title = name ?? (seconds == 604800 ? "Weekly" : seconds == 18000 ? "Five-hour" : seconds == 86400 ? "Daily" : key == "primary_window" ? "Session" : "Extended window")
                let percent = number(row["used_percent"]).map { max(0, 100 - $0) }
                let reset = number(row["reset_at"]).map(Date.init(timeIntervalSince1970:))
                if percent != nil || reset != nil {
                    result.windows.append(UsageWindow(id: "\(prefix)-\(key)", title: title, remainingPercent: percent, resetsAt: reset))
                }
            }
        }
        if let limits = raw["rate_limit"] as? [String: Any] { append(limits, prefix: "core", name: nil) }
        if let review = raw["code_review_rate_limit"] as? [String: Any] { append(review, prefix: "review", name: "Code review") }
        for (index, group) in (raw["additional_rate_limits"] as? [[String: Any]] ?? []).enumerated() {
            if let limits = group["rate_limit"] as? [String: Any] {
                append(limits, prefix: "additional-\(index)", name: group["limit_name"] as? String ?? group["metered_feature"] as? String ?? "Additional limit")
            }
        }
        if let credits = raw["credits"] as? [String: Any] { result.balance = number(credits["balance"]) }
        guard !result.windows.isEmpty || result.balance != nil else { throw ProviderError.message("Codex did not report limits for this account. Open its usage dashboard.") }
        return result
    }

    static func codexBar(_ data: Data) throws -> [UsageSnapshot] {
        let root = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard let entries = root?["entries"] as? [[String: Any]] else { throw ProviderError.message("Unrecognized CodexBar snapshot") }
        var snapshots: [UsageSnapshot] = []
        for row in entries {
            guard let provider = row["provider"] as? String, ["codex", "claude", "grok"].contains(provider) else { continue }
            let providerID = provider == "codex" ? "codex-subscription" : provider == "grok" ? "grok-oauth" : provider
            if provider == "claude" {
                var quota = UsageSnapshot(id: "claude-codexbar", providerID: providerID, accountLabel: "Account selected in CodexBar",
                                          source: "CodexBar cached quota", scope: "CodexBar account selection · identity not supplied",
                                          updatedAt: date(row["updatedAt"] as? String), note: "Refresh Claude in CodexBar to update this view. Harbor does not read browser cookies or the Claude Keychain.")
                for (index, usage) in (row["usageRows"] as? [[String: Any]] ?? []).enumerated() {
                    let window = usage["window"] as? [String: Any]
                    quota.windows.append(UsageWindow(id: "row-\(index)", title: usage["title"] as? String ?? "Plan usage",
                                                     remainingPercent: number(usage["percentLeft"]).map { min(100, $0) },
                                                     resetsAt: date(window?["resetsAt"] as? String)))
                }
                if !quota.windows.isEmpty { snapshots.append(quota) }
            }
            guard let tokens = row["tokenUsage"] as? [String: Any],
                  number(tokens["last30DaysTokens"]) != nil || number(tokens["last30DaysCostUSD"]) != nil else { continue }
            var history = UsageSnapshot(id: "\(provider)-local-history", providerID: providerID, accountLabel: "Local history · all accounts",
                                        source: "CodexBar local logs", scope: "This Mac · not attributed to an OAuth account",
                                        updatedAt: date(tokens["updatedAt"] as? String),
                                        note: "Estimated token value at API prices. This is not your subscription bill or additional charges.", costKind: "estimate")
            if (tokens["currencyCode"] as? String ?? "USD") == "USD" {
                history.costToday = number(tokens["sessionCostUSD"])
                history.cost30Days = number(tokens["last30DaysCostUSD"])
            }
            history.tokensToday = number(tokens["sessionTokens"]).flatMap { Int(exactly: $0) }
            history.tokens30Days = number(tokens["last30DaysTokens"]).flatMap { Int(exactly: $0) }
            history.daily = (row["dailyUsage"] as? [[String: Any]] ?? []).compactMap { day in
                guard let key = day["dayKey"] as? String else { return nil }
                return UsageDay(date: key, costUSD: number(day["costUSD"]), tokens: number(day["totalTokens"]).flatMap { Int(exactly: $0) })
            }.sorted { $0.date < $1.date }
            snapshots.append(history)
        }
        return snapshots
    }
}

struct CodexUsageAccount {
    let id: String
    let label: String
    let token: String
    let accountID: String?

    static func parse(auth: String, label: String? = nil) -> CodexUsageAccount? {
        guard let data = auth.data(using: .utf8), let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let tokens = object["tokens"] as? [String: Any], let token = tokens["access_token"] as? String,
              token.split(separator: ".").count == 3 else { return nil }
        func claims(_ token: String?) -> [String: Any] {
            guard let parts = token?.split(separator: "."), parts.count == 3 else { return [:] }
            var body = String(parts[1]).replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
            body += String(repeating: "=", count: (4 - body.count % 4) % 4)
            guard let bytes = Data(base64Encoded: body) else { return [:] }
            return (try? JSONSerialization.jsonObject(with: bytes) as? [String: Any]) ?? [:]
        }
        let identity = claims(tokens["id_token"] as? String)
        let access = claims(token)
        let authClaims = access["https://api.openai.com/auth"] as? [String: Any]
        let accountID = tokens["account_id"] as? String ?? authClaims?["chatgpt_account_id"] as? String
        let subject = identity["sub"] as? String ?? access["sub"] as? String ?? ""
        guard let accountID, !accountID.isEmpty, !subject.isEmpty else { return nil }
        let hash = SHA256.hash(data: Data((accountID + ":" + subject).utf8)).map { String(format: "%02x", $0) }.joined()
        return CodexUsageAccount(id: "codex-\(hash.prefix(20))", label: label ?? identity["email"] as? String ?? "Current Codex account", token: token, accountID: accountID)
    }
}

private final class UsageNetworkDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}

enum UsageClient {
    private static let transport = URLSession(configuration: .ephemeral, delegate: UsageNetworkDelegate(), delegateQueue: nil)
    static func codex(_ account: CodexUsageAccount) async throws -> UsageSnapshot {
        var request = URLRequest(url: URL(string: "https://chatgpt.com/backend-api/wham/usage")!, timeoutInterval: 20)
        request.setValue("Bearer \(account.token)", forHTTPHeaderField: "Authorization")
        request.setValue(account.accountID, forHTTPHeaderField: "ChatGPT-Account-Id")
        request.setValue("ModelHarbor/1.0", forHTTPHeaderField: "User-Agent")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (bytes, response) = try await transport.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        if [401, 403].contains(status) { throw ProviderError.message("This saved sign-in needs renewal. Sign in again in Settings → Providers; usage checks never switch your active account.") }
        guard status == 200 else { throw ProviderError.message("Codex usage returned HTTP \(status). Harbor will retry later.") }
        guard let raw = try JSONSerialization.jsonObject(with: bytes) as? [String: Any] else { throw ProviderError.message("Unrecognized Codex usage response.") }
        if let returned = raw["account_id"] as? String, returned != account.accountID {
            throw ProviderError.message("Codex returned usage for a different workspace. Sign in again to verify this account.")
        }
        return try UsageParser.codex(raw, id: account.id, label: account.label)
    }
    static func bridge() async throws -> [UsageSnapshot] {
        let bytes = try await GrokAdapter.ownerControl("GET", path: "/harbor/usage")
        struct Envelope: Decodable { var entries: [UsageSnapshot] }
        return try UsageSnapshot.decoder.decode(Envelope.self, from: bytes).entries
    }
    static var codexBarHistoryLocation: URL {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let groups = home.appendingPathComponent("Library/Group Containers")
        let containers = (try? FileManager.default.contentsOfDirectory(at: groups, includingPropertiesForKeys: nil)) ?? []
        let container = containers.first { $0.lastPathComponent.hasSuffix(".com.steipete.codexbar") }
        return container?.appendingPathComponent("widget-snapshot.json")
            ?? home.appendingPathComponent("Library/Application Support/CodexBar/widget-snapshot.json")
    }
    static func codexBarHistory() -> [UsageSnapshot] {
        guard let bookmark = UserDefaults.standard.data(forKey: "harbor.codexBarHistoryBookmark") else { return [] }
        var stale = false
        guard let url = try? URL(resolvingBookmarkData: bookmark, options: [.withSecurityScope, .withoutUI], bookmarkDataIsStale: &stale) else { return [] }
        let accessing = url.startAccessingSecurityScopedResource()
        defer { if accessing { url.stopAccessingSecurityScopedResource() } }
        var metadata = stat()
        guard lstat(url.path, &metadata) == 0, metadata.st_mode & S_IFMT == S_IFREG,
              metadata.st_size > 0, metadata.st_size < 4_000_000,
              let data = try? Data(contentsOf: url), let snapshots = try? UsageParser.codexBar(data) else { return [] }
        if stale, let renewed = try? url.bookmarkData(options: .withSecurityScope, includingResourceValuesForKeys: nil, relativeTo: nil) {
            UserDefaults.standard.set(renewed, forKey: "harbor.codexBarHistoryBookmark")
        }
        return snapshots
    }
}

/// Files in another app's container can stall on macOS metadata or privacy checks.
/// Keep one bounded background read; never let that block the UI or direct usage.
final class UsageHistoryReader: @unchecked Sendable {
    static let shared = UsageHistoryReader()
    private let lock = NSLock()
    private var reading = false
    private var cached: [UsageSnapshot] = []

    private final class Delivery: @unchecked Sendable {
        private let lock = NSLock()
        private var continuation: CheckedContinuation<[UsageSnapshot], Never>?
        init(_ continuation: CheckedContinuation<[UsageSnapshot], Never>) { self.continuation = continuation }
        func send(_ snapshots: [UsageSnapshot]) {
            lock.lock()
            let pending = continuation
            continuation = nil
            lock.unlock()
            pending?.resume(returning: snapshots)
        }
    }
    func read(timeout: TimeInterval = 2, load: @escaping @Sendable () -> [UsageSnapshot] = { UsageClient.codexBarHistory() }) async -> [UsageSnapshot] {
        await withCheckedContinuation { continuation in
            lock.lock()
            let previous = cached
            if reading { lock.unlock(); continuation.resume(returning: previous); return }
            reading = true
            lock.unlock()
            let delivery = Delivery(continuation)
            DispatchQueue.global(qos: .utility).async {
                let result = load()
                self.lock.lock()
                self.cached = result
                self.reading = false
                self.lock.unlock()
                delivery.send(result)
            }
            DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + timeout) { delivery.send(previous) }
        }
    }
}
