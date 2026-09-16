import XCTest
@testable import HarborCore

final class SafetyTests: XCTestCase {
    let writer = CodexConfigWriter(bridgeToken: "test-local-bridge-token")
    func service(_ id: String, existing: Bool = false) -> CodexService {
        CodexService(id: id, name: id, baseURL: "https://example.com/v1", envKey: "EXAMPLE_KEY", apiKey: "secret-never-serialize",
                     models: [CodexModel(id: "test-model", name: "Test"), CodexModel(id: "__native__", name: "Native")], usesExistingProvider: existing)
    }

    func testBasetenTeamCatalogPreservesSelectionAndCustomModels() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let manifestURL = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("ModelHarbor/Support/baseten-models.json")
        let manifest = try Data(contentsOf: manifestURL)
        let selected = SelectedModel(serviceID: "baseten", modelID: "test-model")
        var data = AppData(services: [service("baseten", existing: true)], selectedModel: selected)
        let destination = directory.appendingPathComponent("baseten.json")
        try BasetenCatalog.install(in: &data, manifest: manifest, destination: destination)
        XCTAssertEqual(data.selectedModel, selected)
        XCTAssertEqual(data.services[0].apiKey, "secret-never-serialize")
        XCTAssertTrue(data.services[0].models.contains(where: { $0.id == "test-model" }))
        XCTAssertTrue(data.services[0].models.contains(where: { $0.id == "zai-org/GLM-5.3" }))
        let once = try Data(contentsOf: destination)
        try BasetenCatalog.install(in: &data, manifest: manifest, destination: destination)
        XCTAssertEqual(try Data(contentsOf: destination), once)
        let catalog = try XCTUnwrap(JSONSerialization.jsonObject(with: LiveRouting.catalog(in: data)) as? [String: Any])
        let models = try XCTUnwrap(catalog["models"] as? [[String: Any]])
        let glm = try XCTUnwrap(models.first { $0["slug"] as? String == "harbor/baseten/zai-org/GLM-5.3" })
        XCTAssertEqual(glm["default_reasoning_level"] as? String, "xhigh")
        let deepseek = try XCTUnwrap(models.first { $0["slug"] as? String == "harbor/baseten/deepseek-ai/DeepSeek-V4-Pro-0813" })
        XCTAssertEqual(deepseek["input_modalities"] as? [String], ["text"])
        let kimi = try XCTUnwrap(models.first { $0["slug"] as? String == "harbor/baseten/moonshotai/Kimi-K2.7-Code" })
        XCTAssertEqual(kimi["context_window"] as? Int, 262000)
        XCTAssertEqual((kimi["supported_reasoning_levels"] as? [[String: String]])?.map { $0["effort"]! }, ["high"])
    }

    func testMetadataNeverEncodesSecrets() throws {
        let account = OpenAIAccount(id: "account", name: "Account", authJSON: "private-oauth-token", accountID: "id", email: nil, createdAt: Date())
        let data = AppData(services: [service("example")], selectedModel: nil, openAIAccounts: [account])
        let encoded = String(decoding: try JSONEncoder().encode(data), as: UTF8.self)
        XCTAssertFalse(encoded.contains("secret-never-serialize"))
        XCTAssertFalse(encoded.contains("private-oauth-token"))
        XCTAssertFalse(encoded.contains("authJSON"))
        XCTAssertFalse(encoded.contains("apiKey"))
        XCTAssertEqual(try JSONDecoder().decode(AppData.self, from: Data(encoded.utf8)).openAIAccounts[0].authJSON, "")
    }

    func testSwitchingBackRestoresNativePickerAndPreservesProviders() throws {
        let provider = "[model_providers.baseten]\nname = \"Baseten\"\n[model_providers.baseten.auth]\ncommand = \"/opt/homebrew/bin/op\"\nargs = [\"read\", \"op://vault/item/key\"]\n"
        let input = "model=\"old\"\nmodel_provider = \"baseten\"\nmodel_catalog_json=\"/old/catalog\"\nmodel_reasoning_effort = \"high\"\n\n" + provider
        let output = try writer.rewriteConfig(input, selected: SelectedModel(serviceID: "openai", modelID: "__native__"), data: AppData(services: [service("openai")], selectedModel: nil))
        XCTAssertFalse(output.contains("model_catalog_json"))
        XCTAssertFalse(output.contains("model_provider ="))
        XCTAssertFalse(output.contains("model="))
        XCTAssertTrue(output.contains(provider))
        XCTAssertTrue(output.contains("model_reasoning_effort = \"high\""))
    }

    func testLinkedProviderPreservesAuthenticationExactly() throws {
        let input = "[model_providers.baseten]\nname = \"Existing\"\n[model_providers.baseten.auth]\ncommand = \"/opt/homebrew/bin/op\"\nargs = [\"read\", \"op://vault/item/key\"]\n"
        let output = try writer.rewriteConfig(input, selected: SelectedModel(serviceID: "baseten", modelID: "test-model"), data: AppData(services: [service("baseten", existing: true), service("codex-subscription")], selectedModel: nil))
        XCTAssertTrue(output.contains(input))
        XCTAssertFalse(output.contains("secret-never-serialize"))
        XCTAssertTrue(output.contains("model_provider = \"model-harbor\""))
    }

    func testRefusesToOverwriteUnownedProvider() {
        XCTAssertThrowsError(try writer.rewriteConfig("[model_providers.example]\nname = \"Mine\"\n", selected: SelectedModel(serviceID: "example", modelID: "test-model"), data: AppData(services: [service("example")], selectedModel: nil)))
    }

    func testNewProviderUsesKeychainCommandAndIsIdempotent() throws {
        let selected = SelectedModel(serviceID: "example", modelID: "test-model")
        let data = AppData(services: [service("example")], selectedModel: selected)
        let once = try writer.rewriteConfig("[features]\napps = true\n", selected: selected, data: data)
        let twice = try writer.rewriteConfig(once, selected: selected, data: data)
        XCTAssertFalse(twice.contains("secret-never-serialize"))
        XCTAssertTrue(twice.contains("command = \"/usr/bin/security\""))
        XCTAssertEqual(twice.components(separatedBy: "[model_providers.example]").count, 2)
        XCTAssertTrue(twice.contains("[features]\napps = true"))
        XCTAssertFalse(twice.contains("experimental_bearer_token"))
    }
}

extension SafetyTests {
    func testLiveProviderUsesCodexAuthAndSeparateLocalToken() throws {
        let source = "[model_providers.xai]\nbase_url = \"https://api.x.ai/v1\"\nenv_key = \"XAI_API_KEY\"\n"
        let oauth = CodexService(id: "grok-oauth", name: "Grok browser sign-in", baseURL: "http://127.0.0.1:48118/oauth/v1", envKey: "", apiKey: "", models: [CodexModel(id: "grok-4.6", name: "Grok 4.6")])
        let selection = SelectedModel(serviceID: oauth.id, modelID: "grok-4.6")
        let data = AppData(services: [oauth], selectedModel: selection)
        let output = try writer.rewriteConfig(source, selected: selection, data: data)
        XCTAssertTrue(output.contains(source))
        XCTAssertTrue(output.contains("model_provider = \"model-harbor\""))
        XCTAssertTrue(output.contains("/harbor/v1"))
        XCTAssertTrue(output.contains("requires_openai_auth = true"))
        XCTAssertTrue(output.contains("X-Model-Harbor-Token = \"test-local-bridge-token\""))
        let again = try writer.rewriteConfig(output, selected: selection, data: data)
        XCTAssertEqual(again.components(separatedBy: "[model_providers.model-harbor]").count, 2)
    }
    func testGrokCopiesAuthenticationWithoutChangingSourceProvider() throws {
        let original = "[model_providers.xai]\nname = \"xAI\"\nbase_url = \"https://api.x.ai/v1\"\n[model_providers.xai.auth]\ncommand = \"/opt/homebrew/bin/op\"\nargs = [\"read\", \"op://example/item/key\"]\n"
        let selected = SelectedModel(serviceID: "xai", modelID: "test-model")
        let data = AppData(services: [service("xai", existing: true)], selectedModel: selected)
        let output = try writer.rewriteConfig(original, selected: selected, data: data)
        XCTAssertTrue(output.contains(original))
        XCTAssertTrue(output.contains("model_provider = \"xai-switcher\""))
        XCTAssertTrue(output.contains("http://127.0.0.1:48118/v1"))
        let again = try writer.rewriteConfig(output, selected: selected, data: data)
        XCTAssertEqual(again.components(separatedBy: "# Codex Model Switcher Grok Responses adapter").count, 2)
    }

    func testMalformedTOMLAndUnrelatedEditsAreRejected() throws {
        let selected = SelectedModel(serviceID: "openai", modelID: "__native__")
        let data = AppData(services: [service("openai")], selectedModel: selected)
        XCTAssertThrowsError(try writer.rewriteConfig("broken = [", selected: selected, data: data))
        XCTAssertThrowsError(try ConfigValidation.prepare(original: "approval_policy = \"never\"", updated: "approval_policy = \"on-request\"", provider: "openai", linked: true, grok: false))
    }

    func testSecondConcurrentSwitcherCannotAcquireLock() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let first = try ConfigLock(directory: directory)
        try withExtendedLifetime(first) { XCTAssertThrowsError(try ConfigLock(directory: directory)) }
    }
}

extension SafetyTests {
    func testDefaultChangesKeepProviderAndCredentialsStable() throws {
        let original = "[model_providers.baseten]\nname = \"Baseten\"\nbase_url = \"https://inference.baseten.co/v1\"\n[model_providers.baseten.auth]\ncommand = \"/opt/homebrew/bin/op\"\nargs = [\"read\", \"op://vault/item/key\"]\n"
        let data = AppData(services: [service("grok-oauth"), service("baseten", existing: true), service("codex-subscription")], selectedModel: nil)
        let grok = try writer.rewriteConfig(original, selected: SelectedModel(serviceID: "grok-oauth", modelID: "test-model"), data: data)
        let baseten = try writer.rewriteConfig(grok, selected: SelectedModel(serviceID: "baseten", modelID: "test-model"), data: data)
        let codex = try writer.rewriteConfig(baseten, selected: SelectedModel(serviceID: "codex-subscription", modelID: "test-model"), data: data)
        XCTAssertEqual(grok.replacingOccurrences(of: "harbor/grok-oauth/", with: "harbor/baseten/"), baseten)
        XCTAssertEqual(baseten.replacingOccurrences(of: "harbor/baseten/", with: "harbor/codex-subscription/"), codex)
        XCTAssertTrue(baseten.contains("model = \"harbor/baseten/test-model\""))
        XCTAssertTrue(baseten.contains(original))
        XCTAssertFalse(baseten.contains("secret-never-serialize"))
    }

    func testCatalogHasIndependentRoutesAndHiddenFrozenLegacy() throws {
        let services = [service("grok-oauth"), service("baseten"), service("codex-subscription")].map { s in var s = s; s.models.removeAll { $0.id == "__native__" }; return s }
        let data = AppData(services: services, selectedModel: nil,
                           legacyModel: SelectedModel(serviceID: "grok-oauth", modelID: "test-model"))
        let json = try JSONSerialization.jsonObject(with: LiveRouting.catalog(in: data)) as! [String: Any]
        let entries = json["models"] as! [[String: Any]]
        XCTAssertEqual(entries.compactMap { $0["slug"] as? String }, ["harbor/grok-oauth/test-model", "harbor/baseten/test-model", "harbor/codex-subscription/test-model", "harbor-selected"])
        XCTAssertEqual(entries.last?["visibility"] as? String, "hide")
        XCTAssertEqual(LiveRouting.selection(for: "harbor/baseten/test-model", in: data), SelectedModel(serviceID: "baseten", modelID: "test-model"))
        XCTAssertNil(LiveRouting.selection(for: "harbor/baseten/missing", in: data))
        let restored = try JSONDecoder().decode(AppData.self, from: JSONEncoder().encode(data))
        XCTAssertEqual(restored.legacyModel, data.legacyModel)
    }

    func testLiveProviderCannotOverwriteAnUnownedProvider() throws {
        let data = AppData(services: [service("grok-oauth")], selectedModel: nil)
        XCTAssertThrowsError(try writer.rewriteConfig("[model_providers.model-harbor]\nname = \"Mine\"\n", selected: SelectedModel(serviceID: "grok-oauth", modelID: "test-model"), data: data))
    }
}
