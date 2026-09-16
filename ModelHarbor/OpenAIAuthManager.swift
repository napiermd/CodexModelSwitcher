import Foundation

struct OpenAIAuthManager {
    func loginAccount(suggestedName: String) async throws -> OpenAIAccount {
        let loginID = UUID().uuidString
        let loginHome = AppPaths.loginDirectory.appendingPathComponent(loginID, isDirectory: true)
        try FileManager.default.createDirectory(at: loginHome, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])

        defer {
            try? FileManager.default.removeItem(at: loginHome)
        }

        try await runCodexLogin(codeHome: loginHome)

        let authURL = loginHome.appendingPathComponent("auth.json")
        let authJSON = try String(contentsOf: authURL, encoding: .utf8)
        let accountID = extractAccountID(from: authJSON)
        let email = extractEmail(from: authJSON)
        let suffix = accountID.map { String($0.suffix(6)) }
        let name = email ?? suffix.map { "\(suggestedName) \($0)" } ?? suggestedName

        return OpenAIAccount(
            id: UUID().uuidString,
            name: name,
            authJSON: authJSON,
            accountID: accountID,
            email: email,
            credentialStatus: .valid,
            credentialMessage: nil,
            createdAt: Date()
        )
    }

    func validateAccount(_ account: OpenAIAccount) async -> OpenAICredentialCheckResult {
        if accessTokenIsUsable(account.authJSON) {
            return OpenAICredentialCheckResult(status: .valid, message: nil, authJSON: nil)
        }

        return OpenAICredentialCheckResult(status: .unchecked,
            message: "Codex will refresh this account when used. Sign in again if Codex requests it.")
    }

    private func accessTokenIsUsable(_ authJSON: String) -> Bool {
        guard let data = authJSON.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let tokens = object["tokens"] as? [String: Any],
              let accessToken = tokens["access_token"] as? String,
              let expirationDate = jwtExpirationDate(accessToken) else {
            return false
        }

        return expirationDate.timeIntervalSinceNow > 300
    }

    private func runCodexLogin(codeHome: URL) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            let process = Process()
            process.executableURL = codexExecutableURL()
            process.arguments = codexArguments()
            process.environment = loginEnvironment(codeHome: codeHome)
            process.terminationHandler = { process in
                if process.terminationStatus == 0 {
                    continuation.resume()
                } else {
                    continuation.resume(throwing: AppError.openAIAccountLoginFailed)
                }
            }

            do {
                try process.run()
            } catch {
                continuation.resume(throwing: error)
            }
        }
    }

    private func codexExecutableURL() -> URL {
        for path in ["/Applications/ChatGPT.app/Contents/Resources/codex",
                     "/Applications/Codex.app/Contents/Resources/codex",
                     "/opt/homebrew/bin/codex", "/usr/local/bin/codex"] {
            if FileManager.default.isExecutableFile(atPath: path) {
                return URL(fileURLWithPath: path)
            }
        }
        return URL(fileURLWithPath: "/usr/bin/env")
    }

    private func codexArguments() -> [String] {
        let loginArguments = [
            "login",
            "-c",
            #"cli_auth_credentials_store="file""#
        ]

        if codexExecutableURL().path == "/usr/bin/env" {
            return ["codex"] + loginArguments
        }
        return loginArguments
    }

    private func loginEnvironment(codeHome: URL) -> [String: String] {
        var environment = ProcessInfo.processInfo.environment
        environment["CODEX_HOME"] = codeHome.path
        return environment
    }

    func extractAccountID(from authJSON: String) -> String? {
        guard let data = authJSON.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let tokens = object["tokens"] as? [String: Any] else {
            return nil
        }
        return tokens["account_id"] as? String
    }

    func extractEmail(from authJSON: String) -> String? {
        guard let data = authJSON.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let tokens = object["tokens"] as? [String: Any],
              let idToken = tokens["id_token"] as? String else {
            return nil
        }

        let parts = idToken.split(separator: ".")
        guard parts.count > 1,
              let payloadData = base64URLDecode(String(parts[1])),
              let payload = try? JSONSerialization.jsonObject(with: payloadData) as? [String: Any] else {
            return nil
        }

        return payload["email"] as? String
    }

    private func jwtExpirationDate(_ token: String) -> Date? {
        let parts = token.split(separator: ".")
        guard parts.count > 1,
              let payloadData = base64URLDecode(String(parts[1])),
              let payload = try? JSONSerialization.jsonObject(with: payloadData) as? [String: Any],
              let exp = payload["exp"] as? TimeInterval else {
            return nil
        }

        return Date(timeIntervalSince1970: exp)
    }

    private func base64URLDecode(_ value: String) -> Data? {
        var base64 = value
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")

        let padding = (4 - base64.count % 4) % 4
        base64 += String(repeating: "=", count: padding)
        return Data(base64Encoded: base64)
    }

    private func base64URLEncode(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    private func iso8601Now() -> String {
        ISO8601DateFormatter().string(from: Date())
    }
}

struct OpenAICredentialCheckResult {
    var status: OpenAIAccountCredentialStatus
    var message: String?
    var authJSON: String?
}
