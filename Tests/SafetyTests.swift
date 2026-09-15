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
