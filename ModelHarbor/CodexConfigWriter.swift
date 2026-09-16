import Foundation

struct CodexConfigWriter {
    var bridgeToken: String? = nil
    func restoreOpenAIAuth(from data: AppData) throws {
        guard let id = data.selectedOpenAIAccountID,
              let account = data.openAIAccounts.first(where: { $0.id == id }) else { return }
        guard let bytes = account.authJSON.data(using: .utf8),
              let object = try JSONSerialization.jsonObject(with: bytes) as? [String: Any],
              let tokens = object["tokens"] as? [String: Any],
              let token = tokens["access_token"] as? String, !token.isEmpty else { throw AppError.openAIAccountLoginFailed }
        try privateWrite(bytes, to: AppPaths.codexDirectory.appendingPathComponent("auth.json"))
    }

    func updateCatalog(in data: AppData) throws {
        try FileManager.default.createDirectory(at: LiveRouting.catalogURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        let catalog = try LiveRouting.catalog(in: data)
        if (try? Data(contentsOf: LiveRouting.catalogURL)) != catalog {
            try privateWrite(catalog, to: LiveRouting.catalogURL)
        }
    }

    func applySelection(_ selected: SelectedModel, in data: AppData) throws {
        guard let service = data.services.first(where: { $0.id == selected.serviceID }) else { throw AppError.missingService }
        guard service.models.contains(where: { $0.id == selected.modelID }) else { throw AppError.missingModel }
        let lock = try ConfigLock(directory: AppPaths.codexDirectory)
        defer { withExtendedLifetime(lock) {} }
        let current = try String(contentsOf: AppPaths.codexConfig, encoding: .utf8)
        let authURL = AppPaths.codexDirectory.appendingPathComponent("auth.json")
        let previousAuth = try? Data(contentsOf: authURL)
        if LiveRouting.supports(service.id) {
            try updateCatalog(in: data)
        }
        let updated = try rewriteConfig(current, selected: selected, data: data)
        if LiveRouting.supports(service.id), updated == current { return }
        // Commit credentials before writing a config that references them.
        _ = try data.saveCredentialsAndEncodeMetadata()
        let backup = AppPaths.codexDirectory.appendingPathComponent("config.toml.backup-\(UUID().uuidString)")
        try privateWrite(Data(current.utf8), to: backup)
        guard try String(contentsOf: AppPaths.codexConfig, encoding: .utf8) == current,
              (try? Data(contentsOf: authURL)) == previousAuth else {
            throw NSError(domain: "Switcher", code: 5, userInfo: [NSLocalizedDescriptionKey: "Codex configuration or credentials changed during the switch. Try again."])
        }
        do {
            if selected.serviceID == "openai" { try restoreOpenAIAuth(from: data) }
            try privateWrite(Data(updated.utf8), to: AppPaths.codexConfig)
        } catch {
            if selected.serviceID == "openai", let previousAuth { try? privateWrite(previousAuth, to: authURL) }
            throw error
        }
    }

    func rewriteConfig(_ content: String, selected: SelectedModel, data: AppData) throws -> String {
        guard let service = data.services.first(where: { $0.id == selected.serviceID }) else { throw AppError.missingService }
        if LiveRouting.supports(service.id) {
            var routed = data
            routed.services.removeAll { $0.id == LiveRouting.providerID }
            routed.services.append(LiveRouting.service(in: data))
            return try rewriteConfig(content, selected: SelectedModel(serviceID: LiveRouting.providerID, modelID: LiveRouting.modelID(for: selected)), data: routed)
        }
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
        if service.id != "openai" { prefix.append("model_provider = \"\(service.id == "xai" ? "xai-switcher" : service.id)\"") }
        if let catalog = service.catalogPath {
            guard service.id == LiveRouting.providerID || FileManager.default.fileExists(atPath: catalog) else { throw AppError.missingModel }
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
                    let removalStart = start > 0 && lines[start - 1].isEmpty ? start - 1 : start
                    lines.removeSubrange(removalStart..<end)
                }
                lines.append(contentsOf: ["", marker, header,
                    "name = \"\(tomlEscape(service.name))\"",
                    "base_url = \"\(tomlEscape(service.baseURL))\"", "wire_api = \"responses\""])
                if service.useCompatibilityProxy {
                    throw NSError(domain: "CodexModelSwitcher", code: 2, userInfo: [NSLocalizedDescriptionKey:
                        "The upstream compatibility proxy is disabled pending tool and authentication verification."])
                }
                if service.id == LiveRouting.providerID {
                    let tokenPath = AppPaths.codexDirectory.appendingPathComponent("model-harbor-bridge-token").path
                    let token = try bridgeToken ?? String(contentsOfFile: tokenPath, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !token.isEmpty, !token.contains("\n"), !token.contains("\r") else { throw AppError.openAIAccountLoginFailed }
                    lines.append(contentsOf: ["supports_websockets = false", "requires_openai_auth = true",
                        "stream_idle_timeout_ms = 900000",
                        "[model_providers.model-harbor.http_headers]",
                        "X-Model-Harbor-Token = \"\(tomlEscape(token))\""])
                } else if !service.apiKey.isEmpty {
                    lines.append(contentsOf: ["[model_providers.\(service.id).auth]", "command = \"/usr/bin/security\"",
                        "args = [\"find-generic-password\", \"-s\", \"\(CredentialStore.service)\", \"-a\", \"provider:\(service.id)\", \"-w\"]"])
                } else if !service.envKey.isEmpty {
                    guard service.envKey.range(of: "^[A-Za-z_][A-Za-z0-9_]*$", options: .regularExpression) != nil else { throw AppError.invalidServiceID }
                    lines.append("env_key = \"\(service.envKey)\"")
                }
            }
        }
        let output = prefix.joined(separator: "\n") + "\n" + lines.joined(separator: "\n")
        return try ConfigValidation.prepare(original: content, updated: output, provider: service.id,
            linked: service.usesExistingProvider == true || service.id == "openai", grok: service.id == "xai")
    }

    func writeReasoningEffort(_ effort: ReasoningEffort) throws {
        let lock = try ConfigLock(directory: AppPaths.codexDirectory)
        defer { withExtendedLifetime(lock) {} }
        let current = try String(contentsOf: AppPaths.codexConfig, encoding: .utf8)
        let updated = replacingTopLevel("model_reasoning_effort", in: current.components(separatedBy: .newlines), with: "\"\(effort.rawValue)\"").joined(separator: "\n")
        try privateWrite(Data(current.utf8), to: AppPaths.codexDirectory.appendingPathComponent("config.toml.backup-\(UUID().uuidString)"))
        let validated = try ConfigValidation.prepare(original: current, updated: updated, provider: "", linked: true, grok: false)
        guard try String(contentsOf: AppPaths.codexConfig, encoding: .utf8) == current else { throw CocoaError(.fileWriteUnknown) }
        try privateWrite(Data(validated.utf8), to: AppPaths.codexConfig)
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
