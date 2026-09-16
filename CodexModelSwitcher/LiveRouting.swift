import Foundation

/// One stable Codex provider; the bridge reads the saved selection for each new turn.
enum LiveRouting {
    static let providerID = "model-harbor"
    static let modelID = "harbor-selected"
    static let catalogURL = AppPaths.codexDirectory.appendingPathComponent("model-catalogs/model-harbor.json")

    static func supports(_ serviceID: String) -> Bool {
        serviceID == "grok-oauth" || serviceID == "baseten"
    }

    static func catalog(in data: AppData) throws -> Data {
        var windows: [Int] = []
        for service in data.services where supports(service.id) {
            if let path = service.catalogPath,
               let bytes = try? Data(contentsOf: URL(fileURLWithPath: path)),
               let object = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any],
               let models = object["models"] as? [[String: Any]] {
                windows += models.compactMap { $0["context_window"] as? Int }
            }
        }
        let window = windows.min() ?? 128000
        let model: [String: Any] = [
            "slug": modelID, "display_name": "Model Harbor selection",
            "description": "Follows the Grok or Baseten model selected in Model Harbor. Changes apply on your next turn.",
            "base_instructions": "You are a coding assistant. Follow the user instructions, use the available tools, and verify results.",
            "default_reasoning_level": "high",
            "supported_reasoning_levels": ["low", "medium", "high"].map { ["effort": $0, "description": "\($0.capitalized) reasoning"] },
            "shell_type": "shell_command", "visibility": "list", "supported_in_api": true,
            "priority": 0, "context_window": window, "max_context_window": window,
            "input_modalities": ["text", "image"], "support_verbosity": false,
            "truncation_policy": ["mode": "tokens", "limit": 10000],
            "supports_parallel_tool_calls": false, "experimental_supported_tools": []
        ]
        return try JSONSerialization.data(withJSONObject: ["models": [model]], options: [.prettyPrinted, .sortedKeys])
    }

    static func service() -> CodexService {
        CodexService(id: providerID, name: "Model Harbor", baseURL: "http://127.0.0.1:48118/harbor/v1",
            envKey: "", apiKey: "", models: [CodexModel(id: modelID, name: "Model Harbor selection")],
            catalogPath: catalogURL.path)
    }
}
