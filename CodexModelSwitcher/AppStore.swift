import Foundation
import SwiftUI

@MainActor
final class AppStore: ObservableObject {
    @Published private(set) var data: AppData = .empty
    @Published var errorMessage = ""
    @Published var statusMessage = ""
    @Published var isOpenAIAccountLoginRunning = false
    @Published var checkingOpenAIAccountIDs: Set<String> = []
    @Published var proxyStatus: ProxyServerStatus = .notRunning

    private var storageReady = false

    private let configWriter = CodexConfigWriter()
    private let openAIAuthManager = OpenAIAuthManager()
    private let compatibilityProxy = CompatibilityProxyServer()

    var selectedService: CodexService? {
        guard let selected = data.selectedModel else { return nil }
        return data.services.first { $0.id == selected.serviceID }
    }

    var selectedModel: CodexModel? {
        guard let selected = data.selectedModel else { return nil }
        return selectedService?.models.first { $0.id == selected.modelID }
    }

    init() {
        compatibilityProxy.onStatusChange = { [weak self] status in
            Task { @MainActor in
                self?.proxyStatus = status
            }
        }
        load()
        syncCompatibilityProxy()
    }

    func clearError() {
        errorMessage = ""
        statusMessage = ""
    }

    func clearStatusMessage() {
        statusMessage = ""
    }

    func load() {
        if FileManager.default.fileExists(atPath: AppPaths.appData.path) {
            do {
                let stored = try Data(contentsOf: AppPaths.appData)
                var decoded = try JSONDecoder().decode(AppData.self, from: stored)
                try decoded.loadCredentials()
                data = decoded
                storageReady = true
                // Migrate legacy plaintext only after every credential was loaded successfully.
                persist()
            } catch {
                errorMessage = error.localizedDescription
                return
            }
        } else {
            data = defaultData()
            storageReady = true
            persist()
        }

        if data.selectedModel == nil {
            data.selectedModel = data.services.first.flatMap { service in
                service.models.first.map { SelectedModel(serviceID: service.id, modelID: $0.id) }
            }
            persist()
        }

        if data.selectedOpenAIAccountID == nil {
            data.selectedOpenAIAccountID = data.openAIAccounts.first?.id
            persist()
        }

        // Seed from config.toml when upgrading from an older model-switcher.json
        // that never stored this field.
        if !hasStoredReasoningEffort,
           let fromConfig = reasoningEffortFromConfig() {
            data.modelReasoningEffort = fromConfig
            persist()
        }

        migrateOpenAIAccountEmails()
    }

    private var hasStoredReasoningEffort: Bool {
        guard let stored = try? Data(contentsOf: AppPaths.appData),
              let object = try? JSONSerialization.jsonObject(with: stored) as? [String: Any] else {
            return false
        }
        return object["modelReasoningEffort"] != nil
    }

    private func reasoningEffortFromConfig() -> ReasoningEffort? {
        guard let content = try? String(contentsOf: AppPaths.codexConfig, encoding: .utf8) else {
            return nil
        }

        for line in content.components(separatedBy: .newlines) {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("[") {
                break
            }
            guard trimmed.hasPrefix("model_reasoning_effort") else { continue }
            let parts = trimmed.split(separator: "=", maxSplits: 1).map {
                $0.trimmingCharacters(in: .whitespaces)
            }
            guard parts.count == 2 else { continue }
            let value = parts[1]
                .trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
            return ReasoningEffort.from(rawValue: value)
        }
        return nil
    }

    func setReasoningEffort(_ effort: ReasoningEffort) {
        guard data.modelReasoningEffort != effort else { return }
        data.modelReasoningEffort = effort
        persist()

        guard data.selectedModel != nil else {
            statusMessage = "Reasoning effort saved. Choose a model to write config.toml."
            errorMessage = ""
            return
        }

        do {
            try configWriter.writeReasoningEffort(effort)
            statusMessage = "Reasoning effort set to \(effort.displayName.lowercased()). Restart Codex to apply."
            errorMessage = ""
        } catch {
            statusMessage = ""
            errorMessage = error.localizedDescription
        }
    }

    private func migrateOpenAIAccountEmails() {
        var didChange = false
        for index in data.openAIAccounts.indices where data.openAIAccounts[index].email == nil {
            if let email = openAIAuthManager.extractEmail(from: data.openAIAccounts[index].authJSON) {
                data.openAIAccounts[index].email = email
                data.openAIAccounts[index].name = email
                didChange = true
            }
        }

        if didChange {
            persist()
        }
    }

    func select(serviceID: String, modelID: String) {
        let selected = SelectedModel(serviceID: serviceID, modelID: modelID)
        do {
            try captureActiveAccount()
            try configWriter.applySelection(selected, in: data)
            data.selectedModel = selected
            persist()
            errorMessage = ""
            let selectedService = data.services.first { $0.id == serviceID }
            statusMessage = selectedService?.useCompatibilityProxy == true
                ? "Restart Codex and keep this app running for the proxy."
                : "Restart Codex to use this selection."
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func addOpenAIAccount() {
        guard !isOpenAIAccountLoginRunning else {
            errorMessage = AppError.openAIAccountLoginInProgress.localizedDescription
            return
        }

        isOpenAIAccountLoginRunning = true
        errorMessage = ""
        statusMessage = "Opening Codex login in your browser..."

        Task {
            do {
                let accountName = "OpenAI \(data.openAIAccounts.count + 1)"
                let account = try await openAIAuthManager.loginAccount(suggestedName: accountName)
                data.openAIAccounts.append(account)
                persist()
                statusMessage = "Saved \(account.displayName) in Keychain. Choose the account to activate it."
                errorMessage = ""
            } catch {
                statusMessage = ""
                errorMessage = error.localizedDescription
            }
            isOpenAIAccountLoginRunning = false
        }
    }

    private func captureActiveAccount() throws {
        let url = AppPaths.codexDirectory.appendingPathComponent("auth.json")
        guard FileManager.default.fileExists(atPath: url.path) else { return }
        let auth = try String(contentsOf: url, encoding: .utf8)
        guard let accountID = openAIAuthManager.extractAccountID(from: auth),
              let index = data.openAIAccounts.firstIndex(where: { $0.accountID == accountID && $0.email == openAIAuthManager.extractEmail(from: auth) }) else { return }
        try CredentialStore.write(auth, account: "openai:\(data.openAIAccounts[index].id)")
        data.openAIAccounts[index].authJSON = auth
    }

    func importCurrentOpenAIAccount() {
        do {
            let url = AppPaths.codexDirectory.appendingPathComponent("auth.json")
            let auth = try String(contentsOf: url, encoding: .utf8)
            guard let id = openAIAuthManager.extractAccountID(from: auth) else { throw AppError.openAIAccountLoginFailed }
            let email = openAIAuthManager.extractEmail(from: auth)
            if let index = data.openAIAccounts.firstIndex(where: { $0.accountID == id && $0.email == email }) {
                data.openAIAccounts[index].authJSON = auth
                data.selectedOpenAIAccountID = data.openAIAccounts[index].id
            } else {
                let account = OpenAIAccount(id: UUID().uuidString, name: email ?? "Current Codex account", authJSON: auth,
                    accountID: id, email: email, createdAt: Date())
                data.openAIAccounts.append(account)
                data.selectedOpenAIAccountID = account.id
            }
            persist()
            statusMessage = "Saved the current Codex account in Keychain."
        } catch { errorMessage = error.localizedDescription }
    }

    func selectOpenAIAccount(_ accountID: String) {
        do {
            try captureActiveAccount()
            var candidate = data
            candidate.selectedOpenAIAccountID = accountID
            let selected = SelectedModel(serviceID: "openai", modelID: "__native__")
            candidate.selectedModel = selected
            try configWriter.applySelection(selected, in: candidate)
            data = candidate
            persist()
            statusMessage = "Switched Codex account. Restart Codex to use it."
            errorMessage = ""
        } catch {
            statusMessage = ""
            errorMessage = error.localizedDescription
        }
    }

    func checkOpenAIAccounts() {
        guard !data.openAIAccounts.isEmpty else { return }

        for account in data.openAIAccounts where !checkingOpenAIAccountIDs.contains(account.id) {
            checkOpenAIAccount(account)
        }
    }

    private func checkOpenAIAccount(_ account: OpenAIAccount) {
        if let index = data.openAIAccounts.firstIndex(where: { $0.id == account.id }) {
            data.openAIAccounts[index].credentialStatus = .unchecked
            data.openAIAccounts[index].credentialMessage = nil
        }
        checkingOpenAIAccountIDs.insert(account.id)

        Task {
            let result = await openAIAuthManager.validateAccount(account)
            if let index = data.openAIAccounts.firstIndex(where: { $0.id == account.id }) {
                if let authJSON = result.authJSON {
                    data.openAIAccounts[index].authJSON = authJSON
                    data.openAIAccounts[index].accountID = openAIAuthManager.extractAccountID(from: authJSON)
                    if let email = openAIAuthManager.extractEmail(from: authJSON) {
                        data.openAIAccounts[index].email = email
                        data.openAIAccounts[index].name = email
                    }
                }
                data.openAIAccounts[index].credentialStatus = result.status
                data.openAIAccounts[index].credentialMessage = result.message
                persist()

                if result.authJSON != nil,
                   data.selectedOpenAIAccountID == account.id {
                    do {
                        try configWriter.restoreOpenAIAuth(from: data)
                    } catch {
                        errorMessage = error.localizedDescription
                    }
                }
            }
            checkingOpenAIAccountIDs.remove(account.id)
        }
    }

    func deleteOpenAIAccount(_ account: OpenAIAccount) {
        do {
            try CredentialStore.remove("openai:\(account.id)")
            data.openAIAccounts.removeAll { $0.id == account.id }
            if data.selectedOpenAIAccountID == account.id { data.selectedOpenAIAccountID = nil }
            persist()
            statusMessage = "Removed the saved account. The active Codex session stays signed in."
        } catch { errorMessage = error.localizedDescription }
    }

    func saveService(originalID: String?, form: ServiceFormData) {
        let providerName = form.name.trimmingCharacters(in: .whitespacesAndNewlines)
        let existingID = originalID ?? form.id.trimmingCharacters(in: .whitespacesAndNewlines)
        let serviceID = existingID.isEmpty ? uniqueServiceID(for: providerName) : existingID
        let models = parseModelRows(form.modelsText)

        guard isValidServiceID(serviceID) else {
            errorMessage = AppError.invalidServiceID.localizedDescription
            return
        }
        guard !models.isEmpty else {
            errorMessage = AppError.invalidModelList.localizedDescription
            return
        }
        if data.services.contains(where: { $0.id == serviceID && $0.id != originalID }) {
            errorMessage = AppError.duplicateServiceID.localizedDescription
            return
        }

        let service = CodexService(
            id: serviceID,
            name: providerName.isEmpty ? serviceID : providerName,
            baseURL: form.baseURL.trimmingCharacters(in: .whitespacesAndNewlines),
            envKey: form.envKey.trimmingCharacters(in: .whitespacesAndNewlines),
            apiKey: form.apiKey.trimmingCharacters(in: .whitespacesAndNewlines),
            useCompatibilityProxy: form.useCompatibilityProxy,
            models: models,
            catalogPath: data.services.first { $0.id == originalID }?.catalogPath,
            usesExistingProvider: data.services.first { $0.id == originalID }?.usesExistingProvider ?? false
        )

        if let originalID,
           let index = data.services.firstIndex(where: { $0.id == originalID }) {
            let previousSelection = data.selectedModel
            data.services[index] = service
            if data.selectedModel?.serviceID == originalID {
                let modelID = models.first { $0.id == previousSelection?.modelID }?.id ?? models[0].id
                data.selectedModel = SelectedModel(serviceID: service.id, modelID: modelID)
            }
        } else {
            data.services.append(service)
            data.selectedModel = SelectedModel(serviceID: service.id, modelID: models[0].id)
        }

        persist()
        if let selected = data.selectedModel {
            select(serviceID: selected.serviceID, modelID: selected.modelID)
        }
    }

    func deleteService(_ service: CodexService) {
        guard data.services.count > 1 else { return }
        data.services.removeAll { $0.id == service.id }
        if data.selectedModel?.serviceID == service.id {
            data.selectedModel = data.services.first.flatMap { nextService in
                nextService.models.first.map {
                    SelectedModel(serviceID: nextService.id, modelID: $0.id)
                }
            }
        }
        persist()
        if let selected = data.selectedModel {
            select(serviceID: selected.serviceID, modelID: selected.modelID)
        }
    }

    private func uniqueServiceID(for name: String) -> String {
        let baseID = slugify(name)
        var candidate = baseID
        var suffix = 2

        while data.services.contains(where: { $0.id == candidate }) {
            candidate = "\(baseID)-\(suffix)"
            suffix += 1
        }

        return candidate
    }

    private func persist() {
        guard storageReady else { return }
        do {
            try FileManager.default.createDirectory(
                at: AppPaths.codexDirectory,
                withIntermediateDirectories: true
            )
            let encoded = try data.saveCredentialsAndEncodeMetadata()
            try encoded.write(to: AppPaths.appData, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: AppPaths.appData.path)
            syncCompatibilityProxy()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func syncCompatibilityProxy() {
        compatibilityProxy.updateService(nil)
    }

    private func defaultData() -> AppData {
        var services = [CodexService(id: "openai", name: "Codex accounts", baseURL: "", envKey: "", apiKey: "",
            models: [CodexModel(id: "__native__", name: "Native Codex models")])]
        let config = (try? String(contentsOf: AppPaths.codexConfig, encoding: .utf8)) ?? ""
        for (id, name, url, catalog) in [
            ("baseten", "Baseten · 1Password", "https://inference.baseten.co/v1", "baseten-frontier.json"),
            ("openrouter", "Claude · OpenRouter API", "https://openrouter.ai/api/v1", "unified-openrouter.json"),
            ("xai", "Grok · xAI API", "https://api.x.ai/v1", "xai-frontier.json")
        ] {
            let path = AppPaths.codexDirectory.appendingPathComponent("model-catalogs/\(catalog)")
            guard config.contains("[model_providers.\(id)]"),
                  let raw = try? Data(contentsOf: path),
                  let json = try? JSONSerialization.jsonObject(with: raw) as? [String: Any],
                  let entries = json["models"] as? [[String: Any]] else { continue }
            let models = entries.compactMap { entry -> CodexModel? in
                guard let slug = entry["slug"] as? String else { return nil }
                return CodexModel(id: slug, name: entry["display_name"] as? String ?? slug)
            }
            services.append(CodexService(id: id, name: name, baseURL: url, envKey: "", apiKey: "", models: models,
                catalogPath: path.path, usesExistingProvider: true))
        }
        return AppData(services: services, selectedModel: nil)
    }
}
