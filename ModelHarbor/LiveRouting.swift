import Foundation

/// A stable provider with a separate model identity for each route.
enum LiveRouting {
    static let providerID = "model-harbor"
    static let modelID = "harbor-selected" // Hidden compatibility entry for existing tasks.
    static let catalogURL = AppPaths.codexDirectory.appendingPathComponent("model-catalogs/model-harbor.json")

    static func supports(_ serviceID: String) -> Bool {
        ["grok-oauth", "baseten", "codex-subscription", "openrouter"].contains(serviceID)
    }

    static func modelID(for selection: SelectedModel) -> String {
        "harbor/\(selection.serviceID)/\(selection.modelID)"
    }

    static func selection(for modelID: String, in data: AppData) -> SelectedModel? {
        for service in data.services where supports(service.id) {
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
        default: return "Baseten"
        }
    }

    static func catalog(in data: AppData) throws -> Data {
        var entries: [[String: Any]] = []
        for service in data.services where supports(service.id) {
            let source: [[String: Any]]
            if let path = service.catalogPath,
               let bytes = try? Data(contentsOf: URL(fileURLWithPath: path)),
               let object = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any],
               let models = object["models"] as? [[String: Any]] {
                source = models
            } else { source = [] }
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
                entry["slug"] = modelID(for: SelectedModel(serviceID: service.id, modelID: model.id))
                entry["display_name"] = "\(model.name) · \(providerName(service.id))"
                entry["description"] = "Uses \(providerName(service.id)) through Model Harbor. Selected independently for this task."
                entry["visibility"] = "list"
                entry["supported_in_api"] = true
                entry["priority"] = entries.count
                entry["supports_parallel_tool_calls"] = false
                entries.append(entry)
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
        return try JSONSerialization.data(withJSONObject: ["models": entries], options: [.prettyPrinted, .sortedKeys])
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
