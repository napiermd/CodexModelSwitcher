import AppKit
import Foundation

/// Runs the real store against an explicitly isolated directory and Keychain service.
@MainActor
struct InstallationVerification {
    static func migrateVault() async {
        defer { NSApplication.shared.terminate(nil) }
        do {
            guard try CredentialStore.read("codex-accounts") == nil else { print("Vault already migrated."); return }
            let metadata = try JSONDecoder().decode(AppData.self, from: Data(contentsOf: AppPaths.appData))
            let source = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".codex-switcher/accounts.json")
            let root = try JSONSerialization.jsonObject(with: Data(contentsOf: source)) as? [String: Any]
            let authManager = OpenAIAuthManager()
            var available: [String] = []
            for entry in root?["accounts"] as? [[String: Any]] ?? [] {
                guard var tokens = entry["auth_data"] as? [String: Any], tokens.removeValue(forKey: "type") as? String == "chat_g_p_t" else { continue }
                available.append(String(decoding: try JSONSerialization.data(withJSONObject: ["auth_mode": "chatgpt", "tokens": tokens]), as: UTF8.self))
            }
            // Prefer the active credential, which Codex may have refreshed since import.
            if let active = try? String(contentsOf: AppPaths.codexDirectory.appendingPathComponent("auth.json"), encoding: .utf8) { available.insert(active, at: 0) }
            var vault: [String: String] = [:]
            for account in metadata.openAIAccounts {
                guard let auth = available.first(where: { authManager.extractAccountID(from: $0) == account.accountID && authManager.extractEmail(from: $0) == account.email }),
                      let object = try JSONSerialization.jsonObject(with: Data(auth.utf8)) as? [String: Any],
                      let tokens = object["tokens"] as? [String: Any], let token = tokens["access_token"] as? String else { throw AppError.openAIAccountLoginFailed }
                var request = URLRequest(url: URL(string: "https://chatgpt.com/backend-api/wham/usage")!, timeoutInterval: 30)
                request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
                request.setValue(account.accountID, forHTTPHeaderField: "ChatGPT-Account-ID")
                let (_, response) = try await URLSession.shared.data(for: request)
                guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw AppError.openAIAccountLoginFailed }
                vault[account.id] = auth
            }
            guard !vault.isEmpty else { throw AppError.openAIAccountLoginFailed }
            try CredentialStore.write(String(decoding: JSONEncoder().encode(vault), as: UTF8.self), account: "codex-accounts")
            print("Migrated \(vault.count) authenticated accounts into the signed Model Harbor vault.")
        } catch { print("Migration did not finish: \(error.localizedDescription)") }
    }

    static func run() async {
        guard ProcessInfo.processInfo.arguments.contains("--verify-accounts"),
              let directory = ProcessInfo.processInfo.environment["MODEL_SWITCHER_CONFIG_DIR"],
              ProcessInfo.processInfo.environment["MODEL_SWITCHER_KEYCHAIN_SERVICE"]?.hasPrefix("dev.napier.switcher.verification.") == true,
              URL(fileURLWithPath: directory).standardizedFileURL != FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".codex").standardizedFileURL else { return }
        let reportURL = URL(fileURLWithPath: directory).appendingPathComponent("verification.json")
        var report: [String: Any] = [:]
        let store = AppStore(startAdapter: false)
        defer {
            for account in store.data.openAIAccounts { try? CredentialStore.remove("openai:\(account.id)") }
            try? CredentialStore.remove("verification-probe")
            try? CredentialStore.remove("codex-accounts")
            try? FileManager.default.removeItem(at: AppPaths.codexDirectory.appendingPathComponent("auth.json"))
            if let encoded = try? JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]) {
                try? encoded.write(to: reportURL, options: .atomic)
            }
            NSApplication.shared.terminate(nil)
        }
        do {
            try CredentialStore.write("first", account: "verification-probe")
            guard try CredentialStore.read("verification-probe") == "first" else { throw AppError.openAIAccountLoginFailed }
            try CredentialStore.write("second", account: "verification-probe")
            guard try CredentialStore.read("verification-probe") == "second" else { throw AppError.openAIAccountLoginFailed }
            try CredentialStore.remove("verification-probe")
            guard try CredentialStore.read("verification-probe") == nil else { throw AppError.openAIAccountLoginFailed }
            report["keychain_create_read_update_delete"] = true
            store.importExistingAccounts()
            guard store.errorMessage.isEmpty else { throw NSError(domain: "Verification", code: 1, userInfo: [NSLocalizedDescriptionKey: store.errorMessage]) }
            let accounts = store.data.openAIAccounts
            guard !accounts.isEmpty else { throw AppError.openAIAccountLoginFailed }
            report["imported_accounts"] = accounts.count
            var checks: [[String: Any]] = []
            for (index, account) in accounts.enumerated() {
                store.selectOpenAIAccount(account.id)
                guard store.errorMessage.isEmpty else { throw NSError(domain: "Verification", code: 2, userInfo: [NSLocalizedDescriptionKey: store.errorMessage]) }
                let authURL = AppPaths.codexDirectory.appendingPathComponent("auth.json")
                let auth = try Data(contentsOf: authURL)
                guard let object = try JSONSerialization.jsonObject(with: auth) as? [String: Any],
                      let tokens = object["tokens"] as? [String: Any],
                      let token = tokens["access_token"] as? String,
                      tokens["account_id"] as? String == account.accountID else { throw AppError.openAIAccountLoginFailed }
                let permission = try FileManager.default.attributesOfItem(atPath: authURL.path)[.posixPermissions] as? NSNumber
                let metadata = try String(contentsOf: AppPaths.appData, encoding: .utf8)
                guard !metadata.contains(token), permission?.intValue == 0o600 else { throw AppError.openAIAccountLoginFailed }
                var request = URLRequest(url: URL(string: "https://chatgpt.com/backend-api/wham/usage")!, timeoutInterval: 30)
                request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
                request.setValue(account.accountID, forHTTPHeaderField: "ChatGPT-Account-ID")
                let (_, response) = try await URLSession.shared.data(for: request)
                let status = (response as? HTTPURLResponse)?.statusCode ?? 0
                checks.append(["account": index + 1, "credential_match": true, "auth_mode_0600": true, "metadata_excludes_token": true, "authenticated_http_status": status])
                guard status == 200 else { report["account_checks"] = checks; throw AppError.openAIAccountLoginFailed }
            }
            report["account_checks"] = checks
            report["passed"] = true
        } catch {
            report["passed"] = false
            report["error"] = error.localizedDescription
        }
    }
}
