import Foundation

struct AzureDeployment {
    let name: String
    var modelName: String? = nil
    var effort = "none"
    var context = 128000
    var vision = false
    var contextIsAutomatic: Bool? = nil

    func validate() throws {
        guard name.range(of: #"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"#, options: .regularExpression) != nil else {
            throw ProviderError.message("Enter the exact Azure deployment name using letters, numbers, dots, underscores, or hyphens.")
        }
        guard ["none", "low", "medium", "high", "xhigh"].contains(effort), (4096...1_048_576).contains(context) else {
            throw ProviderError.message("Choose a valid reasoning setting and context limit between 4,096 and 1,048,576 tokens.")
        }
    }

    var catalogEntry: [String: Any] {
        catalogEntry(readNativeMetadata: Self.readNativeMetadata)
    }

    static func readNativeMetadata() throws -> Data {
        try Data(contentsOf: AppPaths.codexDirectory.appendingPathComponent("models_cache.json"))
    }

    func catalogEntry(readNativeMetadata: () throws -> Data) -> [String: Any] {
        var entry: [String: Any] = ["slug": name, "display_name": name,
         "base_instructions": "You are a coding assistant. Follow instructions, use tools, and verify your work.",
         "default_reasoning_level": effort,
         "supported_reasoning_levels": [["effort": effort, "description": effort == "none" ? "Deployment default" : effort.capitalized]],
         "shell_type": "shell_command", "context_window": context, "max_context_window": context,
         "input_modalities": vision ? ["text", "image"] : ["text"], "support_verbosity": false,
         "use_responses_lite": false, "supports_websockets": false,
         "truncation_policy": ["mode": "tokens", "limit": 10000], "experimental_supported_tools": []]
        entry["harbor_model_name"] = modelName
        entry["harbor_context_mode"] = (contextIsAutomatic ?? (context == 128000)) ? "automatic" : "manual"
        return Self.resolvingContext(in: entry, readNativeMetadata: readNativeMetadata)
    }

    static func resolvingContext(in entry: [String: Any],
                                 readNativeMetadata: () throws -> Data = readNativeMetadata) -> [String: Any] {
        let mode = entry["harbor_context_mode"] as? String
        let legacyDefault = mode == nil && entry["context_window"] as? Int == 128000
            && entry["max_context_window"] as? Int == 128000
        guard mode == "automatic" || legacyDefault else { return entry }
        guard let bytes = try? readNativeMetadata(),
              let object = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any],
              let models = object["models"] as? [[String: Any]] else { return entry }
        let identities = [entry["slug"] as? String, entry["harbor_model_name"] as? String].compactMap { $0 }
        guard let native = identities.lazy.compactMap({ identity in
            models.first { $0["slug"] as? String == identity }
        }).first else { return entry }
        var result = entry
        for key in ["context_window", "max_context_window"] {
            if let limit = native[key] as? Int, limit >= 4096, limit <= 1_048_576 {
                result[key] = limit
            }
        }
        result["harbor_context_mode"] = "automatic"
        return result
    }
}

struct AzureDiscoveredDeployment: Identifiable, Equatable {
    let id: String
    let modelName: String?
    let version: String?

    var label: String {
        guard let modelName, modelName != id else { return id }
        return "\(id) — \(modelName)"
    }
}

enum AzureAPI {
    static func endpoint(_ value: String) throws -> URL {
        let text = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let parts = URLComponents(string: text), parts.scheme == "https",
              let host = parts.host?.lowercased(),
              host.range(of: #"^[a-z0-9][a-z0-9-]*\.(openai\.azure\.com|services\.ai\.azure\.com)$"#, options: .regularExpression) != nil,
              parts.user == nil, parts.password == nil, parts.port == nil, parts.query == nil, parts.fragment == nil,
              ["", "/", "/openai/v1", "/openai/v1/"].contains(parts.path),
              let url = URL(string: "https://\(host)/openai/v1") else {
            throw ProviderError.message("Use your HTTPS Azure resource endpoint ending in openai.azure.com or services.ai.azure.com, optionally followed by /openai/v1/.")
        }
        return url
    }

    static func validateKey(_ value: String) throws -> String {
        let key = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !key.isEmpty, key.utf8.count <= 4096, !key.contains(where: { $0.isWhitespace || $0.isNewline }) else {
            throw ProviderError.message("Enter your Azure OpenAI API key. It will be saved in macOS Keychain.")
        }
        return key
    }

    static func discoveryRequest(endpoint: String, key: String) throws -> URLRequest {
        let base = try self.endpoint(endpoint)
        var parts = URLComponents(url: base, resolvingAgainstBaseURL: false)!
        parts.path = "/openai/deployments"
        parts.queryItems = [URLQueryItem(name: "api-version", value: "2023-03-15-preview")]
        var request = URLRequest(url: parts.url!, timeoutInterval: 20)
        request.setValue(try validateKey(key), forHTTPHeaderField: "api-key")
        return request
    }

    static func deploymentOptions(_ bytes: Data) throws -> [AzureDiscoveredDeployment] {
        guard let result = try JSONSerialization.jsonObject(with: bytes) as? [String: Any],
              let rows = result["data"] as? [[String: Any]] else {
            throw ProviderError.message("Azure did not return deployment names. Enter the exact name from Azure manually.")
        }
        var seen = Set<String>()
        let deployments = rows.compactMap { row -> AzureDiscoveredDeployment? in
            guard let name = row["id"] as? String,
                  row["object"] as? String != "model",
                  (row["status"] as? String).map({ $0.lowercased() == "succeeded" }) ?? true,
                  (try? AzureDeployment(name: name).validate()) != nil,
                  seen.insert(name).inserted else { return nil }
            let model = row["model"] as? [String: Any]
            let modelName = model?["name"] as? String ?? row["model"] as? String
            let version = model?["version"] as? String ?? row["model_version"] as? String
            return AzureDiscoveredDeployment(id: name,
                modelName: modelName.flatMap { $0.isEmpty ? nil : $0 },
                version: version.flatMap { $0.isEmpty ? nil : $0 })
        }
        return deployments.sorted { $0.id.localizedStandardCompare($1.id) == .orderedAscending }
    }

    static func discoverDeployments(endpoint: String, key: String) async throws -> [AzureDiscoveredDeployment] {
        let request = try discoveryRequest(endpoint: endpoint, key: key)
        let session = session(timeout: 20)
        defer { session.invalidateAndCancel() }
        let (bytes, response) = try await session.data(for: request)
        try Task.checkCancellation()
        guard let http = response as? HTTPURLResponse else { throw ProviderError.message("Azure did not return an HTTP response.") }
        switch http.statusCode {
        case 200: return try deploymentOptions(bytes)
        case 401, 403: throw ProviderError.message("Azure rejected this key or resource access. Check that the key belongs to this endpoint.")
        case 429: throw ProviderError.message("Azure is limiting deployment discovery. Wait briefly or enter a deployment name manually.")
        default: throw ProviderError.message("This Azure resource does not expose deployment discovery (HTTP \(http.statusCode)). Enter the exact deployment name from Azure manually.")
        }
    }

    private static func session(timeout: TimeInterval) -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = timeout
        configuration.timeoutIntervalForResource = timeout
        return URLSession(configuration: configuration, delegate: AzureNoRedirect(), delegateQueue: nil)
    }

    static func verificationBody(_ deployment: AzureDeployment) -> [String: Any] {
        var body: [String: Any] = [
            "model": deployment.name, "store": false, "stream": false, "max_output_tokens": 4096,
            "input": "Call harbor_connection_check with no arguments to confirm the connection.",
            "tools": [["type": "function", "name": "harbor_connection_check", "description": "Confirm a connection. This tool has no side effects.",
                       "parameters": ["type": "object", "properties": [:], "required": [], "additionalProperties": false]]],
            "tool_choice": ["type": "function", "name": "harbor_connection_check"]]
        if deployment.effort != "none" { body["reasoning"] = ["effort": deployment.effort] }
        if deployment.vision {
            body["input"] = [["role": "user", "content": [
                ["type": "input_text", "text": "Call harbor_connection_check with no arguments. The small test image can be ignored."],
                ["type": "input_image", "image_url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC"]]]]
        }
        return body
    }

    static func verify(endpoint: String, key: String, deployment: AzureDeployment) async throws {
        try deployment.validate()
        let base = try self.endpoint(endpoint)
        let credential = try validateKey(key)
        var request = URLRequest(url: base.appendingPathComponent("responses"), timeoutInterval: 60)
        request.httpMethod = "POST"
        request.setValue(credential, forHTTPHeaderField: "api-key")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: verificationBody(deployment))
        let session = session(timeout: 60)
        defer { session.invalidateAndCancel() }
        let (bytes, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw ProviderError.message("Azure did not return an HTTP response.") }
        guard http.statusCode == 200 else {
            let guidance: String
            switch http.statusCode {
            case 401, 403: guidance = "Check the resource endpoint, API key, and network access rules."
            case 404: guidance = "Check the deployment name and that it belongs to this Azure resource."
            case 429: guidance = "Azure is limiting this deployment. Check its quota and retry after the cooldown."
            case 400: guidance = "Check Responses API support, the reasoning setting, and image support for this deployment."
            case 300..<400: guidance = "Redirects are blocked to protect your key. Use the Azure resource endpoint directly."
            default: guidance = "Check the deployment status in Azure and try again."
            }
            throw ProviderError.message("Azure returned HTTP \(http.statusCode). \(guidance)")
        }
        try Task.checkCancellation()
        try validateResponse(bytes)
    }

    static func validateResponse(_ bytes: Data) throws {
        let result = try JSONSerialization.jsonObject(with: bytes) as? [String: Any]
        let output = result?["output"] as? [[String: Any]] ?? []
        guard result?["status"] as? String == "completed",
              output.contains(where: { $0["type"] as? String == "function_call" && $0["name"] as? String == "harbor_connection_check" }) else {
            throw ProviderError.message("Azure did not complete the tool-call check. Try a lower reasoning setting or a deployment with Responses tool support.")
        }
    }
}

private final class AzureNoRedirect: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}
