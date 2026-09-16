import Foundation
import SwiftUI

@MainActor
final class AppStore: ObservableObject {
    static let shared = AppStore(startAdapter: !ProcessInfo.processInfo.arguments.contains(where: { ["--verify-accounts", "--migrate-vault"].contains($0) }))
    @Published private(set) var data: AppData = .empty
    @Published var errorMessage = ""
    @Published var statusMessage = ""
    @Published var isOpenAIAccountLoginRunning = false
    @Published var checkingOpenAIAccountIDs: Set<String> = []
    @Published var proxyStatus: ProxyServerStatus = .notRunning
    @Published private(set) var storageReady = false
    @Published var grokAccount = "Checking sign-in…"
    @Published var grokIsSignedIn = false
    @Published var isGrokLoginRunning = false
    @Published var isBasetenReconnectRunning = false
    @Published var basetenState = "not_loaded"
    @Published var taskRepairsEnabled = false
    @Published var taskRepairState = "checking"
    @Published var pendingTaskRepairs = 0
    @Published var repairedTaskCount = 0
    @Published var savingTaskRepairs = false
    @Published var providerActivity: [String: ProviderActivity] = [:]
    @Published var lastProviderID = ""
    @Published var lastRequestedModel = ""
    @Published var openRouterReady = false
    @Published var connectingOpenRouter = false
    @Published var openRouterModels: [OpenRouterModel] = []
    @Published var usageSnapshots: [UsageSnapshot] = []
    @Published var usageRefreshing = false
    @Published private(set) var usageLastAttempt = Date.distantPast
    private var usagePolling: Task<Void, Never>?
    private var connectionPolling: Task<Void, Never>?
    private let writer = CodexConfigWriter()
    private let authManager = OpenAIAuthManager()
    private let grokAdapter = GrokAdapter()

    var selectedService: CodexService? {
        data.services.first { $0.id == data.selectedModel?.serviceID }
    }
    var selectedModel: CodexModel? {
        selectedService?.models.first { $0.id == data.selectedModel?.modelID }
    }

    init(startAdapter: Bool = true) {
        load()
        if startAdapter {
            do {
                try grokAdapter.start()
                proxyStatus = .starting
                Task {
                    for _ in 0..<50 {
                        try? grokAdapter.start()
                        if await grokAdapter.isHealthy() {
                            proxyStatus = .active
                            await refreshGrokAccount()
                            await syncOpenRouter()
                            usagePolling = Task { [weak self] in
                                while !Task.isCancelled {
                                    if UserDefaults.standard.object(forKey: "harbor.usageAutoRefresh") as? Bool ?? true {
                                        await self?.refreshUsage()
                                    }
                                    try? await Task.sleep(nanoseconds: 300_000_000_000)
                                }
                            }
                            connectionPolling = Task { [weak self] in
                                while !Task.isCancelled {
                                    await self?.refreshConnectionStatus()
                                    try? await Task.sleep(nanoseconds: 2_000_000_000)
                                }
                            }
                            if let selected = data.selectedModel, LiveRouting.supports(selected.serviceID) {
                                perform { try writer.applySelection(selected, in: data) }
                            }
                            return
                        }
                        try? await Task.sleep(nanoseconds: 100_000_000)
                    }
                    proxyStatus = .error
                }
            } catch { errorMessage = error.localizedDescription; proxyStatus = .error }
        }
    }

    func stopAdapter() { connectionPolling?.cancel(); usagePolling?.cancel(); grokAdapter.stop() }

    func usageSnapshot(for provider: String) -> UsageSnapshot? {
        let id = UserDefaults.standard.string(forKey: "harbor.usageAccount.\(provider)")
        let entries = usageSnapshots.filter { $0.providerID == provider }
        return entries.first { $0.id == id } ?? entries.first
    }

    func refreshUsage() async {
        guard !usageRefreshing, Date().timeIntervalSince(usageLastAttempt) >= 60 else { return }
        usageRefreshing = true
        usageLastAttempt = Date()
        defer { usageRefreshing = false }
        var accounts: [CodexUsageAccount] = []
        if let auth = try? String(contentsOf: AppPaths.codexDirectory.appendingPathComponent("auth.json"), encoding: .utf8),
           let current = CodexUsageAccount.parse(auth: auth) { accounts.append(current) }
        for saved in data.openAIAccounts {
            if let account = CodexUsageAccount.parse(auth: saved.authJSON, label: saved.displayName),
               !accounts.contains(where: { $0.id == account.id }) { accounts.append(account) }
        }
        let history = UserDefaults.standard.object(forKey: "harbor.importCodexBarHistory") as? Bool ?? true
        var next = history ? await UsageHistoryReader.shared.read() : []
        async let providerResults = UsageClient.bridge()
        for account in accounts {
            do { next.append(try await UsageClient.codex(account)) }
            catch {
                var entry = usageSnapshots.first { $0.id == account.id } ?? UsageSnapshot(id: account.id, providerID: "codex-subscription", accountLabel: account.label, source: "Codex account usage", scope: "This ChatGPT account")
                entry.error = (error as? ProviderError)?.localizedDescription ?? "Could not refresh Codex usage. Harbor will retry later."
                next.append(entry)
            }
        }
        if accounts.isEmpty {
            next.append(UsageSnapshot(id: "codex-unavailable", providerID: "codex-subscription", accountLabel: "Codex account", source: "Codex account usage", scope: "", error: "No readable Codex sign-in. Sign in to Codex or unlock saved accounts in Harbor."))
        }
        do { next += try await providerResults }
        catch {
            for provider in ["baseten", "grok-oauth", "openrouter"] {
                var entry = usageSnapshots.first { $0.id == provider } ?? UsageSnapshot(id: provider, providerID: provider, accountLabel: ProviderDefinition.named(provider).name, source: "Provider account API", scope: "")
                entry.error = "Usage service is unavailable. Keep Model Harbor open and refresh."
                next.append(entry)
            }
        }
        if !(UserDefaults.standard.object(forKey: "harbor.importCodexBarHistory") as? Bool ?? true) {
            next.removeAll { $0.source.hasPrefix("CodexBar") }
        }
        usageSnapshots = next.sorted { ($0.isEstimate ? 1 : 0) < ($1.isEstimate ? 1 : 0) }
    }

    func connectCodexBarHistory() {
        let panel = NSOpenPanel()
        panel.title = "Connect CodexBar history"
        panel.message = "Choose CodexBar’s widget-snapshot.json to read its cached usage and token-value history. This grants access to one file; no sign-in or Keychain access is needed."
        panel.prompt = "Connect history"
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.directoryURL = UsageClient.codexBarHistoryLocation.deletingLastPathComponent()
        panel.begin { response in
            guard response == .OK, let url = panel.url else { return }
            do {
                let access = url.startAccessingSecurityScopedResource()
                defer { if access { url.stopAccessingSecurityScopedResource() } }
                guard url.lastPathComponent == "widget-snapshot.json" else {
                    throw ProviderError.message("Choose CodexBar’s widget-snapshot.json file.")
                }
                let bookmark = try url.bookmarkData(options: .withSecurityScope, includingResourceValuesForKeys: nil, relativeTo: nil)
                UserDefaults.standard.set(bookmark, forKey: "harbor.codexBarHistoryBookmark")
                UserDefaults.standard.set(true, forKey: "harbor.importCodexBarHistory")
                self.statusMessage = "CodexBar history connected. Usage will refresh from its saved history."
                Task {
                    let history = await UsageHistoryReader.shared.read()
                    self.usageSnapshots.removeAll { $0.source.hasPrefix("CodexBar") }
                    if UserDefaults.standard.object(forKey: "harbor.importCodexBarHistory") as? Bool ?? true {
                        self.usageSnapshots += history
                    }
                    if history.isEmpty { self.errorMessage = "CodexBar history is not available yet. Refresh CodexBar, then retry here." }
                }
            } catch { self.errorMessage = error.localizedDescription }
        }
    }

    func clearError() { errorMessage = ""; statusMessage = "" }
    func clearStatusMessage() { statusMessage = "" }

    func unlockAccounts() {
        CredentialStore.allowAuthenticationUI = true
        defer { CredentialStore.allowAuthenticationUI = false }
        load()
        if storageReady { errorMessage = ""; Task { await refreshGrokAccount() } }
    }

    func reconnectBaseten() {
        guard !isBasetenReconnectRunning else { return }
        isBasetenReconnectRunning = true
        statusMessage = "Unlock Baseten in 1Password once for this Harbor session."
        Task {
            defer { isBasetenReconnectRunning = false; Task { await refreshConnectionStatus() } }
            do {
                try await grokAdapter.reconnectBaseten()
                errorMessage = ""
                statusMessage = "Baseten is ready. Its credential stays in memory until Harbor quits or you reconnect."
            } catch {
                statusMessage = ""
                errorMessage = error.localizedDescription
            }
        }
    }

    func setTaskRepairsEnabled(_ enabled: Bool) {
        guard !savingTaskRepairs else { return }
        savingTaskRepairs = true
        Task {
            defer { savingTaskRepairs = false }
            do {
                try await grokAdapter.setTaskRepairsEnabled(enabled)
                await refreshConnectionStatus()
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func refreshConnectionStatus() async {
        guard let bytes = try? await grokAdapter.connectionStatus(),
              let value = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any] else {
            proxyStatus = .notRunning
            providerActivity = [:]
            return
        }
        proxyStatus = .active
        basetenState = (value["baseten_auth"] as? [String: Any])?["state"] as? String ?? "not_loaded"
        if let providers = value["providers"] as? [String: Any] {
            openRouterReady = providers["openrouter_ready"] as? Bool ?? false
            providerActivity = (providers["activity"] as? [String: [String: Any]] ?? [:]).mapValues(ProviderActivity.init)
        }
        if let request = value["last_request"] as? [String: Any] {
            lastProviderID = request["provider"] as? String ?? ""
            lastRequestedModel = request["model"] as? String ?? ""
        }
        if let repairs = value["task_repairs"] as? [String: Any] {
            taskRepairsEnabled = repairs["enabled"] as? Bool ?? false
            taskRepairState = repairs["state"] as? String ?? "checking"
            pendingTaskRepairs = repairs["pending"] as? Int ?? 0
            repairedTaskCount = repairs["repaired"] as? Int ?? 0
        }
    }

    func signInGrok() {
        guard !isGrokLoginRunning else { return }
        isGrokLoginRunning = true
        statusMessage = "Finish signing in on Grok’s website."
        Task {
            defer { isGrokLoginRunning = false }
            do {
                try await GrokAdapter.login()
                if await refreshGrokAccount() {
                    errorMessage = ""
                    statusMessage = "Signed in to Grok. Choose a Grok model in the Codex task picker."
                } else {
                    statusMessage = ""
                    errorMessage = "Browser sign-in finished, but Grok’s connection could not be verified. Try again."
                }
            }
            catch { errorMessage = error.localizedDescription }
        }
    }

    @discardableResult
    func refreshGrokAccount() async -> Bool {
        do {
            let bytes = try await grokAdapter.accountStatus()
            guard let value = try JSONSerialization.jsonObject(with: bytes) as? [String: Any],
                  let entries = value["models"] as? [[String: Any]] else { throw AppError.missingModel }
            grokAccount = value["email"] as? String ?? "Signed in"
            grokIsSignedIn = true
            guard storageReady else { return true }
            let catalog = AppPaths.codexDirectory.appendingPathComponent("model-catalogs/grok-oauth.json")
            try FileManager.default.createDirectory(at: catalog.deletingLastPathComponent(), withIntermediateDirectories: true)
            try JSONSerialization.data(withJSONObject: ["models": entries]).write(to: catalog, options: .atomic)
            var candidate = data
            if let index = candidate.services.firstIndex(where: { $0.id == "grok-oauth" }) {
                candidate.services[index].models = entries.compactMap { entry in
                    guard let slug = entry["slug"] as? String else { return nil }
                    return CodexModel(id: slug, name: entry["display_name"] as? String ?? slug)
                }
                candidate.services[index].catalogPath = catalog.path
            }
            try save(candidate)
            return true
        } catch { grokIsSignedIn = false; grokAccount = "Sign in to load your models"; return false }
    }

    func load() {
        do {
            var didMigrate = false
            var candidate = defaultData()
            if FileManager.default.fileExists(atPath: AppPaths.appData.path) {
                candidate = try JSONDecoder().decode(AppData.self, from: Data(contentsOf: AppPaths.appData))
                try candidate.loadCredentials()
                candidate.services.removeAll { $0.id == "xai" }
                for service in defaultData().services where !candidate.services.contains(where: { $0.id == service.id }) {
                    candidate.services.append(service)
                }
            }
            if let index = candidate.services.firstIndex(where: { $0.id == "openai" }),
               !candidate.services[index].models.contains(where: { $0.id == "__native__" }) {
                candidate.services[index].models.insert(CodexModel(id: "__native__", name: "Native Codex models"), at: 0)
            }
            if let subscription = codexSubscriptionService() {
                candidate.services.removeAll { $0.id == subscription.id }
                candidate.services.insert(subscription, at: 0)
            }
            if candidate.legacyModel == nil, let selection = candidate.selectedModel, LiveRouting.supports(selection.serviceID) {
                candidate.legacyModel = selection
                didMigrate = true
            }
            if let manifest = Bundle.main.url(forResource: "baseten-models", withExtension: "json") {
                try BasetenCatalog.install(in: &candidate, manifest: Data(contentsOf: manifest),
                    destination: AppPaths.codexDirectory.appendingPathComponent("model-catalogs/baseten-frontier.json"))
            }
            try reflectActiveConfiguration(in: &candidate)
            try save(candidate)
            storageReady = true
            if didMigrate { statusMessage = "Per-task models are ready. Reopen Codex once to load the new picker entries." }
        } catch { storageReady = false; errorMessage = error.localizedDescription }
    }

    private func reflectActiveConfiguration(in candidate: inout AppData) throws {
        let config = try String(contentsOf: AppPaths.codexConfig, encoding: .utf8)
        let status = try PythonRuntime.run("import json,sys,tomllib; d=tomllib.loads(json.load(sys.stdin)['config']); print(json.dumps({'provider':d.get('model_provider','openai'),'model':d.get('model')}))", input: ["config": config])
        let values = try JSONSerialization.jsonObject(with: status) as? [String: Any]
        var provider = values?["provider"] as? String ?? "openai"
        if provider == "xai-switcher" { provider = "xai" }
        if provider == LiveRouting.providerID {
            if let model = values?["model"] as? String, let selection = LiveRouting.selection(for: model, in: candidate) {
                candidate.selectedModel = selection
            } else if let selection = candidate.selectedModel,
               LiveRouting.supports(selection.serviceID),
               candidate.services.contains(where: { $0.id == selection.serviceID && $0.models.contains(where: { $0.id == selection.modelID }) }) {
                // Preserve the pre-upgrade default until a named route is written.
            } else { candidate.selectedModel = nil }
        } else if provider == "openai" {
            candidate.selectedModel = SelectedModel(serviceID: "openai", modelID: "__native__")
        } else if let model = values?["model"] as? String,
                  candidate.services.contains(where: { $0.id == provider && $0.models.contains(where: { $0.id == model }) }) {
            candidate.selectedModel = SelectedModel(serviceID: provider, modelID: model)
        } else { candidate.selectedModel = nil }
        if let auth = try? String(contentsOf: AppPaths.codexDirectory.appendingPathComponent("auth.json"), encoding: .utf8),
           let id = authManager.extractAccountID(from: auth) {
            candidate.selectedOpenAIAccountID = candidate.openAIAccounts.first {
                $0.accountID == id && $0.email == authManager.extractEmail(from: auth)
            }?.id
        }
    }

    private func save(_ candidate: AppData) throws {
        let encoded = try candidate.saveCredentialsAndEncodeMetadata()
        try FileManager.default.createDirectory(at: AppPaths.codexDirectory, withIntermediateDirectories: true)
        try encoded.write(to: AppPaths.appData, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: AppPaths.appData.path)
        try writer.updateCatalog(in: candidate)
        data = candidate
    }

    private func perform(_ operation: () throws -> Void) {
        guard storageReady else { return }
        do { try operation(); errorMessage = "" }
        catch { statusMessage = ""; errorMessage = error.localizedDescription }
    }

    func setReasoningEffort(_ effort: ReasoningEffort) {
        if selectedService?.id == "xai" {
            statusMessage = "Grok 4.20 uses its built-in reasoning mode. Choose Reasoning or Fast in the model list."
            return
        }
        perform {
            try writer.writeReasoningEffort(effort)
            var candidate = data
            candidate.modelReasoningEffort = effort
            try save(candidate)
            statusMessage = "Reasoning preference saved. Restart Codex to apply."
        }
    }

    private func capturingActiveAccount() throws -> AppData {
        var candidate = data
        let file = AppPaths.codexDirectory.appendingPathComponent("auth.json")
        guard FileManager.default.fileExists(atPath: file.path) else { return candidate }
        let auth = try String(contentsOf: file, encoding: .utf8)
        guard let id = authManager.extractAccountID(from: auth),
              let index = candidate.openAIAccounts.firstIndex(where: {
                  $0.accountID == id && $0.email == authManager.extractEmail(from: auth)
              }) else { return candidate }
        candidate.openAIAccounts[index].authJSON = auth
        return candidate
    }

    func select(serviceID: String, modelID: String) {
        perform {
            if serviceID == "xai" || LiveRouting.supports(serviceID), proxyStatus != .active {
                throw NSError(domain: "Switcher", code: 1, userInfo: [NSLocalizedDescriptionKey: "Model Harbor’s connection is not ready. Reopen Model Harbor and try again."])
            }
            var candidate = try capturingActiveAccount()
            let selection = SelectedModel(serviceID: serviceID, modelID: modelID)
            try writer.applySelection(selection, in: candidate)
            candidate.selectedModel = selection
            try save(candidate)
            statusMessage = LiveRouting.supports(serviceID)
                ? "Default saved for new tasks. Existing tasks keep their own model."
                : "Selection saved. Restart Codex to apply."
        }
    }

    func selectOpenAIAccount(_ accountID: String) {
        perform {
            guard data.openAIAccounts.contains(where: { $0.id == accountID }) else { throw AppError.openAIAccountLoginFailed }
            var candidate = try capturingActiveAccount()
            candidate.selectedOpenAIAccountID = accountID
            let selected = SelectedModel(serviceID: "openai", modelID: "__native__")
            try writer.applySelection(selected, in: candidate)
            candidate.selectedModel = selected
            try save(candidate)
            statusMessage = "Account selected. Restart Codex to use it."
        }
    }

    private func adding(_ auth: String, name: String, to candidate: inout AppData) throws {
        guard let id = authManager.extractAccountID(from: auth) else { throw AppError.openAIAccountLoginFailed }
        let email = authManager.extractEmail(from: auth)
        if let index = candidate.openAIAccounts.firstIndex(where: { $0.accountID == id && $0.email == email }) {
            candidate.openAIAccounts[index].authJSON = auth
        } else {
            candidate.openAIAccounts.append(OpenAIAccount(id: UUID().uuidString, name: name, authJSON: auth,
                accountID: id, email: email, createdAt: Date()))
        }
    }

    func importCurrentOpenAIAccount() {
        perform {
            var candidate = data
            let auth = try String(contentsOf: AppPaths.codexDirectory.appendingPathComponent("auth.json"), encoding: .utf8)
            try adding(auth, name: "Current Codex account", to: &candidate)
            try save(candidate)
            statusMessage = "Saved the current account in Keychain."
        }
    }

    func importExistingAccounts() {
        perform {
            let url = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".codex-switcher/accounts.json")
            let raw = try Data(contentsOf: url)
            guard let root = try JSONSerialization.jsonObject(with: raw) as? [String: Any],
                  let accounts = root["accounts"] as? [[String: Any]] else { throw AppError.openAIAccountLoginFailed }
            var candidate = data
            var imported = 0
            for account in accounts {
                guard let auth = account["auth_data"] as? [String: Any], auth["type"] as? String == "chat_g_p_t" else { continue }
                var tokens = auth
                tokens.removeValue(forKey: "type")
                let value: [String: Any] = ["auth_mode": "chatgpt", "tokens": tokens]
                let json = String(decoding: try JSONSerialization.data(withJSONObject: value), as: UTF8.self)
                let id = authManager.extractAccountID(from: json)
                let email = authManager.extractEmail(from: json)
                if !candidate.openAIAccounts.contains(where: { $0.accountID == id && $0.email == email }) {
                    try adding(json, name: account["name"] as? String ?? "Codex account", to: &candidate)
                }
                imported += 1
            }
            guard imported > 0 else { throw AppError.openAIAccountLoginFailed }
            try reflectActiveConfiguration(in: &candidate)
            try save(candidate)
            statusMessage = "Imported \(imported) accounts into Keychain. Choose an account to activate it."
        }
    }

    func addOpenAIAccount() {
        guard storageReady, !isOpenAIAccountLoginRunning else { return }
        isOpenAIAccountLoginRunning = true
        statusMessage = "Complete Codex sign-in in your browser."
        Task {
            do {
                let account = try await authManager.loginAccount(suggestedName: "Codex account")
                var candidate = data
                try adding(account.authJSON, name: account.name, to: &candidate)
                try save(candidate)
                statusMessage = "Account saved in Keychain. Choose it to activate."
                errorMessage = ""
            } catch { errorMessage = error.localizedDescription; statusMessage = "" }
            isOpenAIAccountLoginRunning = false
        }
    }

    func checkOpenAIAccounts() {
        for account in data.openAIAccounts {
            checkingOpenAIAccountIDs.insert(account.id)
            Task {
                let result = await authManager.validateAccount(account)
                if let index = data.openAIAccounts.firstIndex(where: { $0.id == account.id }) {
                    data.openAIAccounts[index].credentialStatus = result.status
                    data.openAIAccounts[index].credentialMessage = result.message
                }
                checkingOpenAIAccountIDs.remove(account.id)
            }
        }
    }

    func deleteOpenAIAccount(_ account: OpenAIAccount) {
        perform {
            var candidate = data
            candidate.openAIAccounts.removeAll { $0.id == account.id }
            if candidate.selectedOpenAIAccountID == account.id { candidate.selectedOpenAIAccountID = nil }
            try save(candidate)
            try CredentialStore.remove("openai:\(account.id)")
            statusMessage = "Saved account removed. Your active Codex session stays signed in."
        }
    }

    var codexConfigured: Bool {
        data.services.contains { $0.id == "codex-subscription" } && FileManager.default.fileExists(atPath: AppPaths.codexDirectory.appendingPathComponent("auth.json").path)
    }

    func providerConnectionLabel(_ id: String) -> String {
        if proxyStatus != .active { return "Offline" }
        if providerConnected(id) { return "Connected" }
        if id == "codex-subscription" && codexConfigured { return "Configured" }
        return "Not connected"
    }

    func providerConnected(_ id: String) -> Bool {
        guard proxyStatus == .active else { return false }
        switch id {
        case "baseten": return basetenState == "ready"
        case "grok-oauth": return grokIsSignedIn
        case "openrouter": return openRouterReady
        case "codex-subscription": return providerActivity[id]?.verifiedConnection ?? false
        default: return false
        }
    }

    func syncOpenRouter() async {
        guard let service = data.services.first(where: { $0.id == "openrouter" }), !service.apiKey.isEmpty else { return }
        do { try await grokAdapter.configureOpenRouter(key: service.apiKey); await refreshConnectionStatus() }
        catch { errorMessage = error.localizedDescription }
    }

    func disconnectOpenRouter() async {
        do {
            try await grokAdapter.configureOpenRouter(key: "")
            var candidate = data
            if let index = candidate.services.firstIndex(where: { $0.id == "openrouter" }) { candidate.services[index].apiKey = "" }
            try save(candidate)
            await refreshConnectionStatus()
            statusMessage = "OpenRouter disconnected. Its model choices remain saved for reconnecting."
            errorMessage = ""
        } catch { errorMessage = error.localizedDescription }
    }

    func fetchOpenRouterModels(key: String) async -> Bool {
        guard !connectingOpenRouter else { return false }
        connectingOpenRouter = true
        defer { connectingOpenRouter = false }
        do {
            openRouterModels = try await OpenRouterAPI.models(key: key.trimmingCharacters(in: .whitespacesAndNewlines))
            guard !openRouterModels.isEmpty else { throw ProviderError.message("No tool-capable OpenRouter models are available.") }
            errorMessage = ""
            return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    func connectOpenRouter(key: String, models: Set<String>) async -> Bool {
        guard storageReady else { return false }
        do {
            let picked = openRouterModels.filter { models.contains($0.id) }
            guard !picked.isEmpty else { throw ProviderError.message("Choose at least one model.") }
            let key = key.trimmingCharacters(in: .whitespacesAndNewlines)
            let path = AppPaths.codexDirectory.appendingPathComponent("model-catalogs/openrouter.json")
            try FileManager.default.createDirectory(at: path.deletingLastPathComponent(), withIntermediateDirectories: true)
            try JSONSerialization.data(withJSONObject: ["models": picked.map(\.catalogEntry)], options: [.sortedKeys]).write(to: path, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path.path)
            var candidate = data
            candidate.services.removeAll { $0.id == "openrouter" }
            candidate.services.append(CodexService(id: "openrouter", name: "OpenRouter", baseURL: "https://openrouter.ai/api/v1", envKey: "OPENROUTER_API_KEY", apiKey: key, models: picked.map { CodexModel(id: $0.id, name: $0.name) }, catalogPath: path.path))
            try save(candidate)
            try await grokAdapter.configureOpenRouter(key: key)
            await refreshConnectionStatus()
            statusMessage = "OpenRouter connected. Reopen Codex once to load newly added model names. Existing task models stay unchanged."
            errorMessage = ""
            return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    func saveService(originalID: String?, form: ServiceFormData) {
        perform {
            let name = form.name.trimmingCharacters(in: .whitespacesAndNewlines)
            let id = originalID ?? (form.id.isEmpty ? slugify(name) : form.id)
            guard isValidServiceID(id) else { throw AppError.invalidServiceID }
            guard !data.services.contains(where: { $0.id == id && $0.id != originalID }) else { throw AppError.duplicateServiceID }
            let models = parseModelRows(form.modelsText)
            guard !models.isEmpty else { throw AppError.invalidModelList }
            let existing = data.services.first { $0.id == originalID }
            let service = CodexService(id: id, name: name.isEmpty ? id : name,
                baseURL: form.baseURL, envKey: form.envKey, apiKey: form.apiKey, models: models,
                catalogPath: existing?.catalogPath, usesExistingProvider: existing?.usesExistingProvider ?? false)
            var candidate = data
            if let index = candidate.services.firstIndex(where: { $0.id == id }) { candidate.services[index] = service }
            else { candidate.services.append(service) }
            try save(candidate)
            statusMessage = "Provider saved. Select a model to activate it."
        }
    }

    func deleteService(_ service: CodexService) {
        perform {
            guard service.id != "openai" else { return }
            guard data.selectedModel?.serviceID != service.id else {
                throw NSError(domain: "Switcher", code: 2, userInfo: [NSLocalizedDescriptionKey: "Select another provider before removing this one."])
            }
            var candidate = data
            candidate.services.removeAll { $0.id == service.id }
            try save(candidate)
            try CredentialStore.remove("provider:\(service.id)")
        }
    }

    private func codexSubscriptionService() -> CodexService? {
        let cache = AppPaths.codexDirectory.appendingPathComponent("models_cache.json")
        guard let bytes = try? Data(contentsOf: cache),
              let object = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any],
              let entries = object["models"] as? [[String: Any]] else { return nil }
        let models = entries.compactMap { entry -> CodexModel? in
            guard entry["visibility"] as? String == "list", let slug = entry["slug"] as? String else { return nil }
            return CodexModel(id: slug, name: entry["display_name"] as? String ?? slug)
        }
        guard !models.isEmpty else { return nil }
        return CodexService(id: "codex-subscription", name: "Codex · Current subscription",
            baseURL: "https://chatgpt.com/backend-api/codex", envKey: "", apiKey: "",
            models: models, catalogPath: cache.path)
    }

    private func defaultData() -> AppData {
        var services = [CodexService(id: "openai", name: "Codex accounts", baseURL: "", envKey: "", apiKey: "",
            models: [CodexModel(id: "__native__", name: "Native Codex models")])]
        let config = (try? String(contentsOf: AppPaths.codexConfig, encoding: .utf8)) ?? ""
        for (id, name, url, catalog) in [
            ("baseten", "Baseten · Direct API", "https://inference.baseten.co/v1", "baseten-frontier.json")
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
        services.append(CodexService(id: "grok-oauth", name: "Grok · Browser sign-in", baseURL: "http://127.0.0.1:48118/oauth/v1", envKey: "", apiKey: "", models: []))
        return AppData(services: services, selectedModel: nil)
    }
}
