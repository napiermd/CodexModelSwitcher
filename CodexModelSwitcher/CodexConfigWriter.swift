import Foundation

struct CodexConfigWriter {
    func restoreOpenAIAuth(from data: AppData) throws {
        try FileManager.default.createDirectory(
            at: AppPaths.codexDirectory,
            withIntermediateDirectories: true
        )
        try writeOpenAIAuth(from: data)
    }

    func applySelection(_ selected: SelectedModel, in data: AppData) throws {
        guard let service = data.services.first(where: { $0.id == selected.serviceID }) else {
            throw AppError.missingService
        }
        guard service.models.contains(where: { $0.id == selected.modelID }) else {
            throw AppError.missingModel
        }

        try FileManager.default.createDirectory(
            at: AppPaths.codexDirectory,
            withIntermediateDirectories: true
        )

        let current = (try? String(contentsOf: AppPaths.codexConfig, encoding: .utf8)) ?? ""
        let updated = rewriteConfig(current, selected: selected, data: data)
        try updated.write(to: AppPaths.codexConfig, atomically: true, encoding: .utf8)

        try writeAPIKeys(for: data.services)
        if selected.serviceID == "openai" {
            try writeOpenAIAuth(from: data)
        }
        if service.requiresAPIKey, !service.apiKey.isEmpty {
            setLaunchEnvironment(name: service.envKey, value: service.apiKey)
        }
    }

    func rewriteConfig(
        _ content: String,
        selected: SelectedModel,
        data: AppData
    ) -> String {
        var lines = content.components(separatedBy: .newlines)
        if lines.last == "" {
            lines.removeLast()
        }

        lines = removeTopLevelKey("model", from: lines)
        lines = removeTopLevelKey("model_provider", from: lines)
        lines = removeTopLevelKey("model_reasoning_effort", from: lines)
        lines = removeTopLevelKey("cli_auth_credentials_store", from: lines)
        let managedProviderIDs = Set(data.services.map(\.id)).subtracting(["openai"])
        lines = removeModelProviderTables(from: lines, providerIDs: managedProviderIDs)

        let providerBlock = data.services
            .filter { $0.id != "openai" }
            .map(providerTOML)
            .joined(separator: "\n\n")

        var prefix = [
            #"model = "\#(tomlEscape(selected.modelID))""#
        ]

        if selected.serviceID != "openai" {
            prefix.append(#"model_provider = "\#(tomlEscape(selected.serviceID))""#)
        }

        prefix.append(
            #"model_reasoning_effort = "\#(tomlEscape(data.modelReasoningEffort.rawValue))""#
        )

        if !data.openAIAccounts.isEmpty {
            prefix.append(#"cli_auth_credentials_store = "file""#)
        }

        var output = prefix.joined(separator: "\n")
        let remaining = lines.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)

        if !remaining.isEmpty {
            output += "\n\n" + remaining
        }

        if !providerBlock.isEmpty {
            output += "\n\n" + providerBlock
        }

        return output + "\n"
    }

    private func providerTOML(_ service: CodexService) -> String {
        var lines = [
            "# Codex Model Switcher managed provider",
            "[model_providers.\(service.id)]",
            #"name = "\#(tomlEscape(service.name))""#,
            #"base_url = "\#(tomlEscape(baseURL(for: service)))""#
        ]

        if service.useCompatibilityProxy {
            return lines.joined(separator: "\n")
        } else if service.requiresAPIKey, !service.apiKey.isEmpty {
            lines.append(#"experimental_bearer_token = "\#(tomlEscape(service.apiKey))""#)
        } else if service.requiresAPIKey {
            lines.append(#"env_key = "\#(tomlEscape(service.envKey))""#)
        }

        return lines.joined(separator: "\n")
    }

    private func baseURL(for service: CodexService) -> String {
        guard service.useCompatibilityProxy else {
            return service.baseURL
        }
        return "http://\(CompatibilityProxyServer.address)/\(service.id)/v1"
    }

    private func removeTopLevelKey(_ key: String, from lines: [String]) -> [String] {
        var isTopLevel = true
        return lines.filter { line in
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("[") {
                isTopLevel = false
            }
            return !(isTopLevel && trimmed.hasPrefix("\(key) ="))
        }
    }

    private func removeModelProviderTables(from lines: [String], providerIDs: Set<String>) -> [String] {
        var result: [String] = []
        var isSkipping = false
        var hasManagedMarker = false
        let marker = "# Codex Model Switcher managed provider"

        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            if isSkipping, trimmed.hasPrefix("[") {
                isSkipping = false
            }

            if trimmed == marker {
                hasManagedMarker = true
                continue
            }

            if let providerID = providerID(from: trimmed),
               providerIDs.contains(providerID) || hasManagedMarker {
                isSkipping = true
                hasManagedMarker = false
                continue
            }

            if !isSkipping {
                result.append(line)
            }
            hasManagedMarker = false
        }

        return result
    }

    private func providerID(from tableHeader: String) -> String? {
        guard tableHeader.hasPrefix("[model_providers."),
              tableHeader.hasSuffix("]") else {
            return nil
        }

        return String(tableHeader.dropFirst("[model_providers.".count).dropLast())
            .split(separator: ".", maxSplits: 1)
            .first
            .map(String.init)
    }

    private func writeAPIKeys(for services: [CodexService]) throws {
        let exports = services
            .filter { $0.requiresAPIKey && !$0.apiKey.isEmpty }
            .map { "export \($0.envKey)=\(shellEscape($0.apiKey))" }
            .joined(separator: "\n")

        try (exports + (exports.isEmpty ? "" : "\n"))
            .write(to: AppPaths.envFile, atomically: true, encoding: .utf8)
        try ensureShellProfileSourcesEnvFile()
    }

    private func writeOpenAIAuth(from data: AppData) throws {
        guard let accountID = data.selectedOpenAIAccountID,
              let account = data.openAIAccounts.first(where: { $0.id == accountID }) else {
            return
        }

        let authURL = AppPaths.codexDirectory.appendingPathComponent("auth.json")
        try account.authJSON.write(to: authURL, atomically: true, encoding: .utf8)
    }

    private func ensureShellProfileSourcesEnvFile() throws {
        let marker = "# Codex Model Switcher"
        let sourceLine = #"[[ -f "$HOME/.codex/model-switcher.env" ]] && source "$HOME/.codex/model-switcher.env""#
        let existing = (try? String(contentsOf: AppPaths.shellProfile, encoding: .utf8)) ?? ""

        guard !existing.contains(sourceLine) else { return }

        var updated = existing
        if !updated.isEmpty, !updated.hasSuffix("\n") {
            updated += "\n"
        }
        updated += "\(marker)\n\(sourceLine)\n"
        try updated.write(to: AppPaths.shellProfile, atomically: true, encoding: .utf8)
    }

    private func setLaunchEnvironment(name: String, value: String) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = ["setenv", name, value]
        try? process.run()
    }
}
