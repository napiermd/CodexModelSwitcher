import XCTest
@testable import HarborCore

final class UsageTests: XCTestCase {
    func testCodexWindowsRetainRealZeroAndSeparateReview() throws {
        let raw: [String: Any] = ["plan_type": "business", "rate_limit": ["primary_window": ["used_percent": 100, "limit_window_seconds": 604800, "reset_at": 1789805395]], "code_review_rate_limit": ["primary_window": ["used_percent": 12, "limit_window_seconds": 604800, "reset_at": 1789805395]]]
        let snapshot = try UsageParser.codex(raw, id: "account-one", label: "Work")
        XCTAssertEqual(snapshot.windows.map(\.title), ["Weekly", "Code review"])
        XCTAssertEqual(snapshot.windows.map(\.remainingPercent), [0, 88])
        XCTAssertEqual(snapshot.windows.first?.resetsAt, Date(timeIntervalSince1970: 1789805395))
        XCTAssertNil(snapshot.costToday)
        XCTAssertEqual(snapshot.accountLabel, "Work")
    }
    func testMissingQuotaIsNotFullOrEmpty() throws {
        let snapshot = try UsageParser.codex(["rate_limit": ["primary_window": ["reset_at": 1789805395]]], id: "a", label: "A")
        XCTAssertNil(snapshot.windows.first?.remainingPercent)
        XCTAssertThrowsError(try UsageParser.codex([:], id: "a", label: "A"))
        for value: Any in [true, false, "NaN", Double.infinity, -1] { XCTAssertNil(UsageParser.number(value)) }
    }
    func testCodexBarHistoryNeverInheritsAccountQuota() throws {
        let data = Data(#"{"entries":[{"provider":"codex","primary":{"usedPercent":5},"updatedAt":"2026-09-16T12:00:00Z","tokenUsage":{"sessionCostUSD":12,"last30DaysCostUSD":99,"last30DaysTokens":1000,"updatedAt":"2026-09-16T10:00:00Z"},"dailyUsage":[{"dayKey":"2026-09-16","costUSD":12,"totalTokens":50}]},{"provider":"claude","updatedAt":"2026-09-16T12:00:00Z","usageRows":[{"title":"Session","percentLeft":100}]}]}"#.utf8)
        let entries = try UsageParser.codexBar(data)
        let history = try XCTUnwrap(entries.first { $0.id == "codex-local-history" })
        XCTAssertTrue(history.windows.isEmpty)
        XCTAssertTrue(history.isEstimate)
        XCTAssertEqual(history.accountLabel, "Local history · all accounts")
        XCTAssertEqual(history.updatedAt, UsageParser.date("2026-09-16T10:00:00Z"))
        XCTAssertEqual(history.daily.first?.costUSD, 12)
        XCTAssertEqual(entries.first { $0.providerID == "claude" }?.windows.first?.remainingPercent, 100)
    }
    func testBridgeDatesAndUnknownAmountsDecode() throws {
        let bytes = Data(#"{"id":"grok-oauth","providerID":"grok-oauth","accountLabel":"Grok","source":"Grok","scope":"Account","windows":[{"id":"plan","title":"Weekly","resetsAt":"2026-09-17T14:41:10.202382+00:00"}],"daily":[],"updatedAt":1789590000}"#.utf8)
        let result = try UsageSnapshot.decoder.decode(UsageSnapshot.self, from: bytes)
        XCTAssertNotNil(result.windows.first?.resetsAt)
        XCTAssertNil(result.windows.first?.remainingPercent)
        XCTAssertNil(result.costToday)
    }
    func testBlockedHistoryReadDoesNotBlockUsageOrLaunchMoreReaders() async {
        let reader = UsageHistoryReader()
        let entered = DispatchSemaphore(value: 0)
        let release = DispatchSemaphore(value: 0)
        let first = await reader.read(timeout: 0.03) {
            entered.signal()
            release.wait()
            return []
        }
        XCTAssertTrue(first.isEmpty)
        XCTAssertEqual(entered.wait(timeout: .now()), .success)
        let second = await reader.read(timeout: 0.03) {
            XCTFail("A blocked import must not accumulate background workers")
            return []
        }
        XCTAssertTrue(second.isEmpty)
        release.signal()
    }
    func testHistoryUsesCalendarDaysRatherThanLastSevenRecords() {
        var snapshot = UsageSnapshot(id: "history", providerID: "claude", accountLabel: "History", source: "local", scope: "Mac", updatedAt: UsageParser.date("2026-09-16T12:00:00Z"))
        snapshot.daily = ["2026-08-18", "2026-09-09", "2026-09-10", "2026-09-15", "2026-09-16", "2026-09-17", "invalid"].map { UsageDay(date: $0, costUSD: 1) }
        XCTAssertEqual(snapshot.history(days: 7).map(\.date), ["2026-09-10", "2026-09-15", "2026-09-16"])
        XCTAssertEqual(snapshot.history(days: 30).count, 5)
    }

    func testAccountIdentityIncludesWorkspaceAndUserAndNotAccessToken() throws {
        func token(_ subject: String) -> String {
            let data = Data("{\"sub\":\"\(subject)\"}".utf8).base64EncodedString().replacingOccurrences(of: "=", with: "").replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
            return "header.\(data).signature"
        }
        func account(_ workspace: String, _ user: String, _ signature: String = "") -> CodexUsageAccount? {
            CodexUsageAccount.parse(auth: "{\"tokens\":{\"account_id\":\"\(workspace)\",\"access_token\":\"\(token(user))\(signature)\"}}")
        }
        XCTAssertNotEqual(account("work", "one")?.id, account("personal", "one")?.id)
        XCTAssertNotEqual(account("work", "one")?.id, account("work", "two")?.id)
        XCTAssertEqual(account("work", "one")?.id, account("work", "one", "rotated")?.id)
        XCTAssertNil(CodexUsageAccount.parse(auth: "{\"OPENAI_API_KEY\":\"sk-test\"}"))
    }
}
