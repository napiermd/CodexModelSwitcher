import Foundation

enum AppPaths {
    static let codexDirectory: URL = {
        if let path = ProcessInfo.processInfo.environment["MODEL_SWITCHER_CONFIG_DIR"] {
            return URL(fileURLWithPath: path, isDirectory: true)
        }
        return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".codex", isDirectory: true)
    }()
    static let codexConfig = codexDirectory.appendingPathComponent("config.toml")
    static let appData = codexDirectory.appendingPathComponent("model-switcher.json")
    static let loginDirectory = codexDirectory
        .appendingPathComponent("model-switcher", isDirectory: true)
        .appendingPathComponent("login", isDirectory: true)
    static let envFile = codexDirectory.appendingPathComponent("model-switcher.env")
    static let shellProfile = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".zprofile")
}

enum AppError: LocalizedError {
    case missingSelectedModel
    case missingService
    case missingModel
    case invalidServiceID
    case invalidModelList
    case duplicateServiceID
    case openAIAccountLoginInProgress
    case openAIAccountLoginFailed

    var errorDescription: String? {
        switch self {
        case .missingSelectedModel:
            return "Choose a model first."
        case .missingService:
            return "The selected service no longer exists."
        case .missingModel:
            return "The selected model no longer exists."
        case .invalidServiceID:
            return "Service ID can contain only letters, numbers, hyphens, and underscores."
        case .invalidModelList:
            return "Add at least one model."
        case .duplicateServiceID:
            return "A service with that ID already exists."
        case .openAIAccountLoginInProgress:
            return "OpenAI account login is already running."
        case .openAIAccountLoginFailed:
            return "OpenAI login did not finish. Please try again."
        }
    }
}

func slugify(_ value: String) -> String {
    let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_"))
    let scalars = value.lowercased().unicodeScalars.map { scalar in
        allowed.contains(scalar) ? Character(scalar) : "-"
    }
    let collapsed = String(scalars)
        .split(separator: "-", omittingEmptySubsequences: true)
        .joined(separator: "-")
    return collapsed.isEmpty ? "service" : collapsed
}

func tomlEscape(_ value: String) -> String {
    value
        .replacingOccurrences(of: "\\", with: "\\\\")
        .replacingOccurrences(of: "\"", with: "\\\"")
        .replacingOccurrences(of: "\n", with: "\\n")
}

func shellEscape(_ value: String) -> String {
    "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
}

func parseModelRows(_ text: String) -> [CodexModel] {
    text
        .split(whereSeparator: \.isNewline)
        .map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) }
        .filter { !$0.isEmpty }
        .map { CodexModel(id: $0, name: $0) }
}

func isValidServiceID(_ id: String) -> Bool {
    let pattern = #"^[A-Za-z0-9_-]+$"#
    return id.range(of: pattern, options: .regularExpression) != nil
}
