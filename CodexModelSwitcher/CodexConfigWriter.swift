import Foundation

struct CodexConfigWriter {
    func restoreOpenAIAuth(from data: AppData) throws {
        guard let id = data.selectedOpenAIAccountID,
              let account = data.openAIAccounts.first(where: { $0.id == id }) else { return }
        guard let bytes = account.authJSON.data(using: .utf8),
              let object = try JSONSerialization.jsonObject(with: bytes) as? [String: Any],
              object["tokens"] is [String: Any] else { throw AppError.openAIAccountLoginFailed }
        try privateWrite(bytes, to: AppPaths.codexDirectory.appendingPathComponent("auth.json"))
    }

    func applySelection(_ selected: SelectedModel, in data: AppData) throws {
        guard let service = data.services.first(where: { $0.id == selected.serviceID }) else { throw AppError.missingService }
        guard service.models.contains(where: { $0.id == selected.modelID }) else { throw AppError.missingModel }
        let current = try String(contentsOf: AppPaths.codexConfig, encoding: .utf8)
        let updated = try rewriteConfig(current, selected: selected, data: data)
        // Commit credentials before writing a config that references them.
        _ = try data.saveCredentialsAndEncodeMetadata()
        let backup = AppPaths.codexDirectory.appendingPathComponent("config.toml.backup-\(UUID().uuidString)")
        try privateWrite(Data(current.utf8), to: backup)
        if selected.serviceID == "openai" { try restoreOpenAIAuth(from: data) }
        try privateWrite(Data(updated.utf8), to: AppPaths.codexConfig)
    }

    func rewriteConfig(_ content: String, selected: SelectedModel, data: AppData) throws -> String {
        guard let service = data.services.first(where: { $0.id == selected.serviceID }) else { throw AppError.missingService }
        guard isValidServiceID(service.id), !selected.modelID.contains("\n") else { throw AppError.invalidServiceID }
        var lines = content.components(separatedBy: .newlines)
        let keys: Set<String> = ["model", "model_provider", "model_catalog_json"]
        var topLevel = true
        lines = lines.filter { line in
            let text = line.trimmingCharacters(in: .whitespaces)
            if text.hasPrefix("[") { topLevel = false }
            guard topLevel, let equal = text.firstIndex(of: "=") else { return true }
            return !keys.contains(String(text[..<equal]).trimmingCharacters(in: .whitespaces))
        }
        var prefix: [String] = []
        if selected.modelID != "__native__" { prefix.append("model = \"\(tomlEscape(selected.modelID))\"") }
        if service.id != "openai" { prefix.append("model_provider = \"\(service.id)\"") }
        if let catalog = service.catalogPath {
            guard FileManager.default.fileExists(atPath: catalog) else { throw AppError.missingModel }
            prefix.append("model_catalog_json = \"\(tomlEscape(catalog))\"")
        }
        if service.id == "openai", data.selectedOpenAIAccountID != nil {
            lines = replacingTopLevel("cli_auth_credentials_store", in: lines, with: "\"file\"")
        }
        // Keep the global reasoning preference unless the user explicitly edits it.
        if service.id != "openai" {
            let header = "[model_providers.\(service.id)]"
            let exists = lines.contains { $0.trimmingCharacters(in: .whitespaces) == header }
            if service.usesExistingProvider == true {
                guard exists else { throw AppError.missingService }
            } else {
                // An unowned provider must never be silently regenerated.
                let marker = "# Codex Model Switcher managed provider: \(service.id)"
                if exists && !lines.contains(marker) {
                    throw NSError(domain: "CodexModelSwitcher", code: 1, userInfo: [NSLocalizedDescriptionKey:
                        "Provider \(service.id) already exists in config.toml. Use its linked profile or choose a new provider ID."])
                }
                if let start = lines.firstIndex(of: marker) {
                    var end = start + 1
                    while end < lines.count {
                        let line = lines[end].trimmingCharacters(in: .whitespaces)
                        if line.hasPrefix("[") && line != header && !line.hasPrefix("[model_providers.\(service.id).") { break }
                        end += 1
                    }
                    lines.removeSubrange(start..<end)
                }
                lines.append(contentsOf: ["", marker, header,
                    "name = \"\(tomlEscape(service.name))\"",
                    "base_url = \"\(tomlEscape(service.baseURL))\"", "wire_api = \"responses\""])
                if service.useCompatibilityProxy {
                    throw NSError(domain: "CodexModelSwitcher", code: 2, userInfo: [NSLocalizedDescriptionKey:
                        "The upstream compatibility proxy is disabled pending tool and authentication verification."])
                }
                if !service.apiKey.isEmpty {
                    lines.append(contentsOf: ["[model_providers.\(service.id).auth]", "command = \"/usr/bin/security\"",
                        "args = [\"find-generic-password\", \"-s\", \"\(CredentialStore.service)\", \"-a\", \"provider:\(service.id)\", \"-w\"]"])
                } else if !service.envKey.isEmpty {
                    guard service.envKey.range(of: "^[A-Za-z_][A-Za-z0-9_]*$", options: .regularExpression) != nil else { throw AppError.invalidServiceID }
                    lines.append("env_key = \"\(service.envKey)\"")
                }
            }
        }
        return prefix.joined(separator: "\n") + "\n" + lines.joined(separator: "\n")
    }

    func writeReasoningEffort(_ effort: ReasoningEffort) throws {
        let current = try String(contentsOf: AppPaths.codexConfig, encoding: .utf8)
        let updated = replacingTopLevel("model_reasoning_effort", in: current.components(separatedBy: .newlines), with: "\"\(effort.rawValue)\"").joined(separator: "\n")
        try privateWrite(Data(current.utf8), to: AppPaths.codexDirectory.appendingPathComponent("config.toml.backup-\(UUID().uuidString)"))
        try privateWrite(Data(updated.utf8), to: AppPaths.codexConfig)
    }

    private func replacingTopLevel(_ key: String, in lines: [String], with value: String) -> [String] {
        var top = true
        return ["\(key) = \(value)"] + lines.filter { line in
            let text = line.trimmingCharacters(in: .whitespaces)
            if text.hasPrefix("[") { top = false }
            guard top, let equal = text.firstIndex(of: "=") else { return true }
            return text[..<equal].trimmingCharacters(in: .whitespaces) != key
        }
    }

    private func privateWrite(_ data: Data, to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let temporary = url.deletingLastPathComponent().appendingPathComponent(".switcher-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: temporary) }
        guard FileManager.default.createFile(atPath: temporary.path, contents: data, attributes: [.posixPermissions: 0o600]) else {
            throw CocoaError(.fileWriteUnknown)
        }
        if FileManager.default.fileExists(atPath: url.path) {
            _ = try FileManager.default.replaceItemAt(url, withItemAt: temporary)
        } else { try FileManager.default.moveItem(at: temporary, to: url) }
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }
}
