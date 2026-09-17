import XCTest
@testable import HarborCore

final class AzureProviderTests: XCTestCase {
    func testAzureEndpointNormalizesOnlyDirectResources() throws {
        XCTAssertEqual(try AzureAPI.endpoint(" https://test-resource.openai.azure.com/ ").absoluteString,
                       "https://test-resource.openai.azure.com/openai/v1")
        XCTAssertEqual(try AzureAPI.endpoint("https://test-resource.services.ai.azure.com/openai/v1/").absoluteString,
                       "https://test-resource.services.ai.azure.com/openai/v1")
        for value in ["http://test.openai.azure.com", "https://openai.azure.com", "https://test.openai.azure.com.evil.test",
                      "https://user:secret@test.openai.azure.com", "https://test.openai.azure.com:443",
                      "https://test.openai.azure.com/openai/v1/?key=secret", "https://test.openai.azure.com/#x",
                      "https://localhost/openai/v1", "https://test.openai.azure.com/other"] {
            XCTAssertThrowsError(try AzureAPI.endpoint(value), value)
        }
    }

    func testDeploymentAndKeyRejectHeaderOrPathInjection() throws {
        for name in ["", "../other", "a/b", "hello world", String(repeating: "x", count: 129)] {
            XCTAssertThrowsError(try AzureDeployment(name: name).validate())
        }
        for key in ["", "a\r\nsecret", "a b", String(repeating: "x", count: 4097)] {
            XCTAssertThrowsError(try AzureAPI.validateKey(key))
        }
        try AzureDeployment(name: "coding-prod_1.2").validate()
    }

    func testCheckUsesExactDeploymentAndSelectedCapabilities() throws {
        let model = AzureDeployment(name: "my-deployment", effort: "medium", context: 128000, vision: true)
        let body = AzureAPI.verificationBody(model)
        XCTAssertEqual(body["model"] as? String, "my-deployment")
        XCTAssertEqual(body["store"] as? Bool, false)
        XCTAssertEqual((body["reasoning"] as? [String: String])?["effort"], "medium")
        XCTAssertTrue(try String(decoding: JSONSerialization.data(withJSONObject: body), as: UTF8.self).contains("input_image"))
        let defaults = AzureAPI.verificationBody(AzureDeployment(name: "my-deployment"))
        XCTAssertNil(defaults["reasoning"])
        XCTAssertFalse(try String(decoding: JSONSerialization.data(withJSONObject: defaults), as: UTF8.self).contains("input_image"))
    }

    func testVerificationRequiresCompletedToolCall() throws {
        try AzureAPI.validateResponse(Data(#"{"status":"completed","output":[{"type":"function_call","name":"harbor_connection_check"}]}"#.utf8))
        for value in [#"{"status":"completed","output":[]}"#, #"{"status":"incomplete","output":[{"type":"function_call","name":"harbor_connection_check"}]}"#,
                      #"{"status":"completed","output":[{"type":"function_call","name":"wrong"}]}"#] {
            XCTAssertThrowsError(try AzureAPI.validateResponse(Data(value.utf8)))
        }
    }

    func testAzureCatalogKeepsExactModelAndDoesNotAdvertiseUnverifiedEfforts() throws {
        let temp = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: temp) }
        let model = AzureDeployment(name: "coding-deploy", effort: "medium", context: 200000, vision: true)
        try JSONSerialization.data(withJSONObject: ["models": [model.catalogEntry]]).write(to: temp)
        var data = AppData.empty
        data.services = [CodexService(id: "azure", name: "Azure OpenAI", baseURL: "https://fixture.openai.azure.com/openai/v1",
                                     envKey: "AZURE_OPENAI_API_KEY", apiKey: "fixture-secret", models: [.init(id: model.name, name: model.name)], catalogPath: temp.path)]
        let raw = try LiveRouting.catalog(in: data)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: raw) as? [String: Any])
        let entries = try XCTUnwrap(object["models"] as? [[String: Any]])
        XCTAssertEqual(entries.first?["slug"] as? String, "harbor/azure/coding-deploy")
        XCTAssertEqual(entries.first?["use_responses_lite"] as? Bool, false)
        XCTAssertEqual(entries.first?["supported_reasoning_levels"] as? [[String: String]], [["effort": "medium", "description": "Medium"]])
        XCTAssertEqual(entries.first?["input_modalities"] as? [String], ["text", "image"])
        XCTAssertFalse(String(decoding: raw, as: UTF8.self).contains("fixture-secret"))
    }
}
