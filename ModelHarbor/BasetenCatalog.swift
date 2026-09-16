import Foundation

/// Bundled, transport-specific capabilities. Account authentication stays in the provider.
enum BasetenCatalog {
    struct Manifest: Decodable { let models: [Model] }
    struct Model: Decodable {
        let id: String
        let name: String
        let context_window: Int
        let input_modalities: [String]
        let efforts: [String]
        let default_effort: String
        let reasoning_mode: String
    }

    static func install(in data: inout AppData, manifest: Data, destination: URL) throws {
        guard let index = data.services.firstIndex(where: { $0.id == "baseten" }) else { return }
        let models = try JSONDecoder().decode(Manifest.self, from: manifest).models
        guard Set(models.map(\.id)).count == models.count,
              models.allSatisfy({ !$0.id.isEmpty && $0.context_window > 0 && $0.efforts.contains($0.default_effort) }) else {
            throw AppError.invalidModelList
        }
        var entries: [[String: Any]] = []
        if let oldPath = data.services[index].catalogPath,
           let old = try? Data(contentsOf: URL(fileURLWithPath: oldPath)),
           let object = try? JSONSerialization.jsonObject(with: old) as? [String: Any] {
            entries = object["models"] as? [[String: Any]] ?? []
        }
        for model in models {
            entries.removeAll { $0["slug"] as? String == model.id }
            entries.append([
                "slug": model.id, "display_name": model.name,
                "base_instructions": "You are a coding assistant. Follow the user's instructions, use tools, and verify your work.",
                "default_reasoning_level": model.default_effort,
                "supported_reasoning_levels": model.efforts.map { effort in
                    ["effort": effort, "description": model.reasoning_mode == "thinking"
                        ? "Thinking enabled. The provider does not expose reasoning depth."
                        : (effort == "xhigh" ? "Highest verified Baseten Responses effort" : "\(effort.capitalized) reasoning")]
                },
                "shell_type": "shell_command", "context_window": model.context_window,
                "max_context_window": model.context_window, "input_modalities": model.input_modalities,
                "support_verbosity": false, "visibility": "list", "supported_in_api": true,
                "truncation_policy": ["mode": "tokens", "limit": 10000],
                "supports_parallel_tool_calls": false, "experimental_supported_tools": []
            ])
            if let position = data.services[index].models.firstIndex(where: { $0.id == model.id }) {
                data.services[index].models[position].name = model.name
            } else {
                data.services[index].models.append(CodexModel(id: model.id, name: model.name))
            }
        }
        let bytes = try JSONSerialization.data(withJSONObject: ["models": entries], options: [.prettyPrinted, .sortedKeys])
        try FileManager.default.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
        if (try? Data(contentsOf: destination)) != bytes {
            try bytes.write(to: destination, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: destination.path)
        }
        data.services[index].catalogPath = destination.path
    }
}
