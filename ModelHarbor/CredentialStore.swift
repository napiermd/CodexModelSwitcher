import Foundation
import Security
import LocalAuthentication

/// Only metadata is serialized in model-switcher.json. Secrets live in Keychain.
struct CredentialStore {
    static let service = ProcessInfo.processInfo.environment["MODEL_SWITCHER_KEYCHAIN_SERVICE"] ?? "dev.napier.ModelHarbor"
    static var allowAuthenticationUI = false

    static func read(_ account: String, legacy: Bool = false, authenticationUI: Bool? = nil) throws -> String? {
        let context = LAContext()
        context.interactionNotAllowed = !(authenticationUI ?? allowAuthenticationUI)
        return try read(account, legacy: legacy, context: context)
    }

    private static func read(_ account: String, legacy: Bool = false, context: LAContext) throws -> String? {
        var query = baseQuery(account)
        if legacy { query[kSecAttrService as String] = "dev.napier.CodexModelSwitcher" }
        query[kSecUseAuthenticationContext as String] = context
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data,
              let value = String(data: data, encoding: .utf8) else {
            throw failure(status)
        }
        return value
    }

    static func readForRestore(_ account: String) async throws -> String? {
        guard ["provider:azure", "provider:openrouter"].contains(account) else {
            throw ProviderError.message("Only saved Azure and OpenRouter connections can be restored.")
        }
        let context = LAContext()
        context.interactionNotAllowed = false
        return try await CancellableCredentialRead.run(operation: {
            try read(account, context: context)
        }, cancelAuthentication: { context.invalidate() })
    }

    static func write(_ value: String, account: String) throws {
        let query = baseQuery(account)
        let attributes = [kSecValueData as String: Data(value.utf8)]
        let status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            var item = query.merging(attributes) { _, new in new }
            item[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
            let added = SecItemAdd(item as CFDictionary, nil)
            guard added == errSecSuccess else { throw failure(added) }
        } else if status != errSecSuccess {
            throw failure(status)
        }
    }

    static func remove(_ account: String) throws {
        let status = SecItemDelete(baseQuery(account) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw failure(status) }
    }

    private static func baseQuery(_ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    private static func failure(_ status: OSStatus) -> Error {
        NSError(domain: NSOSStatusErrorDomain, code: Int(status), userInfo: [
            NSLocalizedDescriptionKey: "Saved accounts need Keychain access. Click Unlock saved accounts to migrate them to Model Harbor. No credentials were overwritten. (\(status))"
        ])
    }
}


/// A cancelled caller must not remain blocked behind a synchronous system authentication prompt.
final class CancellableCredentialRead: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<String?, Error>?
    private var outcome: Result<String?, Error>?
    private let cancelAuthentication: @Sendable () -> Void

    private init(cancelAuthentication: @escaping @Sendable () -> Void) {
        self.cancelAuthentication = cancelAuthentication
    }

    static func run(operation: @escaping @Sendable () throws -> String?,
                    cancelAuthentication: @escaping @Sendable () -> Void,
                    timeout: TimeInterval = 45) async throws -> String? {
        let read = CancellableCredentialRead(cancelAuthentication: cancelAuthentication)
        return try await withTaskCancellationHandler(operation: {
            try Task.checkCancellation()
            return try await withCheckedThrowingContinuation { continuation in
                read.start(continuation, operation: operation, timeout: timeout)
            }
        }, onCancel: { read.finish(.failure(CancellationError()), cancel: true) })
    }

    private func start(_ continuation: CheckedContinuation<String?, Error>,
                       operation: @escaping @Sendable () throws -> String?, timeout: TimeInterval) {
        lock.lock()
        if let outcome {
            lock.unlock()
            continuation.resume(with: outcome)
            return
        }
        self.continuation = continuation
        lock.unlock()
        let deadline = DispatchWorkItem { [weak self] in
            self?.finish(.failure(ProviderError.message("Saved connection authentication timed out. No credentials were sent.")), cancel: true)
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: deadline)
        DispatchQueue.global().async { [self] in
            defer { deadline.cancel() }
            lock.lock(); let pending = outcome == nil; lock.unlock()
            guard pending else { return }
            finish(Result { try operation() })
        }
    }

    private func finish(_ result: Result<String?, Error>, cancel: Bool = false) {
        lock.lock()
        guard outcome == nil else { lock.unlock(); return }
        outcome = result
        let waiting = continuation
        continuation = nil
        lock.unlock()
        if cancel { cancelAuthentication() }
        waiting?.resume(with: result)
    }
}

extension AppData {
    mutating func loadCredentials() throws {
        for index in services.indices {
            if let key = try CredentialStore.read("provider:\(services[index].id)") {
                services[index].apiKey = key
            }
        }
        let vault = try CredentialStore.read("codex-accounts")
        let saved = try vault.map { try JSONDecoder().decode([String: String].self, from: Data($0.utf8)) }
        for index in openAIAccounts.indices {
            let auth = try saved?[openAIAccounts[index].id] ?? CredentialStore.read("openai:\(openAIAccounts[index].id)", legacy: ProcessInfo.processInfo.environment["MODEL_SWITCHER_KEYCHAIN_SERVICE"] == nil)
            if let auth {
                openAIAccounts[index].authJSON = auth
            } else if openAIAccounts[index].authJSON.isEmpty {
                throw AppError.openAIAccountLoginFailed
            }
        }
    }

    func saveCredentialsAndEncodeMetadata() throws -> Data {
        var metadata = self
        for index in services.indices {
            let id = "provider:\(services[index].id)"
            if services[index].apiKey.isEmpty {
                try CredentialStore.remove(id)
            } else {
                try CredentialStore.write(services[index].apiKey, account: id)
            }
            metadata.services[index].apiKey = ""
        }
        var accounts: [String: String] = [:]
        for index in openAIAccounts.indices {
            guard !openAIAccounts[index].authJSON.isEmpty else { throw AppError.openAIAccountLoginFailed }
            accounts[openAIAccounts[index].id] = openAIAccounts[index].authJSON
            metadata.openAIAccounts[index].authJSON = ""
        }
        try CredentialStore.write(String(decoding: JSONEncoder().encode(accounts), as: UTF8.self), account: "codex-accounts")
        return try JSONEncoder().encode(metadata)
    }
}
