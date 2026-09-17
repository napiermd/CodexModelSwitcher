import XCTest
@testable import HarborCore

final class WarmUpTests: XCTestCase {
    private func defaults() -> UserDefaults {
        UserDefaults(suiteName: "dev.napier.harbor.warmup-test.\(UUID().uuidString)")!
    }

    @MainActor
    func testAutomaticWarmUpIsOptInAndSkipsBusyPeriods() async {
        var requests = 0
        var busy = true
        let controller = WarmUpController(defaults: defaults(), send: { _ in requests += 1 })
        controller.start(isBusy: { busy }, isReady: { true })
        await controller.checkSchedule()
        XCTAssertEqual(requests, 0)
        controller.mode = .afterLaunch
        await controller.checkSchedule()
        XCTAssertEqual(requests, 0)
        busy = false
        await controller.checkSchedule()
        XCTAssertEqual(requests, 1)
        controller.stop()
    }

    @MainActor
    func testFailedAutomaticAttemptIsNotRepeatedAcrossRelaunch() async {
        let prefs = defaults()
        var attempts = 0
        let first = WarmUpController(defaults: prefs, send: { _ in
            attempts += 1
            throw WarmUpError.http(429)
        })
        first.mode = .afterLaunch
        first.start(isBusy: { false }, isReady: { true })
        await first.checkSchedule()
        await first.checkSchedule()
        first.stop()
        let relaunched = WarmUpController(defaults: prefs, send: { _ in attempts += 1 })
        relaunched.start(isBusy: { false }, isReady: { true })
        await relaunched.checkSchedule()
        relaunched.stop()
        XCTAssertEqual(attempts, 1)
    }

    @MainActor
    func testDailyWarmUpDoesNotReplayMissedTimeAndAllowsNextDay() async {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        var date = calendar.date(from: DateComponents(year: 2026, month: 9, day: 16, hour: 9, minute: 10))!
        var requests = 0
        let controller = WarmUpController(defaults: defaults(), now: { date }, calendar: calendar, send: { _ in requests += 1 })
        controller.mode = .daily
        controller.dailyMinute = 540
        controller.start(isBusy: { false }, isReady: { true })
        await controller.checkSchedule()
        XCTAssertEqual(requests, 0)
        date = calendar.date(from: DateComponents(year: 2026, month: 9, day: 17, hour: 9, minute: 1))!
        await controller.checkSchedule()
        await controller.checkSchedule()
        XCTAssertEqual(requests, 1)
        date = calendar.date(byAdding: .day, value: 1, to: date)!
        await controller.checkSchedule()
        XCTAssertEqual(requests, 2)
        controller.stop()
    }

    @MainActor
    func testManualWarmUpWorksWithAutoOffButCannotBurst() async {
        var date = Date()
        var requests = 0
        let controller = WarmUpController(defaults: defaults(), now: { date }, send: { _ in requests += 1 })
        controller.start(isBusy: { false }, isReady: { true })
        await controller.warmNow()
        await controller.warmNow()
        XCTAssertEqual(requests, 1)
        date = date.addingTimeInterval(60)
        await controller.warmNow()
        XCTAssertEqual(requests, 2)
        controller.stop()
        await controller.warmNow()
        XCTAssertEqual(requests, 2)
    }

    @MainActor
    func testAutomaticRequestDoesNotOverlapManualRequest() async {
        var finish: CheckedContinuation<Void, Never>?
        var requests = 0
        let controller = WarmUpController(defaults: defaults(), send: { _ in
            requests += 1
            await withCheckedContinuation { finish = $0 }
        })
        controller.start(isBusy: { false }, isReady: { true })
        let first = Task { await controller.warmNow() }
        while !controller.isRunning { await Task.yield() }
        controller.mode = .afterLaunch
        await controller.checkSchedule()
        await controller.warmNow()
        XCTAssertEqual(requests, 1)
        finish?.resume()
        await first.value
        controller.stop()
    }

    func testRequestUsesOnlyCurrentSubscriptionWithMinimalPayload() throws {
        let expiry = Date().addingTimeInterval(3600).timeIntervalSince1970
        let claims = try JSONSerialization.data(withJSONObject: ["sub": "synthetic-user", "exp": expiry]).base64EncodedString()
        let auth = try JSONSerialization.data(withJSONObject: ["tokens": ["account_id": "synthetic-account", "access_token": "header.\(claims).signature"]])
        let request = try CodexWarmUpTransport.request(auth: auth, prompt: " Reply with OK. ")
        XCTAssertEqual(request.url?.absoluteString, "https://chatgpt.com/backend-api/codex/responses")
        XCTAssertEqual(request.value(forHTTPHeaderField: "ChatGPT-Account-ID"), "synthetic-account")
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: XCTUnwrap(request.httpBody)) as? [String: Any])
        XCTAssertEqual(body["model"] as? String, "gpt-5.6-luna")
        XCTAssertEqual((body["reasoning"] as? [String: String])?["effort"], "low")
        XCTAssertEqual((body["tools"] as? [Any])?.count, 0)
        XCTAssertEqual(body["store"] as? Bool, false)
        XCTAssertNil(body["max_output_tokens"])
        XCTAssertThrowsError(try CodexWarmUpTransport.request(auth: auth, prompt: String(repeating: "a", count: 241)))
        XCTAssertThrowsError(try CodexWarmUpTransport.request(auth: Data(#"{"OPENAI_API_KEY":"sk-synthetic"}"#.utf8), prompt: "OK"))
        XCTAssertThrowsError(try CodexWarmUpTransport.request(auth: auth, prompt: "OK", now: Date(timeIntervalSince1970: expiry + 1)))
    }

    func testMalformedCredentialsReturnSignInErrorWithoutCrashing() throws {
        for token in ["", "abc", "header.payload", "header.invalid.signature"] {
            let auth = try JSONSerialization.data(withJSONObject: [
                "tokens": ["account_id": "synthetic-account", "access_token": token]
            ])
            XCTAssertThrowsError(try CodexWarmUpTransport.request(auth: auth, prompt: "OK")) { error in
                guard case WarmUpError.signIn = error else {
                    return XCTFail("Expected a sign-in error for invalid credentials")
                }
            }
        }
    }

    func testStreamNeedsCompletedEventAndRejectsFailures() throws {
        var parser = WarmUpEventParser()
        for byte in "data: {\"type\":\"response.output_text.delta\",\"delta\":\"OK\"}\n\n".utf8 { try parser.accept(byte) }
        XCTAssertFalse(parser.completed)
        for byte in "data: {\"type\":\"response.completed\",\"response\":{\"status\":\"completed\"}}\r\n\r\n".utf8 { try parser.accept(byte) }
        XCTAssertTrue(parser.completed)
        var truncated = WarmUpEventParser()
        for byte in "data: {\"type\":\"response.completed\",\"response\":{\"status\":\"completed\"}}\n".utf8 { try truncated.accept(byte) }
        XCTAssertFalse(truncated.completed, "An unterminated SSE event must not count as completed")
        var failed = WarmUpEventParser()
        XCTAssertThrowsError(try "data: {\"type\":\"response.failed\"}\n\n".utf8.forEach { try failed.accept($0) })
        var oversized = WarmUpEventParser()
        XCTAssertThrowsError(try Data(repeating: 65, count: 131_073).forEach { try oversized.accept($0) })
    }
}
