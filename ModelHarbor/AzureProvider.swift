import Foundation

struct AzureDeployment {
    let name: String
    var effort = "none"
    var context = 128000
    var vision = false

    func validate() throws {
        guard name.range(of: #"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"#, options: .regularExpression) != nil else {
            throw ProviderError.message("Enter the exact Azure deployment name using letters, numbers, dots, underscores, or hyphens.")
        }
        guard ["none", "low", "medium", "high", "xhigh"].contains(effort), (4096...1_048_576).contains(context) else {
            throw ProviderError.message("Choose a valid reasoning setting and context limit between 4,096 and 1,048,576 tokens.")
        }
    }

    var catalogEntry: [String: Any] {
        ["slug": name, "display_name": name,
         "base_instructions": "You are a coding assistant. Follow instructions, use tools, and verify your work.",
         "default_reasoning_level": effort,
         "supported_reasoning_levels": [["effort": effort, "description": effort == "none" ? "Deployment default" : effort.capitalized]],
         "shell_type": "shell_command", "context_window": context, "max_context_window": context,
         "input_modalities": vision ? ["text", "image"] : ["text"], "support_verbosity": false,
         "use_responses_lite": false, "supports_websockets": false,
         "truncation_policy": ["mode": "tokens", "limit": 10000], "experimental_supported_tools": []]
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
        let session = URLSession(configuration: .ephemeral, delegate: AzureNoRedirect(), delegateQueue: nil)
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
