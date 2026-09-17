import XCTest
@testable import HarborCore

final class ModelPickerTests: XCTestCase {
    private let azure = SelectedModel(serviceID: "azure", modelID: "deployment")
    private let baseten = SelectedModel(serviceID: "baseten", modelID: "test-model")

    private func fixture() -> AppData {
        AppData(services: [
            CodexService(id: "azure", name: "Azure", baseURL: "https://example.openai.azure.com/openai/v1",
                         envKey: "AZURE_OPENAI_API_KEY", apiKey: "secret-key",
                         models: [.init(id: "deployment", name: "Deployment")]),
            CodexService(id: "baseten", name: "Baseten", baseURL: "https://inference.baseten.co/v1",
                         envKey: "BASETEN_API_KEY", apiKey: "secret-key",
                         models: [.init(id: "test-model", name: "Test model"), .init(id: "other", name: "Other")])
        ], selectedModel: azure, legacyModel: baseten)
    }

    private func entries(_ data: AppData) throws -> [[String: Any]] {
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: LiveRouting.catalog(in: data)) as? [String: Any])
        return try XCTUnwrap(json["models"] as? [[String: Any]])
    }

    func testDefaultProviderIsListedFirst() throws {
        var data = fixture()
        data.services.reverse()
        XCTAssertEqual(data.pickerServices.first?.id, "azure")
        XCTAssertEqual(try entries(data).first?["slug"] as? String, "harbor/azure/deployment")
        data.selectedModel = baseten
        XCTAssertEqual(try entries(data).first?["slug"] as? String, "harbor/baseten/test-model")
    }

    func testExistingMetadataKeepsAllPickerEntriesVisible() throws {
        var json = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(fixture())) as? [String: Any])
        json.removeValue(forKey: "modelPicker")
        let old = try JSONDecoder().decode(AppData.self, from: JSONSerialization.data(withJSONObject: json))
        XCTAssertEqual(old.modelPicker, ModelPickerPreferences())
        XCTAssertEqual(try entries(old).filter { $0["visibility"] as? String == "list" }.count, 3)
    }

    func testHiddenProviderRetainsRoutesAndHidesFutureAdditions() throws {
        var data = fixture()
        try data.setPickerVisibility(false, forProvider: "baseten")
        data.services[1].models.append(.init(id: "new-model", name: "New"))
        let catalog = try entries(data)
        XCTAssertEqual(catalog.filter { $0["visibility"] as? String == "list" }.map { $0["slug"] as? String }, ["harbor/azure/deployment"])
        XCTAssertEqual(catalog.count, 5)
        XCTAssertEqual(LiveRouting.selection(for: "harbor/baseten/test-model", in: data), baseten)
        XCTAssertEqual(LiveRouting.selection(for: "harbor/baseten/new-model", in: data)?.modelID, "new-model")
        XCTAssertEqual(data.legacyModel, baseten)
        XCTAssertEqual(data.selectedModel, azure)
        XCTAssertEqual(data.services[1].apiKey, "secret-key")
    }

    func testIndividualChoicesSurviveProviderHideShowAndMetadataReload() throws {
        var data = fixture()
        try data.setPickerVisibility(false, for: baseten)
        try data.setPickerVisibility(false, forProvider: "baseten")
        try data.setPickerVisibility(true, forProvider: "baseten")
        let metadata = try JSONEncoder().encode(data)
        XCTAssertFalse(String(decoding: metadata, as: UTF8.self).contains("secret-key"))
        let restored = try JSONDecoder().decode(AppData.self, from: metadata)
        XCTAssertEqual(restored.modelPicker, data.modelPicker)
        XCTAssertEqual(restored.visibleModels(in: restored.services[1]).map(\.id), ["other"])
        XCTAssertTrue(restored.modelPicker.isVisible(azure))
    }

    func testCannotHideDefaultAndRejectedChangeDoesNotMutatePreferences() throws {
        var data = fixture()
        XCTAssertThrowsError(try data.setPickerVisibility(false, for: azure))
        XCTAssertThrowsError(try data.setPickerVisibility(false, forProvider: "azure"))
        XCTAssertEqual(data.modelPicker, ModelPickerPreferences())
        data.selectedModel = baseten
        try data.setPickerVisibility(false, forProvider: "azure")
        XCTAssertFalse(data.modelPicker.isVisible(azure))
    }

    func testInvalidModelsCannotChangePickerAndSameNamesAreScopedToProvider() throws {
        var data = fixture()
        XCTAssertThrowsError(try data.setPickerVisibility(false, for: .init(serviceID: "azure", modelID: "missing")))
        XCTAssertThrowsError(try data.setPickerVisibility(false, forProvider: "missing"))
        data.services[1].models.append(.init(id: "deployment", name: "Same name"))
        try data.setPickerVisibility(false, for: .init(serviceID: "baseten", modelID: "deployment"))
        XCTAssertTrue(data.modelPicker.isVisible(azure))
        XCTAssertEqual(data.modelPicker.hiddenModels, ["harbor/baseten/deployment"])
    }
}
