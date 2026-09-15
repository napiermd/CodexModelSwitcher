import Foundation
import Security

/// Only metadata is serialized in model-switcher.json. Secrets live in Keychain.
struct CredentialStore {
    static let service = ProcessInfo.processInfo.environment["MODEL_SWITCHER_KEYCHAIN_SERVICE"] ?? "dev.napier.CodexModelSwitcher"

    static func read(_ account: String) throws -> String? {
        var query = baseQuery(account)
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
            NSLocalizedDescriptionKey: "Cannot access saved credentials in macOS Keychain (\(status)). No credentials were overwritten."
        ])
    }
}

extension AppData {
    mutating func loadCredentials() throws {
        for index in services.indices {
            if let key = try CredentialStore.read("provider:\(services[index].id)") {
                services[index].apiKey = key
            }
        }
        for index in openAIAccounts.indices {
            if let auth = try CredentialStore.read("openai:\(openAIAccounts[index].id)") {
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
        for index in openAIAccounts.indices {
            guard !openAIAccounts[index].authJSON.isEmpty else { throw AppError.openAIAccountLoginFailed }
            try CredentialStore.write(openAIAccounts[index].authJSON, account: "openai:\(openAIAccounts[index].id)")
            metadata.openAIAccounts[index].authJSON = ""
        }
        return try JSONEncoder().encode(metadata)
    }
}
