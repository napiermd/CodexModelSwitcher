import Foundation
import CryptoKit

/// A stable provider with a separate model identity for each route.
enum LiveRouting {
    static let providerID = "model-harbor"
    static let modelID = "harbor-selected" // Hidden compatibility entry for existing tasks.
    static let catalogURL = AppPaths.codexDirectory.appendingPathComponent("model-catalogs/model-harbor.json")

    struct CatalogCandidate {
        let data: Data
        let inputs: [URL: Data]

        func inputsAreUnchanged(read: (URL) throws -> Data = { try Data(contentsOf: $0) }) -> Bool {
            inputs.allSatisfy { url, captured in (try? read(url)) == captured }
        }
    }

    static func supports(_ serviceID: String) -> Bool {
        ["grok-oauth", "baseten", "codex-subscription", "openrouter", "azure"].contains(serviceID)
    }

    static func modelID(for selection: SelectedModel) -> String {
        "harbor/\(selection.serviceID)/\(selection.modelID)"
    }

    static func selection(for modelID: String, in data: AppData) -> SelectedModel? {
        for service in data.pickerServices {
            for model in service.models {
                let selection = SelectedModel(serviceID: service.id, modelID: model.id)
                if Self.modelID(for: selection) == modelID { return selection }
            }
        }
        return nil
    }

    static func providerName(_ id: String) -> String {
        switch id {
        case "codex-subscription": return "Codex subscription"
        case "grok-oauth": return "Grok"
        case "openrouter": return "OpenRouter"
        case "azure": return "Azure OpenAI"
        default: return "Baseten"
        }
    }

    static func normalizeReasoningLevels(in model: [String: Any]) -> [String: Any] {
        guard let levels = model["supported_reasoning_levels"] as? [[String: Any]] else { return model }
        let order = ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
        var result = model
        result["supported_reasoning_levels"] = levels.enumerated().sorted {
            let left = order.firstIndex(of: $0.element["effort"] as? String ?? "") ?? order.count
            let right = order.firstIndex(of: $1.element["effort"] as? String ?? "") ?? order.count
            return (left, $0.offset) < (right, $1.offset)
        }.map(\.element)
        return result
    }

    static func catalog(in data: AppData) throws -> Data {
        try catalogCandidate(in: data).data
    }

    static func catalogCandidate(in data: AppData,
                                 read: (URL) throws -> Data = { try Data(contentsOf: $0) }) throws -> CatalogCandidate {
        var entries: [[String: Any]] = []
        var provenance: [[String: Any]] = []
        var inputs: [URL: Data] = [:]
        var sources: [String: [[String: Any]]] = [:]
        for service in data.pickerServices {
            guard let path = service.catalogPath else { continue }
            let url = URL(fileURLWithPath: path)
            let bytes = try read(url)
            guard let object = try JSONSerialization.jsonObject(with: bytes) as? [String: Any],
                  let models = object["models"] as? [[String: Any]] else {
                throw CocoaError(.fileReadCorruptFile)
            }
            inputs[url] = bytes
            sources[service.id] = models
            var origin: [String: Any] = ["provider": service.id, "path": path,
                "sha256": SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()]
            origin["client_version"] = object["client_version"]
            origin["fetched_at"] = object["fetched_at"]
            provenance.append(origin)
        }
        let azureSources = sources["azure"] ?? []
        let azureNeedsNative = data.pickerServices.first(where: { $0.id == "azure" })?.models.contains { model in
            guard let entry = azureSources.first(where: { $0["slug"] as? String == model.id }) else { return true }
            let mode = entry["harbor_context_mode"] as? String
            return mode == "automatic" || (mode == nil && entry["context_window"] as? Int == 128000
                && entry["max_context_window"] as? Int == 128000)
        } ?? false
        var nativeMetadata: Data?
        if azureNeedsNative {
            let url = AppPaths.codexDirectory.appendingPathComponent("models_cache.json")
            let bytes = try read(url)
            guard let object = try JSONSerialization.jsonObject(with: bytes) as? [String: Any],
                  object["models"] is [[String: Any]] else { throw CocoaError(.fileReadCorruptFile) }
            nativeMetadata = bytes
            inputs[url] = bytes
            provenance.append(["provider": "azure_native_context", "path": url.path,
                "sha256": SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()])
        }
        for service in data.pickerServices {
            let source = sources[service.id] ?? []
            for model in service.models {
                var entry = source.first { $0["slug"] as? String == model.id } ?? [
                    "base_instructions": "You are a coding assistant. Follow the user instructions, use the available tools, and verify results.",
                    "default_reasoning_level": "high",
                    "supported_reasoning_levels": ["low", "medium", "high"].map { ["effort": $0, "description": "\($0.capitalized) reasoning"] },
                    "shell_type": "shell_command", "context_window": 128000, "max_context_window": 128000,
                    "input_modalities": ["text", "image"], "support_verbosity": false,
                    "truncation_policy": ["mode": "tokens", "limit": 10000],
                    "experimental_supported_tools": []
                ]
                entry["harbor_context_source"] = source.contains { $0["slug"] as? String == model.id } ? "provider_catalog" : "fallback"
                if entry["harbor_context_source"] as? String == "fallback" {
                    entry["harbor_context_warning"] = "Unverified 128,000-token fallback. Verify the provider capacity."
                }
                if service.id == "azure", let nativeMetadata {
                    entry = AzureDeployment.resolvingContext(in: entry, readNativeMetadata: { nativeMetadata })
                }
                entry = try normalizedContext(in: entry)
                let routeID = modelID(for: SelectedModel(serviceID: service.id, modelID: model.id))
                entry["slug"] = routeID
                if service.id == "azure" { entry["auto_review_model_override"] = routeID }
                entry["display_name"] = "\(model.name) · \(providerName(service.id))"
                entry["description"] = "Uses \(providerName(service.id)) through Model Harbor. Selected independently for this task."
                entry["visibility"] = data.modelPicker.isVisible(SelectedModel(serviceID: service.id, modelID: model.id)) ? "list" : "hide"
                entry["supported_in_api"] = true
                entry["priority"] = entries.count
                entry["supports_parallel_tool_calls"] = false
                entries.append(normalizeReasoningLevels(in: entry))
            }
        }
        if let legacy = data.legacyModel,
           var entry = entries.first(where: { $0["slug"] as? String == modelID(for: legacy) }) {
            entry["slug"] = modelID
            entry["display_name"] = "Previous Harbor selection"
            entry["description"] = "Fixed to the model used before the per-task upgrade. Choose a named model in the task picker."
            entry["visibility"] = "hide"
            entries.append(entry)
        }
        let result = try JSONSerialization.data(withJSONObject: ["models": entries, "harbor_sources": provenance], options: [.prettyPrinted, .sortedKeys])
        return CatalogCandidate(data: result, inputs: inputs)
    }

    private static func normalizedContext(in original: [String: Any]) throws -> [String: Any] {
        var entry = original
        let defaultWindow = entry["context_window"]
        let maximumWindow = entry["max_context_window"]
        if defaultWindow == nil && maximumWindow == nil {
            entry["context_window"] = 128000
            entry["max_context_window"] = 128000
            entry["harbor_context_warning"] = "Unverified 128,000-token fallback. Verify the provider capacity."
            return entry
        }
        guard (defaultWindow == nil || defaultWindow is Int), (maximumWindow == nil || maximumWindow is Int) else {
            throw CocoaError(.fileReadCorruptFile)
        }
        let defaultValue = defaultWindow as? Int ?? maximumWindow as! Int
        let maximumValue = maximumWindow as? Int ?? defaultValue
        guard (4096...1_048_576).contains(defaultValue), (4096...1_048_576).contains(maximumValue),
              defaultValue <= maximumValue else { throw CocoaError(.fileReadCorruptFile) }
        entry["context_window"] = defaultValue
        entry["max_context_window"] = maximumValue
        return entry
    }

    static func service(in data: AppData) -> CodexService {
        let models = data.services.filter { supports($0.id) }.flatMap { service in
            service.models.map { model in
                CodexModel(id: modelID(for: SelectedModel(serviceID: service.id, modelID: model.id)), name: model.name)
            }
        }
        return CodexService(id: providerID, name: "Model Harbor", baseURL: "http://127.0.0.1:48118/harbor/v1",
            envKey: "", apiKey: "", models: models, catalogPath: catalogURL.path)
    }
}
