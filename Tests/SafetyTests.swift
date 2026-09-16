import XCTest
@testable import SwitcherCore

final class SafetyTests: XCTestCase {
    let writer = CodexConfigWriter()
    func service(_ id: String, existing: Bool = false) -> CodexService {
        CodexService(id: id, name: id, baseURL: "https://example.com/v1", envKey: "EXAMPLE_KEY", apiKey: "secret-never-serialize",
                     models: [CodexModel(id: "test-model", name: "Test"), CodexModel(id: "__native__", name: "Native")], usesExistingProvider: existing)
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
        let output = try writer.rewriteConfig(input, selected: SelectedModel(serviceID: "baseten", modelID: "test-model"), data: AppData(services: [service("baseten", existing: true)], selectedModel: nil))
        XCTAssertTrue(output.contains(input))
        XCTAssertFalse(output.contains("secret-never-serialize"))
        XCTAssertTrue(output.contains("model_provider = \"baseten\""))
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
    func testGrokOAuthUsesOnlyLocalCredentialAndPreservesAPIProvider() throws {
        let source = "[model_providers.xai]\nbase_url = \"https://api.x.ai/v1\"\nenv_key = \"XAI_API_KEY\"\n"
        let oauth = CodexService(id: "grok-oauth", name: "Grok browser sign-in", baseURL: "http://127.0.0.1:48118/oauth/v1", envKey: "", apiKey: "", models: [CodexModel(id: "grok-4.6", name: "Grok 4.6")])
        let selection = SelectedModel(serviceID: oauth.id, modelID: "grok-4.6")
        let data = AppData(services: [oauth], selectedModel: selection)
        let output = try writer.rewriteConfig(source, selected: selection, data: data)
        XCTAssertTrue(output.contains(source))
        XCTAssertTrue(output.contains("model_provider = \"grok-oauth\""))
        XCTAssertTrue(output.contains("/oauth/v1"))
        XCTAssertTrue(output.contains("command = \"/bin/cat\""))
        XCTAssertTrue(output.contains("model-harbor-bridge-token"))
        let again = try writer.rewriteConfig(output, selected: selection, data: data)
        XCTAssertEqual(again.components(separatedBy: "[model_providers.grok-oauth]").count, 2)
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
